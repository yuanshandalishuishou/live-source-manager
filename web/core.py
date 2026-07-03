#!/usr/bin/env python3
"""
web.core — 共享基础设施

从 webapp.py 提取的公共组件：
- app 实例、lifespan、中间件、异常处理器
- 静态文件和模板配置
- 配置代理函数 (config.ini 安全读写)
- Session / CSRF / RBAC
- WebSocket ConnectionManager
- 认证辅助函数
- _render / _get_source_summary / _get_system_info 等辅助函数
- SourceManager 懒加载与缓存
"""

# ── 第三方/标准库 import ──────────────────────
import asyncio
import configparser
import hashlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── 项目内 import ────────────────────────
from . import models

# ═══════════════════════════════════════════════════
# 常量 & 全局变量
# ═══════════════════════════════════════════════════

logger = logging.getLogger('web.webapp')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get('CONFIG_PATH', os.path.join(PROJECT_ROOT, 'config', 'config.ini'))

_write_lock = threading.Lock()

# ── conftest 兼容访问器 ──────────────────────────
# conftest.py 会覆写 web.webapp.CONFIG_PATH 和 web.webapp.CSRF_EXEMPT_PATHS，
# core 中的函数/中间件通过以下访问器在运行时读取覆写后的值。


def _get_config_path() -> str:
    """获取 CONFIG_PATH，优先读取 webapp 模块的覆写值（conftest 兼容）。"""
    _ww = sys.modules.get('web.webapp')
    if _ww is not None:
        return getattr(_ww, 'CONFIG_PATH', CONFIG_PATH)
    return CONFIG_PATH


def _get_csrf_exempt_paths():
    """获取 CSRF_EXEMPT_PATHS，优先读取 webapp 模块的覆写值。"""
    _ww = sys.modules.get('web.webapp')
    if _ww is not None:
        return getattr(_ww, 'CSRF_EXEMPT_PATHS', CSRF_EXEMPT_PATHS)
    return CSRF_EXEMPT_PATHS


# ═══════════════════════════════════════════════════
# config.ini 安全读写代理 (原 config_proxy.py)
# ═══════════════════════════════════════════════════

# 字段定义：name -> (type, default, label, help)
# 🔧 维护提示：此处的默认值需与 app/config_utils.py Config._DEFAULT_VALUES 保持一致。
# 修改任一位置请同步修改另一处。
SECTION_SCHEMA: dict[str, dict[str, tuple]] = {
    'Sources': {
        'local_dirs': ('str', './config/sources', '本地源目录', '逗号分隔'),
        'online_urls': (
            'textarea',
            'https://live.zbds.org/tv/iptv4.m3u\n'
            'https://myernestlu.github.io/zby.txt\n'
            'https://raw.githubusercontent.com/Rivens7/Livelist/main/CCTV.m3u\n'
            'https://raw.githubusercontent.com/Rivens7/Livelist/main/CNTV.m3u\n'
            'https://raw.githubusercontent.com/Rivens7/Livelist/main/IPTV.m3u\n'
            'https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/result.m3u\n'
            'https://raw.githubusercontent.com/suxuang/myIPTV/refs/heads/main/ipv4.m3u\n'
            'https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8\n'
            'https://raw.githubusercontent.com/zwc456baby/iptv_alive/refs/heads/master/live.m3u\n'
            'https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u\n'
            'https://raw.githubusercontent.com/vamoschuck/TV/main/M3U\n'
            'https://raw.githubusercontent.com/BigBigGrandG/IPTV-URL/release/Gather.m3u\n'
            'https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u\n'
            'https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u\n'
            'https://raw.githubusercontent.com/huang770101/my-iptv/main/IPTV-ipv4.m3u\n'
            'https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u\n'
            'https://live.fanmingming.cn/tv/m3u/ipv6.m3u\n'
            'https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u\n'
            'https://iptv-org.github.io/iptv/countries/tw.m3u\n'
            'https://iptv-org.github.io/iptv/index.m3u',
            '在线源URL列表',
            '每行一个URL',
        ),
        'github_sources': (
            'textarea',
            'wcb1969/iptv/main\n'
            'joevess/IPTV/main\n'
            'suxuang/myIPTV/main\n'
            'YueChan/Live\n'
            'YanG-1989/m3u\n'
            'qwerttvv/Beijing-IPTV\n'
            'joevess/IPTV\n'
            'cymz6/AutoIPTV-Hotel\n'
            'Rivens7/Livelist',
            'GitHub仓库',
            '格式: owner/repo',
        ),
    },
    'Network': {
        'proxy_enabled': ('bool', 'False', '启用代理', 'True/False'),
        'proxy_type': ('str', 'socks5', '代理类型', 'http/https/socks5'),
        'proxy_host': ('str', '192.168.1.46', '代理主机'),
        'proxy_port': ('int', '1800', '代理端口'),
        'proxy_username': ('str', '', '代理用户名'),
        'proxy_password': ('str', '', '代理密码'),
        'github_mirror': ('str', 'https://ghproxy.com/', 'GitHub镜像站', '用于 mirror 下载方式的代理网站URL'),
        'ipv6_enabled': ('bool', 'True', '启用IPv6', ''),
    },
    'HTTPServer': {
        'enabled': ('bool', 'True', '启用HTTP'),
        'host': ('str', '0.0.0.0', '监听地址'),
        'fileshare_port': ('int', '12345', '文件共享端口'),
        'manager_port': ('int', '23456', '管理端口'),
        'document_root': ('str', './www/output', '文档根目录'),
    },
    'GitHub': {
        'api_url': ('str', 'https://api.github.com', 'API地址', 'GitHub API 基地址，一般无需修改'),
        'api_token': (
            'str',
            '',
            'API Token',
            'GitHub Personal Access Token（无需任何权限，仅用于提升 API 速率限制至 5000次/时）。前往 GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token 生成',
        ),
        'rate_limit': ('int', '5000', '速率限制', '每小时最大 API 请求次数（有 Token: 5000，无 Token: 60）'),
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
        'file': ('str', './log/app.log', '日志文件路径'),
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
        'ua_enabled': ('bool', 'False', '启用UA'),
    },
}

