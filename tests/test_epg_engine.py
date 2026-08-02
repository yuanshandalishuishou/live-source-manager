"""EPG 引擎单元测试：XMLTV 解析 / 归一化 / 入库存取 / 导出生成 / 网格查询"""

import gzip
import io
import os
import tempfile

from app.epg import (
    EPGManager,
    XMLTVParser,
    normalize_channel_name,
    parse_xmltv_time,
    to_utc_str,
    tz_offset_minutes,
    utc_str_to_xmltv,
)
from web import models

SAMPLE_XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="CCTV1.cn">
    <display-name>CCTV-1</display-name>
    <icon src="http://logo/cctv1.png" />
  </channel>
  <channel id="HunanTV">
    <display-name>湖南卫视</display-name>
  </channel>
  <programme start="20260730020000 +0800" stop="20260730030000 +0800" channel="CCTV1.cn">
    <title>新闻联播</title>
    <category>新闻</category>
  </programme>
  <programme start="20200101000000 +0800" stop="20200101010000 +0800" channel="HunanTV">
    <title>历史节目(应被时间窗过滤)</title>
  </programme>
</tv>
"""


def _clear_epg():
    conn = models.get_conn()
    conn.executescript('DELETE FROM epg_programmes; DELETE FROM epg_channels; DELETE FROM epg_sources;')
    conn.commit()
    conn.close()


def test_normalize_channel_name():
    assert normalize_channel_name('CCTV-1 综合 高清') == 'cctv1综合'
    assert normalize_channel_name('湖南卫视HD') == '湖南卫视'
    assert normalize_channel_name('ＣＣＴＶ５') == 'cctv5'  # 全角转半角


def test_parse_xmltv_time():
    dt = parse_xmltv_time('20260730120000 +0800')
    assert dt.strftime('%Y-%m-%d %H:%M:%S') == '2026-07-30 04:00:00'
    assert dt.tzinfo is not None


def test_xmltv_parse_xml_and_gz():
    import datetime as _dt

    utc = _dt.UTC
    win_start = _dt.datetime(2026, 1, 1, tzinfo=utc)
    win_end = _dt.datetime(2027, 1, 1, tzinfo=utc)
    # 普通 xml（带时间窗：2020 年的历史节目应被过滤）
    ch, pg = XMLTVParser(480, win_start, win_end).parse_stream(io.BytesIO(SAMPLE_XMLTV.encode('utf-8')))
    assert len(ch) == 2
    assert len(pg) == 1  # 历史节目被时间窗过滤
    assert pg[0]['title'] == '新闻联播'

    # gzip（走 parse_file 自动识别 gzip）

    fd, gz_path = tempfile.mkstemp(suffix='.xml.gz')
    os.close(fd)
    with gzip.GzipFile(gz_path, 'wb') as gz:
        gz.write(SAMPLE_XMLTV.encode('utf-8'))
    try:
        ch2, pg2 = XMLTVParser(480, win_start, win_end).parse_file(gz_path)
        assert len(ch2) == 2 and len(pg2) == 1
    finally:
        os.remove(gz_path)


def test_roundtrip_xmltv_time():
    dt = parse_xmltv_time('20260730120000 +0800')
    s = to_utc_str(dt)
    back = utc_str_to_xmltv(s, 480)
    assert back.startswith('20260730') and '120000' in back


def test_generate_and_grid(tmp_path):
    _clear_epg()
    sid = models.add_epg_source('测试源', 'http://example.com/e.xml', enabled=True, priority=10)
    assert sid

    # 用当前时间窗内的节目，确保网格能查到（start_utc 必须用 to_utc_str 格式）
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    start = to_utc_str(now - __import__('datetime').timedelta(hours=1))
    stop = to_utc_str(now + __import__('datetime').timedelta(hours=2))

    ch_n, pg_n = models.replace_epg_data(
        sid,
        [
            {'tvg_id': 'CCTV1.cn', 'display_name': 'CCTV-1', 'icon': 'http://logo/cctv1.png'},
            {'tvg_id': 'HunanTV', 'display_name': '湖南卫视', 'icon': ''},
        ],
        [
            {'tvg_id': 'CCTV1.cn', 'start_utc': start, 'stop_utc': stop, 'title': '新闻联播', 'category': '新闻'},
            {'tvg_id': 'HunanTV', 'start_utc': start, 'stop_utc': stop, 'title': '快乐大本营'},
        ],
    )
    assert ch_n == 2 and pg_n == 2

    mgr = EPGManager()
    grid = mgr.get_grid_data(hours=12, limit=50)
    assert grid['total'] == 2
    titles = {c['name']: [p['title'] for p in c['programmes']] for c in grid['channels']}
    # 频道名使用 matched_channel（空）→ display_name
    assert any('新闻联播' in v for v in titles.values())

    # 导出 XMLTV（写入临时目录）
    out = tmp_path / 'epg.xml.gz'
    res = mgr.generate_xmltv(str(out))
    assert res['ok'] is True
    assert out.exists()
    content = gzip.GzipFile(fileobj=io.BytesIO(out.read_bytes())).read().decode('utf-8')
    assert '<tv' in content and 'CCTV1.cn' in content and '新闻联播' in content

    # Now/Next
    nn = mgr.get_now_next(['CCTV1.cn'])
    assert 'CCTV1.cn' in nn


def test_tz_offset():
    assert tz_offset_minutes('Asia/Shanghai') == 480
    # 非法时区回落 480
    assert tz_offset_minutes('Invalid/Zone') == 480
