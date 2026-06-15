#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI 应用入口 + 所有路由 + 页面路由
Web 管理服务器，默认端口 23455
"""
import os
import sys
import json
import logging
import socket
import configparser
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── 项目路径 ──────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from web import models
from web import config_proxy
from web.auth import create_session, destroy_session, get_session, get_current_user, require_admin, optional_current_user
from web.ws_manager import manager as ws_manager

# ── 日志 ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger('web.webapi')

# ── FastAPI 应用 ──────────────────────────────────
app = FastAPI(
    title='Live Source Manager — Web Admin',
    version='1.0.0',
    description='直播源管理器 Web 管理界面',
)

# ── 静态文件 & 模板 ────────────────────────────────
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(WEB_DIR, 'templates'))
app.mount('/static', StaticFiles(directory=os.path.join(WEB_DIR, 'static')), name='static')


# ── 启动事件 ──────────────────────────────────────
@app.on_event('startup')
async def on_startup():
    models.init_db()
    logger.info("数据库初始化完成")

    # 检测源管理器状态文件路径
    status_dir = os.path.join(PROJECT_ROOT, 'data', 'status')
    os.makedirs(status_dir, exist_ok=True)
    logger.info(f"状态文件目录: {status_dir}")


# ══════════════════════════════════════════════════
# 页面路由
# ══════════════════════════════════════════════════

def _render(request: Request, template: str, **kwargs):
    """统一渲染（注入基础变量）"""
    user = None
    session_id = request.cookies.get('session')
    if session_id:
        session = get_session(session_id)
        if session:
            user = session
    return templates.TemplateResponse(
        template,
        {
            'request': request,
            'user': user,
            **kwargs,
        }
    )


@app.get('/login', response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页 - 已登录则跳转仪表盘"""
    session_id = request.cookies.get('session')
    if session_id and get_session(session_id):
        return RedirectResponse(url='/', status_code=303)
    return _render(request, 'login.html')