# 敏感字段（用于审计日志脱敏）
SENSITIVE_FIELDS = {'proxy_password', 'api_token'}

# 字段类型映射
FIELD_TYPE = {'str': 'text', 'textarea': 'textarea', 'int': 'number', 'bool': 'checkbox'}


def _read_raw() -> configparser.ConfigParser:
    """读取 config.ini，返回 ConfigParser 对象"""
    cp = configparser.ConfigParser()
    config_path = _get_config_path()
    if os.path.exists(config_path):
        cp.read(config_path, encoding='utf-8')
    return cp


def read_config() -> dict[str, dict[str, str]]:
    """读取全量配置，返回 {section: {key: value}}
    优先使用 SQLite app_config，无数据时回退到 INI 文件"""
    # 优先使用 SQLite 数据
    try:
        sqlite_config = models.get_all_config()
        if sqlite_config:
            return sqlite_config
    except Exception as e:
        logger.warning(f'read_config(): SQLite 读取失败, 回退至 INI: {e}')
    # 回退到 INI 文件
    cp = _read_raw()
    result = {}
    for section in cp.sections():
        result[section] = dict(cp.items(section))
    return result


def read_section(section: str) -> dict[str, str]:
    """读取指定段配置 — 委托 read_config()，复用 SQLite→INI 回退逻辑。"""
    return read_config().get(section, {})


def get_field_meta() -> dict:
    """返回字段元信息，供前端表单渲染"""
    return SECTION_SCHEMA


def sanitize_config_data(data: dict[str, dict[str, str]]) -> dict:
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


def validate_and_coerce(section: str, key: str, value: str, field_def: tuple) -> tuple[Any, str]:
    """校验并转换单个字段的值"""
    ftype, default, label, *_ = field_def
    if ftype == 'int':
        try:
            return int(value), ''
        except (ValueError, TypeError):
            return default, f'{label} 必须是整数'
    if ftype == 'bool':
        return ('True' if value and str(value).lower() in ('true', '1', 'yes', 'on') else 'False'), ''
    if ftype == 'textarea' or ftype == 'str':
        return str(value), ''
    return str(value), ''


def _write_config_to_sqlite(data: dict[str, dict[str, str]]) -> str | None:
    """仅写入 SQLite，返回错误信息或 None"""
    for section, fields in data.items():
        for key, value in fields.items():
            schema = SECTION_SCHEMA.get(section, {})
            if key in schema:
                _, err = validate_and_coerce(section, key, value, schema[key])
                if err:
                    return f'[{section}] {key}: {err}'
            config_key = f'{section}.{key}'
            models.set_app_config(config_key, str(value))
    return None


