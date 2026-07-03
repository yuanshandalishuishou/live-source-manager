#!/usr/bin/env python3
"""认证 API + 用户管理 API"""

import asyncio
import json
import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse

import web.core as core
from web import models
from web.core import (
    _get_csrf_token,
    create_session,
    destroy_session,
    get_current_user,
    get_session,
    require_admin,
)

router = APIRouter()


# ══════════════════════════════════════════════════
# 认证 API
# ══════════════════════════════════════════════════


@router.post('/api/auth/login')
async def api_login(request: Request, username: str = Form(...), password: str = Form(...)):
    # bcrypt 是同步且CPU密集的，用 asyncio.to_thread 避免阻塞事件循环
    user = await asyncio.to_thread(models.verify_password, username, password)
    if not user:
        raise HTTPException(status_code=401, detail='用户名或密码错误')
    session_id = create_session(user)
    # 审计日志
    models.add_audit_log(
        user_id=user['id'],
        username=user['username'],
        action='login',
        target='',
        ip_address=request.client.host if request.client else '',
    )
    resp = JSONResponse(
        {
            'status': 'ok',
            'role': user['role'],
            'encrypt_key_hint': not core.CONFIG_KEY_IS_MANUAL,
        }
    )
    is_https = os.environ.get('HTTPS', '') == 'on'
    resp.set_cookie(key='session', value=session_id, httponly=True, max_age=86400, secure=is_https, samesite='lax')
    return resp


@router.post('/api/auth/logout')
async def api_logout(request: Request):
    session_id = request.cookies.get('session')
    if session_id:
        session = get_session(session_id)
        if session:
            models.add_audit_log(
                user_id=session['user_id'],
                username=session['username'],
                action='logout',
                target='',
                ip_address=request.client.host if request.client else '',
            )
        destroy_session(session_id)
    resp = JSONResponse({'status': 'ok'})
    resp.delete_cookie('session')
    return resp


@router.get('/api/auth/me')
async def api_auth_me(current_user: dict = Depends(get_current_user)):
    return {'username': current_user['username'], 'role': current_user['role']}


@router.get('/api/auth/csrf-token')
async def api_csrf_token(current_user: dict = Depends(get_current_user)):
    """获取 CSRF token——前端所有写操作必须在 X-CSRF-Token header 中带上此值"""
    token = _get_csrf_token(current_user['session_id'])
    return {'csrf_token': token}


@router.put('/api/auth/password')
async def api_update_password(request: Request, current_user: dict = Depends(get_current_user)):
    """修改当前用户密码"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not old_password:
        raise HTTPException(status_code=400, detail='旧密码不能为空')
    if not new_password:
        raise HTTPException(status_code=400, detail='新密码不能为空')
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail='新密码长度至少6个字符')
    if old_password == new_password:
        raise HTTPException(status_code=400, detail='新密码不能与旧密码相同')

    # 验证旧密码
    user = await asyncio.to_thread(models.verify_password, current_user['username'], old_password)
    if not user:
        raise HTTPException(status_code=400, detail='旧密码错误')

    # 更新密码
    success = models.update_user_password(current_user['user_id'], new_password)
    if not success:
        raise HTTPException(status_code=500, detail='密码修改失败，请稍后重试')

    # 记录审计日志
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='password_change',
        target='self',
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': '密码已修改'}


@router.put('/api/users/{user_id}/password')
async def api_update_user_password(user_id: int, request: Request, current_user: dict = Depends(require_admin)):
    """管理员直接重置指定用户的密码"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')
    new_password = data.get('new_password', '')

    if not new_password:
        raise HTTPException(status_code=400, detail='新密码不能为空')
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail='密码长度至少6个字符')

    # 获取目标用户信息
    target_user = models.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail='用户不存在')

    # 管理员不能修改自己的密码（应使用 /api/auth/password）
    if current_user['user_id'] == user_id:
        raise HTTPException(status_code=400, detail='请使用密码修改接口修改自己的密码')

    # 更新密码
    success = models.update_user_password(user_id, new_password)
    if not success:
        raise HTTPException(status_code=500, detail='密码修改失败，请稍后重试')

    # 记录审计日志
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='user_password_reset',
        target=target_user['username'],
        detail=f'管理员重置用户 {target_user["username"]} 的密码',
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': f'用户 {target_user["username"]} 的密码已重置'}


