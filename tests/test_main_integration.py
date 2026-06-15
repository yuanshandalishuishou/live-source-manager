# -*- coding: utf-8 -*-
"""
集成测试 — app/main.py EnhancedLiveSourceManager

覆盖额外路径：
  1. 初始化流程（完整初始化 + 失败路径）
  2. 媒体类型分类 (classify_media_type / _refine_audio_type)
  3. 增强频道分类 (enhance_channel_classification)
  4. 分类覆盖策略 (_should_override_category)
  5. 分层筛选 (hierarchical_filtering / resolution_based_filtering / condition_based_filtering)
  6. 分辨率检查 (check_resolution)
  7. 源合格性判断 (is_source_qualified)
  8. 播放列表生成 & 写入
  9. ensure_output_directory
  10. logger_info / logger_error / logger_warning / logger_debug
  11. _test_channel_rules
"""

import os
import sys
import json
import tempfile
import asyncio
import logging
import pytest
from unittest.mock import MagicMock, patch, PropertyMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import EnhancedLiveSourceManager


# ═══════════════════════════════════════════════════════════════
# 1. 媒体类型分类测试
# ═══════════════════════════════════════════════════════════════

class TestClassifyMediaType:
    """测试 classify_media_type 及其辅助方法"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()

    def test_video_normal(self):
        """正常视频应返回 video"""
        source = {'has_video_stream': True, 'resolution': '1920x1080', 'bitrate': 5000}
        assert self.manager.classify_media_type(source) == 'video'

    def test_audio_no_video(self):
        """无视频流应返回 audio/radio"""
        source = {'has_video_stream': False, 'resolution': '', 'bitrate': 128}
        result = self.manager.classify_media_type(source)
        assert result in ('radio', 'audio')

    def test_ultra_low_resolution(self):
        """极低分辨率（宽度<100）应视为音频"""
        source = {'has_video_stream': True, 'resolution': '10x10', 'bitrate': 64}
        result = self.manager.classify_media_type(source)
        assert result in ('radio', 'audio')

    def test_low_resolution_just_above_threshold(self):
        """刚好高于低分辨率阈值应视为视频"""
        source = {'has_video_stream': True, 'resolution': '100x150', 'bitrate': 500}
        assert self.manager.classify_media_type(source) == 'video'

    def test_no_resolution(self):
        """无分辨率信息但 has_video=True 应视为 video"""
        source = {'has_video_stream': True, 'resolution': '', 'bitrate': 2000}
        assert self.manager.classify_media_type(source) == 'video'

    def test_missing_fields(self):
        """缺失字段时不崩溃"""
        source = {}
        result = self.manager.classify_media_type(source)
        assert result in ('video', 'radio', 'audio')

    def test_refine_audio_radio_keyword(self):
        """含广播关键词应返回 radio"""
        source = {'name': '中央人民广播电台'}
        assert self.manager._refine_audio_type(source) == 'radio'

    def test_refine_audio_radio_keyword_fm(self):
        """含 fm 应返回 radio"""
        source = {'name': 'FM音乐台'}
        assert self.manager._refine_audio_type(source) == 'radio'

    def test_refine_audio_audio_keyword(self):
        """含音乐关键词应返回 audio"""
        source = {'name': '车载音乐'}
        assert self.manager._refine_audio_type(source) == 'audio'

    def test_refine_audio_default(self):
        """无匹配关键词默认 audio"""
        source = {'name': '未知频道'}
        assert self.manager._refine_audio_type(source) == 'audio'

    def test_refine_audio_empty_name(self):
        """空名称不崩溃"""
        source = {'name': ''}
        assert self.manager._refine_audio_type(source) == 'audio'

    def test_refine_audio_missing_name(self):
        """无 name 字段时不崩溃"""
        source = {}
        assert self.manager._refine_audio_type(source) == 'audio'


# ═══════════════════════════════════════════════════════════════
# 2. 增强频道分类测试
# ═══════════════════════════════════════════════════════════════

class TestEnhanceChannelClassification:
    """测试 enhance_channel_classification"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()
        # Mock channel_rules
        self.manager.channel_rules = MagicMock()
        self.manager.channel_rules.extract_channel_info.return_value = {
            'country': '中国', 'language': 'zh'
        }
        self.manager.channel_rules.determine_category.return_value = '央视频道'
        self.manager.logger = MagicMock()

    def test_enhance_basic(self):
        source = {'name': 'CCTV-1', 'category': '其他频道'}
        result = self.manager.enhance_channel_classification(source)
        assert result['category'] == '央视频道'
        assert result['country'] == '中国'
        assert result['language'] == 'zh'
        assert result['media_type'] == 'video'


