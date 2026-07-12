#!/usr/bin/env python3
"""
web.core — 共享基础设施

从 webapp.py 提取的公共组件：
- app 实例、lifespan、中间件、异常处理器
- 静态文件和模板配置
- 配置代理函数（纯 SQLite，走 app_config 表）
- Session / CSRF / RBAC
- WebSocket ConnectionManager
- 认证辅助函数
- _render / _get_source_summary / _get_system_info 等辅助函数
- SourceManager 懒加载与缓存
"""

# ── 第三方/标准库 import ──────────────────────
import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Any

import yaml
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

# 文件发布服务（HTTPServer.fileshare_port，默认 12345）后台线程句柄
_fileshare_server: Any = None
_fileshare_thread: threading.Thread | None = None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_write_lock = threading.Lock()

# ── conftest 兼容访问器 ──────────────────────────
# conftest.py 会覆写 web.webapp.CSRF_EXEMPT_PATHS，
# core 中的函数/中间件通过以下访问器在运行时读取覆写后的值。


def _get_csrf_exempt_paths():
    """获取 CSRF_EXEMPT_PATHS，优先读取 webapp 模块的覆写值。"""
    _ww = sys.modules.get('web.webapp')
    if _ww is not None:
        return getattr(_ww, 'CSRF_EXEMPT_PATHS', CSRF_EXEMPT_PATHS)
    return CSRF_EXEMPT_PATHS


# ═══════════════════════════════════════════════════
# 配置读写代理（纯 SQLite，走 app_config 表）
# ═══════════════════════════════════════════════════

# 字段定义：name -> (type, default, label, help)
# 🔧 维护提示：此处的默认值需与 app/config.py Config._DEFAULT_VALUES 保持一致。
# 修改任一位置请同步修改另一处。
# 以下默认值可通过 config/config-defaults.yaml 文件覆盖（P2-2 修复）。
# 优先加载 YAML 中的值，YAML 不存在时使用代码内硬编码。

_DEFAULTS_YAML_PATH = os.path.join(PROJECT_ROOT, 'config', 'config-defaults.yaml')


