#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户认证 + session 管理 + 权限装饰器
"""
import uuid
import time
import logging
from typing import Optional, Dict
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse

from web import models

logger = logging.getLogger('web.auth')

# ── Session 存储（内存 dict） ──────────────────────
# sessions: session_id -> {user_id, username, role, created_at, last_active}
_sessions: Dict[str, Dict] = {}
SESSION_TTL = 24 * 3600  # 24 小时
IDLE_TIMEOUT = 2 * 3600  # 2 小时无操作过期


def _clean_expired():
    """清理过期 session"""
    now = time.time()
    expired = []
    for sid, data in _sessions.items():
        if now - data['created_at'] > SESSION_TTL:
            expired.append(sid)
        elif now - data['last_active'] > IDLE_TIMEOUT:
            expired.append(sid)
    for sid in expired:
        _sessions.pop(sid, None)


def create_session(user: Dict) -> str:
    """创建新 session，返回 session_id"""
    _clean_expired()
    session_id = uuid.uuid4().hex
    _sessions[session_id] = {
        'user_id': user['id'],
        'username': user['username'],
        'role': user['role'],
        'created_at': time.time(),
        'last_active': time.time(),
    }
    return session_id


def get_session(session_id: str) -> Optional[Dict]:
    """根据 session_id 获取 session 数据"""
    session = _sessions.get(session_id)
    if not session:
        return None
    now = time.time()
    if now - session['created_at'] > SESSION_TTL or now - session['last_active'] > IDLE_TIMEOUT:
        _sessions.pop(session_id, None)
        return None
    session['last_active'] = now
    return session


def destroy_session(session_id: str):
    """销毁 session"""
    _sessions.pop(session_id, None)


# ── FastAPI 依赖 ──────────────────────────────────

async def get_current_user(request: Request) -> Dict:
    """从 Cookie 中解析当前用户（FastAPI 依赖注入）"""
    session_id = request.cookies.get('session')
    if not session_id:
        raise HTTPException(status_code=401, detail="未登录")
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")
    return session


async def optional_current_user(request: Request) -> Optional[Dict]:
    """可选用户，未登录时返回 None（用于页面路由）"""
    session_id = request.cookies.get('session')
    if not session_id:
        return None
    return get_session(session_id)


async def require_admin(current_user: Dict = Depends(get_current_user)):
    """管理员权限依赖"""
    if current_user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return current_user


def login_required_page(func):
    """页面路由装饰器：未登录时重定向到 /login"""
    import functools
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        request = kwargs.get('request')
        if request:
            session_id = request.cookies.get('session')
            if not session_id or not get_session(session_id):
                return RedirectResponse(url='/login', status_code=303)
        return await func(*args, **kwargs)
    return wrapper
