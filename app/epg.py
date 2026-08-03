"""
EPG（电子节目单）核心模块 — L2 层

职责：
    1. XMLTVParser  — 流式（iterparse）解析 XMLTV，支持 gzip，防 OOM
    2. EPGFetcher   — 下载远端 EPG（复用 Network 代理/超时配置），支持本地文件
    3. EPGManager   — 抓取→解析→入库→频道对齐→导出合并 XMLTV→过期清理

依赖层级：L2（依赖 L0 exceptions/logger/utils + L1 config/security），
不依赖 web 层业务逻辑，仅通过 web.models 做持久化（与 config.py 同样的懒加载方式）。
"""

from __future__ import annotations

import asyncio
import gzip
import os
import re
import socket
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape as xml_escape

if TYPE_CHECKING:
    import io

from app.logger import setup_logger

logger = setup_logger(__name__)

# XMLTV 时间格式：20260730120000 +0800 / 20260730120000
_XMLTV_TIME_RE = re.compile(r'^\s*(\d{14})(?:\s*([+-]\d{4}))?\s*$')
_GZIP_MAGIC = b'\x1f\x8b'

UTC_FMT = '%Y-%m-%d %H:%M:%S'


# ══════════════════════════════════════════════════════════
# 时间与频道名工具
# ══════════════════════════════════════════════════════════


def parse_xmltv_time(value: str, default_offset_minutes: int = 480) -> datetime | None:
    """解析 XMLTV 时间串为 UTC datetime（tz-aware）。

    Args:
        value: 形如 '20260730120000 +0800' 或 '20260730120000'
        default_offset_minutes: 无时区标注时使用的偏移（默认 +08:00）

    Returns:
        UTC 时区的 datetime；无法解析返回 None
    """
    if not value:
        return None
    m = _XMLTV_TIME_RE.match(value)
    if not m:
        return None
    stamp, offset = m.group(1), m.group(2)
    try:
        naive = datetime.strptime(stamp, '%Y%m%d%H%M%S')
    except ValueError:
        return None
    if offset:
        sign = 1 if offset[0] == '+' else -1
        delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])) * sign
        tz = timezone(delta)
    else:
        tz = timezone(timedelta(minutes=default_offset_minutes))
    return naive.replace(tzinfo=tz).astimezone(UTC)