# ═══════════════════════════════════════════════════════════════
# 3. 分类覆盖策略测试
# ═══════════════════════════════════════════════════════════════

class TestShouldOverrideCategory:
    """测试 _should_override_category"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()

    def test_override_when_old_is_default(self):
        """原分类为 '其他频道' 时应覆盖"""
        assert self.manager._should_override_category('央视频道', '其他频道', 'CCTV-1')

    def test_not_override_when_new_is_default(self):
        """新分类为 '其他频道' 时不覆盖"""
        assert not self.manager._should_override_category('其他频道', '央视频道', 'CCTV-1')

    def test_override_higher_priority(self):
        """更高优先级覆盖"""
        assert self.manager._should_override_category('央视频道', '北京频道', 'CCTV-1')

    def test_not_override_lower_priority(self):
        """更低优先级不覆盖"""
        assert not self.manager._should_override_category('北京频道', '央视频道', 'BTV-1')

    def test_weishi_rule(self):
        """含卫视关键词且新分类为卫视频道时覆盖"""
        assert self.manager._should_override_category('卫视频道', '其他频道', '湖南卫视')

    def test_cctv_rule(self):
        """含CCTV且新分类为央视频道时覆盖"""
        assert self.manager._should_override_category('央视频道', '其他频道', 'CCTV-5')

    def test_cctv_lowercase(self):
        """小写cctv也应触发"""
        assert self.manager._should_override_category('央视频道', '其他频道', 'cctv-新闻')

    def test_not_override_same_category(self):
        """相同分类不覆盖"""
        assert not self.manager._should_override_category('央视频道', '央视频道', 'CCTV-1')

    def test_not_override_unknown_new(self):
        """未知新分类不覆盖"""
        assert not self.manager._should_override_category('未知分类', '央视频道', 'X频道')


# ═══════════════════════════════════════════════════════════════
# 4. 分辨率检查和合格性判断测试
# ═══════════════════════════════════════════════════════════════

class TestCheckResolution:
    """测试 check_resolution"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()

    def test_unknown_passes(self):
        assert self.manager.check_resolution('unknown', '720p', '4k', 'range')
        assert self.manager.check_resolution('', '720p', '4k', 'range')

    def test_range_within_bounds(self):
        assert self.manager.check_resolution('1920x1080', '1280x720', '3840x2160', 'range')

    def test_range_below_min(self):
        assert not self.manager.check_resolution('640x480', '1280x720', '3840x2160', 'range')

    def test_range_above_max(self):
        assert not self.manager.check_resolution('7680x4320', '1280x720', '3840x2160', 'range')

    def test_range_no_min(self):
        assert self.manager.check_resolution('640x480', '', '3840x2160', 'range')

    def test_range_no_max(self):
        assert self.manager.check_resolution('1920x1080', '1280x720', '', 'range')

    def test_min_only_mode(self):
        assert self.manager.check_resolution('1920x1080', '1280x720', '', 'min_only')
        assert not self.manager.check_resolution('640x480', '1280x720', '', 'min_only')

    def test_max_only_mode(self):
        assert self.manager.check_resolution('1920x1080', '', '3840x2160', 'max_only')
        assert not self.manager.check_resolution('7680x4320', '', '3840x2160', 'max_only')

    def test_p_format_1080p(self):
        assert self.manager.check_resolution('1080p', '720p', '4k', 'range')

    def test_p_format_below(self):
        assert not self.manager.check_resolution('480p', '720p', '4k', 'range')

    def test_invalid_format_fails_with_min(self):
        """无效格式在有 min constraint 时应不通过"""
        assert not self.manager.check_resolution('invalid', '720p', '4k', 'range')

    def test_invalid_format_no_min(self):
        """无效格式但无 min/max 时应通过"""
        assert self.manager.check_resolution('invalid', '', '', 'range')

    def test_edge_equal_min(self):
        assert self.manager.check_resolution('1280x720', '1280x720', '3840x2160', 'range')

    def test_edge_equal_max(self):
        assert self.manager.check_resolution('3840x2160', '1280x720', '3840x2160', 'range')