@router.get('/api/auth/encrypt-key-status')
async def api_encrypt_key_status(current_user: dict = Depends(get_current_user)):
    """检查当前密钥是否用户自定义"""
    return {'has_custom_key': core.CONFIG_KEY_IS_MANUAL}


@router.put('/api/auth/encrypt-key')
async def api_update_encrypt_key(request: Request, current_user: dict = Depends(require_admin)):
    """修改加密密钥并重新加密所有敏感配置"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='请求体必须为JSON格式（Content-Type: application/json）')
    new_key = data.get('new_key', '').strip()
    if not new_key:
        raise HTTPException(status_code=400, detail='新密钥不能为空')
    if len(new_key) < 16:
        raise HTTPException(status_code=400, detail='密钥长度至少16位')
    from web import crypto_utils

    count = crypto_utils.re_encrypt_all(new_key)
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='encrypt_key_update',
        target='app_config',
        detail=f'已重新加密 {count} 条敏感配置',
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': f'密钥已更新，重新加密 {count} 条记录'}


# ══════════════════════════════════════════════════
# 用户管理 API
# ══════════════════════════════════════════════════


@router.get('/api/users')
async def api_list_users(current_user: dict = Depends(require_admin)):
    return {'users': models.list_users()}


@router.post('/api/users')
async def api_create_user(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'viewer')
    display_name = data.get('display_name', '').strip()

    if not username or len(username) < 2:
        raise HTTPException(status_code=400, detail='用户名至少2个字符')
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail='密码至少6个字符')
    if role not in ('admin', 'viewer'):
        raise HTTPException(status_code=400, detail='角色无效')

    try:
        user_id = models.create_user(username, password, role, display_name)
    except Exception as e:
        raise HTTPException(status_code=409, detail=f'创建用户失败（可能已存在）: {e}')

    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='user_create',
        target=username,
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'created', 'id': user_id}


@router.put('/api/users/{user_id}')
async def api_update_user(user_id: int, data: dict, request: Request, current_user: dict = Depends(require_admin)):
    """更新用户信息（角色、显示名、密码）"""
    if current_user['user_id'] == user_id and data.get('role') and data['role'] != current_user['role']:
        raise HTTPException(status_code=400, detail='不能修改自己的角色')
    kwargs = {}
    if 'role' in data:
        if data['role'] not in ('admin', 'viewer'):
            raise HTTPException(status_code=400, detail='角色无效')
        kwargs['role'] = data['role']
    if 'display_name' in data:
        kwargs['display_name'] = data['display_name'].strip()
    if data.get('password'):
        if len(data['password']) < 6:
            raise HTTPException(status_code=400, detail='密码至少6个字符')
        kwargs['password'] = data['password']
    success = models.update_user(user_id, **kwargs)
    if not success:
        raise HTTPException(status_code=404, detail='用户不存在')
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='user_update',
        target=str(user_id),
        detail=json.dumps({k: '***' if k == 'password' else v for k, v in kwargs.items()}, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'updated'}


@router.patch('/api/users/{user_id}/toggle')
async def api_toggle_user(user_id: int, request: Request, current_user: dict = Depends(require_admin)):
    """启用/禁用用户"""
    if current_user['user_id'] == user_id:
        raise HTTPException(status_code=400, detail='不能禁用自己')
    new_status = models.toggle_user(user_id)
    if new_status is None:
        raise HTTPException(status_code=404, detail='用户不存在')
    action = 'user_enable' if new_status else 'user_disable'
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action=action,
        target=str(user_id),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'toggled', 'is_active': new_status}


@router.delete('/api/users/{user_id}')
async def api_delete_user(user_id: int, request: Request, current_user: dict = Depends(require_admin)):
    if current_user['user_id'] == user_id:
        raise HTTPException(status_code=400, detail='不能删除自己')
    success = models.delete_user(user_id)
    if not success:
        raise HTTPException(status_code=404, detail='用户不存在')
    models.add_audit_log(
        user_id=current_user['user_id'],
        username=current_user['username'],
        action='user_delete',
        target=str(user_id),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'deleted'}
