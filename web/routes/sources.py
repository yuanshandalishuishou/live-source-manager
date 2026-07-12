#!/usr/bin/env python3
"""源管理 API — /api/sources/*, /api/source-files/*, /api/sources/{id}/categories"""

import asyncio
import hashlib
import json
import os
import re
import time

from app.utils import force_remove
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from web import models
from web.core import (
    PROJECT_ROOT,
    _load_source_manager,
    get_current_user,
    get_file_channel_counts,
    get_source_by_id,
    logger,
    parse_all_files_cached,
    require_admin,
    reset_source_manager_cache,
)

router = APIRouter()

# 后台任务集合（防止 asyncio.create_task 被垃圾回收）
_background_tasks: set = set()

# GitHub 源下载状态缓存：{github_entry: {status, discovered, matched, channels, checked_at}}
_github_status_cache: dict = {}

# D-3 修复：GitHub 已发现文件 URL 列表缓存（{entry: (timestamp, discovered_list)}），TTL 5 分钟
# 避免每次展开 GitHub 源都重复打 GitHub API（suxuang/myIPTV 实测可叠加到 60s+）
_github_discover_cache: dict[str, tuple] = {}
_GITHUB_DISCOVER_TTL: float = 300.0  # 5 分钟


async def _discover_github_cached(sm, entry: str, methods: dict):
    """带缓存与总超时的 GitHub 源发现（D-3 修复）。

    - 命中缓存（5 分钟内）→ 直接返回已发现 URL 列表，不打 GitHub API。
    - 未命中 → 加总超时（20s）调用 SourceManager 的发现逻辑，失败快速返回空列表。
    """
    now = time.time()
    cached = _github_discover_cache.get(entry)
    if cached and (now - cached[0]) < _GITHUB_DISCOVER_TTL:
        return cached[1]
    try:
        discovered = await asyncio.wait_for(
            sm._discover_github_source_urls([entry], methods=methods),
            timeout=20,
        )
    except TimeoutError:
        logger.warning(f'GitHub 源发现超时（20s）: {entry}，返回空结果')
        return []
    except Exception as e:
        logger.warning(f'GitHub 源发现失败: {entry}: {e}')
        return []
    _github_discover_cache[entry] = (now, discovered)
    return discovered


# ══════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════


def _read_online_urls_from_db() -> list:
    """从数据库读取 Sources.online_urls 配置，返回 URL 列表"""
    try:
        cfg = models.get_all_config()
        raw = cfg.get('Sources', {}).get('online_urls', '')
        return [u.strip() for u in raw.split('\n') if u.strip()]
    except Exception:
        return []


def _write_online_urls_to_db(urls: list):
    """将 URL 列表写入数据库 Sources.online_urls 配置"""
    models.set_app_config_raw('Sources.online_urls', '\n'.join(urls))


def _url_to_filename(url: str) -> str | None:
    """将 URL 映射到 config/online/ 下的文件名（与 SourceManager.get_filename_from_url 逻辑一致）"""
    clean_url = url.split('?')[0]
    filename = clean_url.split('/')[-1]
    if not filename or '.' not in filename:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        filename = f'source_{url_hash}.txt'
    return re.sub(r'[^\w\-_.]', '_', filename)