@app.get('/', response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """仪表盘"""
    return _render(request, 'dashboard.html')


@app.get('/sources', response_class=HTMLResponse)
async def sources_page(request: Request):
    """源管理页"""
    return _render(request, 'sources.html')


@app.get('/sources/add', response_class=HTMLResponse)
async def source_add_page(request: Request):
    """添加源页面"""
    return _render(request, 'source_form.html', source=None)


@app.get('/sources/{source_id}/edit', response_class=HTMLResponse)
async def source_edit_page(request: Request, source_id: str):
    """编辑源页面"""
    source = get_source_by_id(source_id)
    return _render(request, 'source_form.html', source=source, source_id=source_id)


@app.get('/config', response_class=HTMLResponse)
async def config_page(request: Request):
    """配置中心页"""
    return _render(request, 'config.html')


@app.get('/test', response_class=HTMLResponse)
async def test_page(request: Request):
    """实时测试页"""
    return _render(request, 'livetest.html')


@app.get('/logs', response_class=HTMLResponse)
async def logs_page(request: Request):
    """日志查看页"""
    return _render(request, 'logs.html')


@app.get('/users', response_class=HTMLResponse)
async def users_page(request: Request):
    """用户管理页"""
    return _render(request, 'users.html')


@app.get('/audit', response_class=HTMLResponse)
async def audit_page(request: Request):
    """审计日志页"""
    return _render(request, 'audit.html')


# ══════════════════════════════════════════════════
# 认证 API
# ══════════════════════════════════════════════════

@app.post('/api/auth/login')
async def api_login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = models.verify_password(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    session_id = create_session(user)
    # 审计日志
    models.add_audit_log(
        user_id=user['id'], username=user['username'],
        action='login', target='',
        ip_address=request.client.host if request.client else '',
    )
    resp = JSONResponse({'status': 'ok', 'role': user['role']})
    resp.set_cookie(
        key='session', value=session_id,
        httponly=True, max_age=86400,
        secure=False, samesite='lax'
    )
    return resp


@app.post('/api/auth/logout')
async def api_logout(request: Request):
    session_id = request.cookies.get('session')
    if session_id:
        session = get_session(session_id)
        if session:
            models.add_audit_log(
                user_id=session['user_id'], username=session['username'],
                action='logout', target='',
                ip_address=request.client.host if request.client else '',
            )
        destroy_session(session_id)
    resp = JSONResponse({'status': 'ok'})
    resp.delete_cookie('session')
    return resp


@app.get('/api/auth/me')
async def api_auth_me(current_user: dict = Depends(get_current_user)):
    return {'username': current_user['username'], 'role': current_user['role']}


# ══════════════════════════════════════════════════
# 仪表盘 API
# ══════════════════════════════════════════════════

def _get_source_summary() -> dict:
    """获取源数据的简要统计（从 source_manager 解析结果中读取）"""
    total = 0
    valid = 0
    try:
        # 优先读取 shared state 文件
        status_file = os.path.join(PROJECT_ROOT, 'data', 'status', 'source_summary.json')
        if os.path.exists(status_file):
            with open(status_file, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {'total_sources': 0, 'valid': 0, 'invalid': 0, 'rate': '0%'}


def _get_system_info() -> dict:
    """获取系统信息"""
    import psutil
    info = {
        'memory_usage': 'N/A',
        'cpu': 'N/A',
        'ffprobe_available': False,
        'process_running': False,
    }
    try:
        mem = psutil.virtual_memory()
        info['memory_usage'] = f"{mem.percent}%"
        info['cpu'] = f"{psutil.cpu_percent(interval=0.1)}%"
    except Exception:
        pass
    # 检查 ffprobe
    ffprobe = os.popen('which ffprobe 2>/dev/null').read().strip()
    info['ffprobe_available'] = bool(ffprobe)
    # 检查采集进程
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            cmdline = proc.info.get('cmdline') or []
            if any('main.py' in c for c in cmdline):
                info['process_running'] = True
                break
    except Exception:
        pass
    return info


@app.get('/api/dashboard/stats')
async def api_dashboard_stats(current_user: dict = Depends(get_current_user)):
    return _get_source_summary()


@app.get('/api/dashboard/test-info')
async def api_dashboard_test_info(current_user: dict = Depends(get_current_user)):
    """仪表盘 - 最后测试时间信息"""
    status_file = os.path.join(PROJECT_ROOT, 'data', 'status', 'latest_test.json')
    if not os.path.exists(status_file):
        return '<div class="test-details">暂无测试记录</div>'
    try:
        with open(status_file, 'r') as f:
            data = json.load(f)
        started = data.get('started_at', '未知')
        status = data.get('status', 'idle')
        total = data.get('total', 0)
        passed = data.get('passed', 0)
        failed = data.get('failed', 0)
        pct = f'{(passed / total * 100):.1f}%' if total > 0 else '-'
        status_map = {'running': '运行中', 'completed': '已完成', 'idle': '空闲'}
        return f'''
        <div class="test-details">
            <p>最后测试时间: {started}</p>
            <p>状态: {status_map.get(status, status)}</p>
            <p>通过 <strong>{passed}</strong> / 失败 <strong>{failed}</strong> / 有效率 <strong>{pct}</strong></p>
        </div>
        '''
    except Exception:
        return '<div class="test-details">读取测试状态失败</div>'


@app.get('/api/dashboard/system')
async def api_dashboard_system(current_user: dict = Depends(require_admin)):
    return _get_system_info()


# ══════════════════════════════════════════════════
# 源管理 API
# ══════════════════════════════════════════════════

def _load_source_manager():
    """懒加载 source_manager（同步方式读取已解析的数据）"""
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from app.source_manager import SourceManager
        from app.config_manager import Config, Logger
        config = Config(config_proxy.CONFIG_PATH)
        logger_sm = Logger(config.get_logging_config()).logger
        from app.channel_rules import ChannelRules
        rules = ChannelRules(logger_sm)
        sm = SourceManager(config, logger_sm, rules)
        return sm
    except Exception as e:
        logger.warning(f"无法加载 SourceManager: {e}")
        return None


def get_source_by_id(source_id: str) -> Optional[dict]:
    """根据 ID 获取单个源信息（ID 使用 name 或 url 的 hash 标识）"""
    sm = _load_source_manager()
    if not sm:
        return None
    try:
        sources = sm.parse_all_files()
    except Exception:
        return None
    import hashlib
    for s in sources:
        sid = hashlib.md5(f"{s['name']}|{s['url']}".encode()).hexdigest()[:12]
        if sid == source_id:
            return s
    return None


@app.get('/api/sources')
async def api_list_sources(
    current_user: dict = Depends(get_current_user),
    type: str = 'all',
    page: int = 1,
    size: int = 50,
):
    sm = _load_source_manager()
    if not sm:
        return {'sources': [], 'total': 0, 'page': page}
    try:
        sources = sm.parse_all_files()
    except Exception as e:
        logger.error(f"解析源文件失败: {e}")
        return {'sources': [], 'total': 0, 'page': page}

    # 类型筛选
    if type == 'online':
        sources = [s for s in sources if s.get('source_type') == 'online']
    elif type == 'local':
        sources = [s for s in sources if s.get('source_type') == 'local']

    total = len(sources)
    # 分页
    start = (page - 1) * size
    end = start + size
    page_sources = sources[start:end]

    # 添加 id 字段
    import hashlib
    for s in page_sources:
        s['id'] = hashlib.md5(f"{s.get('name','')}|{s.get('url','')}".encode()).hexdigest()[:12]

    return {'sources': page_sources, 'total': total, 'page': page, 'size': size}


@app.get('/api/sources/{source_id}')
async def api_get_source(source_id: str, current_user: dict = Depends(get_current_user)):
    source = get_source_by_id(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="源不存在")
    return source


@app.post('/api/sources')
async def api_create_source(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    # 源添加记录到审计日志
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='source_add', target=data.get('name', ''),
        detail=json.dumps(data, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    # 当前只记录，实际 CRUD 由采集进程处理
    return {'status': 'created', 'name': data.get('name', '')}


@app.put('/api/sources/{source_id}')
async def api_update_source(source_id: str, data: dict, request: Request, current_user: dict = Depends(require_admin)):
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='source_update', target=data.get('name', '') or source_id,
        detail=json.dumps(data, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'updated'}


@app.delete('/api/sources/{source_id}')
async def api_delete_source(source_id: str, request: Request, current_user: dict = Depends(require_admin)):
    source = get_source_by_id(source_id)
    target_name = source.get('name', source_id) if source else source_id
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='source_delete', target=target_name,
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'deleted'}


# ══════════════════════════════════════════════════
# 配置中心 API
# ══════════════════════════════════════════════════

@app.get('/api/config')
async def api_get_config(current_user: dict = Depends(get_current_user)):
    return config_proxy.read_config()


@app.get('/api/config/{section}')
async def api_get_section(section: str, current_user: dict = Depends(get_current_user)):
    data = config_proxy.read_section(section)
    if not data:
        raise HTTPException(status_code=404, detail=f"配置段落 [{section}] 不存在")
    return data


@app.put('/api/config')
async def api_update_config(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    success, msg = config_proxy.write_config(data)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='config_update', target='config.ini',
        detail=json.dumps(data, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': msg}


@app.get('/api/config/fields')
async def api_get_config_fields(current_user: dict = Depends(get_current_user)):
    """返回配置字段的 schema 信息，供前端动态渲染"""
    return config_proxy.get_field_meta()


@app.post('/api/config/reload')
async def api_reload_config(request: Request, current_user: dict = Depends(require_admin)):
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='config_reload', target='config.ini',
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok'}


# ══════════════════════════════════════════════════
# 测试状态 API
# ══════════════════════════════════════════════════

@app.get('/api/test/status')
async def api_test_status(current_user: dict = Depends(get_current_user)):
    """读取最新测试状态"""
    status_file = os.path.join(PROJECT_ROOT, 'data', 'status', 'latest_test.json')
    if os.path.exists(status_file):
        try:
            with open(status_file, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'status': 'idle', 'progress': 0, 'message': '暂无测试数据'}


@app.post('/api/test/trigger')
async def api_trigger_test(current_user: dict = Depends(require_admin)):
    """触发采集任务（预留，当前仅记录）"""
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='test_trigger', target='stream_test',
        ip_address='',
    )
    return {'status': 'triggered', 'task_id': 'test_' + str(os.getpid())}


# ══════════════════════════════════════════════════
# WebSocket 端点（实时测试推送）
# ══════════════════════════════════════════════════

@app.websocket('/ws/test')
async def websocket_test_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # 保持连接，客户端可发送 ping
            data = await ws.receive_text()
            if data == 'ping':
                await ws.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await ws_manager.disconnect(ws)


# ══════════════════════════════════════════════════
# 日志 API
# ══════════════════════════════════════════════════

@app.get('/api/logs')
async def api_logs(level: str = 'INFO', tail: int = 100, page: int = 1, current_user: dict = Depends(get_current_user)):
    """读取应用日志文件，支持分页"""
    config_data = config_proxy.read_section('Logging')
    log_file = config_data.get('file', '/log/app.log')
    logs = []
    total_lines = 0
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', errors='replace') as f:
                all_lines = f.readlines()
                total_lines = len(all_lines)
                # 按级别筛选
                if level.upper() != 'ALL':
                    filtered = [l.rstrip('\n\r') for l in all_lines if level.upper() in l.upper()]
                else:
                    filtered = [l.rstrip('\n\r') for l in all_lines]
                # 取最后 tail 行
                logs = filtered[-tail:]
        except Exception as e:
            logs = [f"读取日志失败: {e}"]
    return {'logs': logs, 'total': len(logs), 'file_lines': total_lines}


@app.get('/api/logs/download')
async def api_logs_download(current_user: dict = Depends(require_admin)):
    """下载日志文件（返回 JSON 路径，实际文件通过静态路径处理）"""
    config_data = config_proxy.read_section('Logging')
    log_file = config_data.get('file', '/log/app.log')
    if os.path.exists(log_file):
        return JSONResponse({'path': log_file, 'filename': os.path.basename(log_file)})
    raise HTTPException(status_code=404, detail="日志文件不存在")


# ══════════════════════════════════════════════════
# 审计日志 API
# ══════════════════════════════════════════════════

@app.get('/api/audit')
async def api_audit(page: int = 1, size: int = 50, action: str = '', current_user: dict = Depends(require_admin)):
    return models.list_audit_logs(page, size, action_filter=action)


@app.get('/api/audit/actions')
async def api_audit_actions(current_user: dict = Depends(require_admin)):
    """返回所有出现的操作类型列表"""
    return models.list_audit_actions()


# ══════════════════════════════════════════════════
# 用户管理 API
# ══════════════════════════════════════════════════

@app.get('/api/users')
async def api_list_users(current_user: dict = Depends(require_admin)):
    return {'users': models.list_users()}


@app.post('/api/users')
async def api_create_user(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'viewer')
    display_name = data.get('display_name', '').strip()

    if not username or len(username) < 2:
        raise HTTPException(status_code=400, detail="用户名至少2个字符")
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6个字符")
    if role not in ('admin', 'viewer'):
        raise HTTPException(status_code=400, detail="角色无效")

    try:
        user_id = models.create_user(username, password, role, display_name)
    except Exception as e:
        raise HTTPException(status_code=409, detail=f"创建用户失败（可能已存在）: {e}")

    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='user_create', target=username,
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'created', 'id': user_id}


@app.put('/api/users/{user_id}')
async def api_update_user(user_id: int, data: dict, request: Request, current_user: dict = Depends(require_admin)):
    """更新用户信息（角色、显示名、密码）"""
    if current_user['user_id'] == user_id and data.get('role') and data['role'] != current_user['role']:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")
    kwargs = {}
    if 'role' in data:
        if data['role'] not in ('admin', 'viewer'):
            raise HTTPException(status_code=400, detail="角色无效")
        kwargs['role'] = data['role']
    if 'display_name' in data:
        kwargs['display_name'] = data['display_name'].strip()
    if 'password' in data and data['password']:
        if len(data['password']) < 6:
            raise HTTPException(status_code=400, detail="密码至少6个字符")
        kwargs['password'] = data['password']
    success = models.update_user(user_id, **kwargs)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='user_update', target=str(user_id),
        detail=json.dumps({k: '***' if k == 'password' else v for k, v in kwargs.items()}, ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'updated'}


@app.patch('/api/users/{user_id}/toggle')
async def api_toggle_user(user_id: int, request: Request, current_user: dict = Depends(require_admin)):
    """启用/禁用用户"""
    if current_user['user_id'] == user_id:
        raise HTTPException(status_code=400, detail="不能禁用自己")
    new_status = models.toggle_user(user_id)
    if new_status is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    action = 'user_enable' if new_status else 'user_disable'
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action=action, target=str(user_id),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'toggled', 'is_active': new_status}


@app.delete('/api/users/{user_id}')
async def api_delete_user(user_id: int, request: Request, current_user: dict = Depends(require_admin)):
    if current_user['user_id'] == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    success = models.delete_user(user_id)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='user_delete', target=str(user_id),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'deleted'}


# ══════════════════════════════════════════════════
# 端口检测
# ══════════════════════════════════════════════════

def check_port(host: str = '0.0.0.0', port: int = 23455) -> bool:
    """检查端口是否可用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


# ══════════════════════════════════════════════════
# 独立启动
# ══════════════════════════════════════════════════

def main():
    """独立入口"""
    host = os.environ.get('WEB_HOST', '0.0.0.0')
    port = int(os.environ.get('WEB_PORT', '23455'))

    if not check_port(host, port):
        print(f"错误: 端口 {port} 已被占用，无法启动 Web 服务")
        sys.exit(1)

    print(f"🌐 Web 管理界面启动: http://{host}:{port}")
    print(f"   默认管理员账户: admin / admin123")
    print(f"   默认查看者账户: viewer / viewer123")
    uvicorn.run(app, host=host, port=port, log_level='info')


if __name__ == '__main__':
    main()