class TestIsSourceQualified:
    """测试 is_source_qualified"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()
        self.base_params = {
            'max_latency': 5000,
            'min_bitrate': 100,
            'must_hd': False,
            'must_4k': False,
            'min_speed': 40,
            'min_resolution': '720p',
            'max_resolution': '4k',
            'resolution_filter_mode': 'range',
        }

    def test_qualified_video(self):
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 5000,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 500, 'is_hd': True,
        }
        assert self.manager.is_source_qualified(source, self.base_params)

    def test_failed_status(self):
        source = {'status': 'failed', 'name': 'ch1'}
        assert not self.manager.is_source_qualified(source, self.base_params)

    def test_high_latency(self):
        source = {
            'status': 'success', 'response_time': 10000, 'bitrate': 5000,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 500, 'name': 'ch1',
        }
        assert not self.manager.is_source_qualified(source, self.base_params)

    def test_audio_simple_check(self):
        """音频只检查延迟"""
        source = {
            'status': 'success', 'response_time': 200,
            'media_type': 'audio', 'bitrate': 128, 'name': 'RadioFM',
        }
        assert self.manager.is_source_qualified(source, self.base_params)

    def test_low_bitrate(self):
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 50,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 500, 'name': 'ch1',
        }
        assert not self.manager.is_source_qualified(source, self.base_params)

    def test_low_resolution(self):
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 500,
            'resolution': '640x480', 'media_type': 'video',
            'download_speed': 500, 'name': 'ch1',
        }
        assert not self.manager.is_source_qualified(source, self.base_params)

    def test_must_hd_but_not_hd(self):
        params = {**self.base_params, 'must_hd': True}
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 5000,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 500, 'is_hd': False, 'name': 'ch1',
        }
        assert not self.manager.is_source_qualified(source, params)

    def test_must_4k_but_not_4k(self):
        params = {**self.base_params, 'must_4k': True}
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 5000,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 500, 'is_4k': False, 'name': 'ch1',
        }
        assert not self.manager.is_source_qualified(source, params)

    def test_low_speed(self):
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 5000,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 10, 'name': 'ch1',
        }
        assert not self.manager.is_source_qualified(source, self.base_params)

    def test_zero_speed_passes(self):
        """速度为0（未知）时应通过"""
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 5000,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 0, 'name': 'ch1',
        }
        assert self.manager.is_source_qualified(source, self.base_params)

    def test_zero_bitrate_passes(self):
        """比特率为0（未知）时应通过"""
        source = {
            'status': 'success', 'response_time': 100, 'bitrate': 0,
            'resolution': '1920x1080', 'media_type': 'video',
            'download_speed': 500, 'name': 'ch1',
        }
        assert self.manager.is_source_qualified(source, self.base_params)


# ═══════════════════════════════════════════════════════════════
# 5. 分层筛选测试
# ═══════════════════════════════════════════════════════════════

class TestHierarchicalFiltering:
    """测试 hierarchical_filtering 及相关方法"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()
        self.manager.logger = MagicMock()
        self.manager.logger_info = MagicMock()
        self.manager.logger_warning = MagicMock()
        self.manager.logger_debug = MagicMock()
        self.manager.logger_error = MagicMock()

        # Mock channel_rules for enhance_channel_classification
        self.manager.channel_rules = MagicMock()
        self.manager.channel_rules.extract_channel_info.return_value = {}
        self.manager.channel_rules.determine_category.return_value = '其他频道'

        # Mock config for condition_based_filtering
        self.manager.config = MagicMock()
        self.manager.config.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100,
            'must_hd': False, 'must_4k': False,
            'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }

    def test_empty_sources(self):
        result = self.manager.hierarchical_filtering([])
        assert result == ([], [], [])

    def test_all_failed_status(self):
        sources = [
            {'status': 'failed', 'name': 'ch1', 'url': 'http://x'},
            {'status': 'failed', 'name': 'ch2', 'url': 'http://y'},
        ]
        result = self.manager.hierarchical_filtering(sources)
        assert result == ([], [], [])

    def test_basic_filtering(self):
        sources = [
            {
                'status': 'success', 'name': 'CCTV-1',
                'url': 'http://example.com/a',
                'resolution': '1920x1080', 'media_type': 'video',
                'response_time': 100, 'bitrate': 5000,
                'download_speed': 500, 'category': '其他频道',
            },
            {
                'status': 'success', 'name': 'CCTV-1',
                'url': 'http://example.com/b',
                'resolution': '1920x1080', 'media_type': 'video',
                'response_time': 200, 'bitrate': 3000,
                'download_speed': 300, 'category': '其他频道',
            },
        ]
        classified, base, qualified = self.manager.hierarchical_filtering(sources)
        assert len(classified) == 2
        assert len(base) == 2  # within 5-per-group
        assert len(qualified) == 2  # both pass quality check

    def test_filtering_with_failures(self):
        sources = [
            {'status': 'success', 'name': 'CCTV-1', 'url': 'http://a',
             'resolution': '1920x1080', 'media_type': 'video',
             'response_time': 100, 'bitrate': 5000,
             'download_speed': 500, 'category': '其他频道'},
            {'status': 'failed', 'name': 'CCTV-2', 'url': 'http://b'},
        ]
        classified, base, qualified = self.manager.hierarchical_filtering(sources)
        assert len(classified) == 1  # only the successful one classified

    def test_resolution_filtering_limit(self):
        """分辨率筛选应限制每组最多5个"""
        sources = [
            {
                'status': 'success', 'name': 'BTV', 'url': 'http://a',
                'resolution': '1920x1080', 'media_type': 'video',
                'response_time': 100, 'bitrate': 5000,
                'download_speed': 500, 'category': '其他频道',
            },
        ] * 10
        filtered = self.manager.resolution_based_filtering(sources)
        assert len(filtered) <= 5

    def test_resolution_filtering_audio(self):
        """音频内容按名称分组"""
        sources = [
            {
                'status': 'success', 'name': 'RadioFM', 'url': 'http://a',
                'resolution': '10x10', 'media_type': 'radio',
                'response_time': 100, 'bitrate': 128,
                'download_speed': 100, 'category': '收音机',
            },
        ] * 10
        filtered = self.manager.resolution_based_filtering(sources)
        assert len(filtered) <= 5

    def test_condition_filtering_empty(self):
        result = self.manager.condition_based_filtering([])
        assert result == []


