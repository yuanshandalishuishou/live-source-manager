"""Feature 1~5 新增能力单元测试。

覆盖：
- Feature 1: aiohttp 异步探针 + test_method 开关（环境变量覆盖）
- Feature 2: 候选池留存/冻结/择优
- Feature 3: IPv4/IPv6 URL 判定
- Feature 4: 环境变量覆盖
- Feature 5: 失效统计自动停用/恢复
"""

import asyncio
import logging
import os

import pytest
from app import Config
from app.m3u_generator import M3UGenerator
from app.manager import EnhancedLiveSourceManager as Manager
from web import models

logger = logging.getLogger('test_new_features')


@pytest.fixture(autouse=True)
def _clean_tables():
    """清理本次测试涉及的业务表，避免跨测试污染。"""
    models.clear_candidate_pool()
    for row in models.get_failure_stats():
        models.reenable_source(row['url'])
    yield
    models.clear_candidate_pool()


# ────────────────────────────────────────────────
# Feature 4：环境变量覆盖
# ────────────────────────────────────────────────
def test_config_env_override(monkeypatch):
    monkeypatch.setenv('LSM_TESTING_TEST_METHOD', 'aiohttp')
    cfg = Config()
    assert cfg.get('Testing', 'test_method') == 'aiohttp'

    monkeypatch.setenv('LSM_OUTPUT_CANDIDATE_POOL_ENABLED', 'False')
    assert cfg.get('Output', 'candidate_pool_enabled') == 'False'
    assert cfg.getboolean('Output', 'candidate_pool_enabled') is False

    # 环境变量缺失时回退默认（get_testing_params 会应用代码默认值）
    monkeypatch.delenv('LSM_TESTING_TEST_METHOD', raising=False)
    assert cfg.get_testing_params()['test_method'] == 'ffprobe'
    # 裸 get 在缺失时回退到 default 参数（与代码默认值一致）
    assert cfg.get('Testing', 'test_method', 'ffprobe') == 'ffprobe'


# ────────────────────────────────────────────────
# Feature 3：IPv4/IPv6 URL 判定
# ────────────────────────────────────────────────
@pytest.mark.parametrize(
    'url,expected',
    [
        ('http://example.com/tv.m3u', False),
        ('http://192.168.1.10:8080/stream', False),
        ('http://[2001:db8::1]:80/tv.m3u', True),
        ('rtsp://[fe80::1%eth0]/live', True),
        ('https://[2408:400a::1234]/ipv6.m3u', True),
    ],
)
def test_is_ipv6_url(url, expected):
    assert M3UGenerator.is_ipv6_url(url) is expected


# ────────────────────────────────────────────────
# Feature 2：候选池留存 / 冻结 / 统计
# ────────────────────────────────────────────────
def test_candidate_pool_save_freeze_stats():
    entries = [
        {
            'url': 'http://a.com/1',
            'name': 'CCTV1',
            'group': '央视',
            'resolution': '1920x1080',
            'bitrate': 2000,
            'response_time': 120,
            'download_speed': 500.0,
            'media_type': 'video',
            'status': 'success',
        },
        {
            'url': 'http://a.com/2',
            'name': 'CCTV1',
            'group': '央视',
            'resolution': '1280x720',
            'bitrate': 1200,
            'response_time': 80,
            'download_speed': 800.0,
            'media_type': 'video',
            'status': 'success',
        },
        {
            'url': 'http://b.com/x',
            'name': 'BTV',
            'group': '地方',
            'status': 'failed',
        },
    ]
    n = models.save_candidate_pool(entries)
    assert n == 3

    stats = models.get_candidate_pool_stats()
    assert stats['total'] == 3
    assert stats['success'] == 2
    assert stats['failed'] == 1

    # 冻结 CCTV1 的 720p 源
    assert models.set_candidate_frozen('http://a.com/2', True) is True
    frozen = models.get_candidate_frozen_urls()
    assert 'http://a.com/2' in frozen

    # 过滤查询
    only_frozen = models.get_candidate_pool(only_frozen=True)
    assert len(only_frozen) == 1
    assert only_frozen[0]['url'] == 'http://a.com/2'

    by_channel = models.get_candidate_pool(channel='CCTV')
    assert len(by_channel) == 2

    # 幂等更新：同 url 再次写入应更新而非新增
    models.save_candidate_pool(
        [{'url': 'http://a.com/1', 'name': 'CCTV1', 'status': 'success', 'download_speed': 999.0}]
    )
    assert models.get_candidate_pool_stats()['total'] == 3