def _write_config_to_ini(data: dict[str, dict[str, str]]) -> str | None:
    """仅写入 INI 备份，返回错误信息或 None"""
    try:
        cp = _read_raw()
        for section, fields in data.items():
            if not cp.has_section(section):
                cp.add_section(section)
            for key, value in fields.items():
                cp.set(section, key, str(value))
        config_path = _get_config_path()
        with open(config_path, 'w', encoding='utf-8') as f:
            cp.write(f)
    except PermissionError as e:
        return f'权限不足: {e}'
    except Exception as e:
        return f'INI 写入失败: {e}'
    return None


def write_config(data: dict[str, dict[str, str]]) -> tuple[bool, str]:
    """
    写入配置 — 编排 SQLite 主存储写入 + INI 备份写入
    拆分为 _write_config_to_sqlite 和 _write_config_to_ini（纪枢 F-9）
    """
    config_path = _get_config_path()
    config_dir = os.path.dirname(config_path)
    os.makedirs(config_dir, exist_ok=True)

    with _write_lock:
        err = _write_config_to_sqlite(data)
        if err:
            return False, err

        err = _write_config_to_ini(data)
        if err:
            return False, err

        return True, '配置已保存'


# ═══════════════════════════════════════════════════
# WebSocket 连接管理 (原 ws_manager.py)
# ═══════════════════════════════════════════════════

MAX_CONNECTIONS = 50  # 单实例最大连接数


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self, max_connections: int = MAX_CONNECTIONS):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.max_connections = max_connections

    async def connect(self, ws: WebSocket):
        """连接前检查上限，超出拒绝"""
        async with self._lock:
            if len(self._connections) >= self.max_connections:
                await ws.close(code=1013, reason='too_many_connections')
                logger.warning(f'WebSocket 连接已达上限 ({self.max_connections})，拒绝连接')
                return False
            self._connections.add(ws)
        logger.info(f'WebSocket 客户端已连接 (当前: {len(self._connections)})')
        return True

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.discard(ws)
        logger.info(f'WebSocket 客户端断开 (剩余: {len(self._connections)})')

    async def broadcast(self, message: dict):
        """向所有已连接客户端广播 JSON 消息

        修复 P3-新-3: 先快照 connections 副本，在锁外逐个发送 send_json，
        避免长时间持锁阻塞 connect/disconnect。
        """
        async with self._lock:
            conns = self._connections.copy()
        dead = set()
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
                logger.debug('WebSocket 发送消息失败，标记为断开连接')
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


# 全局单例
manager = ConnectionManager()


# ═══════════════════════════════════════════════════
# Session / CSRF / RBAC (原 auth.py，已合并至此文件)
# ═══════════════════════════════════════════════════

# ── session 存储 ──
_auth_sessions: dict[str, dict] = {}  # session_id -> session data
SESSION_TTL = 24 * 3600
IDLE_TIMEOUT = 2 * 3600
CSRF_TTL = 1 * 3600


def _clean_expired():
    """清理过期 session"""
    now = time.time()
    expired = [
        sid
        for sid, data in _auth_sessions.items()
        if now - data['created_at'] > SESSION_TTL or now - data['last_active'] > IDLE_TIMEOUT
    ]
    for sid in expired:
        _auth_sessions.pop(sid, None)


