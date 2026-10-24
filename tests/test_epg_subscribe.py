#!/usr/bin/env python3
"""P0-b: 订阅源 #EXTM3U 头 url-tvg/x-tvg-url 自动注册 EPG 源测试

覆盖 SourceManager._extract_tvg_url / _register_subscribe_epg：
- 属性提取：url-tvg / x-tvg-url、大小写不敏感、单双引号、仅 http(s)
- 注册链路：parse_file 解析到 #EXTM3U 头时调用 add_epg_source（priority=200、auto/subscribe）
- 开关：EPG.open_subscribe_epg=False 时不注册
- 去重：无 url-tvg 头不调用；同 URL 重复头不重复注册
- DB 隔离：monkeypatch web.models.add_epg_source，不写真实库
"""

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

_EPG_URL = 'http://example.com/epg.xml'


def _make_source_manager():
    """构造 SourceManager（真实 Config，走 conftest 隔离的测试库）。"""
    from app import ChannelRules
    from app.config import Config
    from app.source_manager import SourceManager

    return SourceManager(Config(), logging.getLogger('test_epg_subscribe'), ChannelRules())


def _write_m3u(tmp_path, name: str, header: str) -> str:
    """写一个最小 m3u 文件并返回路径。"""
    path = tmp_path / name
    path.write_text(
        header + '\n#EXTINF:-1 group-title="测试",CCTV1\nhttp://example.com/live/cctv1.m3u8\n',
        encoding='utf-8',
    )
    return str(path)


def _patch_add_epg_source(monkeypatch):
    """patch web.models.add_epg_source，返回调用记录列表。"""
    import web.models as models

    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return 1  # 新 ID

    monkeypatch.setattr(models, 'add_epg_source', _fake)
    return calls


class TestExtractTvgUrl:
    def test_url_tvg_double_quotes(self):
        sm = _make_source_manager()
        assert sm._extract_tvg_url('#EXTM3U url-tvg="http://example.com/epg.xml"') == _EPG_URL

    def test_x_tvg_url_single_quotes(self):
        sm = _make_source_manager()
        assert sm._extract_tvg_url("#EXTM3U x-tvg-url='http://example.com/epg.xml'") == _EPG_URL

    def test_case_insensitive(self):
        sm = _make_source_manager()
        assert sm._extract_tvg_url('#EXTM3U URL-TVG="http://example.com/epg.xml"') == _EPG_URL
        assert sm._extract_tvg_url('#EXTM3U X-Tvg-Url="http://example.com/epg.xml"') == _EPG_URL

    def test_no_attribute_returns_none(self):
        sm = _make_source_manager()
        assert sm._extract_tvg_url('#EXTM3U') is None
        assert sm._extract_tvg_url('') is None

    def test_non_http_scheme_returns_none(self):
        sm = _make_source_manager()
        assert sm._extract_tvg_url('#EXTM3U url-tvg="ftp://example.com/e.xml"') is None
        assert sm._extract_tvg_url('#EXTM3U url-tvg="not-a-url"') is None

    def test_first_valid_url_wins(self):
        sm = _make_source_manager()
        line = '#EXTM3U url-tvg="not-a-url" x-tvg-url="http://a.example.com/e.xml"'
        assert sm._extract_tvg_url(line) == 'http://a.example.com/e.xml'


class TestRegisterSubscribeEpg:
    def test_parse_file_registers_epg(self, tmp_path, monkeypatch):
        sm = _make_source_manager()
        calls = _patch_add_epg_source(monkeypatch)
        path = _write_m3u(tmp_path, 'sub.m3u', f'#EXTM3U url-tvg="{_EPG_URL}"')

        sm.parse_file(path)

        assert len(calls) == 1, calls
        assert calls[0]['url'] == _EPG_URL
        assert calls[0]['priority'] == 200
        assert calls[0]['enabled'] is True
        assert 'auto/subscribe' in calls[0]['remark']

    def test_reparse_same_file_not_registered_twice(self, tmp_path, monkeypatch):
        sm = _make_source_manager()
        calls = _patch_add_epg_source(monkeypatch)
        path = _write_m3u(tmp_path, 'sub.m3u', f'#EXTM3U url-tvg="{_EPG_URL}"')

        sm.parse_file(path)
        sm.parse_file(path)  # 同一文件重复解析

        assert len(calls) == 1

    def test_switch_off_skips_registration(self, tmp_path, monkeypatch):
        from app.config import Config

        sm = _make_source_manager()
        calls = _patch_add_epg_source(monkeypatch)

        # 仅对 EPG.open_subscribe_epg 返回 False，其余委托原实现
        orig = Config.getboolean
        monkeypatch.setattr(
            sm.config,
            'getboolean',
            lambda sec, key, default=False: (
                False if (sec, key) == ('EPG', 'open_subscribe_epg') else orig(sm.config, sec, key, default)
            ),
        )

        path = _write_m3u(tmp_path, 'sub.m3u', f'#EXTM3U url-tvg="{_EPG_URL}"')
        sm.parse_file(path)

        assert calls == []

    def test_no_tvg_header_not_registered(self, tmp_path, monkeypatch):
        sm = _make_source_manager()
        calls = _patch_add_epg_source(monkeypatch)
        path = _write_m3u(tmp_path, 'sub.m3u', '#EXTM3U')

        sm.parse_file(path)

        assert calls == []

    def test_duplicate_url_across_files_registered_once(self, tmp_path, monkeypatch):
        sm = _make_source_manager()
        calls = _patch_add_epg_source(monkeypatch)
        p1 = _write_m3u(tmp_path, 'a.m3u', f'#EXTM3U url-tvg="{_EPG_URL}"')
        p2 = _write_m3u(tmp_path, 'b.m3u', f'#EXTM3U x-tvg-url="{_EPG_URL}"')

        sm.parse_file(p1)
        sm.parse_file(p2)

        assert len(calls) == 1

    def test_ssrf_blocked_url_not_registered(self, tmp_path, monkeypatch):
        sm = _make_source_manager()
        calls = _patch_add_epg_source(monkeypatch)
        path = _write_m3u(tmp_path, 'sub.m3u', '#EXTM3U url-tvg="http://127.0.0.1/epg.xml"')

        sm.parse_file(path)

        assert calls == []