# ────────────────────────────────────────────────
# Feature 5：失效统计自动停用 / 恢复
# ────────────────────────────────────────────────
def test_failure_stats_auto_disable_and_reenable():
    # 语义：同一 URL 连续失败达到阈值才停用（而非不同 URL 各失败一次）
    url = 'http://dead.com/s'
    threshold = 3
    newly_total = 0
    for _ in range(threshold):
        newly_total += models.record_source_failures([url], threshold=threshold, cooldown_hours=24)
    # 仅最后一次达到阈值时新停用 1 个
    assert newly_total == 1
    assert url in models.get_disabled_source_urls()

    # 另一个源仅失败 1 次，未达阈值，不应被停用
    models.record_source_failures(['http://alive.com/s'], threshold=threshold, cooldown_hours=24)
    assert 'http://alive.com/s' not in models.get_disabled_source_urls()

    # 成功源重置连续失败计数（但已停用的不强制解除停用）
    models.reset_source_failure(url)
    assert url in models.get_disabled_source_urls()

    # 手动恢复
    assert models.reenable_source(url) is True
    assert url not in models.get_disabled_source_urls()


def test_failure_stats_auto_reenable_after_cooldown():
    models.record_source_failures(['http://old.com/s'], threshold=1, cooldown_hours=0)
    assert 'http://old.com/s' in models.get_disabled_source_urls()
    # cooldown_hours=0 → 立即过期恢复
    n = models.auto_reenable_expired_failures(0)
    assert n >= 1
    assert 'http://old.com/s' not in models.get_disabled_source_urls()


def test_record_outcomes_ignores_interrupted():
    """用户中止运行(interrupted)不应累计失败计数，避免误停健康源。"""
    from app import ChannelRules
    from app.source_manager import SourceManager

    sm = SourceManager(Config(), logging.getLogger('t'), ChannelRules())
    sm.auto_disable_enabled = True
    sm.auto_disable_fail_threshold = 3
    sm.auto_disable_cooldown_hours = 24

    url = 'http://flaky.com/s'
    # 连续 3 次 interrupted（用户多次中止）不应触发停用
    for _ in range(3):
        sm.record_test_outcomes([{'url': url, 'status': 'interrupted'}])
    assert url not in models.get_disabled_source_urls()

    # 真实 failed 达到阈值则正常停用
    for _ in range(3):
        sm.record_test_outcomes([{'url': url, 'status': 'failed'}])
    assert url in models.get_disabled_source_urls()


def test_failure_stats_reset_clears_first_fail_ts():
    url = 'http://reset.com/s'
    models.record_source_failures([url], threshold=10, cooldown_hours=24)
    assert models.get_failure_stats()[0]['first_fail_ts'] is not None
    models.reset_source_failure(url)
    row = next(r for r in models.get_failure_stats() if r['url'] == url)
    assert row['fail_count'] == 0
    assert row['first_fail_ts'] is None


def test_set_candidate_frozen_upsert():
    """冻结尚未入池的 URL 也应生效（UPSERT），支持「提前手动优选」。"""
    url = 'http://prefer.com/s'
    assert models.set_candidate_frozen(url, True) is True
    assert url in models.get_candidate_frozen_urls()
    # 解冻
    assert models.set_candidate_frozen(url, False) is True
    assert url not in models.get_candidate_frozen_urls()


def test_per_source_ua_applied():
    """每源 UA（URL→字符串）须归一化为 {ua_value} 结构并被应用，且不崩溃。"""
    from app import ChannelRules
    from app.source_manager import SourceManager

    sm = SourceManager(Config(), logging.getLogger('t'), ChannelRules())
    sm.per_source_ua = {'http://custom.com/stream': 'CustomUA/9.9'}
    sources = [
        {'name': 'C1', 'url': 'http://custom.com/stream'},
        {'name': 'C2', 'url': 'http://other.com/stream'},
    ]
    out = sm.apply_ua_settings(sources)
    custom = next(s for s in out if s['url'] == 'http://custom.com/stream')
    assert custom['user_agent'] == 'CustomUA/9.9'
    other = next(s for s in out if s['url'] == 'http://other.com/stream')
    assert other.get('user_agent') != 'CustomUA/9.9'


# ────────────────────────────────────────────────
# Feature 2：候选池择优逻辑（Manager._apply_candidate_selection）
# ────────────────────────────────────────────────
def test_manager_parse_resolution_height():
    assert Manager._parse_resolution_height('1920x1080') == 1080
    assert Manager._parse_resolution_height('720p') == 720
    assert Manager._parse_resolution_height('') == 0