# ═══════════════════════════════════════════════════════════════
# 6. 日志辅助方法测试
# ═══════════════════════════════════════════════════════════════

class TestLoggerHelpers:
    """测试 logger_info / logger_error / logger_warning / logger_debug"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()

    def test_info_with_logger(self):
        self.manager.logger = MagicMock()
        self.manager.logger_info("info msg")
        self.manager.logger.info.assert_called_with("info msg")

    def test_info_without_logger(self, capsys):
        self.manager.logger = None
        self.manager.logger_info("info msg")
        captured = capsys.readouterr()
        assert "info msg" in captured.out

    def test_error_with_logger(self):
        self.manager.logger = MagicMock()
        self.manager.logger_error("error msg")
        self.manager.logger.error.assert_called_with("error msg")

    def test_warning_with_logger(self):
        self.manager.logger = MagicMock()
        self.manager.logger_warning("warn msg")
        self.manager.logger.warning.assert_called_with("warn msg")

    def test_debug_with_logger(self):
        self.manager.logger = MagicMock()
        self.manager.logger_debug("debug msg")
        self.manager.logger.debug.assert_called_with("debug msg")


# ═══════════════════════════════════════════════════════════════
# 7. 备份播放列表生成测试
# ═══════════════════════════════════════════════════════════════

class TestBackupContent:
    """测试 _create_backup_m3u_content / _create_backup_txt_content"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()

    def test_backup_m3u(self):
        sources = [
            {'name': 'CCTV-1', 'url': 'http://cctv1'},
            {'name': 'CCTV-2', 'url': 'http://cctv2'},
        ]
        content = self.manager._create_backup_m3u_content(sources, '基础')
        assert '#EXTM3U' in content
        assert 'CCTV-1' in content
        assert 'http://cctv1' in content
        lines = content.strip().split('\n')
        assert len(lines) == 5  # header + 2*(inf+url)

    def test_backup_m3u_empty(self):
        content = self.manager._create_backup_m3u_content([], '测试')
        assert content == '#EXTM3U'

    def test_backup_txt(self):
        sources = [
            {'name': 'BTV', 'url': 'http://btv'},
        ]
        content = self.manager._create_backup_txt_content(sources, '基础')
        assert '基础播放列表' in content
        assert 'BTV,http://btv' in content

    def test_backup_txt_empty(self):
        content = self.manager._create_backup_txt_content([], '测试')
        assert '# 测试播放列表' in content