def _remove_file_from_online_dir(filename: str) -> bool:
    """删除 config/online/ 下的指定文件，返回是否成功"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    file_path = os.path.join(project_root, 'config', 'online', filename)
    if os.path.isfile(file_path):
        try:
            force_remove(file_path)
            logger.info(f'已删除源文件: {file_path}')
            return True
        except OSError as e:
            logger.warning(f'删除源文件失败: {file_path}: {e}')
            return False
    return False


def _read_github_sources_from_db() -> list:
    """从数据库读取 Sources.github_sources 配置，返回 GitHub 仓库条目列表"""
    try:
        cfg = models.get_all_config()
        raw = cfg.get('Sources', {}).get('github_sources', '')
        return [s.strip() for s in raw.split('\n') if s.strip()]
    except Exception:
        return []


def _write_github_sources_to_db(sources: list):
    """将 GitHub 仓库条目列表写入数据库 Sources.github_sources 配置"""
    models.set_app_config_raw('Sources.github_sources', '\n'.join(sources))


# ── GitHub 源下载方式配置 ─────────────────────

GITHUB_DOWNLOAD_METHODS = {
    'raw': {'label': 'raw.githubusercontent.com', 'desc': '直接通过 raw.githubusercontent.com 下载（默认）'},
    'api': {'label': 'GitHub API', 'desc': '通过 api.github.com 内容接口下载（有速率限制）'},
    'proxy': {'label': 'SOCKS/HTTP 代理', 'desc': '通过 Network 配置的代理服务器下载'},
    'mirror': {'label': '代理网站', 'desc': '通过公开镜像站（如 ghproxy.com）中转下载'},
}
GITHUB_DOWNLOAD_METHOD_DEFAULT = 'raw'

DEFAULT_GITHUB_MIRROR = 'https://ghproxy.com/'


def _read_github_source_settings() -> dict:
    """读取 Sources.github_source_settings 配置，返回 {entry: method} 字典"""
    try:
        cfg = models.get_all_config()
        raw = cfg.get('Sources', {}).get('github_source_settings', '{}')
        settings = json.loads(raw) if raw else {}
        return settings if isinstance(settings, dict) else {}
    except Exception:
        return {}


def _write_github_source_settings(settings: dict):
    """写入 Sources.github_source_settings 配置"""
    models.set_app_config_raw('Sources.github_source_settings', json.dumps(settings, ensure_ascii=False))


def _get_github_download_method(entry: str) -> str:
    """获取某个 GitHub 条目的下载方式，未配置则返回默认值"""
    settings = _read_github_source_settings()
    method = settings.get(entry, GITHUB_DOWNLOAD_METHOD_DEFAULT)
    if method not in GITHUB_DOWNLOAD_METHODS:
        method = GITHUB_DOWNLOAD_METHOD_DEFAULT
    return method


def _set_github_download_method(entry: str, method: str):
    """设置某个 GitHub 条目的下载方式"""
    if method not in GITHUB_DOWNLOAD_METHODS:
        raise ValueError(f'无效的下载方式: {method}，可选: {list(GITHUB_DOWNLOAD_METHODS.keys())}')
    settings = _read_github_source_settings()
    settings[entry] = method
    _write_github_source_settings(settings)


# ── 源文件 UA 设置 (per-source-file User-Agent) ──────────


def _read_source_file_ua_settings() -> dict:
    """读取 Sources.source_file_ua_settings 配置，返回 {type:value: {enabled, ua_value, ua_position}} 字典"""
    try:
        cfg = models.get_all_config()
        raw = cfg.get('Sources', {}).get('source_file_ua_settings', '{}')
        settings = json.loads(raw) if raw else {}
        return settings if isinstance(settings, dict) else {}
    except Exception:
        return {}


def _write_source_file_ua_settings(settings: dict):
    """写入 Sources.source_file_ua_settings 配置"""
    models.set_app_config_raw('Sources.source_file_ua_settings', json.dumps(settings, ensure_ascii=False))


def _get_source_file_ua(src_type: str, value: str) -> dict:
    """获取某个源文件的 UA 设置，未配置返回空 dict"""
    settings = _read_source_file_ua_settings()
    key = f'{src_type}:{value}'
    return settings.get(key, {})


def _set_source_file_ua(src_type: str, value: str, ua_settings: dict):
    """设置某个源文件的 UA 设置"""
    settings = _read_source_file_ua_settings()
    key = f'{src_type}:{value}'
    settings[key] = ua_settings
    _write_source_file_ua_settings(settings)


def _del_source_file_ua(src_type: str, value: str):
    """删除某个源文件的 UA 设置"""
    settings = _read_source_file_ua_settings()
    key = f'{src_type}:{value}'
    if key in settings:
        del settings[key]
        _write_source_file_ua_settings(settings)


def _read_channel_ua_overrides() -> dict:
    """读取 Sources.channel_ua_overrides 配置，返回 {url: {ua_value, ua_position}} 字典"""
    try:
        cfg = models.get_all_config()
        raw = cfg.get('Sources', {}).get('channel_ua_overrides', '{}')
        overrides = json.loads(raw) if raw else {}
        return overrides if isinstance(overrides, dict) else {}
    except Exception:
        return {}


def _write_channel_ua_overrides(overrides: dict):
    """写入 Sources.channel_ua_overrides 配置"""
    models.set_app_config_raw('Sources.channel_ua_overrides', json.dumps(overrides, ensure_ascii=False))


def _apply_channel_ua_overrides(channels: list) -> list:
    """对频道列表应用频道级 UA 覆盖"""
    overrides = _read_channel_ua_overrides()
    if not overrides:
        return channels
    for ch in channels:
        url = ch.get('url', '')
        if url in overrides:
            ov = overrides[url]
            if ov.get('ua_value'):
                ch['user_agent'] = ov['ua_value']
                ch['ua_position'] = ov.get('ua_position', 'extinf')
                ch['ua_override'] = True
            else:
                ch['ua_override'] = False
        else:
            ch['ua_override'] = False
    return channels


def _get_github_mirror_url() -> str:
    """获取代理网站（镜像站）URL"""
    try:
        cfg = models.get_all_config()
        return cfg.get('Network', {}).get('github_mirror', DEFAULT_GITHUB_MIRROR)
    except Exception:
        return DEFAULT_GITHUB_MIRROR


def _read_local_dirs_from_db() -> list:
    """从数据库读取 Sources.local_dirs 配置，返回本地目录/文件路径列表"""
    try:
        cfg = models.get_all_config()
        raw = cfg.get('Sources', {}).get('local_dirs', './config/sources')
        if isinstance(raw, str):
            return [d.strip() for d in raw.split(',') if d.strip()]
        return raw if isinstance(raw, list) else []
    except Exception:
        return ['./config/sources']


def _write_local_dirs_to_db(dirs: list):
    """将本地目录/文件路径列表写入数据库 Sources.local_dirs 配置"""
    models.set_app_config_raw('Sources.local_dirs', ','.join(dirs))


def _make_source_file_id(src_type: str, value: str) -> str:
    """生成源文件稳定 ID（type:value 的 md5 前 12 位）"""
    return hashlib.md5(f'{src_type}:{value}'.encode()).hexdigest()[:12]


def _get_online_file_path(url: str) -> str:
    """根据在线 URL 获取 config/online/ 下对应的文件路径"""
    filename = _url_to_filename(url)
    return os.path.join(PROJECT_ROOT, 'config', 'online', filename)


def _count_file_channels(file_path: str) -> int:
    """快速统计文件中的频道数量（不解析完整信息，只计数）"""
    try:
        if not os.path.isfile(file_path):
            return 0
        sm = _load_source_manager()
        if not sm:
            return 0
        content = sm._read_file_with_encoding(file_path)
        lines = content.splitlines()
        count = 0
        prev_was_extinf = False
        for line in lines:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                count += 1
                prev_was_extinf = True
            elif line and not line.startswith('#'):
                if not prev_was_extinf:
                    count += 1
                prev_was_extinf = False
            else:
                prev_was_extinf = False
        return count
    except Exception:
        return 0


def _resolve_local_path(path: str) -> str:
    """将相对路径解析为绝对路径（相对于项目根目录）"""
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


def _find_source_file_by_id(file_id: str):
    """根据 file_id 定位源文件的 type 和 value，返回 (src_type, value) 或 None"""
    for url in _read_online_urls_from_db():
        if _make_source_file_id('online', url) == file_id:
            return ('online', url)
    for entry in _read_github_sources_from_db():
        if _make_source_file_id('github', entry) == file_id:
            return ('github', entry)
    for path in _read_local_dirs_from_db():
        if _make_source_file_id('local', path) == file_id:
            return ('local', path)
    return None


def _enrich_channels_with_mappings(channels: list) -> list:
    """为频道列表附加已有的手动分类映射"""
    if not channels:
        return channels
    # 收集所有频道名，批量查询
    names = list({ch.get('name', '') for ch in channels if ch.get('name')})
    if not names:
        return channels
    # 批量查询 channel_name_mapping
    mappings = {}
    try:
        conn = models.get_conn()
        placeholders = ','.join('?' for _ in names)
        rows = conn.execute(
            f'SELECT channel_name, content, region, language, quality, media_type, genre FROM channel_name_mapping WHERE channel_name IN ({placeholders})',
            names,
        ).fetchall()
        conn.close()
        for row in rows:
            mappings[row['channel_name']] = dict(row)
    except Exception:
        pass

    for ch in channels:
        ch_name = ch.get('name', '')
        if ch_name in mappings:
            ch['existing_mapping'] = {k: v for k, v in mappings[ch_name].items() if k != 'channel_name'}
        else:
            ch['existing_mapping'] = None
    return channels


# ══════════════════════════════════════════════════
# 源管理 API
# ══════════════════════════════════════════════════


@router.get('/api/sources')
async def api_list_sources(
    current_user: dict = Depends(get_current_user),
    src_type: str = 'all',
    page: int = 1,
    size: int = 50,
    search: str = '',
):
    sm = _load_source_manager()
    if not sm:
        return {'sources': [], 'total': 0, 'page': page}
    # D-1 修复：重 IO 解析在 worker 线程执行，避免阻塞事件循环
    sources = await asyncio.to_thread(parse_all_files_cached, sm)

    # 类型筛选
    if src_type == 'online':
        sources = [s for s in sources if s.get('source_type') == 'online']
    elif src_type == 'local':
        sources = [s for s in sources if s.get('source_type') == 'local']

    # 搜索筛选
    if search:
        search_lower = search.lower()
        sources = [
            s
            for s in sources
            if search_lower in s.get('name', '').lower()
            or search_lower in s.get('url', '').lower()
            or search_lower in s.get('group', '').lower()
        ]

    total = len(sources)
    # 分页
    start = (page - 1) * size
    end = start + size
    page_sources = sources[start:end]

    # 添加 id 字段
    for s in page_sources:
        s['id'] = hashlib.md5(f'{s.get("name", "")}|{s.get("url", "")}'.encode()).hexdigest()[:12]

    return {'sources': page_sources, 'total': total, 'page': page, 'size': size}


@router.get('/api/sources/{source_id}')
async def api_get_source(source_id: str, current_user: dict = Depends(get_current_user)):
    source = get_source_by_id(source_id)
    if not source:
        raise HTTPException(status_code=404, detail='源不存在')
    return source


@router.post('/api/sources')
async def api_create_source(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    """添加在线源：下载 URL 文件到 config/online/ 并写入 online_urls 配置"""
    url = (data.get('url', '') or '').strip()
    source_name = (data.get('name', '') or '').strip()

    if not url:
        raise HTTPException(status_code=400, detail='URL 不能为空')
    if not source_name:
        source_name = url

    # 检查重复
    current_urls = _read_online_urls_from_db()
    if url in current_urls:
        return {'status': 'exists', 'name': source_name, 'url': url, 'message': '该 URL 已存在，如需要请先删除旧源'}

    # 追加到配置
    current_urls.append(url)
    _write_online_urls_to_db(current_urls)

    # 下载文件到 config/online/
    download_status = 'skipped'
    try:
        sm = _load_source_manager()
        if sm:
            filepath = await sm.download_with_retry(url)
            if filepath:
                download_status = 'downloaded'
                reset_source_manager_cache()  # 重置缓存让下次列表重新解析
                logger.info(f'源下载成功: {url} -> {filepath}')
            else:
                download_status = 'download_failed'
                logger.warning(f'源下载失败（所有策略均失败）: {url}')
    except Exception as e:
        logger.warning(f'源下载异常 {url}: {e}')
        download_status = f'download_error: {e}'

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='source_add',
        target=source_name,
        detail=json.dumps({'url': url, 'download': download_status}, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )

    msg = f'源 "{source_name}" 已添加'
    if download_status == 'downloaded':
        # 获取新解析到的源数量
        try:
            sm2 = _load_source_manager()
            if sm2:
                sources = await asyncio.to_thread(parse_all_files_cached, sm2)
                msg += f'，已解析 {len(sources)} 个频道'
        except Exception:
            pass

    return {'status': 'created', 'name': source_name, 'url': url, 'download': download_status, 'message': msg}


@router.put('/api/sources/{source_id}')
async def api_update_source(source_id: str, data: dict, request: Request, current_user: dict = Depends(require_admin)):
    """更新源：支持修改 URL（重新下载）或修改名称/分组"""
    source = get_source_by_id(source_id)
    if not source:
        raise HTTPException(status_code=404, detail='源不存在')

    old_url = source.get('url', '')
    new_url = (data.get('url', '') or '').strip()
    new_name = (data.get('name', '') or '').strip()

    changes = []
    # 如果 URL 变了，需要处理文件替换
    if new_url and new_url != old_url:
        # 从 online_urls 中移除旧 URL
        current_urls = _read_online_urls_from_db()
        if old_url in current_urls:
            current_urls.remove(old_url)
        current_urls.append(new_url)
        _write_online_urls_to_db(current_urls)

        # 删除旧文件
        old_filename = _url_to_filename(old_url)
        if old_filename:
            _remove_file_from_online_dir(old_filename)

        # 下载新文件
        try:
            sm = _load_source_manager()
            if sm:
                filepath = await sm.download_with_retry(new_url)
                if filepath:
                    changes.append('URL 已更新并重新下载')
        except Exception as e:
            logger.warning(f'更新源下载失败 {new_url}: {e}')
        reset_source_manager_cache()

    # 名称/分组等仅在前端记录（文件内字段由 m3u 文件本身定义）
    if new_name:
        changes.append(f'名称: {new_name}')

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='source_update',
        target=new_name or source.get('name', '') or source_id,
        detail=json.dumps({'old': old_url, 'new': new_url, 'changes': changes}, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'updated', 'changes': changes, 'message': '; '.join(changes) if changes else '无变更'}


@router.delete('/api/sources/{source_id}')
async def api_delete_source(source_id: str, request: Request, current_user: dict = Depends(require_admin)):
    """删除源：移除 config/online/ 中的对应文件并从 online_urls 配置中删除"""
    source = get_source_by_id(source_id)
    if not source:
        raise HTTPException(status_code=404, detail='源不存在')

    target_name = source.get('name', source_id)
    source_url = source.get('url', '')
    source_path = source.get('source_path', '')

    deleted_file = False
    removed_from_config = False

    # 1. 尝试删除对应的源文件
    if source_path:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        file_path = os.path.join(project_root, 'config', 'online', source_path)
        if os.path.isfile(file_path):
            try:
                force_remove(file_path)
                deleted_file = True
                logger.info(f'已删除源文件: {file_path}')
            except OSError as e:
                logger.warning(f'删除源文件失败（配置仍会移除）: {file_path}: {e}')
                deleted_file = False

    # 2. 如果上面没找到（source_path 可能是 base filename），尝试用 URL 推导文件名删除
    if not deleted_file and source_url:
        filename = _url_to_filename(source_url)
        if filename:
            deleted_file = _remove_file_from_online_dir(filename)

    # 3. 从 online_urls 配置中移除 URL
    current_urls = _read_online_urls_from_db()
    # 尝试多种匹配方式
    removed = False
    # 精确匹配
    if source_url in current_urls:
        current_urls.remove(source_url)
        removed = True
    # 模糊匹配（URL 可能带参数或有变体）
    if not removed:
        new_urls = []
        for u in current_urls:
            if u == source_url or source_url in u or u in source_url:
                removed = True
                continue
            new_urls.append(u)
        if removed:
            current_urls = new_urls
    if removed:
        _write_online_urls_to_db(current_urls)
        removed_from_config = True

    # 4. 重置缓存
    reset_source_manager_cache()

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='source_delete',
        target=target_name,
        detail=json.dumps(
            {'url': source_url, 'file_deleted': deleted_file, 'config_removed': removed_from_config}, ensure_ascii=False
        ),
        ip_address=request.client.host if request.client else '',
    )

    msg_parts = []
    if deleted_file:
        msg_parts.append('源文件已删除')
    if removed_from_config:
        msg_parts.append('配置已移除')
    return {
        'status': 'deleted',
        'name': target_name,
        'message': '; '.join(msg_parts) if msg_parts else '删除成功（仅移除了记录）',
    }


# ══════════════════════════════════════════════════
# 源文件级别管理 API（两步式：文件管理 → 频道展开）
# ══════════════════════════════════════════════════


@router.get('/api/source-files')
async def api_list_source_files(current_user: dict = Depends(get_current_user)):
    """列出所有源文件（文件级别，非频道级别）

    合并三种来源：online_urls（在线URL）、github_sources（GitHub仓库）、local_dirs（本地文件/目录）
    """
    # ── 从预构建缓存读取文件→频道数映射（零遍历）──
    file_channel_counts = get_file_channel_counts()

    files = []

    # 1. 在线 URL 源
    online_urls = _read_online_urls_from_db()
    for url in online_urls:
        file_id = _make_source_file_id('online', url)
        file_path = _get_online_file_path(url)
        file_exists = os.path.isfile(file_path)
        norm = os.path.normpath(os.path.abspath(file_path)) if file_exists else ''
        channel_count = file_channel_counts.get(norm, 0) if file_exists else 0
        file_size = os.path.getsize(file_path) if file_exists else 0
        files.append(
            {
                'id': file_id,
                'name': os.path.basename(file_path) or url,
                'type': 'online',
                'url_or_path': url,
                'file_status': 'downloaded' if file_exists else 'not_downloaded',
                'file_path': file_path,
                'file_size': file_size,
                'channel_count': channel_count,
                'ua_settings': _get_source_file_ua('online', url),
            }
        )

    # 2. GitHub 源
    github_sources = _read_github_sources_from_db()
    for entry in github_sources:
        file_id = _make_source_file_id('github', entry)
        download_method = _get_github_download_method(entry)
        method_info = GITHUB_DOWNLOAD_METHODS.get(
            download_method, GITHUB_DOWNLOAD_METHODS[GITHUB_DOWNLOAD_METHOD_DEFAULT]
        )

        # 从内存缓存读取下载状态（内存 → 数据库 → 展示"待展开"）
        cached = _github_status_cache.get(entry)
        if cached:
            file_status = cached.get('status', 'github_pending')
            file_size = cached.get('total_size', 0)
            channel_count = cached.get('channels', 0)
            discovered_count = cached.get('discovered', 0)
            matched_count = cached.get('matched', 0)
        else:
            # 内存无缓存 → 查数据库持久化记录
            db_records = models.get_github_download_cache(entry)
            if db_records:
                discovered_count = len(db_records)
                matched_count = sum(1 for r in db_records if r['file_size'] > 0)
                file_size = sum(r['file_size'] for r in db_records)
                if matched_count == 0:
                    file_status = 'not_downloaded'
                elif matched_count >= discovered_count:
                    file_status = 'downloaded'
                else:
                    file_status = 'partial'
                channel_count = 0  # DB 不存频道数，需展开才能获取
                # 回填内存缓存
                _github_status_cache[entry] = {
                    'status': file_status,
                    'discovered': discovered_count,
                    'matched': matched_count,
                    'channels': 0,
                    'total_size': file_size,
                }
            else:
                file_status = 'github_pending'
                file_size = 0
                channel_count = 0
                discovered_count = 0
                matched_count = 0

        files.append(
            {
                'id': file_id,
                'name': entry,
                'type': 'github',
                'url_or_path': entry,
                'file_status': file_status,
                'file_path': None,
                'file_size': file_size,
                'channel_count': channel_count,
                'download_method': download_method,
                'download_method_label': method_info['label'],
                'discovered_count': discovered_count,
                'matched_count': matched_count,
                'ua_settings': _get_source_file_ua('github', entry),
            }
        )

    # 3. 本地文件/目录
    local_dirs = _read_local_dirs_from_db()
    for path in local_dirs:
        file_id = _make_source_file_id('local', path)
        abs_path = _resolve_local_path(path)
        if os.path.isfile(abs_path):
            file_exists = True
            norm = os.path.normpath(os.path.abspath(abs_path))
            channel_count = file_channel_counts.get(norm, 0)
            file_size = os.path.getsize(abs_path)
        elif os.path.isdir(abs_path):
            file_exists = True
            file_size = 0
            channel_count = 0
            for f in os.listdir(abs_path):
                if f.endswith(('.m3u', '.m3u8', '.txt')):
                    fp = os.path.join(abs_path, f)
                    norm = os.path.normpath(os.path.abspath(fp))
                    file_size += os.path.getsize(fp)
                    channel_count += file_channel_counts.get(norm, 0)
        else:
            file_exists = False
            file_size = 0
            channel_count = 0
        files.append(
            {
                'id': file_id,
                'name': os.path.basename(path) or path,
                'type': 'local',
                'url_or_path': path,
                'file_status': 'local_exists' if file_exists else 'local_missing',
                'file_path': abs_path,
                'file_size': file_size,
                'channel_count': channel_count,
                'ua_settings': _get_source_file_ua('local', path),
            }
        )

    return {'files': files, 'total': len(files)}


@router.post('/api/source-files')
async def api_create_source_file(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    """添加源文件（支持 online/github/local 三种类型）

    请求体: {type: "online"|"github"|"local", value: "url_or_path", name: "optional"}
    """
    src_type = (data.get('type', '') or '').strip()
    value = (data.get('value', '') or '').strip()
    # name = (data.get('name', '') or '').strip()  # 可选备注名

    if not src_type or not value:
        raise HTTPException(status_code=400, detail='类型和地址不能为空')

    if src_type == 'online':
        # 检查重复
        current_urls = _read_online_urls_from_db()
        if value in current_urls:
            return {'status': 'exists', 'message': '该 URL 已存在'}

        # 添加到配置
        current_urls.append(value)
        _write_online_urls_to_db(current_urls)

        # 立即下载
        download_status = 'not_downloaded'
        try:
            sm = _load_source_manager()
            if sm:
                filepath = await sm.download_with_retry(value)
                if filepath:
                    download_status = 'downloaded'
                    reset_source_manager_cache()
                    logger.info(f'源文件下载成功: {value} -> {filepath}')
                else:
                    download_status = 'download_failed'
        except Exception as e:
            logger.warning(f'源下载失败 {value}: {e}')
            download_status = 'download_failed'

        models.add_audit_log(
            user_id=current_user['user_id'],
            username=current_user['username'],
            action='source_file_add',
            target=value,
            detail=json.dumps({'type': 'online', 'url': value, 'download': download_status}, ensure_ascii=False),
            ip_address=request.client.host if request.client else '',
        )
        msg = '在线源已添加'
        if download_status == 'downloaded':
            msg += '，文件已下载'
        elif download_status == 'download_failed':
            msg += '，但下载失败（可稍后重试采集）'
        return {'status': 'created', 'type': 'online', 'download': download_status, 'message': msg}

    elif src_type == 'github':
        # 检查重复
        current_github = _read_github_sources_from_db()
        if value in current_github:
            return {'status': 'exists', 'message': '该 GitHub 源已存在'}

        download_method = (data.get('download_method', '') or '').strip()
        if download_method and download_method not in GITHUB_DOWNLOAD_METHODS:
            raise HTTPException(
                status_code=400,
                detail=f'无效的下载方式: {download_method}，可选: {list(GITHUB_DOWNLOAD_METHODS.keys())}',
            )

        current_github.append(value)
        _write_github_sources_to_db(current_github)

        if download_method:
            _set_github_download_method(value, download_method)

        reset_source_manager_cache()

        method_label = GITHUB_DOWNLOAD_METHODS.get(
            download_method or GITHUB_DOWNLOAD_METHOD_DEFAULT, GITHUB_DOWNLOAD_METHODS[GITHUB_DOWNLOAD_METHOD_DEFAULT]
        )
        models.add_audit_log(
            user_id=current_user['user_id'],
            username=current_user['username'],
            action='source_file_add',
            target=value,
            detail=json.dumps(
                {
                    'type': 'github',
                    'entry': value,
                    'download_method': download_method or GITHUB_DOWNLOAD_METHOD_DEFAULT,
                },
                ensure_ascii=False,
            ),
            ip_address=request.client.host if request.client else '',
        )
        return {
            'status': 'created',
            'type': 'github',
            'download_method': download_method or GITHUB_DOWNLOAD_METHOD_DEFAULT,
            'message': f'GitHub 源已添加（下载方式: {method_label["label"]}），请点击"采集所有源"下载文件',
        }

    elif src_type == 'local':
        # 验证路径
        abs_path = _resolve_local_path(value)
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=400, detail=f'路径不存在: {value}')

        # 添加到配置
        current_dirs = _read_local_dirs_from_db()
        if value in current_dirs:
            return {'status': 'exists', 'message': '该本地路径已存在'}

        current_dirs.append(value)
        _write_local_dirs_to_db(current_dirs)
        reset_source_manager_cache()

        models.add_audit_log(
            user_id=current_user['user_id'],
            username=current_user['username'],
            action='source_file_add',
            target=value,
            detail=json.dumps({'type': 'local', 'path': value}, ensure_ascii=False),
            ip_address=request.client.host if request.client else '',
        )
        return {'status': 'created', 'type': 'local', 'message': '本地源已添加'}

    else:
        raise HTTPException(status_code=400, detail=f'不支持的类型: {src_type}')


@router.delete('/api/source-files/{file_id}')
async def api_delete_source_file(file_id: str, request: Request, current_user: dict = Depends(require_admin)):
    """删除源文件（根据 ID 定位，根据类型执行不同删除逻辑）"""
    # 1. 在线 URL 源
    online_urls = _read_online_urls_from_db()
    for url in online_urls:
        if _make_source_file_id('online', url) == file_id:
            file_path = _get_online_file_path(url)
            deleted_file = False
            if os.path.isfile(file_path):
                try:
                    force_remove(file_path)
                    deleted_file = True
                except OSError as e:
                    # 物理文件删除失败（如被占用）也不阻断配置更新，避免 500
                    logger.warning(f'删除源文件失败（配置仍会移除）: {file_path}: {e}')
                    deleted_file = False
            online_urls.remove(url)
            _write_online_urls_to_db(online_urls)
            reset_source_manager_cache()
            models.add_audit_log(
                user_id=current_user['user_id'],
                username=current_user['username'],
                action='source_file_delete',
                target=url,
                detail=json.dumps({'type': 'online', 'url': url, 'file_deleted': deleted_file}, ensure_ascii=False),
                ip_address=request.client.host if request.client else '',
            )
            msg = '在线源已删除'
            if deleted_file:
                msg += '，文件已清理'
            return {'status': 'deleted', 'message': msg}

    # 2. GitHub 源
    github_sources = _read_github_sources_from_db()
    for entry in github_sources:
        if _make_source_file_id('github', entry) == file_id:
            github_sources.remove(entry)
            _write_github_sources_to_db(github_sources)

            # 清理下载方式设置
            settings = _read_github_source_settings()
            if entry in settings:
                del settings[entry]
                _write_github_source_settings(settings)

            reset_source_manager_cache()
            models.add_audit_log(
                user_id=current_user['user_id'],
                username=current_user['username'],
                action='source_file_delete',
                target=entry,
                detail=json.dumps({'type': 'github', 'entry': entry}, ensure_ascii=False),
                ip_address=request.client.host if request.client else '',
            )
            return {'status': 'deleted', 'message': 'GitHub 源已移除'}

    # 3. 本地源
    local_dirs = _read_local_dirs_from_db()
    for path in local_dirs:
        if _make_source_file_id('local', path) == file_id:
            local_dirs.remove(path)
            _write_local_dirs_to_db(local_dirs)
            reset_source_manager_cache()
            models.add_audit_log(
                user_id=current_user['user_id'],
                username=current_user['username'],
                action='source_file_delete',
                target=path,
                detail=json.dumps({'type': 'local', 'path': path}, ensure_ascii=False),
                ip_address=request.client.host if request.client else '',
            )
            return {'status': 'deleted', 'message': '本地源已移除（原文件未删除）'}

    raise HTTPException(status_code=404, detail='源文件不存在')


@router.put('/api/source-files/{file_id}')
async def api_update_source_file(
    file_id: str, data: dict, request: Request, current_user: dict = Depends(require_admin)
):
    """更新源文件配置（目前仅支持 GitHub 源的下载方式修改）

    请求体: {download_method: "api"|"raw"|"proxy"|"mirror"}
    """
    # 仅 GitHub 源支持修改下载方式
    github_sources = _read_github_sources_from_db()
    for entry in github_sources:
        if _make_source_file_id('github', entry) == file_id:
            new_method = (data.get('download_method', '') or '').strip()
            if not new_method or new_method not in GITHUB_DOWNLOAD_METHODS:
                raise HTTPException(
                    status_code=400,
                    detail=f'无效的下载方式: {new_method}，可选: {list(GITHUB_DOWNLOAD_METHODS.keys())}',
                )

            _set_github_download_method(entry, new_method)
            method_info = GITHUB_DOWNLOAD_METHODS[new_method]

            models.add_audit_log(
                user_id=current_user['user_id'],
                username=current_user['username'],
                action='source_file_update',
                target=entry,
                detail=json.dumps(
                    {'type': 'github', 'entry': entry, 'download_method': new_method}, ensure_ascii=False
                ),
                ip_address=request.client.host if request.client else '',
            )
            return {
                'status': 'updated',
                'download_method': new_method,
                'download_method_label': method_info['label'],
                'message': f'下载方式已更新为: {method_info["label"]}',
            }

    raise HTTPException(status_code=404, detail='源文件不存在')


def _paginate_channels(channels: list, page: int, size: int, search: str = '') -> dict:
    """对频道列表进行搜索过滤 + 服务端分页，返回标准响应结构。"""
    total = len(channels)
    # 搜索过滤
    if search:
        sl = search.lower()
        channels = [
            ch
            for ch in channels
            if sl in ch.get('name', '').lower()
            or sl in ch.get('url', '').lower()
            or sl in ch.get('group', '').lower()
            or sl in ch.get('tvg_group', '').lower()
        ]
    filtered_total = len(channels)
    # 分页切片
    start = (page - 1) * size
    end = start + size
    page_channels = channels[start:end]
    return {
        'channels': page_channels,
        'total': filtered_total,
        'page': page,
        'size': size,
        'unfiltered_total': total,
    }


@router.get('/api/source-files/{file_id}/channels')
async def api_get_source_file_channels(
    file_id: str,
    page: int = 1,
    size: int = 100,
    search: str = '',
    current_user: dict = Depends(get_current_user),
):
    """获取某源文件解析出的频道列表（第二步：展开文件查看视频源）

    支持服务端分页（page/size）和搜索过滤（search），避免全量传输万级频道。
    """
    sm = _load_source_manager()
    if not sm:
        return {'channels': [], 'total': 0, 'page': page, 'size': size, 'message': 'SourceManager 加载失败'}

    # 1. 在线 URL 源
    online_urls = _read_online_urls_from_db()
    for url in online_urls:
        if _make_source_file_id('online', url) == file_id:
            file_path = _get_online_file_path(url)
            file_ua = _get_source_file_ua('online', url)
            if os.path.isfile(file_path):
                # D-1 修复：parse_file 在 worker 线程执行
                exclusions = []
                channels = await asyncio.to_thread(sm.parse_file, file_path, file_ua=file_ua if file_ua else None, exclusions=exclusions)
                channels = _enrich_channels_with_mappings(channels)
                channels = _apply_channel_ua_overrides(channels)
                result = _paginate_channels(channels, page, size, search)
                result['file_name'] = os.path.basename(file_path)
                result['file_ua'] = file_ua
                result['exclusion_summary'] = sm.summarize_exclusions(exclusions)
                return result
            return {
                'channels': [],
                'total': 0,
                'page': page,
                'size': size,
                'file_name': os.path.basename(file_path),
                'message': '文件尚未下载，请先点击"采集所有源"',
                'file_ua': file_ua,
            }

    # 2. GitHub 源
    github_sources = _read_github_sources_from_db()
    for entry in github_sources:
        if _make_source_file_id('github', entry) == file_id:
            try:
                download_methods = {entry: _get_github_download_method(entry)}
                # D-3 修复：带缓存 + 总超时（20s）的 GitHub 源发现
                discovered = await _discover_github_cached(sm, entry, download_methods)
            except Exception as e:
                return {'channels': [], 'total': 0, 'file_name': entry, 'message': f'GitHub API 调用失败: {e}'}

            all_channels = []
            exclusions = []
            matched_files = 0
            file_ua = _get_source_file_ua('github', entry)
            for d_info in discovered:
                d_url = d_info['url'] if isinstance(d_info, dict) else d_info
                filename = sm.get_filename_from_url(d_url)
                file_path = os.path.join(sm.online_dir, filename)
                if os.path.isfile(file_path):
                    # D-1 修复：parse_file 在 worker 线程执行
                    channels = await asyncio.to_thread(sm.parse_file, file_path, file_ua=file_ua if file_ua else None, exclusions=exclusions)
                    all_channels.extend(channels)
                    matched_files += 1

            all_channels = _enrich_channels_with_mappings(all_channels)
            all_channels = _apply_channel_ua_overrides(all_channels)
            msg = None
            if matched_files == 0:
                msg = f'发现 {len(discovered)} 个源文件，但尚未下载。请先点击"采集所有源"'

            # ── 更新内存缓存，让源文件列表展示真实下载状态 ──
            total_size = 0
            for d_info in discovered:
                d_url = d_info['url'] if isinstance(d_info, dict) else d_info
                fp = os.path.join(sm.online_dir, sm.get_filename_from_url(d_url))
                if os.path.isfile(fp):
                    total_size += os.path.getsize(fp)

            if matched_files == 0:
                cache_status = 'not_downloaded'
            elif matched_files >= len(discovered):
                cache_status = 'downloaded'
            else:
                cache_status = 'partial'

            import datetime as _cache_dt

            _github_status_cache[entry] = {
                'status': cache_status,
                'discovered': len(discovered),
                'matched': matched_files,
                'channels': len(all_channels),
                'total_size': total_size,
                'checked_at': _cache_dt.datetime.now().isoformat(),
            }

            # ── 持久化到数据库，服务重启后仍可显示下载状态 ──
            db_files = []
            for d_info in discovered:
                d_url = d_info['url'] if isinstance(d_info, dict) else d_info
                fn = sm.get_filename_from_url(d_url)
                fp = os.path.join(sm.online_dir, fn)
                db_files.append(
                    {
                        'filename': fn,
                        'file_size': os.path.getsize(fp) if os.path.isfile(fp) else 0,
                    }
                )
            models.clear_github_download_cache(entry)
            models.upsert_github_download_cache(entry, db_files)

            result = _paginate_channels(all_channels, page, size, search)
            result['file_name'] = entry
            result['message'] = msg
            result['discovered_files'] = len(discovered)
            result['matched_files'] = matched_files
            result['file_ua'] = file_ua
            result['exclusion_summary'] = sm.summarize_exclusions(exclusions)
            return result

    # 3. 本地源
    local_dirs = _read_local_dirs_from_db()
    for path in local_dirs:
        if _make_source_file_id('local', path) == file_id:
            abs_path = _resolve_local_path(path)
            file_ua = _get_source_file_ua('local', path)
            all_channels = []
            exclusions = []
            if os.path.isfile(abs_path):
                # D-1 修复：parse_file 在 worker 线程执行
                all_channels = await asyncio.to_thread(sm.parse_file, abs_path, file_ua=file_ua if file_ua else None, exclusions=exclusions)
            elif os.path.isdir(abs_path):
                all_channels = sm.parse_local_files(abs_path, exclusions=exclusions)
                # 对本地目录也应用文件级 UA
                if file_ua and file_ua.get('enabled') and file_ua.get('ua_value'):
                    for ch in all_channels:
                        if not ch.get('user_agent'):
                            ch['user_agent'] = file_ua['ua_value']
                        ch['ua_position'] = file_ua.get('ua_position', 'extinf')
            all_channels = _enrich_channels_with_mappings(all_channels)
            all_channels = _apply_channel_ua_overrides(all_channels)
            result = _paginate_channels(all_channels, page, size, search)
            result['file_name'] = os.path.basename(path)
            result['file_ua'] = file_ua
            result['exclusion_summary'] = sm.summarize_exclusions(exclusions)
            return result

    raise HTTPException(status_code=404, detail='源文件不存在')


# ── 源文件 UA 管理 API ──────────────────────────────


@router.put('/api/source-files/{file_id}/ua')
async def api_set_source_file_ua(
    file_id: str, data: dict, request: Request, current_user: dict = Depends(require_admin)
):
    """设置源文件的文件级 UA 配置

    请求体: {enabled: bool, ua_value: str, ua_position: "extinf"|"url"}
    """
    found = _find_source_file_by_id(file_id)
    if not found:
        raise HTTPException(status_code=404, detail='源文件不存在')

    src_type, value = found
    enabled = bool(data.get('enabled', False))
    ua_value = (data.get('ua_value', '') or '').strip()
    ua_position = (data.get('ua_position', 'extinf') or '').strip()
    if ua_position not in ('extinf', 'url'):
        ua_position = 'extinf'

    if enabled and not ua_value:
        raise HTTPException(status_code=400, detail='启用 UA 时必须填写 UA 值')

    ua_settings = {
        'enabled': enabled,
        'ua_value': ua_value,
        'ua_position': ua_position,
    }
    _set_source_file_ua(src_type, value, ua_settings)

    # 重置 SourceManager 缓存，使下次解析使用新 UA 设置
    reset_source_manager_cache()

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='source_file_ua_set',
        target=f'{src_type}:{value}',
        detail=json.dumps(ua_settings, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'ua_settings': ua_settings, 'message': f'UA 设置已{"启用" if enabled else "关闭"}'}


@router.delete('/api/source-files/{file_id}/ua')
async def api_del_source_file_ua(file_id: str, request: Request, current_user: dict = Depends(require_admin)):
    """删除源文件的文件级 UA 配置"""
    found = _find_source_file_by_id(file_id)
    if not found:
        raise HTTPException(status_code=404, detail='源文件不存在')

    src_type, value = found
    _del_source_file_ua(src_type, value)

    reset_source_manager_cache()

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='source_file_ua_delete',
        target=f'{src_type}:{value}',
        detail='',
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': 'UA 设置已清除'}


@router.put('/api/source-files/{file_id}/channel-ua')
async def api_set_channel_ua(file_id: str, data: dict, request: Request, current_user: dict = Depends(require_admin)):
    """设置频道级 UA 覆盖

    请求体: {url: str, ua_value: str, ua_position: "extinf"|"url"}
    """
    url = (data.get('url', '') or '').strip()
    ua_value = (data.get('ua_value', '') or '').strip()
    ua_position = (data.get('ua_position', 'extinf') or '').strip()
    if ua_position not in ('extinf', 'url'):
        ua_position = 'extinf'

    if not url:
        raise HTTPException(status_code=400, detail='频道 URL 不能为空')
    if not ua_value:
        raise HTTPException(status_code=400, detail='UA 值不能为空')

    overrides = _read_channel_ua_overrides()
    overrides[url] = {'ua_value': ua_value, 'ua_position': ua_position}
    _write_channel_ua_overrides(overrides)

    reset_source_manager_cache()

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='channel_ua_set',
        target=url[:200],
        detail=json.dumps({'ua_value': ua_value[:100], 'ua_position': ua_position}, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': '频道 UA 覆盖已保存'}


@router.delete('/api/source-files/{file_id}/channel-ua')
async def api_del_channel_ua(
    file_id: str, request: Request, url: str = Query(...), current_user: dict = Depends(require_admin)
):
    """删除频道级 UA 覆盖

    查询参数: url=频道URL
    """
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail='频道 URL 不能为空')

    overrides = _read_channel_ua_overrides()
    if url in overrides:
        del overrides[url]
        _write_channel_ua_overrides(overrides)

    reset_source_manager_cache()

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='channel_ua_delete',
        target=url[:200],
        detail='',
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': '频道 UA 覆盖已删除'}


# ══════════════════════════════════════════════════
# 源采集 API
# ══════════════════════════════════════════════════


@router.post('/api/sources/collect')
async def api_collect_sources(request: Request, current_user: dict = Depends(require_admin)):
    """触发源采集（仅下载源文件，不测试流）"""
    import asyncio as _asyncio

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='source_collect',
        target='github_sources',
        ip_address=request.client.host if request.client else '',
    )
    sm = _load_source_manager()
    if not sm:
        return {'status': 'error', 'message': 'SourceManager 加载失败'}

    async def _do_collect():
        try:
            download_methods = _read_github_source_settings()
            files = await sm.download_all_sources(github_download_methods=download_methods)
            logger.info(f'源采集完成: 下载了 {len(files)} 个文件')

            # ── 采集后持久化 GitHub 下载状态到数据库 ──
            github_sources = _read_github_sources_from_db()
            for entry in github_sources:
                try:
                    methods = {entry: _get_github_download_method(entry)}
                    # D-3 修复：采集时强制发现最新 + 总超时（20s）避免无限挂起
                    try:
                        discovered = await asyncio.wait_for(
                            sm._discover_github_source_urls([entry], methods=methods),
                            timeout=20,
                        )
                    except TimeoutError:
                        logger.warning(f'GitHub 源发现超时（20s），跳过采集: {entry}')
                        continue
                    db_files = []
                    for d_info in discovered:
                        d_url = d_info['url'] if isinstance(d_info, dict) else d_info
                        fn = sm.get_filename_from_url(d_url)
                        fp = os.path.join(sm.online_dir, fn)
                        db_files.append(
                            {
                                'filename': fn,
                                'file_size': os.path.getsize(fp) if os.path.isfile(fp) else 0,
                            }
                        )
                    models.clear_github_download_cache(entry)
                    models.upsert_github_download_cache(entry, db_files)
                except Exception as e:
                    logger.warning(f'持久化 GitHub 下载状态失败 [{entry}]: {e}')

            reset_source_manager_cache()
            _github_status_cache.clear()  # 清除状态缓存，下次展开重新检查
        except Exception as e:
            logger.error(f'源采集失败: {e}')
            reset_source_manager_cache()

    _collect_task = _asyncio.create_task(_do_collect())
    _background_tasks.add(_collect_task)
    _collect_task.add_done_callback(_background_tasks.discard)
    return {'status': 'collecting', 'message': '源采集已启动，请稍后刷新查看'}


# ══════════════════════════════════════════════════
# 源多维分类 API
# ══════════════════════════════════════════════════


@router.get('/api/sources/{source_id}/categories')
async def api_get_source_categories(
    source_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取某个源的所有维度分类

    Note: source_id 是 MD5 hash（来自前端），而非 stream_sources.id (INTEGER)。
    由于 stream_sources 表不在此项目创建/管理，优先使用 channel_name_mapping。
    """
    # 先试图获取源名称
    channel_name = ''
    sm = _load_source_manager()
    if sm:
        try:
            # D-1 修复：重 IO 解析在 worker 线程执行，避免阻塞事件循环
            sources = await asyncio.to_thread(parse_all_files_cached, sm)
            for s in sources:
                sid = hashlib.md5(f'{s.get("name", "")}|{s.get("url", "")}'.encode()).hexdigest()[:12]
                if sid == source_id:
                    channel_name = s.get('name', '')
                    break
        except Exception as e:
            logger.warning(f'解析源文件失败 (api_get_source_categories): {e}')

    # 从 channel_name_mapping 获取分类
    mapping_cats = {}
    if channel_name:
        mapping = models.get_channel_name_mapping(channel_name)
        if mapping:
            mapping_cats = {
                'content': mapping.get('content', '-'),
                'region': mapping.get('region', '-'),
                'language': mapping.get('language', '-'),
                'quality': mapping.get('quality', '-'),
                'media_type': mapping.get('media_type', '-'),
                'genre': mapping.get('genre', '-'),
            }

    # 同时获取维度定义，用于前端展示
    dimensions = models.get_all_dimensions()

    return {
        'source_id': source_id,
        'channel_name': channel_name,
        'categories': mapping_cats,
        'dimensions': dimensions,
    }


