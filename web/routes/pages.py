#!/usr/bin/env python3
"""页面路由 — HTML 页面"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web.core import (
    _render,
    get_current_user,
    get_session,
    get_source_by_id,
    require_admin,
)

router = APIRouter()


@router.get('/login', response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页 - 已登录则跳转仪表盘"""
    session_id = request.cookies.get('session')
    if session_id and get_session(session_id):
        return RedirectResponse(url='/', status_code=303)
    return _render(request, 'login.html')


@router.get('/', response_class=HTMLResponse)
async def root_page(request: Request):
    """根路由 - 未登录显示登录页，已登录跳转仪表盘"""
    session_id = request.cookies.get('session')
    if session_id:
        session = get_session(session_id)
        if session:
            return _render(request, 'dashboard.html')
    return RedirectResponse(url='/login', status_code=303)


@router.get('/sources', response_class=HTMLResponse)
async def sources_page(request: Request, current_user: dict = Depends(get_current_user)):
    """源管理页"""
    return _render(request, 'sources.html')


@router.get('/sources/add', response_class=HTMLResponse)
async def source_add_page(request: Request, current_user: dict = Depends(get_current_user)):
    """添加源页面"""
    return _render(request, 'source_form.html', source=None)


@router.get('/sources/{source_id}/edit', response_class=HTMLResponse)
async def source_edit_page(request: Request, source_id: str, current_user: dict = Depends(get_current_user)):
    """编辑源页面"""
    source = get_source_by_id(source_id)
    return _render(request, 'source_form.html', source=source, source_id=source_id)


@router.get('/config', response_class=HTMLResponse)
async def config_page(request: Request, current_user: dict = Depends(get_current_user)):
    """配置中心页"""
    return _render(request, 'config.html')


@router.get('/test', response_class=HTMLResponse)
async def test_page(request: Request, current_user: dict = Depends(get_current_user)):
    """实时测试页"""
    return _render(request, 'livetest.html')


@router.get('/logs', response_class=HTMLResponse)
async def logs_page(request: Request, current_user: dict = Depends(get_current_user)):
    """日志查看页"""
    return _render(request, 'logs.html')


@router.get('/users', response_class=HTMLResponse)
async def users_page(request: Request, current_user: dict = Depends(require_admin)):
    """用户管理页"""
    return _render(request, 'users.html')


@router.get('/rules', response_class=HTMLResponse)
async def rules_page(request: Request, current_user: dict = Depends(get_current_user)):
    """分类规则管理页"""
    return _render(request, 'rules.html')
