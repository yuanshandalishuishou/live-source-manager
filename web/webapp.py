#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 管理服务 — 合并模块 (方案B)

合并自: auth.py, config_proxy.py, ws_manager.py, webapi.py
功能不减少，仅合并文件，去除跨文件 import
"""

# ── 第三方/标准库 import ──────────────────────
import os
import configparser
import logging
import threading
from typing import Dict, Any, Tuple
import asyncio
import json
from typing import Set
from fastapi import WebSocket, WebSocketException
import uuid
import time
from typing import Optional, Dict
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
import sys
import socket
from pathlib import Path
from typing import Optional
import uvicorn
from fastapi import FastAPI, Request, Form, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── 项目内 import ────────────────────────
from . import models

# ═══════════════════════════════════════════════════
# config.ini 安全读写代理 (原 config_proxy.py)
# ═══════════════════════════════════════════════════

logger = logging.getLogger('web.config_proxy')

# 配置文件路径（相对于项目根）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get('CONFIG_PATH', os.path.join(PROJECT_ROOT, 'config', 'config.ini'))

# 写锁（进程内锁，用于保护并发写入时的锁文件获取）
_write_lock = threading.Lock()

# 字段定义：name -> (type, default, label, help)
SECTION_SCHEMA: Dict[str, Dict[str, tuple]] = {
    'Sources': {
        'local_dirs': ('str', '/config/sources', '本地源目录', '逗号分隔'),
        'online_urls': ('textarea', '', '在线源URL列表', '每行一个URL'),
        'github_sources': ('textarea', '', 'GitHub仓库', '格式: owner/repo'),
    },
    'Network': {
        'proxy_enabled': ('bool', 'False', '启用代理', 'True/False'),
        'proxy_type': ('str', 'socks5', '代理类型', 'http/https/socks5'),
        'proxy_host': ('str', '', '代理主机'),
        'proxy_port': ('int', '1800', '代理端口'),
        'proxy_username': ('str', '', '代理用户名'),
        'proxy_password': ('str', '', '代理密码'),
        'ipv6_enabled': ('bool', 'False', '启用IPv6', ''),
    },
    'HTTPServer': {
        'enabled': ('bool', 'False', '启用HTTP'),
        'host': ('str', '0.0.0.0', '监听地址'),
        'port': ('int', '12345', '监听端口'),
        'document_root': ('str', '/www/output', '文档根目录'),
    },
    'GitHub': {
        'api_url': ('str', 'https://api.github.com', 'API地址'),
        'api_token': ('str', '', 'API Token'),
        'rate_limit': ('int', '5000', '速率限制'),
    },
    'Testing': {
        'timeout': ('int', '10', '测试超时(秒)'),
        'concurrent_threads': ('int', '40', '并发线程数'),
        'cache_ttl': ('int', '120', '缓存有效期(分)'),
        'enable_speed_test': ('bool', 'True', '启用速率测试'),
        'speed_test_duration': ('int', '6', '速率测试时长(秒)'),
    },
    'Output': {
        'filename': ('str', 'live.m3u', '输出文件名'),
        'group_by': ('str', 'category', '分组策略'),
        'include_failed': ('bool', 'False', '包含失败源'),
        'max_sources_per_channel': ('int', '8', '每频道最大源数'),
        'enable_filter': ('bool', 'False', '启用过滤'),
    },
    'Logging': {
        'level': ('str', 'INFO', '日志级别'),
        'file': ('str', '/log/app.log', '日志文件路径'),
        'max_size': ('int', '10', '最大日志大小(MB)'),
        'backup_count': ('int', '5', '备份文件数'),
    },
    'Filter': {
        'max_latency': ('int', '4000', '最大延迟(ms)'),
        'min_bitrate': ('int', '80', '最小比特率(kbps)'),
        'must_hd': ('bool', 'False', '必须高清'),
        'must_4k': ('bool', 'False', '必须4K'),
        'min_speed': ('int', '50', '最小下载速度(KB/s)'),
        'min_resolution': ('str', '360p', '最低分辨率'),
        'max_resolution': ('str', '4k', '最高分辨率'),
        'resolution_filter_mode': ('str', 'range', '分辨率筛选模式'),
    },
    'UserAgents': {
        'ua_position': ('str', 'extinf', 'UA位置'),
        'ua_enabled': ('bool', 'True', '启用UA'),
    },
}

# 敏感字段（用于审计日志脱敏）
SENSITIVE_FIELDS = {'proxy_password', 'api_token'}

# 字段类型映射
FIELD_TYPE = {'str': 'text', 'textarea': 'textarea', 'int': 'number', 'bool': 'checkbox'}
def _read_raw() -> configparser.ConfigParser:
    """读取 config.ini，返回 ConfigParser 对象"""
    cp = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        cp.read(CONFIG_PATH, encoding='utf-8')
    return cp
def read_config() -> Dict[str, Dict[str, str]]:
    """读取全量配置，返回 {section: {key: value}}
    优先使用 SQLite app_config，无数据时回退到 INI 文件"""
    # 优先使用 SQLite 数据
    try:
        sqlite_config = models.get_all_config()
        if sqlite_config:
            return sqlite_config
    except Exception:
        pass
    # 回退到 INI 文件
    cp = _read_raw()
    result = {}
    for section in cp.sections():
        result[section] = dict(cp.items(section))
    return result
def read_section(section: str) -> Dict[str, str]:
    """读取指定段配置
    优先使用 SQLite app_config，回退到 INI 文件"""
    # 优先使用 SQLite 数据
    try:
        sqlite_config = models.get_all_config()
        if sqlite_config and section in sqlite_config:
            return sqlite_config[section]
    except Exception:
        pass
    # 回退到 INI 文件
    cp = _read_raw()
    if section in cp:
        return dict(cp.items(section))
    return {}
def get_field_meta() -> Dict:
    """返回字段元信息，供前端表单渲染"""
    return SECTION_SCHEMA
def sanitize_config_data(data: Dict[str, Dict[str, str]]) -> Dict:
    """脱敏处理，用于审计日志"""
    safe = {}
    for section, fields in data.items():
        safe[section] = {}
        for key, value in fields.items():
            if key in SENSITIVE_FIELDS and value:
                safe[section][key] = '***'
            else:
                safe[section][key] = value
    return safe
def validate_and_coerce(section: str, key: str, value: str, field_def: tuple) -> Tuple[Any, str]:
    """校验并转换单个字段的值"""
    ftype, default, label, *_ = field_def
    if ftype == 'int':
        try:
            return int(value), ''
        except (ValueError, TypeError):
            return default, f"{label} 必须是整数"
    if ftype == 'bool':
        return ('True' if value and str(value).lower() in ('true', '1', 'yes', 'on') else 'False'), ''
    if ftype == 'textarea' or ftype == 'str':
        return str(value), ''
    return str(value), ''
def write_config(data: Dict[str, Dict[str, str]]) -> Tuple[bool, str]:
    """
    写入配置 — SQLite 为主，同时同步写入 config.ini 作为可读备份
    """
    config_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(config_dir, exist_ok=True)

    with _write_lock:
        try:
            # 1. 写入 SQLite（主存储）
            for section, fields in data.items():
                for key, value in fields.items():
                    schema = SECTION_SCHEMA.get(section, {})
                    if key in schema:
                        _, err = validate_and_coerce(section, key, value, schema[key])
                        if err:
                            return False, f"[{section}] {key}: {err}"
                    config_key = f"{section}.{key}"
                    models.set_app_config(config_key, str(value))

            # 2. 同步写入 INI（可读备份）
            cp = _read_raw()
            for section, fields in data.items():
                if not cp.has_section(section):
                    cp.add_section(section)
                for key, value in fields.items():
                    cp.set(section, key, str(value))

            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                cp.write(f)

            return True, "配置已保存"

        except PermissionError as e:
            return False, f"权限不足: {e}"
        except Exception as e:
            return False, f"写入失败: {e}"

# ═══════════════════════════════════════════════════
# WebSocket 连接管理 (原 ws_manager.py)
# ═══════════════════════════════════════════════════

logger = logging.getLogger('web.ws_manager')

MAX_CONNECTIONS = 50  # 单实例最大连接数
class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self, max_connections: int = MAX_CONNECTIONS):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.max_connections = max_connections

    async def connect(self, ws: WebSocket):
        """连接前检查上限，超出拒绝"""
        async with self._lock:
            if len(self._connections) >= self.max_connections:
                await ws.close(code=1013, reason="too_many_connections")
                logger.warning(f"WebSocket 连接已达上限 ({self.max_connections})，拒绝连接")
                return False
            self._connections.add(ws)
        logger.info(f"WebSocket 客户端已连接 (当前: {len(self._connections)})")
        return True

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.discard(ws)
        logger.info(f"WebSocket 客户端断开 (剩余: {len(self._connections)})")

    async def broadcast(self, message: dict):
        """向所有已连接客户端广播 JSON 消息"""
        dead = set()
        async with self._lock:
            for ws in self._connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)
# 全局单例
manager = ConnectionManager()

# ═══════════════════════════════════════════════════
# 用户认证 + Session + CSRF — 委托给 auth 模块
# ═══════════════════════════════════════════════════
# CSRF token 函数在 webapp 模块内有别名引用，通过模块级重定向确保
# 所有代码（含 webapp.csrf_middleware）使用 auth 模块的同一份 _csrf_tokens

logger = logging.getLogger('web.auth')

SESSION_TTL = 86400
IDLE_TIMEOUT = 7200
CSRF_TTL = 3600

# 将 CSRF 函数和 Session 函数委托给 auth 模块
from .auth import create_session, get_session, destroy_session, get_current_user, optional_current_user, require_admin, login_required_page, _get_csrf_token, verify_csrf_token, _csrf_tokens

# ═══════════════════════════════════════════════════
# FastAPI 应用入口 + 路由 (原 webapi.py)
# ═══════════════════════════════════════════════════

sys.path.insert(0, PROJECT_ROOT)

# ── 日志 ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger('web.webapp')

# ── lifespan（替代弃用的 on_event）先于 app 定义 ──
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """应用生命周期 startup + shutdown"""
    # ── startup ────────────────────────────────
    import secrets
    import string
    admin_pw = os.environ.get('WEB_ADMIN_PASSWORD') or \
        ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    viewer_pw = os.environ.get('WEB_VIEWER_PASSWORD') or \
        ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    await asyncio.to_thread(models.init_db, admin_password=admin_pw, viewer_password=viewer_pw)
    await asyncio.to_thread(models.cleanup_audit_logs, max_days=90)
    logger.info("数据库初始化完成，清理90天前审计日志")
    if not os.environ.get('WEB_ADMIN_PASSWORD'):
        logger.warning(f"⚠️  默认管理员密码: {admin_pw}（请通过环境变量 WEB_ADMIN_PASSWORD 设置）")
    if not os.environ.get('WEB_VIEWER_PASSWORD'):
        logger.info(f"查看者密码: {viewer_pw}（可通过环境变量 WEB_VIEWER_PASSWORD 设置）")

    # ── 首次运行初始化 ────────────────────────
    from app.config_manager import Config as _Config

    # 1. 检查 config.ini，不存在则创建默认
    if not os.path.exists(CONFIG_PATH):
        _Config.create_default_at(CONFIG_PATH)
        logger.info(f"已创建默认配置文件: {CONFIG_PATH}")

    # 2. 检查 SQLite app_config 是否有数据，无则从 INI 导入
    if not models.has_app_config_data():
        count = models.import_from_ini_file(CONFIG_PATH)
        logger.info(f"已从 config.ini 导入 {count} 条配置到 SQLite")

    # 3. 检查 channel_rules.yml，不存在则创建默认
    CHANNEL_RULES_PATH = os.path.join(PROJECT_ROOT, 'config', 'channel_rules.yml')
    if not os.path.exists(CHANNEL_RULES_PATH):
        os.makedirs(os.path.dirname(CHANNEL_RULES_PATH), exist_ok=True)
        with open(CHANNEL_RULES_PATH, 'w', encoding='utf-8') as f:
            f.write("# 默认频道分类规则\n# 请根据实际需求修改\n")
        logger.info(f"已创建默认频道规则文件: {CHANNEL_RULES_PATH}")

    status_dir = os.path.join(PROJECT_ROOT, 'data', 'status')
    os.makedirs(status_dir, exist_ok=True)
    logger.info(f"状态文件目录: {status_dir}")

    # ── shutdown（资源清理） ────────────────────
    logger.info("Web 服务关闭，清理资源...")


# ── FastAPI 应用实例 ──────────────────────────────
app = FastAPI(
    title='Live Source Manager - Web Admin',
    version='1.0.0',
    description='直播源管理器 Web 管理界面',
    lifespan=lifespan,
)




# ── CSRF 中间件 ──────────────────────────────────
# 跳过 GET/HEAD/OPTIONS + 登录路径和 WebSocket
# 所有写操作必须在 header 中携带 X-CSRF-Token

CSRF_EXEMPT_PATHS = {'/api/auth/login', '/api/auth/logout', '/login', '/health'}

@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        path = request.url.path
        if path not in CSRF_EXEMPT_PATHS and not path.startswith('/ws/'):
            session_id = request.cookies.get('session')
            token = request.headers.get('x-csrf-token', '')
            if not session_id or not token:
                return JSONResponse(
                    status_code=403,
                    content={'detail': 'CSRF token missing（缺少安全令牌）'}
                )
            if not verify_csrf_token(session_id, token):
                return JSONResponse(
                    status_code=403,
                    content={'detail': 'CSRF token invalid（安全令牌无效）'}
                )
    return await call_next(request)


# ── 静态文件 & 模板 ────────────────────────────────
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
# 禁用模板缓存，避免 Jinja2 LRU 因不可哈希模板上下文（含 dict user）抛出 TypeError
templates = Jinja2Templates(directory=os.path.join(WEB_DIR, 'templates'))
templates.env.auto_reload = True
app.mount('/static', StaticFiles(directory=os.path.join(WEB_DIR, 'static')), name='static')
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
            # 使用不可变tuple作为user上下文（避免Jinja2缓存unhashable dict报错）
            user = {
                'username': session['username'],
                'role': session['role'],
                'user_id': session['user_id'],
            }
    # Starlette 新版本中 TemplateResponse 签名改为 (request, name, context, ...)
    # 使用关键字参数避免位置错位
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
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
async def dashboard_page(request: Request, current_user: dict = Depends(get_current_user)):
    """仪表盘"""
    return _render(request, 'dashboard.html')
@app.get('/sources', response_class=HTMLResponse)
async def sources_page(request: Request, current_user: dict = Depends(get_current_user)):
    """源管理页"""
    return _render(request, 'sources.html')
@app.get('/sources/add', response_class=HTMLResponse)
async def source_add_page(request: Request, current_user: dict = Depends(get_current_user)):
    """添加源页面"""
    return _render(request, 'source_form.html', source=None)
@app.get('/sources/{source_id}/edit', response_class=HTMLResponse)
async def source_edit_page(request: Request, source_id: str, current_user: dict = Depends(get_current_user)):
    """编辑源页面"""
    source = get_source_by_id(source_id)
    return _render(request, 'source_form.html', source=source, source_id=source_id)
@app.get('/config', response_class=HTMLResponse)
async def config_page(request: Request, current_user: dict = Depends(get_current_user)):
    """配置中心页"""
    return _render(request, 'config.html')
@app.get('/test', response_class=HTMLResponse)
async def test_page(request: Request, current_user: dict = Depends(get_current_user)):
    """实时测试页"""
    return _render(request, 'livetest.html')
@app.get('/logs', response_class=HTMLResponse)
async def logs_page(request: Request, current_user: dict = Depends(get_current_user)):
    """日志查看页"""
    return _render(request, 'logs.html')
@app.get('/users', response_class=HTMLResponse)
async def users_page(request: Request, current_user: dict = Depends(require_admin)):
    """用户管理页"""
    return _render(request, 'users.html')
@app.get('/audit', response_class=HTMLResponse)
async def audit_page(request: Request, current_user: dict = Depends(require_admin)):
    """审计日志页"""
    return _render(request, 'audit.html')
# ══════════════════════════════════════════════════
# 认证 API
# ══════════════════════════════════════════════════

@app.post('/api/auth/login')
async def api_login(request: Request, username: str = Form(...), password: str = Form(...)):
    import asyncio
    # bcrypt 是同步且CPU密集的，用 asyncio.to_thread 避免阻塞事件循环
    user = await asyncio.to_thread(models.verify_password, username, password)
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
@app.get('/api/auth/csrf-token')
async def api_csrf_token(current_user: dict = Depends(get_current_user)):
    """获取 CSRF token——前端所有写操作必须在 X-CSRF-Token header 中带上此值"""
    token = _get_csrf_token(current_user['session_id'])
    return {'csrf_token': token}
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
    import shutil
    info['ffprobe_available'] = shutil.which('ffprobe') is not None
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
        from app.source_manager import SourceManager
        from app.config_manager import Config, Logger
        config = Config(CONFIG_PATH)
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
    search: str = '',
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

    # 搜索筛选
    if search:
        search_lower = search.lower()
        sources = [s for s in sources
                   if search_lower in s.get('name', '').lower()
                   or search_lower in s.get('url', '').lower()
                   or search_lower in s.get('group', '').lower()]

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
    # 当前为展示模式 —— 源文件由采集进程周期性处理
    # 用户可通过本地文件系统或 GitHub 仓库管理源
    return {'status': 'created', 'name': data.get('name', ''), 'note': '源已记录。展示模式下，实际源文件由采集进程管理。'}
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
    return read_config()
@app.get('/api/config/fields')
async def api_get_config_fields(current_user: dict = Depends(get_current_user)):
    """返回配置字段的 schema 信息，供前端动态渲染"""
    return get_field_meta()
@app.get('/api/config/{section}')
async def api_get_section(section: str, current_user: dict = Depends(get_current_user)):
    data = read_section(section)
    if not data:
        raise HTTPException(status_code=404, detail=f"配置段落 [{section}] 不存在")
    return data
@app.put('/api/config')
async def api_update_config(data: dict, request: Request, current_user: dict = Depends(require_admin)):
    success, msg = write_config(data)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    models.add_audit_log(
        user_id=current_user['user_id'], username=current_user['username'],
        action='config_update', target='config.ini',
        detail=json.dumps(sanitize_config_data(data), ensure_ascii=False),
        ip_address=request.client.host if request.client else '',
    )
    return {'status': 'ok', 'message': msg}
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
    # 先 accept 再检查认证，否则无法收发消息
    await ws.accept()
    # Cookie 认证（WebSocket 握手阶段检查 session）
    session_id = ws.cookies.get('session')
    if not session_id or not get_session(session_id):
        await ws.close(code=4001, reason="unauthorized")
        return
    
    connected = await manager.connect(ws)
    if not connected:
        # 连接数已达上限
        return
    try:
        while True:
            data = await ws.receive_text()
            if data == 'ping':
                await ws.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(ws)
# ══════════════════════════════════════════════════
# 日志 API
# ══════════════════════════════════════════════════

@app.get('/api/logs')
async def api_logs(level: str = 'INFO', tail: int = 100, page: int = 1, current_user: dict = Depends(get_current_user)):
    """读取应用日志文件，支持分页"""
    config_data = read_section('Logging')
    log_file = config_data.get('file', '/log/app.log')
    logs = []
    total_lines = 0
    if os.path.exists(log_file):
        try:
            total_lines = 0
            filtered = []
            # 使用从尾读取策略，避免 OOM
            block_size = 8192
            with open(log_file, 'rb') as f:
                f.seek(0, 2)  # 尾端
                file_size = f.tell()
                total_lines = 0
                # 先统计总行数
                f.seek(0)
                for _ in f:
                    total_lines += 1
                # 从尾端读取最后 block_size*4 字节（足够取 tail 行）
                read_size = min(file_size, block_size * (tail // 10 + 4))
                f.seek(file_size - read_size)
                tail_bytes = f.read(read_size)
                # 解码
                tail_text = tail_bytes.decode('utf-8', errors='replace')
                all_tail_lines = tail_text.split('\n')
                # 按级别筛选
                if level.upper() != 'ALL':
                    filtered = [l.rstrip('\n\r') for l in all_tail_lines if level.upper() in l.upper()]
                else:
                    filtered = [l.rstrip('\n\r') for l in all_tail_lines]
                # 取最后 tail 行
                logs = filtered[-tail:]
        except Exception as e:
            logs = [f"读取日志失败: {e}"]
    return {'logs': logs, 'total': len(logs), 'file_lines': total_lines}
@app.get('/api/logs/download')
async def api_logs_download(current_user: dict = Depends(require_admin)):
    """下载日志文件（返回 JSON 路径，实际文件通过静态路径处理）"""
    config_data = read_section('Logging')
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
    print(f"   首次启动密码由 WEB_ADMIN_PASSWORD / WEB_VIEWER_PASSWORD 环境变量设置")
    print(f"   未设置时自动生成随机密码，请查看启动日志")
    uvicorn.run(app, host=host, port=port, log_level='info')
if __name__ == '__main__':
    main()