def create_session(user: dict) -> str:
    """创建新 session，返回 session_id"""
    _clean_expired()
    session_id = uuid.uuid4().hex
    _auth_sessions[session_id] = {
        'user_id': user['id'],
        'username': user['username'],
        'role': user['role'],
        'created_at': time.time(),
        'last_active': time.time(),
    }
    try:
        conn = models.get_conn()
        now = time.time()
        conn.execute(
            'INSERT OR REPLACE INTO sessions (id, user_id, username, role, created_at, last_active) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, user['id'], user['username'], user['role'], now, now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f'Failed to persist session to SQLite: {e}')
    return session_id


def get_session(session_id: str) -> dict | None:
    """根据 session_id 获取 session 数据（内存优先，SQLite 回退）"""
    session = _auth_sessions.get(session_id)
    now = time.time()
    if session:
        if now - session['created_at'] > SESSION_TTL or now - session['last_active'] > IDLE_TIMEOUT:
            _auth_sessions.pop(session_id, None)
            return None
        session['last_active'] = now
        return session
    try:
        db_session = models.get_session_db(session_id)
        if db_session:
            created_at = db_session['created_at']
            if isinstance(created_at, str):
                import datetime as _dt

                created_at = _dt.datetime.fromisoformat(created_at).timestamp()
            last_active = db_session.get('last_active', created_at)
            if isinstance(last_active, str):
                import datetime as _dt

                last_active = _dt.datetime.fromisoformat(last_active).timestamp()
            if now - created_at > SESSION_TTL or now - last_active > IDLE_TIMEOUT:
                models.destroy_session_db(session_id)
                return None
            session = {
                'user_id': db_session['user_id'],
                'username': db_session['username'],
                'role': db_session['role'],
                'created_at': created_at,
                'last_active': now,
            }
            _auth_sessions[session_id] = session
            models.update_session_activity_db(session_id, now)
            return session
    except Exception as e:
        logger.warning(f'Failed to load session from SQLite: {e}')
    return None


def destroy_session(session_id: str):
    """销毁 session（内存和 SQLite）"""
    _auth_sessions.pop(session_id, None)
    _auth_csrf_tokens.pop(session_id, None)
    try:
        models.destroy_session_db(session_id)
    except Exception as e:
        logger.warning(f'Failed to destroy session in SQLite: {e}')


async def get_current_user(request: Request) -> dict:
    """从 Cookie 中解析当前用户（FastAPI 依赖注入）"""
    session_id = request.cookies.get('session')
    if not session_id:
        raise HTTPException(status_code=401, detail='未登录')
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail='会话已过期，请重新登录')
    session['session_id'] = session_id
    return session


async def require_admin(current_user: dict = Depends(get_current_user)):
    """管理员权限依赖"""
    if current_user['role'] != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可执行此操作')
    return current_user


# ── CSRF 令牌管理 ────────────────────────────

_auth_csrf_tokens: dict[str, tuple] = {}
CSRF_EXEMPT_PATHS = frozenset({'/api/auth/login', '/api/auth/logout', '/login'})


def _get_csrf_token(session_id: str) -> str:
    """生成并存储 CSRF token（1小时复用）"""
    now = time.time()
    stored = _auth_csrf_tokens.get(session_id)
    if stored:
        token, expires = stored
        if now < expires:
            return token
    token = hashlib.sha256(f'{session_id}:{uuid.uuid4().hex}'.encode()).hexdigest()
    _auth_csrf_tokens[session_id] = (token, now + CSRF_TTL)
    return token


def verify_csrf_token(session_id: str, token: str) -> bool:
    """验证 CSRF token"""
    stored = _auth_csrf_tokens.get(session_id)
    if not stored:
        return False
    stored_token, expires = stored
    if time.time() > expires:
        _auth_csrf_tokens.pop(session_id, None)
        return False
    return stored_token == token


# ═══════════════════════════════════════════════════
# 加密密钥状态标记
# ═══════════════════════════════════════════════════

CONFIG_KEY_IS_MANUAL = True


# ═══════════════════════════════════════════════════
# FastAPI 应用实例 + lifespan + 中间件
# ═══════════════════════════════════════════════════

sys.path.insert(0, PROJECT_ROOT)

