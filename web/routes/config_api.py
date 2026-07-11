#!/usr/bin/env python3
"""配置中心 API — /api/config/*"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from web import models
from web.core import (
    SECTION_SCHEMA,
    get_current_user,
    get_field_meta,
    logger,
    read_config,
    read_section,
    require_admin,
    reset_source_manager_cache,
    sanitize_config_data,
    validate_and_coerce,
    write_config,
)

router = APIRouter()


@router.get('/api/config')
async def api_get_config(current_user: dict = Depends(get_current_user)):
    return read_config()


@router.get('/api/config/fields')
async def api_get_config_fields(current_user: dict = Depends(get_current_user)):
    """返回配置字段的 schema 信息，供前端动态渲染"""
    return get_field_meta()


@router.get('/api/config/history')
async def api_config_history(
    current_user: dict = Depends(require_admin),
    page: int = 1,
    size: int = 50,
):
    """返回配置变更历史（从审计日志中过滤 config_change / config_update / config_section_update 操作）"""
    config_actions = ('config_update', 'config_section_update', 'config_change', 'config_reload')
    result = {'total': 0, 'page': page, 'size': size, 'history': []}

    try:
        conn = models.get_conn()
        offset = (page - 1) * size

        placeholders = ','.join('?' for _ in config_actions)
        total_row = conn.execute(
            f'SELECT COUNT(*) FROM audit_logs WHERE action IN ({placeholders})', config_actions
        ).fetchone()
        total = total_row[0] if total_row else 0

        if total > 0:
            rows = conn.execute(
                f'SELECT * FROM audit_logs WHERE action IN ({placeholders}) ORDER BY created_at DESC LIMIT ? OFFSET ?',
                (*config_actions, size, offset),
            ).fetchall()
            result['history'] = [dict(r) for r in rows]
            result['total'] = total

        conn.close()
    except Exception as e:
        logger.error(f'读取配置变更历史失败: {e}')

    return result


@router.get('/api/config/{section}')
async def api_get_section(section: str, current_user: dict = Depends(get_current_user)):
    """获取单个配置段"""
    data = read_section(section)
    if not data:
        raise HTTPException(status_code=404, detail=f'配置段落 [{section}] 不存在')
    return data


@router.put('/api/config')
async def api_update_config(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    """全量配置更新"""
    success, msg = write_config(data)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='config_update',
        target='SQLite',
        detail=json.dumps(sanitize_config_data(data), ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': msg}


@router.put('/api/config/{section}')
async def api_update_section(section: str, request: Request, current_user: dict = Depends(require_admin)):
    """保存单个配置段"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）') from None

    # 校验请求体必须是键值对（非嵌套字典）
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail='请求体必须是键值对对象')

    # 校验 section 是否存在 schema 定义
    if section not in SECTION_SCHEMA:
        raise HTTPException(status_code=400, detail=f'未知配置段落 [{section}]')

    # 校验并写入
    section_data = {}
    section_data[section] = {}
    for key, value in data.items():
        if key in SECTION_SCHEMA[section]:
            section_data[section][key] = str(value)
        else:
            raise HTTPException(status_code=400, detail=f'[{section}] 不存在字段 "{key}"')

    success, msg = write_config(section_data)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    # 保存后刷新 SourceManager 缓存，让新配置（如 GitHub Token）立即生效
    reset_source_manager_cache()

    # 记录审计日志
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='config_section_update',
        target=f'[{section}]',
        detail=json.dumps(sanitize_config_data(section_data), ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': f'配置段 [{section}] 已保存'}


@router.post('/api/config/validate')
async def api_validate_config(request: Request, current_user: dict = Depends(get_current_user)):
    """单字段校验"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）') from None

    section = data.get('section', '').strip()
    key = data.get('key', '').strip()
    value = data.get('value', '')

    if not section:
        raise HTTPException(status_code=400, detail='缺少字段: section')
    if not key:
        raise HTTPException(status_code=400, detail='缺少字段: key')

    # 校验 section 是否存在
    if section not in SECTION_SCHEMA:
        return {
            'valid': False,
            'error': f'未知配置段落 [{section}]',
            'coerced_value': None,
        }

    # 校验字段是否存在
    field_def = SECTION_SCHEMA[section].get(key)
    if not field_def:
        return {
            'valid': False,
            'error': f'[{section}] 不存在字段 "{key}"',
            'coerced_value': None,
        }

    # 校验值
    coerced, err = validate_and_coerce(section, key, str(value), field_def)
    if err:
        return {
            'valid': False,
            'error': err,
            'coerced_value': str(coerced),
        }

    return {
        'valid': True,
        'error': '',
        'coerced_value': str(coerced),
    }


@router.post('/api/config/reload')
async def api_reload_config(request: Request, current_user: dict = Depends(require_admin)):
    """
    触发配置重载：从 SQLite 重新读取全量配置并更新全局缓存。
    同时清空 SourceManager 缓存实例让下次访问时重新加载。
    """
    reloaded_items = 0
    # 从 SQLite 重读配置：触发重新取数，更新 models 内部缓存/依赖
    try:
        config_data = models.get_all_config()
        reloaded_items = sum(len(v) for v in config_data.values())
        logger.info(f'配置重载完成，读取 {reloaded_items} 个配置项')
    except Exception as e:
        logger.error(f'配置重载失败: {e}')
        models.add_audit_log(
            user_id=current_user['user_id'],
            username=current_user['username'],
            action='config_reload',
            target='SQLite',
            detail=f'重载失败: {e}',
            ip_address=request.client.host if request.client else '',
        )
        return {'status': 'error', 'message': f'配置重载失败: {e}', 'reloaded': 0}

    # 清空 SourceManager 缓存实例，下次 API 调用时重新创建
    reset_source_manager_cache()

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='config_reload',
        target='SQLite',
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': f'配置重载完成，共 {reloaded_items} 项', 'reloaded': reloaded_items}
