"""P2-⑤/⑥ 输出优化集成测试（对标 Guovin/iptv-api）：

- 输出按速度排序（快源在前）
- 全局白名单强制保留（未过质量过滤也进输出）
"""

from unittest.mock import MagicMock

from app.m3u_generator import M3UGenerator


def _make_gen(output_params: dict, whitelist: str = '') -> M3UGenerator:
    cfg = MagicMock()
    cfg.get_output_params.return_value = {
        'filename': 'live.m3u',
        'group_by': 'category',
        'include_failed': False,
        'max_sources_per_channel': 8,
        'enable_filter': False,
        'whitelist_force_keep': output_params.pop('whitelist_force_keep', False),
        'output_dir': './www/output',
        **output_params,
    }
    cfg.get_filter_params.return_value = {
        'max_latency': 4000,
        'min_bitrate': 80,
        'must_hd': False,
        'must_4k': False,
        'min_speed': 50,
        'min_resolution': '360p',
        'max_resolution': '',
        'resolution_filter_mode': 'range',
    }
    cfg.get_ua_position.return_value = 'extinf'
    cfg.is_ua_enabled.return_value = False
    cfg.get.side_effect = lambda sec, key, default='': (
        whitelist if (sec, key) == ('Testing', 'global_whitelist') else default
    )
    return M3UGenerator(cfg, MagicMock())


def _src(name, speed, rt=100, status='success'):
    return {
        'name': name,
        'url': f'http://{name}.example.com/x',
        'status': status,
        'download_speed': speed,
        'response_time': rt,
        'content': '新闻',
        'category': '新闻',
    }


class TestOutputSortBy:
    def test_speed_sort_puts_fast_first(self):
        gen = _make_gen({'output_sort_by': 'speed'})
        srcs = [_src('a', 100), _src('b', 500), _src('c', 50)]
        grouped = gen.enhanced_group_and_sort_sources(srcs, 'base')
        flat = [s['name'] for grp in grouped.values() for s in grp]
        # 按 download_speed 降序：b(500) > a(100) > c(50)
        assert flat == ['b', 'a', 'c'], flat

    def test_name_sort(self):
        gen = _make_gen({'output_sort_by': 'name'})
        srcs = [_src('c', 100), _src('a', 500), _src('b', 50)]
        grouped = gen.enhanced_group_and_sort_sources(srcs, 'base')
        flat = [s['name'] for grp in grouped.values() for s in grp]
        assert flat == ['a', 'b', 'c'], flat

    def test_resolution_sort(self):
        gen = _make_gen({'output_sort_by': 'resolution'})
        srcs = [
            {
                'name': 'low',
                'url': 'u1',
                'status': 'success',
                'download_speed': 0,
                'response_time': 100,
                'content': 'C',
                'category': 'C',
                'resolution': '720x404',
            },
            {
                'name': 'high',
                'url': 'u2',
                'status': 'success',
                'download_speed': 0,
                'response_time': 100,
                'content': 'C',
                'category': 'C',
                'resolution': '1920x1080',
            },
        ]
        grouped = gen.enhanced_group_and_sort_sources(srcs, 'base')
        flat = [s['name'] for grp in grouped.values() for s in grp]
        assert flat == ['high', 'low'], flat


class TestWhitelistForceKeep:
    def test_whitelist_kept_despite_filter(self):
        # 启用过滤 + 白名单强制保留：白名单源（速度低于 min_speed）仍进输出
        gen = _make_gen({'enable_filter': True, 'whitelist_force_keep': True}, whitelist='keep.example.com')
        srcs = [
            _src('keep', 10),  # download_speed=10 < min_speed=50，但白名单应保留
            _src('good', 200),
        ]
        filtered = gen.enhanced_filter_sources(srcs)
        names = {s['name'] for s in filtered}
        assert 'keep' in names, names
        assert 'good' in names, names

    def test_whitelist_disabled_drops_slow(self):
        # 未启用强制保留：慢源被过滤掉
        gen = _make_gen({'enable_filter': True, 'whitelist_force_keep': False}, whitelist='keep.example.com')
        srcs = [_src('keep', 10), _src('good', 200)]
        filtered = gen.enhanced_filter_sources(srcs)
        names = {s['name'] for s in filtered}
        assert 'keep' not in names, names
        assert 'good' in names, names