# ── 日志 ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """应用生命周期 startup + shutdown"""
    # ── startup ────────────────────────────────
    admin_pw = os.environ.get('WEB_ADMIN_PASSWORD') or 'admin123'
    await asyncio.to_thread(models.init_db, admin_password=admin_pw)
    await asyncio.to_thread(models.cleanup_expired_sessions)
    await asyncio.to_thread(models.cleanup_audit_logs, max_days=90)
    logger.info('数据库初始化完成，已清除过期Session，清理90天前审计日志')
    if not os.environ.get('WEB_ADMIN_PASSWORD'):
        logger.warning(f'⚠️  默认管理员密码: {admin_pw}（请通过环境变量 WEB_ADMIN_PASSWORD 设置）')

    # ═══════════════════════════════════════════════════════
    # 加密密钥初始化（必须在 SQLite导入之前，确保加密体系就绪）
    # ═══════════════════════════════════════════════════════
    from web import crypto_utils

    crypto_utils.ensure_key_initialized()
    # 更新模块级标记供 login API 和 encrypt-key-status API 使用
    global CONFIG_KEY_IS_MANUAL
    CONFIG_KEY_IS_MANUAL = crypto_utils.is_custom_key()
    logger.info(f'加密密钥初始状态: {"自定义" if CONFIG_KEY_IS_MANUAL else "自动生成"}')

    # ── 首次运行初始化 ────────────────────────
    # 策略：SQLite 为主存储，config.ini 仅作为向后兼容的导入源
    # 不再主动创建 config.ini（远山总要求完全不用 config.ini）

    config_path = _get_config_path()

    # 1. 检查 SQLite app_config 是否有数据
    if not models.has_app_config_data():
        # 1a. 若存在旧的 config.ini，从 INI 导入（向后兼容）
        if os.path.exists(config_path):
            count = models.import_from_ini_file(config_path)
            logger.info(f'已从 config.ini 导入 {count} 条配置到 SQLite')
        else:
            # 1b. 无 INI 也无 SQLite → 写入代码默认值
            seeded = models.seed_app_config_defaults()
            logger.info(f'SQLite app_config 已写入 {seeded} 条默认配置')
    else:
        logger.info('SQLite app_config 已有数据')

    # 2. 检查 channel_rules.yml，不存在则创建默认
    CHANNEL_RULES_PATH = os.path.join(PROJECT_ROOT, 'config', 'channel_rules.yml')
    if not os.path.exists(CHANNEL_RULES_PATH):
        os.makedirs(os.path.dirname(CHANNEL_RULES_PATH), exist_ok=True)
        with open(CHANNEL_RULES_PATH, 'w', encoding='utf-8') as f:
            f.write("""# 默认频道分类规则
# 请根据实际需求修改
# ── 命名规则 ──────────────────────────────────
# category_keywords:
#   category_name: [关键词列表]

category_keywords:
  央视频道:
    - CCTV
  卫视频道:
    - 卫视
  体育频道:
    - 体育
    - NBA
    - CBA
    - 直播
    - 赛事
  新闻频道:
    - 新闻
    - NEWS
    - CNC
  影视频道:
    - 电影
    - 电视剧
    - 影院
    - 影视
  少儿频道:
    - 少儿
    - 卡通
    - 动画
    - kids
    - Kids
  音乐频道:
    - 音乐
    - MV
  纪实频道:
    - 纪实
    - 纪录片
    - 探索
  财经频道:
    - 财经
    - 金融
  教育频道:
    - 教育
    - 学堂
  生活频道:
    - 生活
    - 美食
    - 旅游
    - 健康
  收音机:
    - 广播
    - FM
    - 交通广播
  港澳台:
    - 香港
    - 澳门
    - 台湾
    - TVB
    - 凤凰
""")
        logger.info(f'已创建默认频道规则文件: {CHANNEL_RULES_PATH}')

    status_dir = os.path.join(PROJECT_ROOT, 'data', 'status')
    os.makedirs(status_dir, exist_ok=True)
    logger.info(f'状态文件目录: {status_dir}')

    # ── yield: 进入运行状态 ───────────────────────
    yield

    # ── shutdown（资源清理） ────────────────────
    logger.info('Web 服务关闭，清理资源...')


# ── FastAPI 应用实例 ──────────────────────────────
app = FastAPI(
    title='Live Source Manager - Web Admin',
    version='1.0.0',
    description='直播源管理器 Web 管理界面',
    lifespan=lifespan,
)


# ── 全局 401 异常处理器 — 未登录或 session 过期时重定向到登录页
# 纪枢 P1-1: 确保浏览器请求得到 HTML 响应而非 JSON
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        accept = request.headers.get('accept', '')
        if 'text/html' in accept:
            return RedirectResponse(url='/login', status_code=303)
        return JSONResponse(status_code=401, content={'detail': exc.detail or 'Not authenticated'})
    return JSONResponse(
        status_code=exc.status_code,
        content={'detail': exc.detail},
    )


# ── CSRF 中间件 ──────────────────────────────────
# 跳过 GET/HEAD/OPTIONS + 登录路径和 WebSocket
# 所有写操作必须在 header 中携带 X-CSRF-Token
# 注意：'/health' 路由如果不存在，不从豁免列表移除（保留为空也没危害）
# 与 auth.py 中的 CSRF_EXEMPT_PATHS 保持一致