# ═══════════════════════════════════════════════════════════════
# 8. 输出目录准备测试
# ═══════════════════════════════════════════════════════════════

class TestEnsureOutputDirectory:
    """测试 ensure_output_directory"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()
        self.manager.logger = MagicMock()
        self.manager.logger_info = MagicMock()
        self.manager.logger_error = MagicMock()
        self.manager.logger_warning = MagicMock()
        self.manager.config = MagicMock()

    def test_ensure_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.manager.config.get_output_params.return_value = {
                'output_dir': os.path.join(tmpdir, 'new_output'),
                'filename': 'live.m3u',
            }
            result = self.manager.ensure_output_directory()
            assert result is True
            assert os.path.exists(os.path.join(tmpdir, 'new_output'))

    def test_ensure_with_existing_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.manager.config.get_output_params.return_value = {
                'output_dir': tmpdir,
                'filename': 'live.m3u',
            }
            result = self.manager.ensure_output_directory()
            assert result is True

    def test_ensure_creates_default_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.manager.config.get_output_params.return_value = {
                'output_dir': tmpdir,
                'filename': 'live.m3u',
            }
            result = self.manager.ensure_output_directory()
            assert result is True
            # 检查默认文件
            m3u_path = os.path.join(tmpdir, 'live.m3u')
            txt_path = os.path.join(tmpdir, 'live.txt')
            assert os.path.exists(m3u_path)
            assert os.path.exists(txt_path)


# ═══════════════════════════════════════════════════════════════
# 9. 初始化流程测试（额外路径）
# ═══════════════════════════════════════════════════════════════

class TestInitializeExtraPaths:
    """覆盖初始化中的额外路径"""

    @patch('main.Config')
    @patch('main.Logger')
    def test_verify_nginx_directory_fails(self, MockLogger, MockConfig):
        """_verify_nginx_directory 失败时初始化应返回 False"""
        MockConfig.return_value.get_logging_config.return_value = {
            'level': 'INFO', 'file': '/tmp/test.log',
            'max_size': 10, 'backup_count': 5, 'enable_console': True,
        }
        MockConfig.return_value.get_output_params.return_value = {
            'output_dir': '/nonexistent_protected_dir',
            'filename': 'live.m3u',
        }
        MockConfig.return_value.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100,
            'must_hd': False, 'must_4k': False,
            'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }
        mock_logger = MagicMock()
        MockLogger.return_value.logger = mock_logger

        manager = EnhancedLiveSourceManager()
        with patch.object(manager, '_verify_nginx_directory', return_value=False):
            result = manager.initialize()
        assert result is False

    @patch('main.Config')
    def test_init_config_exception(self, MockConfig):
        """Config() 抛出异常时应返回 False"""
        from app.utils import ConfigError
        MockConfig.side_effect = ConfigError("配置加载失败")
        manager = EnhancedLiveSourceManager()
        result = manager.initialize()
        assert result is False

    @patch('main.Config')
    @patch('main.Logger')
    def test_init_source_error(self, MockLogger, MockConfig):
        """SourceError 应被捕获并返回 False"""
        from app.utils import SourceError
        MockConfig.return_value.get_logging_config.return_value = {
            'level': 'INFO', 'file': '/tmp/test.log',
            'max_size': 10, 'backup_count': 5, 'enable_console': True,
        }
        MockConfig.return_value.get_output_params.side_effect = SourceError("源错误")
        mock_logger = MagicMock()
        MockLogger.return_value.logger = mock_logger

        manager = EnhancedLiveSourceManager()
        result = manager.initialize()
        assert result is False

    @patch('main.Config')
    @patch('main.Logger')
    def test_init_generic_exception(self, MockLogger, MockConfig):
        """通用 Exception 应被捕获并返回 False"""
        MockConfig.return_value.get_logging_config.return_value = {
            'level': 'INFO', 'file': '/tmp/test.log',
            'max_size': 10, 'backup_count': 5, 'enable_console': True,
        }
        MockConfig.return_value.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100,
            'must_hd': False, 'must_4k': False,
            'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }
        # 让 get_output_params 抛出一个非 ConfigError/SourceError 的异常
        MockConfig.return_value.get_output_params.side_effect = RuntimeError("未知错误")
        mock_logger = MagicMock()
        MockLogger.return_value.logger = mock_logger

        manager = EnhancedLiveSourceManager()
        result = manager.initialize()
        assert result is False

    @patch('main.Config')
    @patch('main.Logger')
    def test_init_channel_rules_failure(self, MockLogger, MockConfig):
        """_test_channel_rules 失败但初始化仍应继续"""
        MockConfig.return_value.get_logging_config.return_value = {
            'level': 'INFO', 'file': '/tmp/test.log',
            'max_size': 10, 'backup_count': 5, 'enable_console': True,
        }
        MockConfig.return_value.get_output_params.return_value = {
            'output_dir': '/tmp/test_output_init',
            'filename': 'live.m3u',
        }
        MockConfig.return_value.get_filter_params.return_value = {
            'max_latency': 5000, 'min_bitrate': 100,
            'must_hd': False, 'must_4k': False,
            'min_speed': 40, 'min_resolution': '720p',
            'max_resolution': '4k', 'resolution_filter_mode': 'range',
        }
        mock_logger = MagicMock()
        MockLogger.return_value.logger = mock_logger

        # Mock ChannelRules to avoid complex setup
        with patch('main.ChannelRules') as MockCR:
            mock_cr_instance = MagicMock()
            mock_cr_instance.test_classification.side_effect = ValueError("无效数据")
            MockCR.return_value = mock_cr_instance

            with patch('main.SourceManager'), patch('main.StreamTester'):
                manager = EnhancedLiveSourceManager()
                # Mock _verify_nginx_directory to pass
                with patch.object(manager, '_verify_nginx_directory', return_value=True):
                    result = manager.initialize()
                    assert result is True  # 初始化应成功，分类测试失败只是 warning


# ═══════════════════════════════════════════════════════════════
# 10. 统计信息输出测试
# ═══════════════════════════════════════════════════════════════

class TestStatisticsOutput:
    """测试 enhanced_output_statistics"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()
        self.manager.logger = MagicMock()
        self.manager.logger_info = MagicMock()
        self.manager.logger_debug = MagicMock()

    def test_empty_stats(self):
        self.manager.enhanced_output_statistics([], [], [])
        assert self.manager.logger_info.called

    def test_basic_stats(self):
        valid = [
            {'name': 'CCTV-1', 'media_type': 'video', 'category': '央视频道',
             'resolution': '1920x1080'},
            {'name': 'BTV', 'media_type': 'video', 'category': '北京频道',
             'resolution': '1280x720'},
            {'name': 'Radio', 'media_type': 'radio', 'category': '收音机',
             'resolution': 'unknown'},
        ]
        self.manager.enhanced_output_statistics(valid, valid[:2], valid[:1])
        assert self.manager.logger_info.called

    def test_no_video_sources(self):
        """只有音频源时分辨率统计不应抛出 ZeroDivisionError"""
        valid = [
            {'name': 'FM', 'media_type': 'audio', 'category': '收音机',
             'resolution': 'unknown'},
        ]
        self.manager.enhanced_output_statistics(valid, valid, [])
        assert self.manager.logger_info.called

    def test_low_accuracy_channel_rules(self):
        """准确率低于80%时应返回 False"""
        manager = EnhancedLiveSourceManager()
        manager.logger = MagicMock()
        manager.channel_rules = MagicMock()
        manager.channel_rules.test_classification.return_value = [
            ("CCTV-1", "央视频道", "央视频道", "✗"),
            ("CCTV-2", "央视频道", "卫视频道", "✗"),
        ]
        result = manager._test_channel_rules()
        assert result is False

    def test_high_accuracy_channel_rules(self):
        """准确率>=80%时应返回 True"""
        manager = EnhancedLiveSourceManager()
        manager.logger = MagicMock()
        manager.channel_rules = MagicMock()
        manager.channel_rules.test_classification.return_value = [
            ("CCTV-1", "央视频道", "央视频道", "✓"),
            ("CCTV-2", "央视频道", "央视频道", "✓"),
            ("CCTV-3", "央视频道", "央视频道", "✓"),
            ("CCTV-4", "央视频道", "央视频道", "✓"),
            ("BTV", "北京频道", "其他频道", "✗"),
        ]
        result = manager._test_channel_rules()
        assert result is True