@router.put('/api/sources/{source_id}/categories/{dim_key}')
async def api_update_source_category(
    source_id: str,
    dim_key: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """人工修正某个源在某个维度的分类值

    实际写入 channel_name_mapping（因为 stream_source_categories.source_id 是 INTEGER，
    而前端使用 hash string，无法匹配）。
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式') from None

    dim_value = data.get('dim_value', '').strip()
    if not dim_value:
        raise HTTPException(status_code=400, detail='dim_value 不能为空')

    # 找到对应的 channel_name
    channel_name = ''
    sm = _load_source_manager()
    if sm:
        try:
            # D-1 修复：重 IO 解析在 worker 线程执行，避免阻塞事件循环
            sources = await asyncio.to_thread(parse_all_files_cached, sm)
            for s in sources:
                sid = hashlib.md5(f'{s.get("name", "")}|{s.get("url", "")}'.encode()).hexdigest()[:12]
                if sid == source_id:
                    channel_name = s.get('name', '')
                    break
        except Exception as e:
            logger.warning(f'解析源文件失败 (api_update_source_category): {e}')

    if not channel_name:
        raise HTTPException(status_code=404, detail='无法找到对应的频道')

    # 使用 channel_name_mapping 的保存逻辑
    mapping = models.get_channel_name_mapping(channel_name) or {}
    mapping[dim_key] = dim_value
    success = models.save_channel_name_mapping(channel_name, mapping)
    if not success:
        raise HTTPException(status_code=500, detail='更新失败')

    # 审计日志
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='source_category_update',
        target=f'source:{source_id}/{dim_key}',
        detail=f'{dim_key} → {dim_value}',
        ip_address=request.client.host if request.client else '',
    )

    return {'message': f'{dim_key} → {dim_value} 已更新', 'is_manual': 1}
