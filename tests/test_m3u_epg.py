"""M3U 生成器 EPG 注入测试：tvg-id/tvg-logo 映射 + #EXTM3U url-tvg 注入"""

from unittest.mock import MagicMock, patch

from app.m3u_generator import M3UGenerator


def _make_gen(inject: bool, with_epg_manager=True, enabled: bool = True):
    cfg = MagicMock()
    cfg.get_output_params.return_value = {
        'filename': 'live.m3u',
        'group_by': 'category',
        'include_failed': False,
        'max_sources_per_channel': 8,
        'enable_filter': False,
        'whitelist_force_keep': False,
        'output_dir': './www/output',
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
    cfg.get.return_value = ''
    cfg.get_epg_config.return_value = {
        'enabled': enabled,
        'inject_into_m3u': inject,
        'web_base_url': '',
        'output_filename': 'epg.xml.gz',
    }
    cfg.get_http_server_config.return_value = {'host': '0.0.0.0', 'fileshare_port': 12345}
    return M3UGenerator(cfg, MagicMock())


def _src(name, logo=None):
    return {
        'name': name,
        'url': f'http://{name}.example.com/x',
        'status': 'success',
        'download_speed': 100,
        'response_time': 100,
        'content': '新闻',
        'category': '新闻',
        'logo': logo,
    }


def _fake_epg_manager(config):
    m = MagicMock()
    m.get_epg_url.return_value = 'http://127.0.0.1:12345/epg.xml.gz'
    return m


def _tvg_map():
    return {
        'CCTV-1 综合': {'tvg_id': 'CCTV1.cn', 'tvg_logo': 'http://logo/cctv1.png'},
        '湖南卫视': {'tvg_id': 'HunanTV', 'tvg_logo': 'http://logo/hn.png'},
    }


def test_inject_enabled():
    with (
        patch('web.models.get_all_channel_tvg_map', return_value=_tvg_map()),
        patch('app.epg.EPGManager', side_effect=_fake_epg_manager),
        patch('app.m3u_generator.os.path.exists', return_value=True),
        patch('app.m3u_generator.os.path.getsize', return_value=100),
    ):
        gen = _make_gen(inject=True)
        out = gen.generate_enhanced_m3u(
            [_src('CCTV-1 综合'), _src('湖南卫视', 'http://own/hn.png'), _src('未知台')], 'base'
        )
        assert 'url-tvg="http://127.0.0.1:12345/epg.xml.gz"' in out
        assert 'x-tvg-url="http://127.0.0.1:12345/epg.xml.gz"' in out
        assert 'tvg-id="CCTV1.cn"' in out
        assert 'tvg-id="HunanTV"' in out
        # 源自带 logo 优先于 EPG logo
        assert 'tvg-logo="http://own/hn.png"' in out
        # 未映射频道回退 slug
        assert 'tvg-id="___"' in out


def test_inject_disabled_no_header_but_map_applied():
    with (
        patch('web.models.get_all_channel_tvg_map', return_value=_tvg_map()),
        patch('app.epg.EPGManager', side_effect=_fake_epg_manager),
    ):
        gen = _make_gen(inject=False)
        out = gen.generate_enhanced_m3u([_src('CCTV-1 综合')], 'base')
        assert 'url-tvg' not in out
        # 即使关闭 header 注入，tvg_id 映射仍应生效
        assert 'tvg-id="CCTV1.cn"' in out


def test_epg_disabled_skips_header_even_if_inject_on():
    """EPG 总开关关闭时不得注入 url-tvg。

    否则调度器不抓取、epg.xml.gz 不生成，播放器按 url-tvg 只会拉到 404，
    比完全不注入更糟（部分播放器会因 EPG 拉取失败而弹错甚至拒绝加载列表）。
    """
    with (
        patch('web.models.get_all_channel_tvg_map', return_value=_tvg_map()),
        patch('app.epg.EPGManager', side_effect=_fake_epg_manager),
    ):
        gen = _make_gen(inject=True, enabled=False)
        out = gen.generate_enhanced_m3u([_src('CCTV-1 综合')], 'base')
        assert 'url-tvg' not in out
        assert 'x-tvg-url' not in out
        # 频道级 tvg-id 映射不受总开关影响，仍应生效
        assert 'tvg-id="CCTV1.cn"' in out