def to_utc_str(dt: datetime) -> str:
    """UTC datetime → 'YYYY-MM-DD HH:MM:SS' 字符串（入库统一格式）"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime(UTC_FMT)


def utc_str_to_xmltv(utc_str: str, offset_minutes: int = 480) -> str:
    """'YYYY-MM-DD HH:MM:SS'(UTC) → XMLTV 时间串（带目标时区偏移）"""
    try:
        dt = datetime.strptime(utc_str, UTC_FMT).replace(tzinfo=UTC)
    except ValueError:
        return ''
    tz = timezone(timedelta(minutes=offset_minutes))
    local = dt.astimezone(tz)
    sign = '+' if offset_minutes >= 0 else '-'
    total = abs(offset_minutes)
    return f'{local.strftime("%Y%m%d%H%M%S")} {sign}{total // 60:02d}{total % 60:02d}'


def tz_offset_minutes(tz_name: str) -> int:
    """时区名 → 相对 UTC 的分钟偏移（失败回落 +480 / Asia/Shanghai）"""
    if not tz_name:
        return 480
    try:
        from zoneinfo import ZoneInfo

        off = datetime.now(ZoneInfo(tz_name)).utcoffset()
        if off is not None:
            return int(off.total_seconds() // 60)
    except Exception as e:  # pragma: no cover - 依赖系统 tzdata
        logger.debug(f'解析时区 {tz_name} 失败，使用 +08:00: {e}')
    return 480


_CN_NUM = {
    '一': '1',
    '二': '2',
    '三': '3',
    '四': '4',
    '五': '5',
    '六': '6',
    '七': '7',
    '八': '8',
    '九': '9',
    '十': '10',
}
_QUALITY_SUFFIX = re.compile(r'(高清|超清|标清|蓝光|超高清|hd|uhd|fhd|sd|4k|8k|1080p?|720p?|ipv6|ipv4)$', re.I)


def normalize_channel_name(name: str) -> str:
    """频道名归一化，用于 EPG 频道与本地频道的模糊对齐。

    规则：全角转半角 → 去空白与分隔符 → 中文数字转阿拉伯 → 剥离画质后缀 → 小写。
    例：'CCTV-1 综合 高清' / 'CCTV１综合' → 'cctv1综合'
    """
    if not name:
        return ''
    s = unicodedata.normalize('NFKC', str(name)).strip()
    s = re.sub(r'[\s\-_·・.．,，、()（）\[\]【】|/\\]+', '', s)
    for cn, num in _CN_NUM.items():
        s = s.replace(f'CCTV{cn}', f'CCTV{num}').replace(f'cctv{cn}', f'cctv{num}')
    s = s.lower()
    # 反复剥离末尾画质词（'cctv1综合高清' → 'cctv1综合'）
    for _ in range(3):
        new = _QUALITY_SUFFIX.sub('', s)
        if new == s:
            break
        s = new
    return s


# ══════════════════════════════════════════════════════════
# XMLTV 解析
# ══════════════════════════════════════════════════════════


class XMLTVParser:
    """流式 XMLTV 解析器（iterparse + 增量 clear，防大文件 OOM）"""

    def __init__(
        self,
        default_offset_minutes: int = 480,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ):
        self.default_offset_minutes = default_offset_minutes
        self.window_start = window_start
        self.window_end = window_end
        self.channels: list[dict[str, str]] = []
        self.programmes: list[dict[str, str]] = []
        self.skipped_out_of_window = 0
        self.skipped_bad_time = 0

    @staticmethod
    def open_stream(path: str):
        """按 magic bytes 自动识别 gzip / 纯文本，返回二进制流"""
        with open(path, 'rb') as probe:
            head = probe.read(2)
        if head == _GZIP_MAGIC:
            return gzip.open(path, 'rb')
        return open(path, 'rb')

    def parse_file(self, path: str) -> tuple[list[dict], list[dict]]:
        """解析 XMLTV 文件，返回 (channels, programmes)"""
        stream = self.open_stream(path)
        try:
            return self.parse_stream(stream)
        finally:
            with_suppress_close(stream)

    def parse_stream(self, stream: io.IOBase) -> tuple[list[dict], list[dict]]:
        self.channels.clear()
        self.programmes.clear()
        self.skipped_out_of_window = 0
        self.skipped_bad_time = 0

        try:
            context = ET.iterparse(stream, events=('end',))
            for _event, elem in context:
                tag = elem.tag.lower()
                if tag == 'channel':
                    self._handle_channel(elem)
                    elem.clear()
                elif tag == 'programme':
                    self._handle_programme(elem)
                    elem.clear()
        except ET.ParseError as e:
            # 截断文件常见：已解析部分仍然可用
            logger.warning(f'XMLTV 解析在中途出错（保留已解析数据）: {e}')
        return self.channels, self.programmes

    def _handle_channel(self, elem: ET.Element) -> None:
        tvg_id = (elem.get('id') or '').strip()
        if not tvg_id:
            return
        names = [(n.text or '').strip() for n in elem.findall('display-name') if (n.text or '').strip()]
        icon_el = elem.find('icon')
        icon = (icon_el.get('src') or '').strip() if icon_el is not None else ''
        self.channels.append(
            {
                'tvg_id': tvg_id,
                'display_name': names[0] if names else tvg_id,
                'aliases': '|'.join(names),
                'icon': icon,
            }
        )

    def _handle_programme(self, elem: ET.Element) -> None:
        tvg_id = (elem.get('channel') or '').strip()
        if not tvg_id:
            return
        start = parse_xmltv_time(elem.get('start') or '', self.default_offset_minutes)
        stop = parse_xmltv_time(elem.get('stop') or '', self.default_offset_minutes)
        if not start:
            self.skipped_bad_time += 1
            return
        if not stop:
            stop = start + timedelta(minutes=30)
        if self.window_start and stop <= self.window_start:
            self.skipped_out_of_window += 1
            return
        if self.window_end and start >= self.window_end:
            self.skipped_out_of_window += 1
            return

        def _text(tag: str) -> str:
            node = elem.find(tag)
            return (node.text or '').strip() if node is not None and node.text else ''

        icon_el = elem.find('icon')
        self.programmes.append(
            {
                'tvg_id': tvg_id,
                'start_utc': to_utc_str(start),
                'stop_utc': to_utc_str(stop),
                'title': _text('title')[:300],
                'sub_title': _text('sub-title')[:300],
                'description': _text('desc')[:2000],
                'category': _text('category')[:100],
                'episode': _text('episode-num')[:100],
                'icon': (icon_el.get('src') or '').strip() if icon_el is not None else '',
            }
        )


def with_suppress_close(stream: Any) -> None:
    """安静关闭流"""
    try:
        stream.close()
    except Exception as e:  # pragma: no cover
        logger.debug(f'关闭 EPG 流失败: {e}')


# ══════════════════════════════════════════════════════════
# 下载
# ══════════════════════════════════════════════════════════


class EPGFetcher:
    """EPG 下载器：复用 Network 段的代理 / IPv6 / 超时配置"""

    def __init__(self, network_config: dict | None = None, timeout: int = 60):
        self.network_config = network_config or {}
        self.timeout = max(5, int(timeout))

    def _build_connector(self):
        import aiohttp

        family = socket.AF_UNSPEC if self.network_config.get('ipv6_enabled') else socket.AF_INET
        if self.network_config.get('proxy_enabled'):
            proxy_type = str(self.network_config.get('proxy_type', 'http')).lower()
            host = self.network_config.get('proxy_host', '')
            port = self.network_config.get('proxy_port', 0)
            user = self.network_config.get('proxy_username', '')
            pwd = self.network_config.get('proxy_password', '')
            if proxy_type in ('socks5', 'socks5h') and host and port:
                try:
                    import aiohttp_socks

                    auth = f'{user}:{pwd}@' if user and pwd else ''
                    return aiohttp_socks.ProxyConnector.from_url(
                        f'{proxy_type}://{auth}{host}:{port}', family=family, verify_ssl=False, limit=10
                    )
                except Exception as e:
                    logger.warning(f'EPG SOCKS 代理连接器创建失败，改直连: {e}')
        return aiohttp.TCPConnector(family=family, verify_ssl=False, limit=10)

    def _http_proxy(self) -> str | None:
        """HTTP(S) 代理地址（aiohttp 需在请求参数里传）"""
        if not self.network_config.get('proxy_enabled'):
            return None
        proxy_type = str(self.network_config.get('proxy_type', 'http')).lower()
        if proxy_type in ('socks5', 'socks5h'):
            return None
        host = self.network_config.get('proxy_host', '')
        port = self.network_config.get('proxy_port', 0)
        if not host or not port:
            return None
        user = self.network_config.get('proxy_username', '')
        pwd = self.network_config.get('proxy_password', '')
        auth = f'{user}:{pwd}@' if user and pwd else ''
        return f'http://{auth}{host}:{port}'

    async def fetch_to_file(self, url: str, dest_dir: str | None = None) -> str:
        """下载 EPG 到临时文件，返回本地路径。支持 file:// 与本地绝对路径。

        Raises:
            ValueError: URL 非法
            RuntimeError: 下载失败
        """
        url = (url or '').strip()
        if not url:
            raise ValueError('EPG 源地址为空')

        # ── 本地文件校验（I3修复）：仅允许白名单目录内的文件，杜绝任意文件读取 ──
        # 离线/内网部署可把 EPG 文件放入允许目录；协议仍以 http(s) 为主。
        local_path = url[7:] if url.lower().startswith('file://') else url
        is_file_scheme = url.lower().startswith('file://')
        # 允许的本地目录白名单（应用运行时数据目录，相对当前工作目录解析）
        candidate_roots = [
            './config/sources',
            './config/online',
            './www/output',
            './data',
            './web/data',
        ]
        allowed_roots = []
        for _cand in candidate_roots:
            _abs = os.path.realpath(os.path.abspath(_cand))
            if os.path.isdir(_abs):
                allowed_roots.append(_abs)
        allowed_roots = list(set(allowed_roots))

        # 任意 scheme 裸路径或 file:// 均按“本地文件”处理前，先判白名单
        looks_local = is_file_scheme or not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url)
        if looks_local:
            abs_local = os.path.realpath(os.path.abspath(local_path))
            in_allowed = any(
                abs_local == root or abs_local.startswith(root + os.sep) for root in allowed_roots
            )
            if not in_allowed:
                raise ValueError(
                    'EPG 本地文件不在允许目录内（已拒绝任意文件读取）：仅允许 config/sources、config/online、www/output、data 等应用数据目录，请改用 http(s) 源'
                )
            if not os.path.isfile(abs_local):
                raise RuntimeError(f'本地 EPG 文件不存在: {abs_local}')
            return abs_local

        from app.security import is_static_safe

        ok, reason = _static_safe(is_static_safe, url)
        if not ok:
            raise ValueError(f'EPG 源地址被安全门禁拒绝: {reason}')

        import aiohttp

        dest_dir = dest_dir or tempfile.gettempdir()
        os.makedirs(dest_dir, exist_ok=True)
        suffix = '.gz' if url.lower().endswith('.gz') else '.xml'
        fd, dest = tempfile.mkstemp(prefix='epg_', suffix=suffix, dir=dest_dir)
        os.close(fd)

        timeout_cfg = aiohttp.ClientTimeout(total=self.timeout, connect=min(15, self.timeout))
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; LiveSourceManager/1.0 EPG)'}
        connector = self._build_connector()
        proxy = self._http_proxy()
        try:
            async with (
                aiohttp.ClientSession(connector=connector, timeout=timeout_cfg) as session,
                session.get(url, headers=headers, proxy=proxy, allow_redirects=True) as resp,
            ):
                if resp.status != 200:
                    raise RuntimeError(f'HTTP {resp.status}')
                total = 0
                with open(dest, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        f.write(chunk)
                        total += len(chunk)
                    if total == 0:
                        raise RuntimeError('下载内容为空')
            return dest
        except Exception as e:
            with_suppress_unlink(dest)
            raise RuntimeError(f'下载失败: {e}') from e


def _static_safe(checker, url: str) -> tuple[bool, str]:
    """兼容 is_static_safe 返回 bool 或 (bool, reason) 两种形态"""
    try:
        result = checker(url)
    except Exception as e:
        return False, str(e)
    if isinstance(result, tuple):
        return bool(result[0]), str(result[1]) if len(result) > 1 else ''
    return bool(result), '' if result else '不在允许的协议白名单或触发 SSRF 检查'


def with_suppress_unlink(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception as e:  # pragma: no cover
        logger.debug(f'清理临时 EPG 文件失败 {path}: {e}')


# ══════════════════════════════════════════════════════════
# 管理器
# ══════════════════════════════════════════════════════════


class EPGManager:
    """EPG 抓取 / 入库 / 对齐 / 导出 的统一入口"""

    def __init__(self, config=None):
        if config is None:
            from app.config import Config

            config = Config()
        self.config = config
        self.epg_config = config.get_epg_config()
        self.network_config = config.get_network_config()
        self.tz_offset = tz_offset_minutes(self.epg_config.get('timezone', 'Asia/Shanghai'))

    # ── 持久化懒加载（与 Config 一致的方式，避免 L2 硬依赖 web） ──
    @staticmethod
    def _models():
        from web import models

        return models

    def time_window(self) -> tuple[datetime, datetime]:
        """当前有效节目时间窗（UTC）"""
        now = datetime.now(UTC)
        start = now - timedelta(hours=self.epg_config.get('past_hours', 6))
        end = now + timedelta(days=self.epg_config.get('keep_days', 7))
        return start, end

    # ── 抓取 ──────────────────────────────────────────────
    async def refresh_source(self, source: dict) -> dict:
        """抓取并入库单个 EPG 源，返回结果摘要"""
        models = self._models()
        source_id = int(source['id'])
        url = source['url']
        name = source.get('name') or url
        started = time.time()
        tmp_path = ''
        is_temp = False

        try:
            fetcher = EPGFetcher(self.network_config, self.epg_config.get('fetch_timeout', 60))
            tmp_path = await fetcher.fetch_to_file(url)
            is_temp = os.path.basename(tmp_path).startswith('epg_')

            win_start, win_end = self.time_window()
            loop = asyncio.get_running_loop()
            channels, programmes = await loop.run_in_executor(
                None,
                lambda: XMLTVParser(self.tz_offset, win_start, win_end).parse_file(tmp_path),
            )
            if not channels and not programmes:
                raise RuntimeError('解析结果为空（文件可能不是有效的 XMLTV）')

            ch_n, pg_n = await loop.run_in_executor(None, models.replace_epg_data, source_id, channels, programmes)
            cost = int((time.time() - started) * 1000)
            models.mark_epg_source_result(source_id, 'ok', '', ch_n, pg_n, cost)
            logger.info(f'EPG 源刷新成功 [{name}]: 频道 {ch_n} / 节目 {pg_n} / 耗时 {cost}ms')
            return {
                'source_id': source_id,
                'name': name,
                'ok': True,
                'channels': ch_n,
                'programmes': pg_n,
                'duration_ms': cost,
            }
        except Exception as e:
            cost = int((time.time() - started) * 1000)
            msg = str(e)
            models.mark_epg_source_result(source_id, 'error', msg, 0, 0, cost)
            logger.warning(f'EPG 源刷新失败 [{name}]: {msg}')
            return {'source_id': source_id, 'name': name, 'ok': False, 'error': msg, 'duration_ms': cost}
        finally:
            if is_temp:
                with_suppress_unlink(tmp_path)

    async def refresh_all(self, source_ids: list[int] | None = None) -> dict:
        """并发刷新全部（或指定）启用中的 EPG 源"""
        models = self._models()
        sources = models.list_epg_sources(enabled_only=source_ids is None)
        if source_ids:
            wanted = set(source_ids)
            sources = [s for s in sources if int(s['id']) in wanted]
        if not sources:
            return {'total': 0, 'ok': 0, 'failed': 0, 'results': [], 'message': '没有可刷新的 EPG 源'}

        sem = asyncio.Semaphore(3)

        async def _run(src):
            async with sem:
                return await self.refresh_source(src)

        results = await asyncio.gather(*[_run(s) for s in sources], return_exceptions=True)
        normalized = []
        for src, r in zip(sources, results, strict=False):
            if isinstance(r, BaseException):
                normalized.append({'source_id': src['id'], 'name': src.get('name'), 'ok': False, 'error': str(r)})
            else:
                normalized.append(r)

        matched = await asyncio.get_running_loop().run_in_executor(None, self.match_channels)
        removed = await asyncio.get_running_loop().run_in_executor(None, self.cleanup_expired)
        ok = sum(1 for r in normalized if r.get('ok'))
        return {
            'total': len(normalized),
            'ok': ok,
            'failed': len(normalized) - ok,
            'matched_channels': matched,
            'cleaned_programmes': removed,
            'results': normalized,
        }

    # ── 频道对齐 ──────────────────────────────────────────
    def match_channels(self, local_channels: list[str] | None = None) -> int:
        """把 EPG 频道对齐到本地频道名，并回写 channel_name_mapping.tvg_id/tvg_logo。

        Args:
            local_channels: 本地频道名列表；为空时自动从 stream_source_categories 取

        Returns:
            成功对齐的频道数
        """
        models = self._models()
        if local_channels is None:
            local_channels = self._load_local_channel_names()
        if not local_channels:
            logger.info('EPG 频道对齐跳过：本地暂无频道名')
            return 0

        # 本地频道：归一化名 → 原始名
        local_index: dict[str, str] = {}
        for ch in local_channels:
            key = normalize_channel_name(ch)
            if key and key not in local_index:
                local_index[key] = ch

        epg_channels, _ = models.list_epg_channels(limit=100000)
        pairs: list[tuple[int, str, str]] = []
        tvg_writes: list[tuple[str, str, str]] = []
        for ec in epg_channels:
            candidates = [ec.get('display_name', ''), ec.get('tvg_id', '')]
            hit = ''
            for cand in candidates:
                key = normalize_channel_name(cand)
                if key and key in local_index:
                    hit = local_index[key]
                    break
            if not hit:
                continue
            if ec.get('matched_channel') != hit:
                pairs.append((int(ec['source_id']), ec['tvg_id'], hit))
            tvg_writes.append((hit, ec['tvg_id'], ec.get('icon', '')))

        if pairs:
            models.bulk_set_epg_channel_match(pairs)
        for channel_name, tvg_id, logo in tvg_writes:
            models.set_channel_tvg_info(channel_name, tvg_id, logo)
        logger.info(f'EPG 频道对齐完成：命中 {len(tvg_writes)} 个（新增/变更 {len(pairs)} 个）')
        return len(tvg_writes)

    @staticmethod
    def _load_local_channel_names() -> list[str]:
        """从已分类的源表取本地频道名（去重）"""
        try:
            models = EPGManager._models()
            conn = models.get_conn()
            rows = conn.execute(
                "SELECT DISTINCT channel_name FROM stream_source_categories WHERE channel_name != ''"
            ).fetchall()
            names = [r[0] for r in rows]
            rows = conn.execute('SELECT channel_name FROM channel_name_mapping').fetchall()
            names.extend(r[0] for r in rows)
            conn.close()
            return list(dict.fromkeys(n for n in names if n))
        except Exception as e:
            logger.warning(f'读取本地频道名失败: {e}')
            return []

    # ── 清理 ──────────────────────────────────────────────
    def cleanup_expired(self) -> int:
        """清理过期节目（早于 now - past_hours）"""
        models = self._models()
        cutoff = datetime.now(UTC) - timedelta(hours=self.epg_config.get('past_hours', 6))
        return models.cleanup_epg_programmes(to_utc_str(cutoff))

    # ── 导出合并 XMLTV ────────────────────────────────────
    def generate_xmltv(self, output_path: str | None = None) -> dict:
        """从库导出合并后的 XMLTV 文件（按输出文件名后缀决定是否 gzip）。

        频道 id 优先使用对齐后的本地频道名映射的 tvg_id，保证与 M3U 中的 tvg-id 一致。
        """
        models = self._models()
        if not output_path:
            out_params = self.config.get_output_params()
            output_path = os.path.join(out_params['output_dir'], self.epg_config['output_filename'])
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        win_start, win_end = self.time_window()
        start_s, end_s = to_utc_str(win_start), to_utc_str(win_end)

        epg_channels, _ = models.list_epg_channels(limit=100000)
        if not epg_channels:
            return {'ok': False, 'message': 'EPG 库为空，请先抓取节目单', 'path': output_path}

        # 同一 tvg_id 可能来自多个源，按源优先级取第一个（list_epg_channels 已按名称排序，
        # 这里用源优先级字典再做一次去重）
        priority = {int(s['id']): int(s.get('priority', 100)) for s in models.list_epg_sources()}
        best: dict[str, dict] = {}
        for ec in epg_channels:
            tvg_id = ec.get('tvg_id') or ''
            if not tvg_id:
                continue
            p = priority.get(int(ec['source_id']), 999)
            if tvg_id not in best or p < priority.get(int(best[tvg_id]['source_id']), 999):
                best[tvg_id] = ec

        programmes = models.query_epg_programmes(list(best.keys()), start_s, end_s)

        is_gz = output_path.lower().endswith('.gz')
        opener = gzip.open if is_gz else open
        written = 0
        with opener(output_path, 'wt', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
            f.write(
                '<tv generator-info-name="LiveSourceManager" '
                f'generator-info-url="https://github.com/" date="{datetime.now().strftime("%Y%m%d%H%M%S")}">\n'
            )
            for tvg_id, ec in best.items():
                f.write(f'  <channel id="{xml_escape(tvg_id)}">\n')
                display = ec.get('matched_channel') or ec.get('display_name') or tvg_id
                f.write(f'    <display-name>{xml_escape(display)}</display-name>\n')
                if display != (ec.get('display_name') or ''):
                    f.write(f'    <display-name>{xml_escape(ec.get("display_name") or "")}</display-name>\n')
                if ec.get('icon'):
                    f.write(f'    <icon src="{xml_escape(ec["icon"])}" />\n')
                f.write('  </channel>\n')
            for p in programmes:
                start = utc_str_to_xmltv(p['start_utc'], self.tz_offset)
                stop = utc_str_to_xmltv(p['stop_utc'], self.tz_offset)
                if not start or not stop:
                    continue
                f.write(f'  <programme start="{start}" stop="{stop}" channel="{xml_escape(p["tvg_id"])}">\n')
                f.write(f'    <title lang="zh">{xml_escape(p.get("title") or "")}</title>\n')
                if p.get('sub_title'):
                    f.write(f'    <sub-title lang="zh">{xml_escape(p["sub_title"])}</sub-title>\n')
                if p.get('description'):
                    f.write(f'    <desc lang="zh">{xml_escape(p["description"])}</desc>\n')
                if p.get('category'):
                    f.write(f'    <category lang="zh">{xml_escape(p["category"])}</category>\n')
                if p.get('episode'):
                    f.write(f'    <episode-num system="onscreen">{xml_escape(p["episode"])}</episode-num>\n')
                f.write('  </programme>\n')
                written += 1
            f.write('</tv>\n')

        size = os.path.getsize(output_path)
        logger.info(f'EPG 导出完成: {output_path} (频道 {len(best)} / 节目 {written} / {size / 1024:.1f}KB)')
        return {
            'ok': True,
            'path': output_path,
            'channels': len(best),
            'programmes': written,
            'size': size,
            'gzip': is_gz,
        }

    # ── 供页面使用的查询 ──────────────────────────────────
    def get_grid_data(self, hours: int = 12, keyword: str = '', limit: int = 80) -> dict:
        """节目单网格数据：返回频道列表 + 时间窗内节目，时间已转本地时区 ISO 串"""
        models = self._models()
        now = datetime.now(UTC)
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=max(1, hours))
        channels, total = models.list_epg_channels(keyword=keyword, limit=limit)
        if not channels:
            return {
                'channels': [],
                'total': 0,
                'start': self._to_local_iso(start),
                'end': self._to_local_iso(end),
                'now': self._to_local_iso(now),
                'tz_offset_minutes': self.tz_offset,
            }

        tvg_ids = [c['tvg_id'] for c in channels if c.get('tvg_id')]
        rows = models.query_epg_programmes(tvg_ids, to_utc_str(start), to_utc_str(end))
        by_channel: dict[str, list[dict]] = {}
        for r in rows:
            by_channel.setdefault(r['tvg_id'], []).append(
                {
                    'title': r.get('title') or '未知节目',
                    'sub_title': r.get('sub_title') or '',
                    'desc': r.get('description') or '',
                    'category': r.get('category') or '',
                    'start': self._to_local_iso(datetime.strptime(r['start_utc'], UTC_FMT).replace(tzinfo=UTC)),
                    'stop': self._to_local_iso(datetime.strptime(r['stop_utc'], UTC_FMT).replace(tzinfo=UTC)),
                }
            )
        for items in by_channel.values():
            items.sort(key=lambda x: x['start'])

        result_channels = []
        for c in channels:
            result_channels.append(
                {
                    'tvg_id': c['tvg_id'],
                    'name': c.get('matched_channel') or c.get('display_name') or c['tvg_id'],
                    'raw_name': c.get('display_name') or '',
                    'icon': c.get('icon') or '',
                    'matched': bool(c.get('matched_channel')),
                    'source_name': c.get('source_name') or '',
                    'programmes': by_channel.get(c['tvg_id'], []),
                }
            )
        return {
            'channels': result_channels,
            'total': total,
            'start': self._to_local_iso(start),
            'end': self._to_local_iso(end),
            'now': self._to_local_iso(now),
            'tz_offset_minutes': self.tz_offset,
        }

    def get_now_next(self, tvg_ids: list[str]) -> dict[str, dict]:
        """取每个频道的「正在播 / 下一档」"""
        models = self._models()
        now = datetime.now(UTC)
        rows = models.query_epg_programmes(
            tvg_ids, to_utc_str(now - timedelta(hours=1)), to_utc_str(now + timedelta(hours=8))
        )
        now_s = to_utc_str(now)
        out: dict[str, dict] = {}
        for r in rows:
            slot = out.setdefault(r['tvg_id'], {'now': None, 'next': None})
            if r['start_utc'] <= now_s < r['stop_utc']:
                slot['now'] = {'title': r.get('title'), 'start': r['start_utc'], 'stop': r['stop_utc']}
            elif r['start_utc'] > now_s and slot['next'] is None:
                slot['next'] = {'title': r.get('title'), 'start': r['start_utc'], 'stop': r['stop_utc']}
        return out

    def _to_local_iso(self, dt: datetime) -> str:
        return dt.astimezone(timezone(timedelta(minutes=self.tz_offset))).isoformat(timespec='seconds')

    # ── EPG 外链地址 ──────────────────────────────────────
    def get_epg_url(self, request_host: str = '') -> str:
        """生成供 M3U url-tvg 使用的 EPG 地址（先用 http，符合大多数播放器兼容性）"""
        base = (self.epg_config.get('web_base_url') or '').strip().rstrip('/')
        if base:
            return f'{base}/{self.epg_config["output_filename"]}'
        http_cfg = self.config.get_http_server_config()
        host = request_host or http_cfg.get('host') or '127.0.0.1'
        if host in ('0.0.0.0', '::', ''):
            host = _guess_lan_ip()
        port = http_cfg.get('fileshare_port', 12345)
        return f'http://{host}:{port}/{self.epg_config["output_filename"]}'


def _guess_lan_ip() -> str:
    """尽力猜测本机局域网 IP（用于生成可被播放器访问的 EPG 外链）"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(('223.5.5.5', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        if s:
            with_suppress_close(s)


__all__ = [
    'EPGFetcher',
    'EPGManager',
    'XMLTVParser',
    'normalize_channel_name',
    'parse_xmltv_time',
    'to_utc_str',
    'tz_offset_minutes',
    'utc_str_to_xmltv',
]