# ═══════════════════════════════════════════════════════════════
# 11. 生成播放列表测试
# ═══════════════════════════════════════════════════════════════

class TestGenerateEnhancedPlaylist:
    """测试 _generate_enhanced_playlist"""

    def setup_method(self):
        self.manager = EnhancedLiveSourceManager()
        self.manager.logger = MagicMock()
        self.manager.logger_info = MagicMock()
        self.manager.logger_error = MagicMock()
        self.manager.logger_debug = MagicMock()
        self.manager.logger_warning = MagicMock()
        self.manager.config = MagicMock()
        self.manager.config.get_output_params.return_value = {
            'output_dir': '/tmp/test_playlist_gen',
            'filename': 'live.m3u',
        }
        self.generator = MagicMock()
        self.generator.generate_m3u.return_value = "#EXTM3U\nch1,url1"
        self.generator.generate_txt.return_value = "ch1,url1"
        self.sources = [{'name': 'CH1', 'url': 'http://test'},]

    def test_generate_success(self):
        result = self.manager._generate_enhanced_playlist(
            self.generator, self.sources, "", "测试"
        )
        assert result is True

    def test_generate_m3u_fallback(self):
        """M3U生成失败时使用备份内容"""
        self.generator.generate_m3u.side_effect = RuntimeError("M3U生成失败")
        result = self.manager._generate_enhanced_playlist(
            self.generator, self.sources, "", "测试"
        )
        assert result is True

    def test_generate_txt_fallback(self):
        """TXT生成失败时使用备份内容"""
        self.generator.generate_txt.side_effect = RuntimeError("TXT生成失败")
        result = self.manager._generate_enhanced_playlist(
            self.generator, self.sources, "", "测试"
        )
        assert result is True

    def test_generate_empty_sources(self):
        """空源列表也能生成备份内容"""
        result = self.manager._generate_enhanced_playlist(
            self.generator, [], "empty_", "空"
        )
        assert result is True


