#!/usr/bin/env python3
"""候选池与失效统计 API — Feature 2 / Feature 5 的闭环接口

- /api/candidates            GET   候选池列表（支持 channel / only_frozen / limit 过滤）+ 统计
- /api/candidates/freeze    POST  切换某 URL 的手动冻结（优选固定）
- /api/candidates/clear     POST  清空候选池
- /api/sources/failures     GET   失效统计列表（含停用状态）
- /api/sources/failures/reenable POST 手动恢复某停用源
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from web import models
from web.core import logger, require_admin

router = APIRouter()


# ── 请求体模型 ──────────────────────────────────────
class FreezeRequest(BaseModel):
    url: str
    frozen: bool = True


class ReenableRequest(BaseModel):
    url: str


# ══════════════════════════════════════════════════
# 候选池（Feature 2）
# ══════════════════════════════════════════════════
@router.get('/api/candidates')
async def list_candidates(
    request: Request,
    channel: str = '',
    only_frozen: bool = False,
    limit: int = 0,
    _: object = Depends(require_admin),
):
    """候选池列表 + 统计。"""
    try:
        items = models.get_candidate_pool(channel=channel or None, only_frozen=only_frozen, limit=limit)
        stats = models.get_candidate_pool_stats()
        return {'ok': True, 'items': items, 'stats': stats}
    except Exception as e:
        logger.warning(f'查询候选池失败: {e}')
        raise HTTPException(status_code=500, detail=f'查询候选池失败: {e}') from e


@router.post('/api/candidates/freeze')
async def freeze_candidate(req: FreezeRequest, _: object = Depends(require_admin)):
    """手动冻结/解冻某候选源（冻结的优选源在输出择优时始终保留）。"""
    ok = models.set_candidate_frozen(req.url, bool(req.frozen))
    return {'ok': ok, 'url': req.url, 'frozen': bool(req.frozen)}


@router.post('/api/candidates/clear')
async def clear_candidates(_: object = Depends(require_admin)):
    """清空候选池。"""
    n = models.clear_candidate_pool()
    return {'ok': True, 'cleared': n}


# ══════════════════════════════════════════════════
# 失效统计（Feature 5）
# ══════════════════════════════════════════════════
@router.get('/api/sources/failures')
async def list_failures(_: object = Depends(require_admin)):
    """失效统计列表（含停用状态）。"""
    try:
        items = models.get_failure_stats()
        disabled = len(models.get_disabled_source_urls())
        return {'ok': True, 'items': items, 'disabled_count': disabled}
    except Exception as e:
        logger.warning(f'查询失效统计失败: {e}')
        raise HTTPException(status_code=500, detail=f'查询失效统计失败: {e}') from e


@router.post('/api/sources/failures/reenable')
async def reenable_failure(req: ReenableRequest, _: object = Depends(require_admin)):
    """手动恢复某停用源（解除停用并清零计数）。"""
    ok = models.reenable_source(req.url)
    return {'ok': ok, 'url': req.url}
