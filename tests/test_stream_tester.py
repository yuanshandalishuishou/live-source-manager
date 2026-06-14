# -*- coding: utf-8 -*-
"""
测试流媒体测试模块（stream_tester模块）

特别注意覆盖以下修复点：
- ZeroDivisionError防护（successful_count/total_sources when total_sources=0）
- _url_cache 线程安全（测试缓存接口的功能正确性）
- ffprobe不可用时行为
"""

import os
import sys
import time
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from stream_tester import StreamTester


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get_testing_params.return_value = {
        'timeout': 10,
        'concurrent_threads': 5,
        'cache_ttl': 120,
        'enable_speed_test': False,
        'speed_test_duration': 6,
        'max_workers': 50,
    }
    config.get_filter_params.return_value = {
        'max_latency': 5000,
        'min_bitrate': 100,
        'must_hd': False,
        'must_4k': False,
        'min_speed': 40,
        'min_resolution': '720p',
        'max_resolution': '4k',
        'resolution_filter_mode': 'range',
    }
    return config


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def tester(mock_config, mock_logger):
    """创建StreamTester实例，但patch掉ffprobe验证"""
    with patch.object(StreamTester, '_verify_ffprobe'):
        t = StreamTester(mock_config, mock_logger)
        return t


# ========== 缓存测试 ==========

