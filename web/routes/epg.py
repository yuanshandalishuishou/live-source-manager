#!/usr/bin/env python3
"""EPG 电子节目单路由 — 页面 + 源管理 CRUD + 抓取/生成 API + 网格数据查询

一期（数据闭环）+ 二期（premium 网格视图）合并实现：
  - /epg          节目单网格页（premium 玻璃拟态 + 磁性 hover + 自包含 light/dark 切换）
  - /epg/sources  EPG 源管理页（启用/地址/更新时间/优先级 配置）
  - /api/epg/*    源 CRUD、手动抓取、全量刷新、生成 xml.gz、网格/NowNext/状态查询、频道对齐
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from web import models
from web.core import (
    PROJECT_ROOT,
    _load_source_manager,
    _render,
    get_current_user,
    logger,
    require_admin,
)

router = APIRouter()

# ── 后台刷新状态（防止 asyncio.create_task 被 GC，且供前端轮询）────────────
_epg_fetch_running = False
_epg_fetch_task = None
_epg_tasks: set = set()


def _track_task(task):
    """持有 create_task 返回的引用，避免被 GC；完成后自动移除"""
    _epg_tasks.add(task)
    task.add_done_callback(_epg_tasks.discard)


_STATE_DIR = os.path.join(PROJECT_ROOT, 'data', 'status')
_STATE_PATH = os.path.join(_STATE_DIR, 'epg_fetch_state.json')


def _state_load() -> dict:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _state_save(st: dict) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_STATE_PATH, 'w') as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f'[EPG] 保存刷新状态失败: {e}')


def get_epg_manager():
    """用共享 Config 构造 EPGManager（与 SourceManager 同源，避免重复加载）"""
    sm = _load_source_manager()
    if sm is not None and getattr(sm, 'config', None) is not None:
        return __import__('app.epg', fromlist=['EPGManager']).EPGManager(sm.config)
    from app.epg import EPGManager

    return EPGManager()


async def run_refresh(specific_ids: list[int] | None = None) -> bool:
    """后台执行 EPG 刷新（全部或指定源）。返回是否成功启动。

    刷新完成后把各源最近抓取时间写入 epg_fetch_state.json，供调度器去重判断。
    """
    global _epg_fetch_running, _epg_fetch_task
    if _epg_fetch_running:
        return False
    _epg_fetch_running = True
    started = time.time()
    try:
        mgr = get_epg_manager()
        result = await mgr.refresh_all(specific_ids)
        # 刷新成功后自动生成 XMLTV 文件，做到「抓取即出文件」闭环（epg_scheduler 只抓不生成的历史缺口）
        try:
            gen = await asyncio.to_thread(mgr.generate_xmltv)
            if gen.get('ok'):
                logger.info(
                    f'[EPG] 自动生成 XMLTV: {gen.get("path")} '
                    f'(频道 {gen.get("channels")} / 节目 {gen.get("programmes")} / '
                    f'{gen.get("size", 0) / 1024:.1f}KB)'
                )
            else:
                logger.info(f'[EPG] 跳过 XMLTV 生成: {gen.get("message")}')
        except Exception as ge:
            logger.warning(f'[EPG] 自动生成 XMLTV 失败: {ge}')
        now_iso = datetime.now().isoformat(timespec='seconds')
        st = _state_load()
        per_source = st.setdefault('per_source_last', {})
        for r in result.get('results', []):
            sid = r.get('source_id')
            if sid is not None:
                per_source[str(sid)] = now_iso
        st['last_refresh'] = now_iso
        st['last_result'] = {
            'total': result.get('total', 0),
            'ok': result.get('ok', 0),
            'failed': result.get('failed', 0),
            'matched_channels': result.get('matched_channels', 0),
        }
        _state_save(st)
        logger.info(
            f'[EPG] 刷新完成: 共 {result.get("total")} / 成功 {result.get("ok")} / '
            f'失败 {result.get("failed")} / 对齐 {result.get("matched_channels")} / '
            f'耗时 {int((time.time() - started) * 1000)}ms'
        )
    except Exception as e:
        logger.warning(f'[EPG] 刷新失败: {e}')
    finally:
        _epg_fetch_running = False
    return True


async def epg_scheduler():
    """EPG 定时刷新调度（常驻后台任务）。

    每个启用的源可单独配置 refresh_mode / refresh_at / refresh_minutes（覆盖全局）。
    到点即触发增量刷新，状态写入 epg_fetch_state.json，避免跨分钟重复触发。
    """
    await asyncio.sleep(20)  # 启动后稍候，待缓存预热
    while True:
        try:
            from app.config import Config

            epg_cfg = Config().get_epg_config()
            if not epg_cfg.get('enabled'):
                await asyncio.sleep(60)
                continue

            global_mode = (epg_cfg.get('refresh_mode') or 'daily').strip().lower()
            global_at = (epg_cfg.get('refresh_at') or '03:30').strip()
            try:
                global_minutes = int(epg_cfg.get('refresh_minutes') or 360)
            except (TypeError, ValueError):
                global_minutes = 360

            sources = models.list_epg_sources(enabled_only=True)
            now = datetime.now()
            st = _state_load()
            per_source = st.get('per_source_last', {})

            due: list[int] = []
            for s in sources:
                mode = (s.get('refresh_mode') or '').strip().lower() or global_mode
                last_raw = per_source.get(str(s['id']))
                last_dt = None
                if last_raw:
                    try:
                        last_dt = datetime.fromisoformat(last_raw)
                    except Exception:
                        last_dt = None
                if mode == 'interval':
                    minutes = s.get('refresh_minutes') or global_minutes
                    try:
                        minutes = max(5, int(minutes))
                    except (TypeError, ValueError):
                        minutes = max(5, global_minutes)
                    if last_dt is None or (now - last_dt) >= timedelta(minutes=minutes):
                        due.append(int(s['id']))
                else:  # daily
                    at = (s.get('refresh_at') or global_at).strip()
                    try:
                        h, m = (int(x) for x in at.split(':'))
                    except Exception:
                        h, m = 3, 30
                    if now.hour == h and now.minute == m and (last_dt is None or last_dt.date() < now.date()):
                        due.append(int(s['id']))

            if due:
                logger.info(f'[EPG-SCHED] 到点触发刷新 {len(due)} 个源')
                _track_task(asyncio.create_task(run_refresh(due)))
        except Exception as e:
            logger.warning(f'[EPG-SCHED] 调度循环异常: {e}')
        await asyncio.sleep(60)  # 每分钟检查一次


def _norm_source(s: dict) -> dict:
    """把 DB 行规整为前端友好结构（enabled 转 bool，时间字段保留）"""
    s = dict(s)
    s['enabled'] = bool(s.get('enabled'))
    for k in ('refresh_mode', 'refresh_at'):
        s.setdefault(k, '')
    if not s.get('refresh_mode'):
        s['refresh_mode'] = ''
    s['refresh_minutes'] = int(s.get('refresh_minutes') or 0)
    return s


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为 JSON 格式') from None


# ═══════════════════════════════════════════════════
# 页面路由
# ═══════════════════════════════════════════════════


@router.get('/epg', response_class=JSONResponse)
async def epg_grid_page(request: Request, current_user: dict = Depends(get_current_user)):
    """EPG 节目单网格页"""
    return _render(request, 'epg_grid.html')


@router.get('/epg/sources', response_class=JSONResponse)
async def epg_sources_page(request: Request, current_user: dict = Depends(get_current_user)):
    """EPG 源管理页"""
    return _render(request, 'epg_sources.html')


# ═══════════════════════════════════════════════════
# 源管理 API
# ═══════════════════════════════════════════════════


@router.get('/api/epg/sources')
async def api_list_epg_sources(enabled_only: bool = False, current_user: dict = Depends(get_current_user)):
    rows = models.list_epg_sources(enabled_only=enabled_only)
    return {'sources': [_norm_source(r) for r in rows], 'count': len(rows)}


@router.post('/api/epg/sources')
async def api_add_epg_source(request: Request, current_user: dict = Depends(require_admin)):
    data = await _json_body(request)
    url = (data.get('url') or '').strip()
    if not url:
        raise HTTPException(status_code=400, detail='EPG 源地址（url）不能为空')
    name = (data.get('name') or '').strip() or url
    sid = models.add_epg_source(
        name=name,
        url=url,
        enabled=bool(data.get('enabled', True)),
        priority=int(data.get('priority', 100) or 100),
        refresh_mode=(data.get('refresh_mode') or '').strip(),
        refresh_at=(data.get('refresh_at') or '').strip(),
        refresh_minutes=int(data.get('refresh_minutes') or 0),
        remark=(data.get('remark') or '').strip(),
    )
    if not sid:
        raise HTTPException(status_code=409, detail='该地址已存在或写入失败')
    return {'ok': True, 'id': sid}


@router.put('/api/epg/sources/{source_id}')
async def api_update_epg_source(source_id: int, request: Request, current_user: dict = Depends(require_admin)):
    data = await _json_body(request)
    fields = {}
    for k in ('name', 'url', 'enabled', 'priority', 'refresh_mode', 'refresh_at', 'refresh_minutes', 'remark'):
        if k in data:
            fields[k] = data[k]
    ok = models.update_epg_source(source_id, **fields)
    if not ok:
        # 区分「不存在」与「无变化/冲突」
        if not models.get_epg_source(source_id):
            raise HTTPException(status_code=404, detail='EPG 源不存在')
        raise HTTPException(status_code=409, detail='更新失败（地址冲突或无有效变更）')
    return {'ok': True}


@router.delete('/api/epg/sources/{source_id}')
async def api_delete_epg_source(source_id: int, current_user: dict = Depends(require_admin)):
    ok = models.delete_epg_source(source_id)
    if not ok:
        raise HTTPException(status_code=404, detail='EPG 源不存在')
    return {'ok': True}


# ═══════════════════════════════════════════════════
# 抓取 / 生成 API
# ═══════════════════════════════════════════════════


@router.post('/api/epg/sources/{source_id}/refresh')
async def api_refresh_one(source_id: int, current_user: dict = Depends(require_admin)):
    src = models.get_epg_source(source_id)
    if not src:
        raise HTTPException(status_code=404, detail='EPG 源不存在')
    global _epg_fetch_task
    _epg_fetch_task = asyncio.create_task(run_refresh([int(source_id)]))
    return {'ok': True, 'message': '已加入刷新队列', 'running': True}


@router.post('/api/epg/refresh-all')
async def api_refresh_all(current_user: dict = Depends(require_admin)):
    global _epg_fetch_running
    if _epg_fetch_running:
        return {'ok': True, 'message': '刷新任务正在进行中', 'running': True}
    _track_task(asyncio.create_task(run_refresh(None)))
    return {'ok': True, 'message': '已触发全量刷新', 'running': True}


@router.post('/api/epg/generate')
async def api_generate_epg(current_user: dict = Depends(require_admin)):
    mgr = get_epg_manager()
    try:
        result = await asyncio.to_thread(mgr.generate_xmltv)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'生成 EPG 文件失败: {e}') from None
    return {'ok': result.get('ok', False), **result}


# ═══════════════════════════════════════════════════
# 查询 API（网格 / 频道 / NowNext / 状态）
# ═══════════════════════════════════════════════════


@router.get('/api/epg/grid')
async def api_epg_grid(
    hours: int = 12,
    keyword: str = '',
    limit: int = 80,
    current_user: dict = Depends(get_current_user),
):
    mgr = get_epg_manager()
    try:
        data = mgr.get_grid_data(hours=hours, keyword=keyword, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'读取节目单失败: {e}') from None
    return data


@router.get('/api/epg/channels')
async def api_epg_channels(
    page: int = 1,
    page_size: int = 50,
    keyword: str = '',
    source_id: int = 0,
    current_user: dict = Depends(get_current_user),
):
    offset = max(0, (page - 1) * page_size)
    channels, total = models.list_epg_channels(
        source_id=source_id or None, keyword=keyword, limit=page_size, offset=offset
    )
    return {'channels': channels, 'total': total, 'page': page, 'page_size': page_size}


@router.get('/api/epg/now')
async def api_epg_now(tvg_ids: str = '', current_user: dict = Depends(get_current_user)):
    ids = [x.strip() for x in (tvg_ids or '').split(',') if x.strip()]
    if not ids:
        return {'now_next': {}}
    mgr = get_epg_manager()
    return {'now_next': mgr.get_now_next(ids)}


@router.post('/api/epg/channels/{channel_id}/match')
async def api_epg_channel_match(channel_id: int, request: Request, current_user: dict = Depends(require_admin)):
    data = await _json_body(request)
    matched = (data.get('matched_channel') or '').strip()
    tvg_id = (data.get('tvg_id') or '').strip()
    ch = models.get_epg_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail='EPG 频道不存在')
    ok = models.set_epg_channel_match_by_id(channel_id, matched, tvg_id if tvg_id else None)
    if not ok:
        raise HTTPException(status_code=404, detail='EPG 频道不存在')
    # 对齐成功后回写 channel_name_mapping，使 M3U 的 tvg-id/tvg-logo 注入生效
    if matched:
        models.set_channel_tvg_info(
            matched,
            tvg_id or ch.get('tvg_id') or '',
            ch.get('icon') or '',
        )
    return {'ok': True}


@router.get('/api/epg/status')
async def api_epg_status(current_user: dict = Depends(get_current_user)):
    try:
        from app.config import Config

        epg_cfg = Config().get_epg_config()
    except Exception:
        epg_cfg = {}
    mgr = get_epg_manager()
    stats = models.get_epg_stats()
    st = _state_load()
    return {
        'config': {
            'enabled': bool(epg_cfg.get('enabled')),
            'output_filename': epg_cfg.get('output_filename'),
            'timezone': epg_cfg.get('timezone'),
            'refresh_mode': epg_cfg.get('refresh_mode'),
            'refresh_at': epg_cfg.get('refresh_at'),
            'refresh_minutes': epg_cfg.get('refresh_minutes'),
            'inject_into_m3u': bool(epg_cfg.get('inject_into_m3u')),
        },
        'url': mgr.get_epg_url(),
        'stats': stats,
        'fetch_running': _epg_fetch_running,
        'last_refresh': st.get('last_refresh'),
        'last_result': st.get('last_result'),
    }


@router.get('/api/epg/url')
async def api_epg_url(current_user: dict = Depends(get_current_user)):
    mgr = get_epg_manager()
    return {'url': mgr.get_epg_url()}