@app.middleware('http')
async def csrf_middleware(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        path = request.url.path
        if path not in _get_csrf_exempt_paths() and not path.startswith('/ws/'):
            session_id = request.cookies.get('session')
            token = request.headers.get('x-csrf-token', '')
            if not session_id or not token:
                return JSONResponse(status_code=403, content={'detail': 'CSRF token missing（缺少安全令牌）'})
            if not verify_csrf_token(session_id, token):
                return JSONResponse(status_code=403, content={'detail': 'CSRF token invalid（安全令牌无效）'})
    return await call_next(request)


# ── 静态文件 & 模板 ────────────────────────────────
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
# 禁用模板缓存，避免 Jinja2 LRU 因不可哈希模板上下文（含 dict user）抛出 TypeError
templates = Jinja2Templates(directory=os.path.join(WEB_DIR, 'templates'))
templates.env.auto_reload = True
app.mount('/static', StaticFiles(directory=os.path.join(WEB_DIR, 'static')), name='static')


# ═══════════════════════════════════════════════════
# 页面渲染辅助
# ═══════════════════════════════════════════════════


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
        },
    )


# ═══════════════════════════════════════════════════
# 仪表盘辅助函数
# ═══════════════════════════════════════════════════

# _get_source_summary 的 TTL 缓存（缓存 60 秒，避免每次 API 请求读文件）
# P3-新-3: 使用 threading.Lock 保护缓存读写，Uvicorn 多 worker 下安全（每个 worker 进程独立）
_source_summary_cache = None
_source_summary_cache_time = 0
_SOURCE_SUMMARY_TTL = 60  # 秒
_source_summary_lock = threading.Lock()


def _get_source_summary() -> dict:
    """获取源数据的简要统计（优先从 source_summary.json，其次从 m3u 文件推断）

    使用 TTL 缓存避免高并发场景下的 IO 浪费。
    使用 threading.Lock 确保读写原子性（P3-新-3）。
    """
    global _source_summary_cache, _source_summary_cache_time
    now = time.time()
    # 先不加锁读——快速路径，读旧值比每次加锁好
    cached = _source_summary_cache
    cached_time = _source_summary_cache_time
    if cached is not None and now - cached_time < _SOURCE_SUMMARY_TTL:
        return cached

    # 缓存过期或不存在，加锁后重新计算
    with _source_summary_lock:
        # 双重检查（double-checked locking）
        now = time.time()
        if _source_summary_cache is not None and now - _source_summary_cache_time < _SOURCE_SUMMARY_TTL:
            return _source_summary_cache

        result = _compute_source_summary()
        _source_summary_cache = result
        _source_summary_cache_time = now
        return result


def _compute_source_summary() -> dict:
    """实际计算源统计（无缓存版本）"""
    try:
        status_file = os.path.join(PROJECT_ROOT, 'data', 'status', 'source_summary.json')
        if os.path.exists(status_file):
            with open(status_file) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f'读取 source_summary.json 失败, 回退至 M3U 统计: {e}')
    # 兜底：从 m3u 输出文件统计
    try:
        m3u_paths = [
            './www/output/live.m3u',
            os.path.join(PROJECT_ROOT, 'www', 'output', 'live.m3u'),
        ]
        for m3u in m3u_paths:
            if os.path.exists(m3u):
                count = 0
                with open(m3u) as f:
                    for line in f:
                        if line.startswith('#EXTINF:'):
                            count += 1
                valid_path = os.path.join(os.path.dirname(m3u), 'qualified_live.m3u')
                valid_cnt = 0
                if os.path.exists(valid_path):
                    with open(valid_path) as f:
                        for line in f:
                            if line.startswith('#EXTINF:'):
                                valid_cnt += 1
                return {
                    'total_sources': count,
                    'valid': valid_cnt,
                    'invalid': count - valid_cnt,
                    'rate': f'{(valid_cnt / count * 100):.1f}%' if count > 0 else '0%',
                }
    except Exception as e:
        logger.warning(f'M3U 文件统计回退失败: {e}')
    return {'total_sources': 0, 'valid': 0, 'invalid': 0, 'rate': '0%'}


