"""P0 性能优化集成测试（对标 Guovin/iptv-api）：

- 同 Host 测速复用（ffprobe 调用量降一个数量级）
- 失败源指数退避冻结（死源拉黑冷却，跨进程持久化）
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from app.stream_tester import StreamTester


@pytest.fixture
def tester(tmp_path):
    """构造隔离的 StreamTester（不触发真实 ffprobe 验证，持久化落 tmp_path）。"""
    cfg = MagicMock()
    cfg.get_testing_params.return_value = {
        'timeout': 10,
        'concurrent_threads': 4,
        'cache_ttl': 120,
        'enable_speed_test': False,
        'speed_test_duration': 6,
        'max_workers': 50,
        'enable_host_speed_share': True,
        'enable_source_freeze': True,
        'freeze_fail_threshold': 3,
        'freeze_base_seconds': 60,
        'freeze_max_hours': 24,
    }
    cfg.get_filter_params.return_value = {}
    with patch.object(StreamTester, '_verify_ffprobe', lambda self: None):
        t = StreamTester(cfg, MagicMock())
    t._ffprobe_path = None
    t._ffprobe_verified = False
    t._status_dir = str(tmp_path)  # 隔离持久化目录，避免污染项目 data
    t._frozen_map = {}
    return t


class TestHostSpeedShare:
    """P0-①：同 Host 测速复用"""

    def test_extract_host_lowercases_and_keeps_port(self, tester):
        assert tester._extract_host('http://CDN.Example.com:8080/path') == 'cdn.example.com:8080'
        assert tester._extract_host('https://a.com/x') == 'a.com'

    def test_cache_only_success_and_reused(self, tester):
        tester._cache_host_result(
            'cdn.example.com:8080',
            {'status': 'success', 'response_time': 12, 'resolution': '1920x1080'},
        )
        got = tester._get_host_cached_result('cdn.example.com:8080')
        assert got['status'] == 'success' and got['resolution'] == '1920x1080'
        # 失败态不写入 host 缓存，避免死 host 复用扩散误伤
        tester._cache_host_result('bad.host', {'status': 'failed'})
        assert tester._get_host_cached_result('bad.host') is None

    def test_full_path_reduces_ffprobe_calls(self, tester):
        calls = {'n': 0}

        def fake_probe(url, *a, **k):
            calls['n'] += 1
            return ('success', {'resolution': '1920x1080', 'bitrate': 3000})

        srcs = [{'name': f'c{i}', 'url': f'http://same.example.com/chan{i}'} for i in range(5)]
        with (
            patch.object(tester, 'test_stream_url', side_effect=fake_probe),
            patch.object(tester, '_check_network_compatibility', return_value=True),
        ):
            results = [tester.test_single_stream(s) for s in srcs]
        # 5 个同 Host 源，ffprobe 应只调用 1 次，其余复用
        assert calls['n'] == 1, calls
        # 第 1 个源真实探测（host_shared 应为 None/False），其余复用（host_shared=True）
        assert results[0]['status'] == 'success' and not results[0].get('host_shared')
        assert all(r['status'] == 'success' and r.get('host_shared') for r in results[1:])


class TestSourceFreeze:
    """P0-②：失败源指数退避冻结"""

    def test_exponential_backoff_and_persist(self, tester):
        url = tester.normalize_url('http://dead.example.com/a')
        with patch.object(time, 'time', return_value=1000.0):
            # 阈值前失败不冻结
            for _ in range(tester._freeze_fail_threshold - 1):
                tester._record_failure(url)
            assert tester._check_frozen(url) is None
            # 第 threshold 次失败触发冻结
            tester._record_failure(url)
            fu = tester._check_frozen(url)
        assert fu is not None and fu > 1000.0
        expected = 1000.0 + min(
            (2**tester._freeze_fail_threshold) * tester._freeze_base_seconds,
            tester._freeze_max_seconds,
        )
        assert abs(fu - expected) < 1

    def test_success_unfreezes(self, tester):
        url = tester.normalize_url('http://dead.example.com/b')
        with patch.object(time, 'time', return_value=1000.0):
            for _ in range(tester._freeze_fail_threshold):
                tester._record_failure(url)
            assert tester._check_frozen(url) is not None
            tester._record_success(url)
            assert tester._check_frozen(url) is None

    def test_persist_roundtrip(self, tester):
        url = tester.normalize_url('http://dead2.example.com/b')
        tester._frozen_map[url] = {'fail_count': 5, 'frozen_until': 9999.0}
        tester._save_frozen_map()
        reloaded = tester._load_frozen_map()
        assert reloaded.get(url, {}).get('frozen_until') == 9999.0

    def test_full_path_freezes_after_threshold(self, tester):
        src = {'name': 'd', 'url': 'http://dead3.example.com/x'}
        with (
            patch.object(tester, 'test_stream_url', return_value=('failed', {'error_reason': 'no_valid_streams'})),
            patch.object(tester, '_check_network_compatibility', return_value=True),
        ):
            results = [tester.test_single_stream(src) for _ in range(tester._freeze_fail_threshold + 1)]
        # 连续失败达阈值后，再次测试应直接返回 frozen（跳过 ffprobe）
        assert results[-1]['status'] == 'frozen'
        assert all(r['status'] in ('failed', 'frozen') for r in results)


@pytest.fixture
def tester_p1p2(tmp_path):
    """构造启用广告检测/黑白名单的 StreamTester（隔离持久化目录）。"""
    cfg = MagicMock()
    cfg.get_testing_params.return_value = {
        'timeout': 10,
        'concurrent_threads': 4,
        'cache_ttl': 120,
        'enable_speed_test': False,
        'speed_test_duration': 6,
        'max_workers': 50,
        'enable_host_speed_share': False,  # 关闭以隔离本批次逻辑
        'enable_source_freeze': False,
        'freeze_fail_threshold': 3,
        'freeze_base_seconds': 60,
        'freeze_max_hours': 24,
        'enable_ad_detect': True,
        'ad_keywords': 'no_signal,/ad/,advertisement',
        'ad_max_duration': 90,
        'global_blacklist': 'spam.example.com,http://blocked.list/x',
        'global_whitelist': 'keep.example.com',
        'output_sort_by': 'speed',
    }
    cfg.get_filter_params.return_value = {}
    with patch.object(StreamTester, '_verify_ffprobe', lambda self: None):
        t = StreamTester(cfg, MagicMock())
    t._ffprobe_path = None
    t._ffprobe_verified = False
    t._status_dir = str(tmp_path)
    t._frozen_map = {}
    return t


class TestAdDetect:
    """P1：广告/循环占位源检测"""

    def test_keyword_hit_marks_ad(self, tester_p1p2):
        ad_playlist = '#EXTM3U\n#EXTINF:10.0,Ad\nhttp://cdn.example.com/ad/slot.ts\n#EXT-X-ENDLIST\n'

        def fake_probe(url, *a, **k):
            return ('success', {'resolution': '1280x720', 'bitrate': 2000})

        src = {'name': '广告台', 'url': 'http://live.example.com/ad.m3u8'}
        with (
            patch.object(tester_p1p2, 'test_stream_url', side_effect=fake_probe),
            patch.object(tester_p1p2, '_check_network_compatibility', return_value=True),
            patch('urllib.request.urlopen', return_value=_FakeResp(ad_playlist)),
        ):
            r = tester_p1p2.test_single_stream(src)
        assert r['status'] == 'failed'
        assert r.get('is_ad') is True
        assert r['error_reason'] == 'ad_playlist'

    def test_loop_placeholder_short_endlist_marks_ad(self, tester_p1p2):
        # 含 ENDLIST 且累计时长 <= ad_max_duration(90) → 循环占位
        loop_playlist = (
            '#EXTM3U\n'
            '#EXTINF:30.0,Loop\n'
            'http://cdn.example.com/seg0.ts\n'
            '#EXTINF:30.0,Loop\n'
            'http://cdn.example.com/seg1.ts\n'
            '#EXT-X-ENDLIST\n'
        )
        src = {'name': '测试卡', 'url': 'http://live.example.com/loop.m3u8'}
        with (
            patch.object(tester_p1p2, 'test_stream_url', return_value=('success', {'resolution': '1920x1080'})),
            patch.object(tester_p1p2, '_check_network_compatibility', return_value=True),
            patch('urllib.request.urlopen', return_value=_FakeResp(loop_playlist)),
        ):
            r = tester_p1p2.test_single_stream(src)
        assert r.get('is_ad') is True

    def test_live_playlist_without_endlist_not_ad(self, tester_p1p2):
        # 正常直播：无 ENDLIST、无广告关键字 → 不是广告
        live_playlist = '#EXTM3U\n#EXTINF:6.0,CCTV1\nhttp://cdn.example.com/seg.ts\n'
        src = {'name': 'CCTV1', 'url': 'http://live.example.com/cctv1.m3u8'}
        with (
            patch.object(tester_p1p2, 'test_stream_url', return_value=('success', {'resolution': '1920x1080'})),
            patch.object(tester_p1p2, '_check_network_compatibility', return_value=True),
            patch('urllib.request.urlopen', return_value=_FakeResp(live_playlist)),
        ):
            r = tester_p1p2.test_single_stream(src)
        assert r['status'] == 'success'
        assert r.get('is_ad') is not True

    def test_non_hls_not_checked(self, tester_p1p2):
        # rtmp/其他非 m3u8 源不拉 playlist，直接成功
        src = {'name': 'rtmp台', 'url': 'rtmp://live.example.com/app/stream'}
        with (
            patch.object(tester_p1p2, 'test_stream_url', return_value=('success', {'resolution': '1920x1080'})),
            patch.object(tester_p1p2, '_check_network_compatibility', return_value=True),
        ):
            r = tester_p1p2.test_single_stream(src)
        assert r['status'] == 'success'


class TestGlobalList:
    """P2-⑥：全局黑白名单"""

    def test_blacklist_skips_test(self, tester_p1p2):
        src = {'name': '垃圾源', 'url': 'http://spam.example.com/channel'}
        r = tester_p1p2.test_single_stream(src)
        assert r['status'] == 'blacklisted'
        assert r['error_reason'] == 'global_blacklist'

    def test_whitelist_exempts_blacklist(self, tester_p1p2):
        # keep.example.com 在白名单 → 即使命中黑名单关键字也不跳过
        src = {'name': '保留源', 'url': 'http://keep.example.com/channel'}
        with (
            patch.object(tester_p1p2, 'test_stream_url', return_value=('success', {'resolution': '1920x1080'})),
            patch.object(tester_p1p2, '_check_network_compatibility', return_value=True),
        ):
            r = tester_p1p2.test_single_stream(src)
        assert r['status'] == 'success'

    def test_blacklist_by_full_url_substr(self, tester_p1p2):
        src = {'name': 'x', 'url': 'http://anything.com/path?ref=http://blocked.list/x'}
        r = tester_p1p2.test_single_stream(src)
        assert r['status'] == 'blacklisted'


class _FakeResp:
    """模拟 urllib.response（供 ad 检测拉 playlist 使用）。"""

    def __init__(self, data: str):
        self._data = data.encode('utf-8')

    def read(self, n=-1):
        if n == -1:
            return self._data
        out, self._data = self._data[:n], self._data[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