def _load_defaults_from_yaml() -> dict | None:
    """从 config-defaults.yaml 加载默认值"""
    if os.path.exists(_DEFAULTS_YAML_PATH):
        try:
            with open(_DEFAULTS_YAML_PATH, encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f'加载 config-defaults.yaml 失败: {e}')
    return None


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
        'github_mirror': (
            'str',
            'https://ghproxy.com/',
            'GitHub镜像站',
            '用于 mirror 下载方式的代理网站URL',
        ),
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
        'api_url': (
            'str',
            'https://api.github.com',
            'API地址',
            'GitHub API 基地址，一般无需修改',
        ),
        'api_token': (
            'str',
            '',
            'API Token',
            'GitHub Personal Access Token（无需任何权限，仅用于提升 API 速率限制至 5000次/时）。前往 GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token 生成',
        ),
        'rate_limit': (
            'int',
            '5000',
            '速率限制',
            '每小时最大 API 请求次数（有 Token: 5000，无 Token: 60）',
        ),
    },
    'Testing': {
        'timeout': ('int', '10', '测试超时(秒)'),
        'concurrent_threads': ('int', '40', '并发线程数'),
        'max_concurrent_ffprobe': ('int', '16', 'ffprobe并发数(实时测试)'),
        'cache_ttl': ('int', '120', '缓存有效期(分)'),
        'enable_speed_test': ('bool', 'True', '启用速率测试'),
        'speed_test_duration': ('int', '6', '速率测试时长(秒)'),
        'auto_scan_enabled': ('bool', 'False', '启用自动扫描测试'),
        'auto_scan_mode': (
            'str',
            'interval',
            '自动扫描模式',
            'interval=按间隔小时数；daily=每日指定时刻（与下方参数配合）',
        ),
        'auto_scan_interval_hours': (
            'int',
            '24',
            '间隔小时数',
            'mode=interval 时生效：每 N 小时自动测试一次',
        ),
        'auto_scan_daily_time': (
            'str',
            '03:00',
            '每日启动时刻',
            'mode=daily 时生效：格式 HH:MM，每天该时刻自动测试一次',
        ),
        'enable_host_speed_share': (
            'bool',
            'True',
            '同 Host 测速复用',
            '同 CDN/Host 仅 ffprobe 一次并复用结果，大幅减少重复探测（对标 Guovin）',
        ),
        'enable_source_freeze': (
            'bool',
            'True',
            '失败源冻结',
            '连续失败的源按 2^n×基数 秒指数退避冻结冷却，省资源',
        ),
        'freeze_fail_threshold': (
            'int',
            '3',
            '冻结阈值',
            '连续失败达到该次数后开始冻结',
        ),
        'freeze_base_seconds': (
            'int',
            '60',
            '退避基数(秒)',
            '冻结时长 = 2^失败次数 × 基数，封顶 freeze_max_hours',
        ),
        'freeze_max_hours': ('int', '24', '冻结上限(小时)', '单次冻结最长时间'),
        'enable_ad_detect': (
            'bool',
            'True',
            '广告/循环源检测',
            '拉取 m3u8 检查广告关键字与循环占位标志',
        ),
        'ad_keywords': (
            'str',
            'no_signal,/ad/,advertisement,测试卡,无信号,test_pattern,colorbar,broadcast_test,signal_lost',
            '广告关键字',
            '命中即判为广告源（逗号或换行分隔）',
        ),
        'ad_max_duration': (
            'int',
            '90',
            '循环占位阈值(秒)',
            '含 #EXT-X-ENDLIST 且累计时长<=该值判为循环占位',
        ),
        'global_blacklist': (
            'str',
            '',
            '全局黑名单',
            '命中(URL/host)的源跳过测试，逗号或换行分隔',
        ),
        'global_whitelist': (
            'str',
            '',
            '全局白名单',
            'URL/host 清单，豁免于黑名单与冻结，逗号或换行分隔',
        ),
        'output_sort_by': (
            'str',
            'speed',
            '输出排序',
            'speed=快源在前；name=按名；resolution=按分辨率',
        ),
        'max_test_attempts': (
            'int',
            '1',
            '实时测试次数',
            '每个地址的总测试次数：1=测一次；2=测两次(含1次自动重试)；默认1',
        ),
    },
    'Output': {
        'filename': ('str', 'live.m3u', '输出文件名'),
        'group_by': ('str', 'category', '分组策略'),
        'include_failed': ('bool', 'False', '包含失败源'),
        'max_sources_per_channel': ('int', '8', '每频道最大源数'),
        'enable_filter': ('bool', 'False', '启用过滤'),
        'whitelist_force_keep': (
            'bool',
            'False',
            '白名单强制保留',
            '白名单源即使未过质量过滤也保留到输出',
        ),
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

# 从 YAML 加载外部化默认值，覆盖 SECTION_SCHEMA 中的硬编码默认值
_yaml_defaults = _load_defaults_from_yaml()
if _yaml_defaults:
    for section, fields in _yaml_defaults.items():
        if section in SECTION_SCHEMA:
            for key, value in fields.items():
                if key in SECTION_SCHEMA[section]:
                    # 只替换默认值（tuple 第2个元素），保持类型、标签等其他信息不变
                    existing = list(SECTION_SCHEMA[section][key])
                    if len(existing) >= 2:
                        existing[1] = str(value)
                        SECTION_SCHEMA[section][key] = tuple(existing)
    logger.info('已从 config-defaults.yaml 加载配置默认值覆盖')

# 敏感字段（用于审计日志脱敏）
SENSITIVE_FIELDS = {
    'proxy_password',
    'proxy_username',
    'api_token',
    'github_mirror',
    'proxy_host',
    'proxy_port',
    'local_dirs',
}

# 字段类型映射
FIELD_TYPE = {
    'str': 'text',
    'textarea': 'textarea',
    'int': 'number',
    'bool': 'checkbox',
}


def read_config() -> dict[str, dict[str, str]]:
    """读取全量配置，返回 {section: {key: value}}

    直接从 SQLite app_config 表读取，不再支持 INI 回退。
    """
    return models.get_all_config()


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


def _validate_config_values(data: dict[str, dict[str, str]]) -> list[dict] | None:
    """批量校验配置值，返回结构化错误列表或 None（全部通过）

    每个错误条目包含：
    - section: str
    - key: str
    - error: str
    - field_label: str

    用于 API 端点返回结构化校验错误（P1-3 修复）。
    """
    errors = []
    for section, fields in data.items():
        schema = SECTION_SCHEMA.get(section, {})
        for key, value in fields.items():
            field_def = schema.get(key)
            if not field_def:
                errors.append(
                    {
                        'section': section,
                        'key': key,
                        'error': '未知字段',
                        'field_label': key,
                    }
                )
                continue
            _, err = validate_and_coerce(section, key, value, field_def)
            if err:
                label = field_def[2] if len(field_def) > 2 else key
                errors.append({'section': section, 'key': key, 'error': err, 'field_label': label})
    return errors if errors else None


def write_config(data: dict[str, dict[str, str]]) -> tuple[bool, str]:
    """
    写入配置 — 仅写入 SQLite（不再写 INI 备份）
    """
    with _write_lock:
        err = _write_config_to_sqlite(data)
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
CSRF_EXEMPT_PATHS = frozenset({'/api/auth/login', '/login'})


def _get_csrf_token(session_id: str, user_agent: str = '') -> str:
    """生成并存储 CSRF token（1小时复用），D-8 修复：绑定 User-Agent 提升安全性"""
    now = time.time()
    stored = _auth_csrf_tokens.get(session_id)
    if stored:
        token, ua, expires = stored
        if now < expires and ua == user_agent:
            return token
    token = hashlib.sha256(f'{session_id}:{user_agent}:{uuid.uuid4().hex}'.encode()).hexdigest()
    _auth_csrf_tokens[session_id] = (token, user_agent, now + CSRF_TTL)
    return token


def verify_csrf_token(session_id: str, token: str, user_agent: str = '') -> bool:
    """验证 CSRF token（D-8 修复：同时校验 User-Agent 一致性）"""
    stored = _auth_csrf_tokens.get(session_id)
    if not stored:
        return False
    stored_token, ua, expires = stored
    if time.time() > expires:
        _auth_csrf_tokens.pop(session_id, None)
        return False
    if ua != user_agent:
        return False
    return hmac.compare_digest(stored_token, token)


# ═══════════════════════════════════════════════════
# 加密密钥状态标记
# ═══════════════════════════════════════════════════

CONFIG_KEY_IS_MANUAL = True


# ═══════════════════════════════════════════════════
# 登录失败锁定机制（《网络安全法》第24条）
# ═══════════════════════════════════════════════════

LOGIN_LOCKOUT_MAX_ATTEMPTS = 5
# 登录锁定函数已迁移至 web.models
# 以下函数仅为向后兼容的别名，实际逻辑在 models.py 中
# 迁移背景（P2-1 修复）：消除 core.py 和 models.py 之间的重复 SQL 逻辑


def check_login_lockout(username: str) -> tuple[bool, int]:
    """检查用户是否被锁定。委托 models 实现。"""
    return models.check_login_lockout(username)


def record_login_failure(username: str):
    """记录登录失败。委托 models 实现。"""
    models.record_login_failure(username)


def reset_login_lockout(username: str):
    """登录成功后重置锁定计数器。委托 models 实现。"""
    models.reset_login_lockout(username)


# ═══════════════════════════════════════════════════
# FastAPI 应用实例 + lifespan + 中间件
# ═══════════════════════════════════════════════════

sys.path.insert(0, PROJECT_ROOT)


# ═══════════════════════════════════════════════════
# 文件发布服务（fileshare_port，默认 12345）
# 非 Docker 环境下，由本线程提供静态文件服务，serve document_root，
# 用于对外发布生成的 M3U/TXT 播放列表。Docker 环境由 nginx 承担，本服务跳过。
# ═══════════════════════════════════════════════════


def _start_fileshare_server() -> None:
    """在后台线程启动文件发布静态服务（监听 HTTPServer.fileshare_port）"""
    global _fileshare_server, _fileshare_thread
    try:
        from app.config import Config

        cfg = Config().get_http_server_config()
        if not cfg.get('enabled'):
            logger.info('文件发布服务未启用（HTTPServer.enabled=False），跳过 12345 绑定')
            return
        doc_root = cfg.get('document_root') or './www/output'
        if not os.path.isabs(doc_root):
            doc_root = os.path.abspath(doc_root)
        os.makedirs(doc_root, exist_ok=True)

        host = cfg.get('host') or '0.0.0.0'
        port = int(cfg.get('fileshare_port') or 12345)

        import functools
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        class _FileShareHandler(SimpleHTTPRequestHandler):
            def guess_type(self, path):
                # 正确 MIME，避免浏览器把 .m3u 当文本下载
                if path.endswith('.m3u') or path.endswith('.m3u8'):
                    return 'audio/x-mpegurl'
                if path.endswith('.txt'):
                    return 'text/plain; charset=utf-8'
                if path.endswith('.json'):
                    return 'application/json'
                return super().guess_type(path)

            def log_message(self, fmt, *args):
                # 降低噪音：仅 warning 级别以上才进主日志
                logger.debug(f'[fileshare] {fmt % args}')

        handler = functools.partial(_FileShareHandler, directory=doc_root)
        httpd = ThreadingHTTPServer((host, port), handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        _fileshare_server = httpd
        _fileshare_thread = t
        logger.info(f'📂 文件发布服务已启动: http://{host}:{port}  →  {doc_root}')
    except OSError as e:
        logger.warning(f'⚠️ 文件发布服务启动失败（端口 {port} 可能已被占用）: {e}')
    except Exception as e:
        logger.warning(f'⚠️ 文件发布服务启动异常: {e}')


def _stop_fileshare_server() -> None:
    """优雅停止文件发布服务"""
    global _fileshare_server, _fileshare_thread
    if _fileshare_server is not None:
        try:
            _fileshare_server.shutdown()
            _fileshare_server.server_close()
        except Exception as e:
            logger.warning(f'停止文件发布服务时出错: {e}')
        _fileshare_server = None
    if _fileshare_thread is not None:
        _fileshare_thread.join(timeout=5)
        _fileshare_thread = None


# ── 日志 ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """应用生命周期 startup + shutdown"""
    # ── startup ────────────────────────────────
    admin_pw = os.environ.get('WEB_ADMIN_PASSWORD')
    if admin_pw is not None:
        # 仅当用户显式设置了密码时才校验复杂度；未设置则交由 init_db 自动生成强密码
        # （首次部署零配置即可用，仍满足合规复杂度要求）
        _pw = admin_pw
        _categories = 0
        if re.search(r'[A-Z]', _pw):
            _categories += 1
        if re.search(r'[a-z]', _pw):
            _categories += 1
        if re.search(r'[0-9]', _pw):
            _categories += 1
        if re.search(r'[^A-Za-z0-9]', _pw):
            _categories += 1
        if len(_pw) < 8 or _categories < 3:
            raise RuntimeError(
                f'【合规拒绝启动】密码不满足GB/T 39786-2021复杂度要求。\n'
                f'  当前长度: {len(_pw)}, 包含字符类别数: {_categories}（至少需要3类，长度≥8）\n'
                f'  密码需含大写字母、小写字母、数字、特殊符号中的至少三类。'
            )
    # 首次部署：未设置 WEB_ADMIN_PASSWORD 时 init_db 自动生成强密码并写入日志
    effective_pw = await asyncio.to_thread(models.init_db, admin_password=admin_pw)
    if effective_pw is not None:
        logger.info(
            '📌 初始管理员密码已就绪（请妥善保存，首次登录后建议修改）: %s',
            effective_pw,
        )
    await asyncio.to_thread(models.cleanup_expired_sessions)
    await asyncio.to_thread(models.cleanup_audit_logs, max_days=180)
    logger.info('数据库初始化完成，已清除过期Session，清理180天前审计日志')
    # 首次登录强制修改密码标记
    await asyncio.to_thread(models.set_password_change_required, 'admin', True)
    logger.info('首次登录强制修改密码策略已启用')
    # 初始化登录锁定表
    await asyncio.to_thread(models.init_login_lockout_table)
    logger.info('登录失败锁定机制已启用（5次失败锁定15分钟）—— 依据《网络安全法》第24条')

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
    # 策略：纯 SQLite，配置读写直接走 app_config 表。
    # 首次运行时，若 app_config 表为空则写入默认配置。

    if not models.has_app_config_data():
        # app_config 无数据 → 写入代码默认值
        seeded = models.seed_app_config_defaults()
        logger.info(f'SQLite app_config 已写入 {seeded} 条默认配置（首次运行初始化）')
    else:
        logger.info('SQLite app_config 已有数据')

    # 始终补全 schema 新增的默认值键（幂等，不覆盖已有值）
    # 防止「首次 seed 时该键尚不存在」导致新配置项从 /api/config 与 UI 上消失
    filled = models.fill_missing_app_config_defaults()
    if filled:
        logger.info(f'SQLite app_config 已补全 {filled} 条缺失默认值')

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

    # ── 定时清理任务（每天执行一次，替代外部 cron 依赖）────
    # P2-3 修复：内置定时 session 清理机制
    _cleanup_task_started = False

    async def _periodic_cleanup():
        """每天执行一次 session 和审计日志清理"""
        await asyncio.sleep(3600)  # 启动后 1 小时执行首次
        while True:
            try:
                await asyncio.to_thread(models.cleanup_expired_sessions)
                await asyncio.to_thread(models.cleanup_audit_logs, max_days=180)
                logger.info('定时清理完成：已清理过期Session和180天前审计日志')
                # 清理内存 session
                _clean_expired()
            except Exception as _e:
                logger.warning(f'定时清理任务执行失败: {_e}')
            await asyncio.sleep(86400)  # 24 小时间隔

    cleanup_task = asyncio.create_task(_periodic_cleanup())
    _cleanup_task_started = True
    logger.info('定时 session 清理任务已启动（每24小时执行一次）')

    # ── 自动扫描调度任务（按配置中心的「测试」自动扫描参数定时触发流测试）────
    async def _auto_scan_scheduler():
        """根据配置中心的「测试」自动扫描参数，定时触发全量流测试。

        支持两种模式：
          - interval：每 N 小时触发一次（auto_scan_interval_hours）
          - daily：每天指定时刻（HH:MM，auto_scan_daily_time）触发一次
        调度状态持久化在 data/status/auto_scan_state.json，避免重启/跨分钟重复触发。
        """
        await asyncio.sleep(30)  # 启动后稍候，待缓存预热完成
        _state_path = os.path.join(PROJECT_ROOT, 'data', 'status', 'auto_scan_state.json')
        os.makedirs(os.path.dirname(_state_path), exist_ok=True)

        def _load_state():
            try:
                with open(_state_path) as f:
                    return json.load(f)
            except Exception:
                return {}

        def _save_state(st):
            try:
                with open(_state_path, 'w') as f:
                    json.dump(st, f)
            except Exception as e:
                logger.warning(f'[AUTO-SCAN] 保存调度状态失败: {e}')

        while True:
            try:
                cfg = read_config().get('Testing', {})
                enabled = str(cfg.get('auto_scan_enabled', 'False')).lower() == 'true'
                if enabled:
                    mode = (cfg.get('auto_scan_mode') or 'interval').strip().lower()
                    now = datetime.datetime.now()
                    st = _load_state()
                    last_run = st.get('last_run')
                    last_dt = None
                    if last_run:
                        try:
                            last_dt = datetime.datetime.fromisoformat(last_run)
                        except Exception:
                            last_dt = None
                    should_run = False
                    if mode == 'daily':
                        daily_time = (cfg.get('auto_scan_daily_time') or '03:00').strip()
                        try:
                            h, m = (int(x) for x in daily_time.split(':'))
                        except Exception:
                            h, m = 3, 0
                        if now.hour == h and now.minute == m and (last_dt is None or last_dt.date() < now.date()):
                            should_run = True
                    else:  # interval
                        try:
                            hours = int(float(cfg.get('auto_scan_interval_hours') or 24))
                        except Exception:
                            hours = 24
                        if hours < 1:
                            hours = 1
                        if last_dt is None or (now - last_dt) >= datetime.timedelta(hours=hours):
                            should_run = True
                    if should_run:
                        try:
                            from web.routes import system as _sys_routes

                            ok = _sys_routes.schedule_auto_test()
                            if ok:
                                st['last_run'] = now.isoformat()
                                _save_state(st)
                                logger.info(f'[AUTO-SCAN] 已触发自动测试（mode={mode}）')
                            else:
                                logger.info('[AUTO-SCAN] 已有测试运行，跳过本次触发')
                        except Exception as e:
                            logger.warning(f'[AUTO-SCAN] 触发失败: {e}')
            except Exception as e:
                logger.warning(f'[AUTO-SCAN] 调度循环异常: {e}')
            await asyncio.sleep(60)  # 每分钟检查一次

    auto_scan_task = asyncio.create_task(_auto_scan_scheduler())
    logger.info('自动扫描调度任务已启动（配置中心「测试」可设置间隔/每日定时）')

    # ── 源文件缓存预热（后台线程，不阻塞启动）────────────
    # 30,000+ 源的全量解析约需 3 分钟，预热后页面请求直接命中缓存
    async def _prewarm_parse_cache():
        """后台预热 parse_all_files 缓存，避免首次页面请求卡 3 分钟"""
        try:
            sm = _load_source_manager()
            if sm:
                logger.info('开始预热源文件缓存（后台）...')
                await asyncio.to_thread(parse_all_files_cached, sm)
                logger.info('源文件缓存预热完成')
        except Exception as e:
            logger.warning(f'源文件缓存预热失败（不影响启动，首次请求时会重新解析）: {e}')

    prewarm_task = asyncio.create_task(_prewarm_parse_cache())
    logger.info('源文件缓存预热任务已启动（后台执行）')

    # ── 文件发布服务（12345，非 Docker 环境提供静态文件发布）──
    _start_fileshare_server()

    # ── yield: 进入运行状态 ───────────────────────
    yield

    # ── shutdown（资源清理） ────────────────────
    _stop_fileshare_server()
    prewarm_task.cancel()
    with suppress(asyncio.CancelledError):
        await prewarm_task
    if _cleanup_task_started:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
    try:
        auto_scan_task.cancel()
        with suppress(asyncio.CancelledError):
            await auto_scan_task
    except Exception:
        pass
    logger.info('Web 服务关闭，清理资源...')


# ── FastAPI 应用实例 ──────────────────────────────
app = FastAPI(
    title='Live Source Manager - Web Admin',
    version='1.0.0',
    description='直播源管理器 Web 管理界面',
    lifespan=lifespan,
)


@app.get('/api/health')
async def api_health():
    """健康检查端点（GET，无需认证；供部署脚本/监控探活）"""
    return {'status': 'ok', 'service': 'live-source-manager'}


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
                return JSONResponse(
                    status_code=403,
                    content={'detail': 'CSRF token missing（缺少安全令牌）'},
                )
            if not verify_csrf_token(session_id, token, request.headers.get('user-agent', '')):
                return JSONResponse(
                    status_code=403,
                    content={'detail': 'CSRF token invalid（安全令牌无效）'},
                )
    return await call_next(request)


# ── 静态文件 & 模板 ────────────────────────────────
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
# 禁用模板缓存，避免 Jinja2 LRU 因不可哈希模板上下文（含 dict user）抛出 TypeError
templates = Jinja2Templates(directory=os.path.join(WEB_DIR, 'templates'))
# D-5 修复：仅开发环境开启模板自动重载，生产环境关闭以省去每个请求的 mtime 检查
_APP_ENV = os.environ.get('APP_ENV', os.environ.get('ENV', 'production')).lower()
templates.env.auto_reload = _APP_ENV == 'development'
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
    # 内联 CSRF token 到页面，避免前端依赖异步 fetch 时序（D-8 后所有写请求必须带 token）。
    # 页面渲染时即注入，window.__csrf_token 在脚本执行前已就绪，杜绝"token 为空"的偶发问题。
    csrf_token = ''
    if session_id:
        try:
            csrf_token = _get_csrf_token(session_id, request.headers.get('user-agent', ''))
        except Exception:
            csrf_token = ''
    # Starlette 新版本中 TemplateResponse 签名改为 (request, name, context, ...)
    # 使用关键字参数避免位置错位
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            'request': request,
            'user': user,
            'csrf_token': csrf_token,
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

# parse_all_files() 的缓存 — 基于文件 mtime 而非 TTL
# 缓存只在源文件实际变化时失效（增删/采集），消除定时过期导致的卡顿
# parse_all_files() 的缓存 — 基于源文件集合指纹（文件数 + size + mtime 哈希）
# 任一文件增删/修改都改变指纹 → 缓存失效（D-2 修复：原 mtime 方案无法检测文件删除）
# 重解析在 worker 线程进行，且采用 stale-while-revalidate：刷新期间仍返回旧缓存（D-1 修复）
_parse_cache: list | None = None
_parse_cache_fingerprint: str = ''  # 缓存对应的源文件集合指纹
_parse_cache_lock = threading.Lock()

# 文件→频道数 预构建映射（与 _parse_cache 同生命周期，避免每次请求遍历 31K 源）
_file_channel_counts: dict | None = None


def _compute_source_fingerprint(sm) -> str:
    """计算源文件集合指纹：所有源文件 (路径|大小|mtime) 的稳定哈希。

    相比只取"最大 mtime"，任一文件增删都会改变指纹 → 缓存正确失效（D-2 修复）。
    """
    entries = []
    dirs_to_check = []
    try:
        local_dirs = sm.config.get_sources().get('local_dirs', [])
        if isinstance(local_dirs, str):
            local_dirs = [d.strip() for d in local_dirs.split(',') if d.strip()]
        dirs_to_check.extend(local_dirs)
    except Exception:
        pass
    if hasattr(sm, 'online_dir'):
        dirs_to_check.append(sm.online_dir)

    for d in dirs_to_check:
        if not d or not os.path.isdir(d):
            continue
        try:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(('.m3u', '.m3u8', '.txt')):
                        fp = os.path.join(root, f)
                        try:
                            st = os.stat(fp)
                            entries.append(f'{os.path.normpath(os.path.abspath(fp))}|{st.st_size}|{int(st.st_mtime)}')
                        except OSError:
                            pass
        except Exception:
            pass
    entries.sort()
    return hashlib.md5('\n'.join(entries).encode('utf-8', 'ignore')).hexdigest()


def parse_all_files_cached(sm) -> list:
    """基于源文件集合指纹的缓存。

    - 文件未变 → 直接返回缓存（毫秒级）。
    - 文件变化 → 单线程重解析（其他并发请求返回旧缓存，不阻塞事件循环 D-1）。

    通常由 `await asyncio.to_thread(parse_all_files_cached, sm)` 调用，
    重解析发生在 worker 线程，绝不冻结 uvicorn 事件循环。
    """
    global _parse_cache, _parse_cache_fingerprint, _file_channel_counts

    # 快速路径：缓存存在且指纹未变 → 直接返回
    cached = _parse_cache
    if cached is not None:
        try:
            fp = _compute_source_fingerprint(sm)
        except Exception:
            fp = ''
        if fp and fp == _parse_cache_fingerprint:
            return cached

    # 需要刷新：尝试非阻塞获取刷新锁（避免并发重解析风暴）
    acquired = _parse_cache_lock.acquire(blocking=False)
    if not acquired:
        # 别的线程正在刷新 → stale-while-revalidate：返回旧缓存（即使指纹已变）
        if _parse_cache is not None:
            return _parse_cache
        # 缓存为空（首次）且锁被占用 → 阻塞等待刷新完成
        _parse_cache_lock.acquire()
        _parse_cache_lock.release()
        return _parse_cache if _parse_cache is not None else []

    try:
        # 双重检查（等锁期间可能已完成刷新）
        try:
            fp = _compute_source_fingerprint(sm)
        except Exception:
            fp = ''
        if _parse_cache is not None and fp and fp == _parse_cache_fingerprint:
            return _parse_cache

        # 真正重解析（在 to_thread 线程中执行，不阻塞事件循环）
        try:
            result = sm.parse_all_files()
        except Exception:
            result = _parse_cache if _parse_cache is not None else []

        try:
            fp = _compute_source_fingerprint(sm)
        except Exception:
            fp = ''

        _parse_cache = result
        _parse_cache_fingerprint = fp
        # 同时构建文件→频道数映射（一次遍历，后续请求零成本读取）
        counts: dict[str, int] = {}
        for s in result:
            sp = s.get('source_path', '')
            if sp:
                norm = os.path.normpath(os.path.abspath(sp))
                counts[norm] = counts.get(norm, 0) + 1
        _file_channel_counts = counts
        return result
    finally:
        _parse_cache_lock.release()


def invalidate_parse_cache():
    """使 parse_all_files 缓存失效（源文件变更时调用）。

    清空缓存并重置指纹，下次请求强制重解析。配合 D-1 的 to_thread 调用，
    重解析在后台线程进行，不冻结事件循环。
    """
    global _parse_cache, _parse_cache_fingerprint, _file_channel_counts
    with _parse_cache_lock:
        _parse_cache = None
        _parse_cache_fingerprint = ''
        _file_channel_counts = None


def get_file_channel_counts() -> dict:
    """返回预构建的 文件路径→频道数 映射（由 parse_all_files_cached 构建）。

    如果缓存不存在则返回空 dict，调用方应做降级处理。
    """
    return _file_channel_counts if _file_channel_counts is not None else {}


def _load_source_manager():
    """懒加载 source_manager（同步方式读取已解析的数据）

    内部缓存实例避免重复创建 ChannelRules/SourceManager。
    """
    global _sm_instance
    if _sm_instance is not None:
        return _sm_instance
    try:
        from app import ChannelRules, Config, Logger, SourceManager

        config = Config()
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
    import hashlib

    sources = parse_all_files_cached(sm)
    for s in sources:
        sid = hashlib.md5(f'{s["name"]}|{s["url"]}'.encode()).hexdigest()[:12]
        if sid == source_id:
            return s
    return None


def reset_source_manager_cache():
    """重置 SourceManager 缓存实例（供路由模块调用）。"""
    global _sm_instance
    _sm_instance = None
    # 同时使 parse 缓存失效，让下次请求重新解析
    invalidate_parse_cache()