def _get_system_info() -> dict:
    """获取系统信息（psutil 可选，缺失时使用 /proc 兜底）"""
    info = {
        'memory_usage': 'N/A',
        'cpu': 'N/A',
        'ffprobe_available': False,
        'process_running': False,
    }
    try:
        import psutil

        try:
            mem = psutil.virtual_memory()
            info['memory_usage'] = f'{mem.percent}%'
            info['cpu'] = f'{psutil.cpu_percent(interval=0.1)}%'
        except Exception as e:
            logger.debug(f'psutil 内存/CPU 信息获取失败: {e}')
        # 检查主进程（web.webapp / main.py 或其容器级进程）
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                cmdline = proc.info.get('cmdline') or []
                if any(k in c for c in cmdline for k in ['web.webapp', 'main.py']):
                    info['process_running'] = True
                    break
            # 如果 psutil 循环没找到，检查是否在容器中（pid 1 就是我们的应用）
            if not info['process_running']:
                try:
                    p1 = psutil.Process(1)
                    cmd1 = ' '.join(p1.cmdline())
                    if 'webapp' in cmd1 or 'main.py' in cmd1 or 'python' in cmd1:
                        info['process_running'] = True
                except Exception as e:
                    logger.debug(f'检查 pid 1 进程信息失败: {e}')
        except Exception as e:
            logger.debug(f'进程迭代检查失败: {e}')
    except ImportError:
        # psutil 不可用时使用 /proc 兜底
        try:
            with open('/proc/meminfo') as f:
                meminfo = f.read()
            for line in meminfo.splitlines():
                if line.startswith('MemTotal:'):
                    total_kb = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    avail_kb = int(line.split()[1])
            if total_kb and avail_kb:
                used_pct = (total_kb - avail_kb) / total_kb * 100
                info['memory_usage'] = f'{used_pct:.1f}%'
        except Exception as e:
            logger.debug(f'/proc/meminfo 读取失败: {e}')
        try:
            cpu_times = os.times()
            info['cpu'] = f'{cpu_times.user + cpu_times.system:.0f}s'
        except Exception as e:
            logger.debug(f'os.times() 获取失败: {e}')
    # 检查 ffprobe/ffmpeg（优先 ffprobe，降级 ffmpeg）
    try:
        from app import StreamTester

        ffprobe_path = StreamTester._find_executable('ffprobe')
        ffmpeg_path = StreamTester._find_executable('ffmpeg')
        info['ffprobe_available'] = ffprobe_path is not None or ffmpeg_path is not None
        if ffprobe_path:
            info['probe_tool'] = f'ffprobe ({os.path.basename(ffprobe_path)})'
        elif ffmpeg_path:
            info['probe_tool'] = f'ffmpeg ({os.path.basename(ffmpeg_path)})'
        else:
            info['probe_tool'] = '不可用'
    except Exception as e:
        logger.warning(f'FFprobe/FFmpeg检测异常: {e}', exc_info=True)
        info['ffprobe_available'] = False
        info['probe_tool'] = f'检测失败: {e}'
    return info


# ═══════════════════════════════════════════════════
# SourceManager 懒加载与缓存
# ═══════════════════════════════════════════════════

_sm_instance = None


def _load_source_manager():
    """懒加载 source_manager（同步方式读取已解析的数据）

    内部缓存实例避免重复创建 ChannelRules/SourceManager。
    """
    global _sm_instance
    if _sm_instance is not None:
        return _sm_instance
    try:
        from app import ChannelRules, Config, Logger, SourceManager

        config = Config(_get_config_path())
        logger_sm = Logger(config.get_logging_config()).logger
        rules = ChannelRules()
        _sm_instance = SourceManager(config, logger_sm, rules)
        return _sm_instance
    except Exception as e:
        import traceback

        logger.warning(f'无法加载 SourceManager: {e}')
        logger.warning(f'SourceManager 加载详情: {traceback.format_exc()}')
        return None


def get_source_by_id(source_id: str) -> dict | None:
    """根据 ID 获取单个源信息（ID 使用 name 或 url 的 hash 标识）"""
    sm = _load_source_manager()
    if not sm:
        return None
    try:
        sources = sm.parse_all_files()
    except Exception as e:
        logger.warning(f'解析源文件失败 (get_source_by_id): {e}')
        return None
    import hashlib

    for s in sources:
        sid = hashlib.md5(f'{s["name"]}|{s["url"]}'.encode()).hexdigest()[:12]
        if sid == source_id:
            return s
    return None


def reset_source_manager_cache():
    """重置 SourceManager 缓存实例（供路由模块调用）。"""
    global _sm_instance
    _sm_instance = None