# ═══════════════════════════════════════════════════════════════
# 12. enhanced_process_sources（异步方法）
# ═══════════════════════════════════════════════════════════════

class TestEnhancedProcessSources:
    """测试 enhanced_process_sources 异步方法"""

    @pytest.mark.asyncio
    async def test_missing_components(self):
        """组件未就绪时返回 False"""
        manager = EnhancedLiveSourceManager()
        manager.logger = MagicMock()
        result = await manager.enhanced_process_sources()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_sources(self):
        """下载后无源时返回 False"""
        manager = EnhancedLiveSourceManager()
        manager.logger = MagicMock()
        manager.logger_info = MagicMock()
        manager.logger_error = MagicMock()
        manager.logger_warning = MagicMock()
        manager.source_manager = MagicMock()
        manager.source_manager.download_all_sources = AsyncMock(return_value=[])
        manager.source_manager.parse_all_files.return_value = []
        manager.stream_tester = MagicMock()

        result = await manager.enhanced_process_sources()
        assert result is False

    @pytest.mark.asyncio
    async def test_full_success_path(self):
        """完整成功路径"""
        manager = EnhancedLiveSourceManager()
        manager.logger = MagicMock()
        manager.logger_info = MagicMock()
        manager.logger_error = MagicMock()
        manager.logger_warning = MagicMock()
        manager.logger_debug = MagicMock()

        # Mock source_manager
        manager.source_manager = MagicMock()
        # download_all_sources is async, use AsyncMock for the coroutine
        manager.source_manager.download_all_sources = AsyncMock(return_value=['file1', 'file2'])

        test_source = {
            'status': 'success', 'name': 'CCTV-1',
            'url': 'http://example.com',
            'resolution': '1920x1080', 'media_type': 'video',
            'response_time': 100, 'bitrate': 5000,
            'download_speed': 500, 'category': '其他频道',
        }
        manager.source_manager.parse_all_files.return_value = [test_source]

        # Mock stream_tester
        manager.stream_tester = MagicMock()
        manager.stream_tester.test_all_sources.return_value = [test_source]

        # Mock hierarchical_filtering to return expected structure
        manager.hierarchical_filtering = MagicMock(
            return_value=([test_source], [test_source], [test_source])
        )

        # Mock Config for playlist generation
        manager.config = MagicMock()
        manager.config.get_output_params.return_value = {
            'output_dir': '/tmp/test_process',
            'filename': 'live.m3u',
        }

        # Mock _generate_enhanced_playlist to avoid file I/O
        with patch.object(manager, '_generate_enhanced_playlist', return_value=True):
            with patch.object(manager, 'enhanced_output_statistics'):
                result = await manager.enhanced_process_sources()
                assert result is True
