#!/usr/bin/env python3
"""配置一致性测试（D-01 / P0-b 配置部分）

守护两条链路：
  1. config/config-defaults.yaml 多行默认值（Sources.online_urls / github_sources）
     经 YAML 加载 + 归一化后，按行与 app.config.Config._DEFAULT_VALUES 完全一致，
     且不存在「单引号折叠标量把 URL 粘成一个巨串」的历史缺陷（每条 URL 独立成行）。
  2. EPG.open_subscribe_epg 开关：SECTION_SCHEMA 暴露 + _DEFAULT_VALUES 种子默认 True。
"""

import re

from app.config import Config
from web.core import SECTION_SCHEMA


def _split_lines(raw: str) -> list[str]:
    """按 \\n 切分并去空行（与 Config 消费端一致）。"""
    return [line.strip() for line in str(raw).split('\n') if line.strip()]


def test_online_urls_yaml_override_matches_default_values():
    """YAML 覆盖后 online_urls 按行与 _DEFAULT_VALUES 一致，且每条均为独立 URL。"""
    yaml_value = SECTION_SCHEMA['Sources']['online_urls'][1]
    default_value = Config._DEFAULT_VALUES['Sources.online_urls']

    yaml_lines = _split_lines(yaml_value)
    default_lines = _split_lines(default_value)

    assert yaml_lines, 'YAML 覆盖后 online_urls 为空'
    assert len(yaml_lines) == len(default_lines) > 0
    assert yaml_lines == default_lines

    # 防折叠回归：每条必须含 scheme，且 URL 内部无空格（折叠会把换行变空格）
    for line in yaml_lines:
        assert '://' in line, f'URL 缺少 scheme: {line!r}'
        assert ' ' not in line, f'URL 内部含空格（疑似折叠粘连）: {line!r}'


def test_github_sources_yaml_override_matches_default_values():
    """YAML 覆盖后 github_sources 按行与 _DEFAULT_VALUES 一致，且每条为 owner/repo 形态。"""
    yaml_value = SECTION_SCHEMA['Sources']['github_sources'][1]
    default_value = Config._DEFAULT_VALUES['Sources.github_sources']

    yaml_lines = _split_lines(yaml_value)
    default_lines = _split_lines(default_value)

    assert yaml_lines, 'YAML 覆盖后 github_sources 为空'
    assert yaml_lines == default_lines

    for line in yaml_lines:
        # owner/repo 或 owner/repo/branch 形态
        assert re.fullmatch(r'[^/\s]+/[^/\s]+(/[^/\s]+)?', line), f'非 owner/repo 形态: {line!r}'
        assert ' ' not in line, f'条目内部含空格（疑似折叠粘连）: {line!r}'


def test_epg_open_subscribe_epg_schema_exposed():
    """EPG.open_subscribe_epg 必须在 SECTION_SCHEMA 中暴露为布尔开关且默认 True。"""
    assert 'open_subscribe_epg' in SECTION_SCHEMA['EPG']
    field_def = SECTION_SCHEMA['EPG']['open_subscribe_epg']
    assert field_def[0] == 'bool'
    assert field_def[1] == 'True'


def test_epg_open_subscribe_epg_default_value_seeded():
    """EPG.open_subscribe_epg 必须进入 _DEFAULT_VALUES 种子且默认 True。"""
    assert Config._DEFAULT_VALUES.get('EPG.open_subscribe_epg') == 'True'
