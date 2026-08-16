"""
代理路径单元测试

覆盖 H1 修复：
- SourceManager._get_http_proxy_url() 的各种代理配置场景
- 确保 HTTP 代理返回正确的 proxy URL（供 session.get(proxy=...) 使用）
- 确保 SOCKS5 代理返回 None（在连接器层面处理）
- 确保代理禁用时返回 None

覆盖 M2 修复：
- web.routes.epg._parse_hhmm() 的时间格式边界检查
"""

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ══════════════════════════════════════════════════
# H1: SourceManager._get_http_proxy_url
# ══════════════════════════════════════════════════


def _make_source_manager(network_cfg: dict):
    """创建带有指定 network_config 的 SourceManager 实例（不触发真实网络连接）。"""
    from app import ChannelRules
    from app.config import Config
    from app.source_manager import SourceManager

    sm = SourceManager(Config(), logging.getLogger('test_proxy'), ChannelRules())
    # 直接覆写 network_config，避免依赖 DB 中的配置
    sm.network_config = network_cfg
    return sm


def test_http_proxy_disabled_returns_none():
    """代理禁用时 _get_http_proxy_url 返回 None。"""
    sm = _make_source_manager({'proxy_enabled': False})
    assert sm._get_http_proxy_url() is None


def test_http_proxy_basic():
    """HTTP 代理（无认证）返回正确的 proxy URL。"""
    sm = _make_source_manager(
        {
            'proxy_enabled': True,
            'proxy_type': 'http',
            'proxy_host': '127.0.0.1',
            'proxy_port': 8080,
            'proxy_username': '',
            'proxy_password': '',
        }
    )
    url = sm._get_http_proxy_url()
    assert url == 'http://127.0.0.1:8080'


def test_http_proxy_with_auth():
    """HTTP 代理（带认证）返回包含用户名密码的 proxy URL。"""
    sm = _make_source_manager(
        {
            'proxy_enabled': True,
            'proxy_type': 'http',
            'proxy_host': '10.0.0.1',
            'proxy_port': 3128,
            'proxy_username': 'admin',
            'proxy_password': 'secret',
        }
    )
    url = sm._get_http_proxy_url()
    assert url == 'http://admin:secret@10.0.0.1:3128'


def test_socks5_proxy_returns_none():
    """SOCKS5 代理在连接器层面处理，_get_http_proxy_url 返回 None。"""
    sm = _make_source_manager(
        {
            'proxy_enabled': True,
            'proxy_type': 'socks5',
            'proxy_host': '127.0.0.1',
            'proxy_port': 1080,
            'proxy_username': '',
            'proxy_password': '',
        }
    )
    assert sm._get_http_proxy_url() is None


def test_socks5h_proxy_returns_none():
    """SOCKS5h 代理同样在连接器层面处理。"""
    sm = _make_source_manager(
        {
            'proxy_enabled': True,
            'proxy_type': 'socks5h',
            'proxy_host': '127.0.0.1',
            'proxy_port': 1080,
            'proxy_username': '',
            'proxy_password': '',
        }
    )
    assert sm._get_http_proxy_url() is None


def test_http_proxy_missing_host_returns_none():
    """HTTP 代理但 host 为空时返回 None。"""
    sm = _make_source_manager(
        {
            'proxy_enabled': True,
            'proxy_type': 'http',
            'proxy_host': '',
            'proxy_port': 8080,
            'proxy_username': '',
            'proxy_password': '',
        }
    )
    assert sm._get_http_proxy_url() is None


def test_http_proxy_missing_port_returns_none():
    """HTTP 代理但 port 为 0 时返回 None。"""
    sm = _make_source_manager(
        {
            'proxy_enabled': True,
            'proxy_type': 'http',
            'proxy_host': '127.0.0.1',
            'proxy_port': 0,
            'proxy_username': '',
            'proxy_password': '',
        }
    )
    assert sm._get_http_proxy_url() is None


def test_http_proxy_type_case_insensitive():
    """代理类型大小写不敏感（HTTP / Http 都应识别）。"""
    sm = _make_source_manager(
        {
            'proxy_enabled': True,
            'proxy_type': 'HTTP',
            'proxy_host': '127.0.0.1',
            'proxy_port': 8080,
            'proxy_username': '',
            'proxy_password': '',
        }
    )
    assert sm._get_http_proxy_url() == 'http://127.0.0.1:8080'


# ══════════════════════════════════════════════════
# M2: _parse_hhmm 边界检查
# ══════════════════════════════════════════════════


def test_parse_hhmm_valid():
    """正常 HH:MM 格式正确解析。"""
    from web.routes.epg import _parse_hhmm

    assert _parse_hhmm('03:30') == (3, 30)
    assert _parse_hhmm('00:00') == (0, 0)
    assert _parse_hhmm('23:59') == (23, 59)
    assert _parse_hhmm(' 12:00 ') == (12, 0)  # 带空格


def test_parse_hhmm_empty_returns_default():
    """空字符串返回默认值。"""
    from web.routes.epg import _parse_hhmm

    assert _parse_hhmm('') == (3, 30)
    assert _parse_hhmm(None) == (3, 30)  # type: ignore[arg-type]


def test_parse_hhmm_invalid_format_returns_default():
    """无效格式返回默认值。"""
    from web.routes.epg import _parse_hhmm

    assert _parse_hhmm('abc') == (3, 30)
    assert _parse_hhmm('25:00') == (3, 30)  # hour 越界
    assert _parse_hhmm('12:60') == (3, 30)  # minute 越界
    assert _parse_hhmm('12') == (3, 30)  # 缺少冒号
    assert _parse_hhmm('12:30:00') == (3, 30)  # 多余部分


def test_parse_hhmm_custom_default():
    """自定义默认值。"""
    from web.routes.epg import _parse_hhmm

    assert _parse_hhmm('', default=(6, 0)) == (6, 0)
    assert _parse_hhmm('invalid', default=(0, 15)) == (0, 15)
