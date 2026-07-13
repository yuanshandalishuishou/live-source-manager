"""实时测试源去重单元测试。

锁定 `web.routes.system.dedup_sources_by_url` 的行为：
- 按频道原始地址(url)去重，相同 url 只保留首个；
- 跨文件/跨源全局去重（不限于同一文件）；
- 空 url 的源保留（不静默丢弃）；
- 不修改入参；返回新列表。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.routes.system import dedup_sources_by_url


def _mk(url, name='c'):
    return {'url': url, 'name': name, 'source_path': 'f.m3u'}


def test_dedup_removes_exact_duplicate_url():
    sources = [
        _mk('http://a.com/1.m3u8', 'A1'),
        _mk('http://a.com/1.m3u8', 'A2'),  # 与 A1 同地址 → 应被去重
        _mk('http://b.com/2.m3u8', 'B1'),
    ]
    out = dedup_sources_by_url(sources)
    assert len(out) == 2
    assert out[0]['name'] == 'A1'  # 保留首个
    assert out[1]['name'] == 'B1'


def test_dedup_is_cross_file():
    # 不同 source_path 但同 url → 视为重复（李总诉求：多个源文件地址一样）
    sources = [
        {'url': 'http://x.com/s.m3u8', 'name': 'X1', 'source_path': 'file_a.m3u'},
        {'url': 'http://x.com/s.m3u8', 'name': 'X2', 'source_path': 'file_b.m3u'},
    ]
    out = dedup_sources_by_url(sources)
    assert len(out) == 1
    assert out[0]['source_path'] == 'file_a.m3u'


def test_dedup_keeps_empty_url_sources():
    sources = [
        _mk('http://a.com/1.m3u8'),
        {'name': 'no-url', 'source_path': 'f.m3u'},  # 无 url
        _mk('http://a.com/1.m3u8'),  # 与第一条同 url → 去重
    ]
    out = dedup_sources_by_url(sources)
    # 空 url 源保留；重复 url 去掉一个 → 2 个
    assert len(out) == 2
    assert any(s.get('name') == 'no-url' for s in out)


def test_dedup_does_not_mutate_input():
    sources = [_mk('http://a.com/1.m3u8'), _mk('http://a.com/1.m3u8')]
    before = len(sources)
    out = dedup_sources_by_url(sources)
    assert len(sources) == before  # 入参未变
    assert len(out) == 1


def test_dedup_empty_list():
    assert dedup_sources_by_url([]) == []