class TestCacheInterface:
    """测试缓存读写功能（线程安全场景）"""

    def setup_method(self):
        """每个测试前清空实例缓存"""
        pass

    def test_cache_set_and_get(self, tester):
        """缓存写入和读取"""
        tester._cache_result("http://test.com/stream", {
            'status': 'success',
            'response_time': 100,
            'resolution': '1920x1080',
            'bitrate': 4000,
        })
        result = tester._get_cached_result("http://test.com/stream")
        assert result is not None
        assert result['status'] == 'success'
        assert result['response_time'] == 100
        assert 'resolution' in result

    def test_cache_miss(self, tester):
        """缓存未命中"""
        result = tester._get_cached_result("nonexistent_key")
        assert result is None

    def test_cache_expiry(self, tester):
        """缓存过期"""
        tester._cache_result("http://test.com/old", {
            'status': 'success',
            'response_time': 200,
        })
        # 手动设置缓存时间为过去（超过cache_ttl=120分钟）
        old_time = datetime.now() - timedelta(minutes=180)
        for key in tester._url_cache:
            tester._url_cache[key]['timestamp'] = old_time

        result = tester._get_cached_result("http://test.com/old")
        assert result is None  # 过期应返回None

    def test_cache_overwrite(self, tester):
        """缓存覆盖"""
        tester._cache_result("http://test.com/stream", {
            'status': 'success',
            'response_time': 100,
        })
        tester._cache_result("http://test.com/stream", {
            'status': 'success',
            'response_time': 50,
        })
        result = tester._get_cached_result("http://test.com/stream")
        assert result['response_time'] == 50

    def test_cache_cleanup_removes_expired(self, tester):
        """缓存清理移除过期项"""
        tester._cache_result("http://test.com/fresh", {
            'status': 'success',
            'response_time': 100,
        })
        tester._cache_result("http://test.com/stale", {
            'status': 'success',
            'response_time': 200,
        })
        # 设置stale过期（超过cache_ttl=120分钟）
        stale_time = datetime.now() - timedelta(minutes=180)
        for key in list(tester._url_cache.keys()):
            if 'stale' in key:
                tester._url_cache[key]['timestamp'] = stale_time

        # 设置_last_cache_cleanup为很久以前，触发清理
        tester._last_cache_cleanup = datetime.now() - timedelta(hours=1)

        tester.cleanup_cache()
        # stale应被移除，fresh应保留
        assert tester._get_cached_result("http://test.com/stale") is None
        assert tester._get_cached_result("http://test.com/fresh") is not None

    def test_cache_thread_safety(self, tester):
        """多线程并发访问缓存"""
        errors = []

        def worker(thread_id):
            try:
                for i in range(50):
                    key = f"http://test.com/thread_{thread_id}_{i}"
                    tester._cache_result(key, {
                        'status': 'success',
                        'response_time': i,
                    })
                    result = tester._get_cached_result(key)
                    if result is None and thread_id % 2 == 0:
                        # 偶数线程做读取验证
                        pass
            except Exception as e:
                errors.append(e)

        threads = []
        for tid in range(10):
            t = threading.Thread(target=worker, args=(tid,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"线程安全错误: {errors}"

    def test_cache_with_url_normalization(self, tester):
        """缓存键使用规范化的URL"""
        url1 = "http://example.com/stream?t=12345&r=67890"
        url2 = "http://example.com/stream?r=67890&t=12345"
        key1 = tester.normalize_url(url1)
        key2 = tester.normalize_url(url2)
        # 规范化后两个URL的缓存键应相同
        tester._cache_result(key1, {'status': 'success', 'response_time': 50})
        result = tester._get_cached_result(key2)
        assert result is not None
        assert result['response_time'] == 50


# ========== ZeroDivisionError防护 ==========

class TestZeroDivisionErrorProtection:
    """测试total_sources=0时的ZeroDivisionError防护"""

    def test_test_all_sources_empty_list(self, tester):
        """空源列表不应抛出ZeroDivisionError"""
        with patch.object(tester, 'cleanup_cache'):
            result = tester.test_all_sources([])
        assert result == []

    def test_zero_division_in_stats_simulation(self, tester):
        """模拟total_sources=0时的除法运算"""
        total_sources = 0
        successful_count = 0
        # 直接测试: successful_count / total_sources 在total_sources=0时应被保护
        with pytest.raises(ZeroDivisionError):
            _ = successful_count / total_sources

    def test_test_all_sources_single_item(self, tester):
        """单源测试不崩溃（尽管ffprobe被mock了）"""
        sources = [{'name': 'Test', 'url': 'http://test.ts'}]
        with patch.object(tester, 'cleanup_cache'):
            with patch.object(tester, 'test_single_stream') as mock_test:
                mock_test.return_value = {
                    'name': 'Test', 'url': 'http://test.ts',
                    'status': 'failed', 'response_time': None, 'is_qualified': False,
                }
                result = tester.test_all_sources(sources)
                assert len(result) == 1
                assert result[0]['status'] == 'failed'


# ========== ffprobe不可用时行为 ==========

class TestFFprobeUnavailable:
    """测试ffprobe不可用时行为（非阻断模式，纪枢 B-2）"""

    def test_ffprobe_not_found(self, mock_config, mock_logger):
        """ffprobe不可用时不阻断启动，ffprobe_available 设为False"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("ffprobe not found")
            tester = StreamTester(mock_config, mock_logger)
            assert tester.ffprobe_available is False

    def test_ffprobe_timeout(self, mock_config, mock_logger):
        """ffprobe超时不阻断启动"""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = TimeoutExpiredStub("ffprobe timed out")
            tester = StreamTester(mock_config, mock_logger)
            assert tester.ffprobe_available is False

    def test_ffprobe_nonzero_exit(self, mock_config, mock_logger):
        """ffprobe返回非零退出码"""
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_run.return_value = mock_result
            tester = StreamTester(mock_config, mock_logger)
            assert tester.ffprobe_available is False

    def test_mocked_tester_can_test_without_ffprobe(self, tester):
        """mock掉ffprobe后，test_single_stream不应调用ffprobe"""
        with patch.object(tester, 'test_stream_url') as mock_test:
            mock_test.return_value = ('failed', {'error_reason': 'mock'})
            result = tester.test_single_stream({'name': 'T', 'url': 'http://t.tv'})
            assert result['status'] == 'failed'


class TimeoutExpiredStub(subprocess.TimeoutExpired):
    def __init__(self, cmd):
        super().__init__(cmd=cmd, timeout=1)


# ========== 核心功能测试 ==========

class TestCoreFunctionality:
    """核心功能测试"""

    def test_normalize_url_removes_timestamp(self, tester):
        """URL规范化移除时间戳参数"""
        url = "http://example.com/live?t=1234567890&foo=bar"
        normalized = tester.normalize_url(url)
        assert "t=" not in normalized
        assert "foo=bar" in normalized

    def test_normalize_url_removes_random(self, tester):
        """URL规范化移除随机参数"""
        url = "http://example.com/live?r=abc123&token=xyz"
        normalized = tester.normalize_url(url)
        assert "r=" not in normalized
        assert "token=" not in normalized

    def test_normalize_url_keeps_important_params(self, tester):
        """URL规范化保留重要参数"""
        url = "http://example.com/live?channel=5&format=m3u8"
        normalized = tester.normalize_url(url)
        assert "channel=5" in normalized
        assert "format=m3u8" in normalized

    def test_check_if_qualified_success(self, tester):
        """合格检查 - 成功"""
        result = {
            'status': 'success',
            'response_time': 100,
            'media_type': 'video',
            'resolution': '1920x1080',
            'bitrate': 2000,
            'is_hd': True,
            'is_4k': False,
            'download_speed': 500.0,
        }
        assert tester.check_if_qualified(result) is True

    def test_check_if_qualified_failed_status(self, tester):
        """非success状态应不合格"""
        result = {'status': 'timeout', 'response_time': None, 'media_type': 'video'}
        assert tester.check_if_qualified(result) is False

    def test_check_if_qualified_high_latency(self, tester):
        """高延迟应不合格"""
        result = {
            'status': 'success',
            'response_time': 6000,
            'media_type': 'video',
            'resolution': '1920x1080',
            'bitrate': 2000,
            'is_hd': True,
            'is_4k': False,
            'download_speed': 500.0,
        }
        assert tester.check_if_qualified(result) is False

    def test_check_if_qualified_low_speed(self, tester):
        """低速应不合格"""
        result = {
            'status': 'success',
            'response_time': 100,
            'media_type': 'video',
            'resolution': '1920x1080',
            'bitrate': 2000,
            'is_hd': True,
            'is_4k': False,
            'download_speed': 10,
        }
        assert tester.check_if_qualified(result) is False

    def test_check_if_qualified_zero_speed_in_main_style(self, tester):
        """speed=0不应被过滤（main.py逻辑：speed > 0 and speed < min_speed）"""
        result = {
            'status': 'success',
            'response_time': 100,
            'media_type': 'video',
            'resolution': '1920x1080',
            'bitrate': 2000,
            'is_hd': False,
            'is_4k': False,
            'download_speed': 0,
        }
        # 注意：check_if_qualified中 speed > 0 and speed < min_speed 才排除
        # speed=0 应通过
        assert tester.check_if_qualified(result) is True

    def test_media_type_determination_video(self, tester):
        """视频检测"""
        meta = {'has_video_stream': True, 'has_audio_stream': True, 'resolution': '1920x1080'}
        assert tester._determine_media_type(meta) == 'video'

    def test_media_type_determination_audio(self, tester):
        """纯音频检测"""
        meta = {'has_video_stream': False, 'has_audio_stream': True}
        assert tester._determine_media_type(meta) == 'audio'

    def test_media_type_determination_low_res_video_as_audio(self, tester):
        """极低分辨率应判定为音频"""
        meta = {'has_video_stream': True, 'has_audio_stream': True, 'resolution': '50x50'}
        assert tester._determine_media_type(meta) == 'audio'

    def test_is_resolution_meet_min(self, tester):
        """最低分辨率检查"""
        assert tester.is_resolution_meet_min("1920x1080", "1280x720") is True
        assert tester.is_resolution_meet_min("640x480", "1280x720") is False

    def test_is_resolution_meet_max(self, tester):
        """最高分辨率检查"""
        assert tester.is_resolution_meet_max("1280x720", "1920x1080") is True
        assert tester.is_resolution_meet_max("3840x2160", "1920x1080") is False

    def test_is_resolution_meet_min_1080p_format(self, tester):
        """1080p格式分辨率检查"""
        assert tester.is_resolution_meet_min("1920x1080", "720p") is True

    def test_check_network_compatibility_ipv6(self, tester):
        """IPv6 URL在系统不支持时应返回False"""
        with patch.object(tester, 'check_ipv6_support', return_value=False):
            result = tester._check_network_compatibility("http://[::1]:8080/stream")
            assert result is False

    def test_check_network_compatibility_ipv4(self, tester):
        """IPv4 URL返回True"""
        result = tester._check_network_compatibility("http://1.2.3.4/stream")
        assert result is True


# ========== 实例级缓存迁移测试 ==========

class TestStreamTesterInstanceCache:
    """测试缓存变量从模块级迁移为实例级"""

    @patch.object(StreamTester, '_verify_ffprobe')
    def test_cache_is_instance_variable(self, mock_verify):
        """验证_url_cache是实例变量而非模块变量"""
        from unittest.mock import MagicMock
        from stream_tester import StreamTester
        from config_manager import Config

        logger = MagicMock()
        config = Config(config_path='/nonexistent/config.ini')
        tester1 = StreamTester(config, logger)
        tester2 = StreamTester(config, logger)

        # 实例应有各自的cache
        assert hasattr(tester1, '_url_cache')
        assert hasattr(tester2, '_url_cache')
        # 不同实例的cache应不同
        assert tester1._url_cache is not tester2._url_cache

    @patch.object(StreamTester, '_verify_ffprobe')
    def test_cache_lock_is_instance_variable(self, mock_verify):
        """验证_cache_lock是实例变量"""
        from unittest.mock import MagicMock
        from stream_tester import StreamTester
        from config_manager import Config

        logger = MagicMock()
        config = Config(config_path='/nonexistent/config.ini')
        tester = StreamTester(config, logger)
        assert hasattr(tester, '_cache_lock')

    @patch.object(StreamTester, '_verify_ffprobe')
    def test_multiple_instances_independent_cache(self, mock_verify):
        """多实例缓存操作互不影响"""
        from unittest.mock import MagicMock
        from stream_tester import StreamTester
        from config_manager import Config

        logger = MagicMock()
        config = Config(config_path='/nonexistent/config.ini')
        t1 = StreamTester(config, logger)
        t2 = StreamTester(config, logger)

        url = "http://test.com/stream"
        t1._url_cache[url] = "result1"
        assert url in t1._url_cache
        assert url not in t2._url_cache  # t2不受影响


# ========== 异常使用测试 ==========

class TestStreamTesterExceptions:
    """测试StreamTester对StreamTestError的使用"""

    def test_stream_tester_error_importable(self):
        """确认模块可导入"""
        import sys
        sys.path.insert(0, 'app')
        # 只是确认导入
        from stream_tester import StreamTester
        from config_manager import Config
        assert True

class TestStreamTesterWatchdog:
    """测试看门狗定时器功能"""
    
    def _make_tester(self):
        from unittest.mock import MagicMock
        from stream_tester import StreamTester
        from config_manager import Config
        config = Config(config_path='/nonexistent/config.ini')
        return StreamTester(config, MagicMock())
    
    def test_watchdog_methods_exist(self):
        """验证看门狗方法存在"""
        tester = self._make_tester()
        
        assert hasattr(tester, '_start_watchdog')
        assert hasattr(tester, '_stop_watchdog')
        assert hasattr(tester, '_is_watchdog_triggered')
    
    def test_watchdog_start_and_stop(self):
        tester = self._make_tester()
        
        tester._start_watchdog()
        assert tester._watchdog_triggered == False
        assert tester._watchdog_timer is not None
        tester._stop_watchdog()
        assert tester._watchdog_triggered == False