def test_manager_candidate_selection_topn_and_frozen():
    # 用轻量桩绑定 Manager 方法，避免构造完整 Manager
    class Stub:
        config = Config()
        logger = logger

        def _log(self, level, message):
            getattr(self.logger, level, print)(message)

        def logger_info(self, message):
            self._log('info', message)

        def logger_debug(self, message):
            self._log('debug', message)

        def _parse_resolution_height(self, r):
            return Manager._parse_resolution_height(r)

    stub = Stub()
    # 确保择优开启 + speed 指标 + Top2
    monkeypatch_cfg(stub.config, 'Output', 'candidate_pool_enabled', 'True')
    monkeypatch_cfg(stub.config, 'Output', 'auto_select_metric', 'speed')
    monkeypatch_cfg(stub.config, 'Output', 'max_sources_per_channel', '2')

    sources = [
        {'name': 'CCTV1', 'url': 'http://a/1', 'download_speed': 100.0, 'response_time': 200},
        {'name': 'CCTV1', 'url': 'http://a/2', 'download_speed': 900.0, 'response_time': 50},
        {'name': 'CCTV1', 'url': 'http://a/3', 'download_speed': 500.0, 'response_time': 90},
        {'name': 'CCTV2', 'url': 'http://b/1', 'download_speed': 300.0, 'response_time': 70},
    ]
    # 冻结最慢的 a/1，验证冻结源固定保留（set_candidate_frozen 为 UPDATE，需先入池）
    models.save_candidate_pool([{'url': 'http://a/1', 'name': 'CCTV1', 'status': 'success'}])
    models.set_candidate_frozen('http://a/1', True)

    selected = Manager._apply_candidate_selection.__get__(stub)(sources)
    cctv1 = [s for s in selected if s['name'] == 'CCTV1']
    # CCTV1：冻结 a/1 固定 + speed Top(2-1)=1 → a/2(900) 入选；a/3(500) 落选
    urls = {s['url'] for s in cctv1}
    assert 'http://a/1' in urls  # 冻结固定
    assert 'http://a/2' in urls  # 速度最高
    assert 'http://a/3' not in urls  # 超出 TopN
    assert len([s for s in selected if s['name'] == 'CCTV2']) == 1


def monkeypatch_cfg(config, section, key, value):
    """在 Config 的 SQLite 上层用环境变量覆盖不便，这里直接写库以驱动测试。"""
    models.set_app_config(f'{section}.{key}', value)


# ────────────────────────────────────────────────
# Feature 1：aiohttp 异步探针（本地临时服务，无需 ffprobe）
# ────────────────────────────────────────────────
def test_stream_tester_aiohttp_probe():
    # 通过环境变量切换到 aiohttp 引擎（同时验证环境变量覆盖链路）
    os.environ['LSM_TESTING_TEST_METHOD'] = 'aiohttp'
    try:
        cfg = Config()
        tester = StreamTesterLite(cfg, logger)
        assert tester.test_method == 'aiohttp'

        # 本地 aiohttp 测试服务与探针共用同一事件循环，避免服务端 socket 随旧 loop 关闭
        async def _run():
            from aiohttp import web

            async def handler(request):
                return web.Response(body=b'x' * 2048)

            app = web.Application()
            app.router.add_get('/', handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                status, meta = await tester._aiohttp_probe(f'http://127.0.0.1:{port}/', None, 5, 5)
                return status, meta
            finally:
                await runner.cleanup()

        status, meta = asyncio.run(_run())
        assert status == 'success', meta
        assert meta['download_speed'] > 0
        assert meta.get('media_type') in ('video', 'unknown', 'audio')
    finally:
        os.environ.pop('LSM_TESTING_TEST_METHOD', None)


# StreamTesterLite：避免完整 StreamTester.__init__ 触发 ffprobe 探测子进程，
# 仅构造最小属性以便测试 _aiohttp_probe。
class StreamTesterLite:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.testing_params = config.get_testing_params()
        _method = (self.testing_params.get('test_method') or 'ffprobe').lower()
        import aiohttp as _aio

        if _method == 'aiohttp' and _aio is None:
            _method = 'ffprobe'
        self.test_method = _method

    def check_ipv6_support(self):
        return False

    def _aiohttp_probe(self, url, user_agent, connect_timeout, read_timeout):
        from app.stream_tester import StreamTester

        # 复用真实实现：将本精简对象的必要属性注入真实方法
        real = StreamTester.__new__(StreamTester)
        real.config = self.config
        real.logger = self.logger
        real.testing_params = self.testing_params
        real.test_method = self.test_method
        real._abort = _AbortStub()
        real.check_ipv6_support = self.check_ipv6_support
        return StreamTester._aiohttp_probe(real, url, user_agent, connect_timeout, read_timeout)


class _AbortStub:
    def is_set(self):
        return False
