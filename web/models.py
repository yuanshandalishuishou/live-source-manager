#!/usr/bin/env python3
"""
SQLite ORM — 用户表、审计日志表、Session表
"""

import threading
import time

# ── get_all_config() TTL 缓存（5 秒，避免同一请求内多次读取 SQLite）──
_all_config_cache: dict | None = None
_all_config_cache_time: float = 0
_ALL_CONFIG_CACHE_TTL: float = 5.0
_all_config_cache_lock = threading.Lock()


def invalidate_config_cache():
    """使配置缓存失效（write 操作时调用）。"""
    global _all_config_cache, _all_config_cache_time
    with _all_config_cache_lock:
        _all_config_cache = None
        _all_config_cache_time = 0


import datetime
import logging
import os
import re
import sqlite3
from contextlib import contextmanager, suppress

import bcrypt

logger = logging.getLogger('web.models')

# 加密工具模块（延迟导入避免循环引用）


# ── 分类规则表定义（元信息，非 ORM -- 实际使用 raw SQLite） ─────────
class ClassificationRule:
    """分类规则表 — classification_rules
    列:
        id (INTEGER PK), rule_type (TEXT, 'category'|'channel_type'),
        name (TEXT), keywords (TEXT — JSON数组字符串),
        priority (INTEGER DEFAULT 100), sort_order (INTEGER DEFAULT 0),
        is_active (INTEGER DEFAULT 1), created_at (TEXT), updated_at (TEXT)
    """

    __tablename__ = 'classification_rules'


class ProvinceExclusionMap:
    """省份排除映射表 — province_exclusion_map
    列:
        id (INTEGER PK), province_keyword (TEXT NOT NULL),
        excluded_keyword (TEXT NOT NULL), note (TEXT DEFAULT ''),
        created_at (TEXT)
    UNIQUE(province_keyword, excluded_keyword)
    """

    __tablename__ = 'province_exclusion_map'


# SQLite 数据目录：默认 <项目根>/web/data
# 可用环境变量 WEB_DATA_DIR 覆盖（Docker 部署时指向持久卷 /data，容器重建不丢库）
_DATA_DIR_ENV = os.getenv('WEB_DATA_DIR')
DATA_DIR = (
    _DATA_DIR_ENV
    if _DATA_DIR_ENV
    else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'data')
)
DB_PATH = os.path.join(DATA_DIR, 'web.db')

# 写锁 — 保护并发写入（check_same_thread=False + WAL 下仍可能竞态）
_write_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    """返回独立连接（避免 async 环境下 threading.local 导致连接状态竞态）

    注意：调用者必须 conn.close() 关闭连接避免泄露。
    推荐使用 with_conn() 上下文管理器自动管理。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


@contextmanager
def get_conn_cm():
    """get_conn 的上下文管理器版本，自动 close()"""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def _execute(sql: str, params=()):
    """带写锁保护的执行（含重试机制）"""
    max_retries = 5
    for attempt in range(max_retries):
        conn = None
        try:
            with _write_lock:
                conn = get_conn()
                cursor = conn.execute(sql, params)
                conn.commit()
                return cursor
        except sqlite3.OperationalError as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as _re:
                    logger.warning(f'回滚失败(sqlite锁竞争场景,可忽略): {_re}')
            if 'locked' in str(e) and attempt < max_retries - 1:
                import time

                time.sleep(0.2 * (attempt + 1))
                continue
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _re:
                    logger.warning(f'关闭数据库连接异常: {_re}')


def password_complexity_ok(pw: str) -> bool:
    """校验密码是否满足 GB/T 39786-2021 复杂度要求（长度≥8 且含大写/小写/数字/特殊符号中至少3类）。"""
    if not pw or len(pw) < 8:
        return False
    cats = 0
    if re.search(r'[A-Z]', pw):
        cats += 1
    if re.search(r'[a-z]', pw):
        cats += 1
    if re.search(r'[0-9]', pw):
        cats += 1
    if re.search(r'[^A-Za-z0-9]', pw):
        cats += 1
    return cats >= 3


def init_db(admin_password: str | None = None):
    """建表 + 默认管理员用户。

    - admin_password 为 None 且用户表为空（首次部署）：使用默认初始密码
      ``Admin@123``（李总指定，与 Go 版一致；启动后可在 Web 端修改）。
    - admin_password 提供：首次部署时使用该密码创建 admin（调用方应保证复杂度合规）。
    - 用户已存在：默认保留现有密码不变（幂等，不覆盖用户已修改的密码）。
      **例外**：若环境变量显式提供了【合规】且与库内当前密码【不同】的密码，
      则强制覆盖为 env 密码（救活“弱密码写库→每次启动被合规校验拒绝”的死锁，
      并支持部署时通过环境变量修改管理员密码）；env 密码与库一致则幂等跳过，
      env 密码不合规时不更新并交由 lifespan 拒绝启动。

    返回实际生效的管理员密码（默认或提供）；若用户已存在则返回 None。
    首次创建时会向 stdout 打印 ``ADMIN_PASSWORD_INITIALIZED=xxx`` 供部署脚本捕获。
    """
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            display_name TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        -- 注意：username 上的 UNIQUE 约束已自动创建索引，无需冗余的 idx_users_username

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
        CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_logs(username);

        CREATE TABLE IF NOT EXISTS app_config (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            username TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            created_at REAL NOT NULL,
            last_active REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

        -- P2-2: audit_logs (action, created_at) 复合索引
        CREATE INDEX IF NOT EXISTS idx_audit_action_created ON audit_logs(action, created_at);
    """)
    conn.commit()

    # 创建用户密码
    # 策略：如果用户已存在（非首次部署），保留现有密码不变。
    # 仅当用户表为空（首次部署）时创建初始用户。
    # 只保留 admin 用户，删除 viewer 用户（如存在）
    existing_admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    effective_pw = None
    if not existing_admin:
        if admin_password is None:
            # 首次部署且未提供密码：使用默认初始密码 Admin@123
            # （李总指定，与 Go 版一致；9 位含大写/小写/数字/特殊符号 4 类字符，
            #   满足 GB/T 39786-2021 复杂度；首次登录会强制要求修改密码）
            effective_pw = DEFAULT_ADMIN_PASSWORD
            logger.warning('未设置 WEB_ADMIN_PASSWORD，使用默认初始密码 Admin@123（首次登录后请立即修改）')
        else:
            effective_pw = admin_password
        admin_hash = bcrypt.hashpw(effective_pw.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            'INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)',
            ('admin', admin_hash, 'admin', '管理员'),
        )
        logger.info('创建管理员用户')
        # 向 stdout 打印初始密码，供部署脚本捕获；同时记入日志（首次部署提示）
        if effective_pw is not None:
            print('ADMIN_PASSWORD_INITIALIZED=' + effective_pw, flush=True)
            logger.info('初始管理员密码（请妥善保存）: %s', effective_pw)
    else:
        # 用户已存在：默认保留现有密码（不覆盖用户在 Web 端修改的密码）。
        # 例外：若环境变量显式提供了【合规】且与库内当前密码【不同】的密码，
        # 则强制更新为环境变量密码——救活“弱密码写库→每次启动被合规校验拒绝”的死锁，
        # 并支持部署时通过环境变量修改管理员密码（env 密码与库一致则幂等跳过）。
        # 注意：env 密码不合规时不更新，交由 lifespan 拒绝启动，避免弱密码落地。
        if admin_password is not None and password_complexity_ok(admin_password):
            if not verify_password('admin', admin_password):
                new_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
                conn.execute(
                    "UPDATE users SET password_hash=?, updated_at=CURRENT_TIMESTAMP WHERE username='admin'",
                    (new_hash,),
                )
                conn.commit()
                logger.info('环境变量提供了合规密码，已更新管理员密码（覆盖旧密码）')
            else:
                logger.info('环境变量密码与库内一致，无需更新')
        else:
            logger.info('管理员用户已存在，保留现有密码')
    # 删除 viewer 用户（如存在）
    conn.execute("DELETE FROM users WHERE username='viewer'")
    if conn.total_changes > 0:
        logger.info('已删除查看者用户（viewer）')
    conn.commit()

    # ── 分类维度定义表 ───────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS classification_dimensions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dim_key TEXT UNIQUE NOT NULL,
            dim_name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # 插入默认维度（仅表为空时）
    row = conn.execute('SELECT COUNT(*) FROM classification_dimensions').fetchone()
    if row and row[0] == 0:
        default_dims = [
            ('content', '内容分类', 1),
            ('region', '地域', 2),
            ('language', '语言', 3),
            ('quality', '清晰度', 4),
            ('media_type', '媒体类型', 5),
            ('genre', '节目类型', 6),
        ]
        for dim_key, dim_name, sort_order in default_dims:
            conn.execute(
                'INSERT INTO classification_dimensions (dim_key, dim_name, sort_order) VALUES (?, ?, ?)',
                (dim_key, dim_name, sort_order),
            )
        conn.commit()
        logger.info('默认分类维度已初始化')
    else:
        logger.info('classification_dimensions 已有数据，跳过初始化')

    # ── 分类规则表 ────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS classification_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type TEXT NOT NULL,
            name TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '[]',
            priority INTEGER DEFAULT 100,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS province_exclusion_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            province_keyword TEXT NOT NULL,
            excluded_keyword TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(province_keyword, excluded_keyword)
        );
    """)
    conn.commit()
    logger.info('分类规则表/省排除表已就绪')

    # ── 流源分类结果持久化表 ───────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stream_source_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES stream_sources(id) ON DELETE CASCADE,
            dim_key TEXT NOT NULL,
            dim_value TEXT NOT NULL DEFAULT '未知',
            is_manual INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_id, dim_key)
        );
        CREATE INDEX IF NOT EXISTS idx_source_categories_source
        ON stream_source_categories(source_id);
    """)
    conn.commit()
    logger.info('stream_source_categories 表已就绪')

    # ── 频道全名映射表 ────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS channel_name_mapping (
            channel_name TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '其他频道',
            region TEXT NOT NULL DEFAULT '未知',
            language TEXT NOT NULL DEFAULT '未知',
            quality TEXT NOT NULL DEFAULT '高清',
            media_type TEXT NOT NULL DEFAULT '电视节目',
            genre TEXT NOT NULL DEFAULT '综合',
            is_manual INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    # 老库自愈：为频道映射补 EPG 对齐列（tvg_id / tvg_logo）
    _ensure_columns(
        conn,
        'channel_name_mapping',
        {
            'tvg_id': "TEXT NOT NULL DEFAULT ''",
            'tvg_logo': "TEXT NOT NULL DEFAULT ''",
        },
    )
    logger.info('channel_name_mapping 表已就绪')

    # ── 分类字典表 ────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS category_dictionary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension TEXT NOT NULL,
            value TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            UNIQUE(dimension, value)
        );
        CREATE INDEX IF NOT EXISTS idx_category_dict_dim
        ON category_dictionary(dimension);
    """)
    conn.commit()
    logger.info('category_dictionary 表已就绪')

    # ── 种子数据：分类字典（仅空表时写入） ─────────
    row = conn.execute('SELECT COUNT(*) FROM category_dictionary').fetchone()
    if row and row[0] == 0:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        yaml_path = os.path.join(project_root, 'config', 'channel_rules.yml')
        _seed_category_dictionary(conn, yaml_path)

    # ── GitHub 下载缓存表（持久化下载状态，不受重启影响） ─
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS github_download_cache (
            repo_key TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (repo_key, filename)
        );
        CREATE INDEX IF NOT EXISTS idx_github_dl_repo
        ON github_download_cache(repo_key);
    """)
    conn.commit()
    logger.info('github_download_cache 表已就绪')

    # ── EPG（电子节目单）三表 ──────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS epg_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 100,
            refresh_mode TEXT NOT NULL DEFAULT '',
            refresh_at TEXT NOT NULL DEFAULT '',
            refresh_minutes INTEGER NOT NULL DEFAULT 0,
            remark TEXT NOT NULL DEFAULT '',
            last_fetch_at TEXT,
            last_status TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            last_channel_count INTEGER NOT NULL DEFAULT 0,
            last_programme_count INTEGER NOT NULL DEFAULT 0,
            last_duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_epg_sources_enabled
        ON epg_sources(enabled, priority);

        CREATE TABLE IF NOT EXISTS epg_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            tvg_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT '',
            matched_channel TEXT NOT NULL DEFAULT '',
            updated_at TEXT,
            UNIQUE(source_id, tvg_id)
        );
        CREATE INDEX IF NOT EXISTS idx_epg_channels_tvg ON epg_channels(tvg_id);
        CREATE INDEX IF NOT EXISTS idx_epg_channels_matched
        ON epg_channels(matched_channel);

        CREATE TABLE IF NOT EXISTS epg_programmes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            tvg_id TEXT NOT NULL,
            start_utc TEXT NOT NULL,
            stop_utc TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            sub_title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            episode TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT '',
            UNIQUE(source_id, tvg_id, start_utc)
        );
        CREATE INDEX IF NOT EXISTS idx_epg_prog_lookup
        ON epg_programmes(tvg_id, start_utc);
        CREATE INDEX IF NOT EXISTS idx_epg_prog_range
        ON epg_programmes(start_utc, stop_utc);
        CREATE INDEX IF NOT EXISTS idx_epg_prog_source
        ON epg_programmes(source_id);
    """)
    conn.commit()
    logger.info('EPG 三表（epg_sources/epg_channels/epg_programmes）已就绪')

    # ── 种子数据：预置 EPG 源（仅空表时写入） ────────
    row = conn.execute('SELECT COUNT(*) FROM epg_sources').fetchone()
    if row and row[0] == 0:
        _seed_epg_sources(conn)

    # ── 从 YAML 导入种子数据 ──────────────────────
    _seed_from_yaml(conn)
    conn.close()

    # ── 幂等写入应用配置默认值 ─────────────────────
    # 首次部署自动将 Config._DEFAULT_VALUES 灌入 app_config 表，
    # 使「建库」步骤自包含，无需等待 Web 启动（部署脚本调用 init_db 即可获得默认值）。
    try:
        seed_app_config_defaults()
        fill_missing_app_config_defaults()
    except Exception as _e:
        logger.warning('应用配置默认值种子失败（Web 启动时会重试）: %s', _e)

    # ── 默认将生成的输出文件加入本地文件源 ───────────
    # 确保 <Output.output_dir>/<Output.filename>（默认 ./www/output/live.m3u）作为本地
    # 文件源存在于 Sources.local_dirs，使产出文件默认即可被解析与源管理展示。
    # 幂等：已包含则跳过；文件名跟随 Output.filename 设置变化。每次 init_db（含 Web 启动）
    # 都会执行，保证「默认加入」语义始终成立。
    try:
        from app import Config

        _cfg = Config()
        _raw = _cfg.get_sources().get('local_dirs', [])
        _out_dir = _cfg.get('Output', 'output_dir', './www/output')
        _fname = _cfg.get('Output', 'filename', 'live.m3u')
        # 统一正斜杠：保证 Windows/Linux/Docker 跨平台一致，
        # 避免 Windows 反斜杠在 Linux 部署时被误当作文件名一部分导致永久缺失。
        _rel = (_out_dir.rstrip('/\\') + '/' + _fname).strip()
        _rel = re.sub(r'[/\\]+', '/', _rel)
        if not _rel.startswith('.'):
            _rel = './' + _rel
        # 规范化：过滤空串 + 去重（保序），并将历史脏值（含 Windows 反斜杠）重写为 / 版本
        _seen: set[str] = set()
        _clean: list[str] = []
        for _d in _raw:
            if not isinstance(_d, str) or not _d.strip():
                continue
            _norm = re.sub(r'[/\\]+', '/', _d.strip())  # 统一分隔符后参与比对与存储
            if _norm in _seen:
                continue
            _seen.add(_norm)
            _clean.append(_norm)
        # 确保「规范默认本地源目录」始终存在（如 ./config/sources）。
        # 该目录是种子默认值，是本地源的主位置；输出文件 live.m3u 只是「附加」的其中一个源。
        # 历史上 ensure 曾在 local_dirs 退化时把该默认目录整体覆盖丢失，导致用户放进 config/sources 的源解析不到。
        # 此处强制保证其存在，且缺失时自动建目录，避免界面误报「路径缺失」。
        _default_local = Config._DEFAULT_VALUES.get('Sources.local_dirs', './config/sources')
        _default_local_norm = re.sub(r'[/\\]+', '/', str(_default_local).strip())
        if not _default_local_norm.startswith('.'):
            _default_local_norm = './' + _default_local_norm
        if _default_local_norm not in _seen:
            _clean.insert(0, _default_local_norm)
            _seen.add(_default_local_norm)
            try:
                _abs = os.path.abspath(_default_local_norm)
                if not os.path.isdir(_abs):
                    os.makedirs(_abs, exist_ok=True)
                    logger.info('已自动创建默认本地源目录: %s', _default_local_norm)
            except Exception as _mk_e:
                logger.warning('创建默认本地源目录失败（忽略）: %s', _mk_e)
        # 确保生成的输出文件默认作为本地文件源存在（规范 / 版本）
        if _rel not in _seen:
            _clean.append(_rel)
            _seen.add(_rel)
        _new_val = ','.join(_clean)
        # 仅当值确有变化才写回（避免无谓 DB 写与重启抖动）
        _old_val = ','.join([_d for _d in _raw if isinstance(_d, str)])
        if _new_val != _old_val:
            _cfg.set('Sources', 'local_dirs', _new_val)
            logger.info('已确保输出文件在本地源并规范化: %s', _rel)
    except Exception as _e2:
        logger.warning('输出文件加入本地源失败（忽略）: %s', _e2)

    logger.info('init_db 完成')
    # 确保登录失败锁定表始终存在（S1修复：即使跳过 lifespan 直接调用 init_db（如测试/手动初始化），登录锁定也能工作）
    init_login_lockout_table()
    return effective_pw


# ── 应用配置操作 ────────────────────────────────────

# ── 应用配置操作（原始读写——不自动加解密） ──────────


def get_app_config_raw(key: str) -> str | None:
    """读取配置原始值（不自动解密）"""
    conn = get_conn()
    row = conn.execute('SELECT value FROM app_config WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else None


def set_app_config_raw(key: str, value: str):
    """写入配置原始值（不自动加密）"""
    _execute(
        "INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    invalidate_config_cache()


def get_all_sensitive_config() -> dict[str, str]:
    """获取所有敏感配置的原始加密值"""
    from web.crypto_utils import SENSITIVE_KEYS

    if not SENSITIVE_KEYS:
        return {}
    conn = get_conn()
    rows = conn.execute(
        'SELECT key, value FROM app_config WHERE key IN ({})'.format(','.join('?' for _ in SENSITIVE_KEYS)),
        list(SENSITIVE_KEYS),
    ).fetchall()
    conn.close()
    return {row['key']: row['value'] for row in rows}


def get_all_sensitive_raw() -> dict[str, str]:
    """获取所有敏感配置的原始加密值（解密前的原始值），供 re_encrypt_all 使用

    排除机器绑定字段（MACHINE_BOUND_KEYS），因为它们的加密密钥来源不同。
    """
    from web.crypto_utils import MACHINE_BOUND_KEYS, SENSITIVE_KEYS

    # 排除机器绑定字段
    keys_to_query = SENSITIVE_KEYS - MACHINE_BOUND_KEYS
    if not keys_to_query:
        return {}
    conn = get_conn()
    placeholders = ','.join('?' for _ in keys_to_query)
    rows = conn.execute(
        f'SELECT key, value FROM app_config WHERE key IN ({placeholders})',
        list(keys_to_query),
    ).fetchall()
    conn.close()
    return {row['key']: row['value'] for row in rows}


# ── 应用配置操作（带自动加解密） ────────────────


def get_app_config(key: str) -> str | None:
    """读取单个配置值（敏感字段自动解密，机器绑定字段用机器ID解密）"""
    from web.crypto_utils import (
        decrypt_machine_bound,
        decrypt_value,
        is_machine_bound_key,
        is_sensitive_key,
    )

    conn = get_conn()
    try:
        row = conn.execute('SELECT value FROM app_config WHERE key = ?', (key,)).fetchone()
        if row:
            val = row['value']
            if is_machine_bound_key(key):
                return decrypt_machine_bound(val)
            if is_sensitive_key(key):
                return decrypt_value(val)
            return val
        return None
    finally:
        conn.close()


_CONFIG_VALID_KEYS = frozenset(
    {
        'proxy_enabled',
        'proxy_type',
        'proxy_host',
        'proxy_port',
        'proxy_username',
        'proxy_password',
        'download_interval',
        'test_interval',
        'test_concurrency',
        'retry_count',
        'test_timeout',
        'connect_timeout',
        'max_sources',
        'log_level',
        'log_rotation',
        'm3u_output_dir',
        'sort_by_group',
        'enable_insecure_sources',
        'Testing.timeout',
        'Testing.concurrent_threads',
        'Output.filename',
        'Network.proxy_enabled',
        'Logging.level',
        'Logging.file',
    }
)


def set_app_config(key: str, value: str):
    """INSERT OR REPLACE 写入单个配置值（敏感字段自动加密，机器绑定字段用机器ID加密）"""
    # Input validation
    if not any(k in key for k in _CONFIG_VALID_KEYS) and '.' not in key and '/' not in key:
        raise ValueError(f'Config key not allowed: {key!r}')
    if type(value) is str and len(value) > 65536:
        raise ValueError(f'Config value too long: {len(value)} bytes (max 65536)')

    from web.crypto_utils import (
        _is_valid_fernet_token,
        encrypt_machine_bound,
        encrypt_value,
        is_encrypted,
        is_machine_bound_encrypted,
        is_machine_bound_key,
        is_sensitive_key,
    )

    # 机器绑定字段：优先使用机器 ID 加密
    if is_machine_bound_key(key):
        if not is_machine_bound_encrypted(value):
            value = encrypt_machine_bound(value)
    elif is_sensitive_key(key) and not (is_encrypted(value) and _is_valid_fernet_token(value)):
        value = encrypt_value(value)
    _execute(
        'INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
        (key, value),
    )
    invalidate_config_cache()


def _get_all_config_raw() -> dict[str, dict[str, str]]:
    """无缓存版 get_all_config，实际查询 SQLite 并解密敏感字段"""
    from web.crypto_utils import (
        decrypt_machine_bound,
        decrypt_value,
        is_machine_bound_key,
        is_sensitive_key,
    )

    conn = get_conn()
    try:
        rows = conn.execute('SELECT key, value FROM app_config ORDER BY key').fetchall()
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            key = row['key']
            value = row['value']
            if is_machine_bound_key(key):
                value = decrypt_machine_bound(value)
            elif is_sensitive_key(key):
                value = decrypt_value(value)
            if '.' in key:
                section, field = key.split('.', 1)
            else:
                section = '__default__'
                field = key
            if section not in result:
                result[section] = {}
            result[section][field] = value
        return result
    finally:
        conn.close()


def get_all_config() -> dict[str, dict[str, str]]:
    """返回 {section: {key: value}} 格式的全量配置（敏感字段自动解密，5 秒 TTL 缓存）"""
    global _all_config_cache, _all_config_cache_time
    now = time.time()
    cached = _all_config_cache
    cached_time = _all_config_cache_time
    if cached is not None and now - cached_time < _ALL_CONFIG_CACHE_TTL:
        return cached
    with _all_config_cache_lock:
        now = time.time()
        if _all_config_cache is not None and now - _all_config_cache_time < _ALL_CONFIG_CACHE_TTL:
            return _all_config_cache
        result = _get_all_config_raw()
        _all_config_cache = result
        _all_config_cache_time = now
        return result


def has_app_config_data() -> bool:
    """检查 app_config 表是否有数据"""
    conn = get_conn()
    try:
        row = conn.execute('SELECT COUNT(*) as cnt FROM app_config').fetchone()
        return row['cnt'] > 0
    finally:
        conn.close()


def delete_app_config_by_section(section: str):
    """删除指定 section 的所有配置项"""
    _execute('DELETE FROM app_config WHERE key LIKE ?', (f'{section}.%',))


# ── 配置默认值种子 ────────────────────────────────


def seed_app_config_defaults() -> int:
    """首次启动时，将配置默认值写入 app_config 表（仅空表执行，幂等）。

    默认值来源：app.config.Config._DEFAULT_VALUES。
    返回写入的条目数。如果表已有数据则跳过，返回 0。
    """
    conn = get_conn()
    try:
        count = conn.execute('SELECT COUNT(*) FROM app_config').fetchone()[0]
        if count > 0:
            logger.info('app_config 已有数据，跳过默认值种子')
            return 0

        from app import Config

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entries = [(key, value, now) for key, value in Config._DEFAULT_VALUES.items()]
        conn.executemany(
            'INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, ?)',
            entries,
        )
        conn.commit()
        logger.info(f'已写入 {len(entries)} 条配置默认值到 app_config')
        return len(entries)
    except Exception as e:
        logger.error(f'app_config 默认值种子失败: {e}')
        return 0
    finally:
        conn.close()


def fill_missing_app_config_defaults() -> int:
    """补全 app_config 中缺失的默认值键（不覆盖已有值）。

    场景：当 schema（`Config._DEFAULT_VALUES`）新增配置键后，对「首次 seed 时尚无该键」
    的旧库，由于 seed_app_config_defaults 幂等跳过（表非空即跳过），新键不会自动入库，
    导致对应配置项从 /api/config 与配置中心 UI 上「消失」。

    本函数在每次启动补齐「默认值中存在、但 DB 缺失」的键，使老库自愈、配置项不再丢失。
    注意：仅写入代码默认值，绝不覆盖用户已修改的值。
    返回的条目数（0 表示无需补齐）。
    """
    from app import Config

    conn = get_conn()
    try:
        existing = {row[0] for row in conn.execute('SELECT key FROM app_config').fetchall()}
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entries = [(key, value, now) for key, value in Config._DEFAULT_VALUES.items() if key not in existing]
        if entries:
            conn.executemany(
                'INSERT OR IGNORE INTO app_config (key, value, updated_at) VALUES (?, ?, ?)',
                entries,
            )
            conn.commit()
            logger.info(f'已补全 {len(entries)} 条缺失配置默认值: ' + ', '.join(k for k, _, _ in entries))
        return len(entries)
    except Exception as e:
        logger.error(f'补全配置默认值失败: {e}')
        return 0
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> list[str]:
    """幂等补列：老库升级时为已存在的表补上新增列。

    Args:
        conn: 已打开的连接
        table: 表名
        columns: {列名: "TYPE NOT NULL DEFAULT ''"} 形式的列定义

    Returns:
        实际新增的列名列表
    """
    added: list[str] = []
    try:
        existing = {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    except sqlite3.Error as e:
        logger.warning(f'读取 {table} 表结构失败: {e}')
        return added
    if not existing:
        return added
    for col, ddl in columns.items():
        if col in existing:
            continue
        try:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
            added.append(col)
        except sqlite3.Error as e:
            logger.warning(f'{table} 补列 {col} 失败: {e}')
    if added:
        conn.commit()
        logger.info(f'{table} 已补列: ' + ', '.join(added))
    return added


# 预置 EPG 源清单（首次建库时写入 epg_sources）
# 字段：(名称, URL, 是否默认启用, 优先级[越小越优先], 备注)
DEFAULT_EPG_SOURCES: list[tuple[str, str, int, int, str]] = [
    (
        '51zmt',
        'http://epg.51zmt.top:8000/e.xml',
        1,
        10,
        '国内直连，每日 0 点更新，覆盖央视/卫视/地方台',
    ),
    (
        'Meroser 稳定版',
        'https://mirror.ghproxy.com/https://raw.githubusercontent.com/Meroser/IPTV/main/tvxml.xml',
        1,
        20,
        '稳定维护，每天 00:25 左右更新',
    ),
    (
        'Meroser 详情版',
        'https://mirror.ghproxy.com/https://raw.githubusercontent.com/Meroser/EPG-test/main/tvxml-test.xml.gz',
        0,
        15,
        '含剧情简介、演职员信息，体积较大',
    ),
    (
        'zbds',
        'https://epg.zbds.top/t.xml.gz',
        0,
        30,
        '备用源，gz 压缩',
    ),
    (
        'BurningC4 (jsDelivr)',
        'https://cdn.jsdelivr.net/gh/BurningC4/Chinese-IPTV@master/guide.xml',
        0,
        40,
        'Chinese-IPTV 的 CDN 加速地址，国内可达性较好',
    ),
    (
        'BurningC4 (GitHub)',
        'https://raw.githubusercontent.com/BurningC4/Chinese-IPTV/master/guide.xml',
        0,
        50,
        'Chinese-IPTV 原始地址，需能直连 GitHub',
    ),
    (
        'epg.pw',
        'https://epg.pw/xmltv/epg.xml',
        0,
        60,
        '可在 https://epg.pw 自定义频道后取专属地址',
    ),
]


def _seed_epg_sources(conn: sqlite3.Connection) -> int:
    """首次建库写入预置 EPG 源（幂等：URL 唯一，已存在则跳过）"""
    now = datetime.datetime.now().isoformat(timespec='seconds')
    rows = [
        (name, url, enabled, priority, remark, now, now) for name, url, enabled, priority, remark in DEFAULT_EPG_SOURCES
    ]
    try:
        conn.executemany(
            'INSERT OR IGNORE INTO epg_sources '
            '(name, url, enabled, priority, remark, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            rows,
        )
        conn.commit()
        logger.info(f'已写入 {len(rows)} 条预置 EPG 源')
        return len(rows)
    except sqlite3.Error as e:
        logger.warning(f'写入预置 EPG 源失败: {e}')
        return 0


def _seed_from_yaml(conn: sqlite3.Connection):
    """初始化数据库种子数据（分类规则 + 省份排除映射）

    分类规则：从 data/seed_classification_rules.sql 导入
    省份排除映射：内置硬编码项 + 从 YAML geography 自动推断
    """
    import yaml

    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RULES_PATH = os.path.join(PROJECT_ROOT, 'app', 'data', 'seed_classification_rules.sql')
    YAML_RULES_PATH = os.path.join(PROJECT_ROOT, 'config', 'channel_rules.yml')

    # 1. 导入分类规则 — 仅表为空时执行
    row = conn.execute('SELECT COUNT(*) FROM classification_rules').fetchone()
    if row and row[0] == 0:
        if not os.path.exists(RULES_PATH):
            logger.warning(f'种子 SQL 脚本不存在: {RULES_PATH}')
        else:
            try:
                with open(RULES_PATH, encoding='utf-8') as f:
                    sql_text = f.read()
                conn.executescript(sql_text)
                conn.commit()
                logger.info('已从种子 SQL 脚本导入分类规则')
            except Exception as e:
                conn.rollback()
                logger.error(f'种子 SQL 导入失败: {e}')
    else:
        logger.info('classification_rules 已有数据，跳过导入')

    # 2. 导入省份排除映射 — 仅表为空时执行
    row = conn.execute('SELECT COUNT(*) FROM province_exclusion_map').fetchone()
    if row and row[0] == 0:
        # 硬编码排除项
        hardcoded_exclusions = [
            ('北京', '河北', '河北→北京误匹配'),
            ('天津', '河北', '河北→天津误匹配（如"河北天津"）'),
            ('陕西', '山西', '陕→山字形含包'),
            ('山西', '陕西', '山→陕西字形含包'),
            ('广东', '广西', '广字形含包'),
            ('广西', '广东', '广字形含包'),
            ('湖南', '湖北', '湖字形含包'),
            ('湖北', '湖南', '湖字形含包'),
            ('河南', '河北', '河字形含包'),
            ('河北', '河南', '河字形含包'),
            ('山东', '山西', '山字形含包'),
            ('山西', '山东', '山字形含包'),
            ('江西', '江苏', '江字形含包'),
            ('江苏', '江西', '江字形含包'),
            ('黑龙', '黑河', '黑河市(黑龙江)vs黑龙江'),
            ('黑河', '黑龙', '黑河市不自动归入黑龙江'),
        ]
        for pk, ek, note in hardcoded_exclusions:
            with suppress(Exception):
                conn.execute(
                    'INSERT OR IGNORE INTO province_exclusion_map (province_keyword, excluded_keyword, note) VALUES (?, ?, ?)',
                    (pk, ek, note),
                )

        # 从 YAML 的 geography 中提取省份关键词，自动推断省份排除
        province_keywords_map = {}
        if os.path.exists(YAML_RULES_PATH):
            try:
                with open(YAML_RULES_PATH, encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                geo = data.get('geography') or {}
                for continent in geo.get('continents') or []:
                    for country in continent.get('countries') or []:
                        if country.get('code') != 'CN':
                            continue
                        for prov in country.get('provinces') or []:
                            pname = prov.get('name', '')
                            pkws = prov.get('keywords', [])
                            province_keywords_map[pname] = pkws
            except Exception:
                logger.warning('YAML 规则文件加载失败，跳过自动排除')

        prov_order = list(province_keywords_map.keys())
        for i in range(len(prov_order)):
            for j in range(len(prov_order)):
                if i == j:
                    continue
                pname_a = prov_order[i]
                pname_b = prov_order[j]
                kw_set_a = province_keywords_map[pname_a]
                kw_set_b = province_keywords_map[pname_b]
                for kwa in kw_set_a:
                    if len(kwa) < 2:
                        continue
                    for kwb in kw_set_b:
                        if len(kwb) < 2:
                            continue
                        # 如果kwa是kwb的子串或者kwb是kwa的子串
                        if kwa in kwb and kwa != kwb:
                            try:
                                conn.execute(
                                    'INSERT OR IGNORE INTO province_exclusion_map (province_keyword, excluded_keyword, note) VALUES (?, ?, ?)',
                                    (kwa, kwb, f'自动排除: {kwa}含于{kwb}'),
                                )
                            except Exception as _re:
                                logger.warning(f'插入省份排除异常({kwa}含于{kwb}): {_re}')
                        elif kwb in kwa and kwa != kwb:
                            try:
                                conn.execute(
                                    'INSERT OR IGNORE INTO province_exclusion_map (province_keyword, excluded_keyword, note) VALUES (?, ?, ?)',
                                    (kwb, kwa, f'自动排除: {kwb}含于{kwa}'),
                                )
                            except Exception as _re:
                                logger.warning(f'插入省份排除异常({kwb}含于{kwa}): {_re}')

        conn.commit()
        logger.info('已导入省份排除映射种子数据')
    else:
        logger.info('province_exclusion_map 已有数据，跳过导入')


# ── 分类字典表操作 ─────────────────────────────────
# 维度说明：content=内容分类, region=地区, language=语言,
#          quality=画质, media_type=媒体类型, genre=类型


def _seed_category_dictionary(conn: sqlite3.Connection, yaml_path: str):
    """从 channel_rules.yml 提取分类名，初始化 category_dictionary 表"""
    import yaml

    seeds = {}  # dimension -> [(value, label, sort_order)]

    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, encoding='utf-8') as f:
                data = yaml.safe_load(f)

            # 1. content 维度：从 categories 提取所有分类名
            cats = data.get('categories') or []
            seeds['content'] = []
            for i, cat in enumerate(cats):
                name = cat.get('name', '')
                if name and name != '其他频道':
                    seeds['content'].append((name, name, i))

            # 2. region 维度：从 geography → 中国 provinces/regions 提取省名
            geo = data.get('geography') or {}
            seeds['region'] = [('未知', '未知', 0)]
            sort_idx = 1
            for continent in geo.get('continents') or []:
                for country in continent.get('countries') or []:
                    if country.get('code') == 'CN':
                        for prov in country.get('provinces') or []:
                            pname = prov.get('name', '')
                            if pname:
                                seeds['region'].append((pname, pname, sort_idx))
                                sort_idx += 1
                        for reg in country.get('regions') or []:
                            rname = reg.get('name', '')
                            if rname:
                                seeds['region'].append((rname, rname, sort_idx))
                                sort_idx += 1

            # 3. quality 维度：从 channel_types 提取技术属性
            ch_types = data.get('channel_types') or {}
            seeds['quality'] = [('未知', '未知', 0)]
            quality_keys = ['高清', '超高清', '标清', '流畅']
            for i, qk in enumerate(quality_keys):
                if qk in ch_types:
                    seeds['quality'].append((qk, qk, i + 1))

        except Exception as e:
            logger.warning(f'从 YAML 提取分类字典种子失败: {e}')

    # 4. language 维度：硬编码
    if 'language' not in seeds:
        seeds['language'] = [
            ('未知', '未知', 0),
            ('中文', '中文', 1),
            ('英文', '英文', 2),
            ('日文', '日文', 3),
            ('韩文', '韩文', 4),
            ('粤语', '粤语', 5),
            ('闽南语', '闽南语', 6),
            ('其他语言', '其他语言', 99),
        ]

    # 5. media_type 维度：硬编码
    if 'media_type' not in seeds:
        seeds['media_type'] = [
            ('未知', '未知', 0),
            ('电视节目', '电视节目', 1),
            ('音频', '音频', 2),
            ('收音机', '收音机', 3),
            ('在线音频', '在线音频', 4),
        ]

    # 6. genre 维度：硬编码
    if 'genre' not in seeds:
        seeds['genre'] = [
            ('综合', '综合', 0),
            ('新闻', '新闻', 1),
            ('体育', '体育', 2),
            ('影视', '影视', 3),
            ('综艺', '综艺', 4),
            ('少儿', '少儿', 5),
            ('音乐', '音乐', 6),
            ('纪实', '纪实', 7),
            ('教育', '教育', 8),
            ('生活', '生活', 9),
            ('财经', '财经', 10),
            ('交通', '交通', 11),
            ('其他', '其他', 99),
        ]

    # 批量写入
    total = 0
    for dim, items in seeds.items():
        for value, label, sort_order in items:
            try:
                conn.execute(
                    'INSERT OR IGNORE INTO category_dictionary (dimension, value, label, sort_order) VALUES (?, ?, ?, ?)',
                    (dim, value, label, sort_order),
                )
                total += 1
            except Exception:
                pass
    conn.commit()
    logger.info(f'分类字典种子数据已写入 {total} 条')


def get_category_dictionary() -> dict[str, list[dict]]:
    """获取全部分类字典（按维度分组）"""
    conn = get_conn()
    rows = conn.execute(
        'SELECT dimension, value, label, sort_order FROM category_dictionary ORDER BY dimension, sort_order, value'
    ).fetchall()
    conn.close()
    result = {}
    for row in rows:
        dim = row['dimension']
        if dim not in result:
            result[dim] = []
        result[dim].append(
            {
                'value': row['value'],
                'label': row['label'],
                'sort_order': row['sort_order'],
            }
        )
    return result


def add_category_dictionary_option(dimension: str, value: str, label: str = '', sort_order: int = 99) -> bool:
    """添加一条分类字典选项"""
    try:
        conn = get_conn()
        conn.execute(
            'INSERT INTO category_dictionary (dimension, value, label, sort_order) VALUES (?, ?, ?, ?)',
            (dimension, value, label or value, sort_order),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f'添加分类字典选项失败 [{dimension}.{value}]: {e}')
        return False


def delete_category_dictionary_option(dimension: str, value: str) -> bool:
    """删除一条分类字典选项"""
    try:
        conn = get_conn()
        cursor = conn.execute(
            'DELETE FROM category_dictionary WHERE dimension = ? AND value = ?',
            (dimension, value),
        )
        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f'删除分类字典选项失败 [{dimension}.{value}]: {e}')
        return False


def set_category_dictionary_dimension(dimension: str, options: list[dict]) -> bool:
    """批量设置某个维度的所有选项（先删后插）"""
    try:
        conn = get_conn()
        conn.execute('DELETE FROM category_dictionary WHERE dimension = ?', (dimension,))
        for i, opt in enumerate(options):
            conn.execute(
                'INSERT INTO category_dictionary (dimension, value, label, sort_order) VALUES (?, ?, ?, ?)',
                (
                    dimension,
                    opt['value'],
                    opt.get('label', opt['value']),
                    opt.get('sort_order', i),
                ),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f'批量设置分类字典维度失败 [{dimension}]: {e}')
        return False


def cleanup_audit_logs(max_days: int = 90):
    """清理旧审计日志（启动时调用一次，不频繁操作）"""
    try:
        conn = get_conn()
        try:
            conn.execute(
                "DELETE FROM audit_logs WHERE created_at < datetime('now', ? || ' days')",
                (str(-max_days),),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f'审计日志清理失败: {e}')


# ── 用户操作 ──────────────────────────────────────


def get_user_by_username(username: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute('SELECT * FROM users WHERE username = ? AND is_active = 1', (username,)).fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def verify_password(username: str, password: str) -> dict | None:
    """验证密码，成功返回用户 dict，失败返回 None"""
    user = get_user_by_username(username)
    if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        return user
    return None


def list_users() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            'SELECT id, username, role, display_name, is_active, created_at FROM users ORDER BY id'
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_user(username: str, password: str, role: str = 'viewer', display_name: str = '') -> int:
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cursor = _execute(
        'INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)',
        (username, pw_hash, role, display_name),
    )
    return cursor.lastrowid


def delete_user(user_id: int) -> bool:
    cursor = _execute('DELETE FROM users WHERE id = ?', (user_id,))
    return cursor.rowcount > 0


def update_user(user_id: int, **kwargs) -> bool:
    """更新用户信息（role, display_name, password等）"""
    updates = []
    params = []
    for key in ('role', 'display_name'):
        if key in kwargs and kwargs[key] is not None:
            updates.append(f'{key} = ?')
            params.append(kwargs[key])
    if kwargs.get('password'):
        pw_hash = bcrypt.hashpw(kwargs['password'].encode(), bcrypt.gensalt()).decode()
        updates.append('password_hash = ?')
        params.append(pw_hash)
    if not updates:
        return False
    updates.append('updated_at = CURRENT_TIMESTAMP')
    params.append(user_id)
    cursor = _execute(f'UPDATE users SET {", ".join(updates)} WHERE id = ?', params)
    return cursor.rowcount > 0


def toggle_user(user_id: int) -> bool | None:
    """切换用户启用/禁用状态，返回新状态或None（用户不存在）"""
    user = get_user_by_id(user_id)
    if not user:
        return None
    new_status = 0 if user['is_active'] else 1
    _execute(
        'UPDATE users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (new_status, user_id),
    )
    return bool(new_status)


def update_user_password(user_id: int, new_password: str) -> bool:
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    cursor = _execute(
        'UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (pw_hash, user_id),
    )
    if cursor.rowcount > 0:
        # 用户已更换为管理员/自身设置的新密码，解除首次强制改密标记，避免死循环
        set_password_change_required(_username_for_id(user_id), False)
    return cursor.rowcount > 0


# ── Session 操作 ──────────────────────────────────


def create_session_db(user_id: int, username: str, role: str, ttl: float = 86400) -> str:
    """创建 session 并存入 SQLite"""
    import time
    import uuid

    session_id = uuid.uuid4().hex
    now = time.time()
    conn = get_conn()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO sessions (id, user_id, username, role, created_at, last_active) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, user_id, username, role, now, now),
        )
        conn.commit()
        return session_id
    finally:
        conn.close()


def get_session_db(session_id: str, idle_timeout: int = 7200, session_ttl: int = 86400) -> dict | None:
    """从 SQLite 获取 session"""
    import time

    conn = get_conn()
    try:
        row = conn.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if not row:
            return None
        s = dict(row)
        now = time.time()
        if now - s['created_at'] > session_ttl or now - s['last_active'] > idle_timeout:
            conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
            conn.commit()
            return None
        # 刷新最后活跃时间
        conn.execute('UPDATE sessions SET last_active = ? WHERE id = ?', (now, session_id))
        conn.commit()
        s['last_active'] = now
        return s
    finally:
        conn.close()


def destroy_session_db(session_id: str):
    conn = get_conn()
    try:
        conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        conn.commit()
    finally:
        conn.close()


def update_session_activity_db(session_id: str, last_active: float):
    """更新 session 最后活跃时间"""
    _execute('UPDATE sessions SET last_active = ? WHERE id = ?', (last_active, session_id))


# Session 超时常量（与 auth.py 保持一致）
SESSION_IDLE_TIMEOUT = 2 * 3600  # 2小时无操作过期
SESSION_TOTAL_TTL = 24 * 3600  # 24小时总过期


def cleanup_expired_sessions():
    """清理过期 session（使用写锁保护并发）"""
    import time

    now = time.time()
    _execute(
        'DELETE FROM sessions WHERE last_active < ? OR created_at < ?',
        (now - SESSION_IDLE_TIMEOUT, now - SESSION_TOTAL_TTL),
    )


# ── 审计日志 ──────────────────────────────────────


def add_audit_log(
    user_id: int,
    username: str,
    action: str,
    target: str = '',
    detail: str = '',
    ip_address: str = '',
):
    _execute(
        'INSERT INTO audit_logs (user_id, username, action, target, detail, ip_address) VALUES (?, ?, ?, ?, ?, ?)',
        (user_id, username, action, target, detail, ip_address),
    )


def list_audit_logs(page: int = 1, size: int = 50, action_filter: str = '') -> dict:
    conn = get_conn()
    try:
        offset = (page - 1) * size
        if action_filter:
            total = conn.execute('SELECT COUNT(*) FROM audit_logs WHERE action = ?', (action_filter,)).fetchone()[0]
            rows = conn.execute(
                'SELECT * FROM audit_logs WHERE action = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
                (action_filter, size, offset),
            ).fetchall()
        else:
            total = conn.execute('SELECT COUNT(*) FROM audit_logs').fetchone()[0]
            rows = conn.execute(
                'SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ? OFFSET ?',
                (size, offset),
            ).fetchall()
        return {
            'total': total,
            'page': page,
            'size': size,
            'logs': [dict(r) for r in rows],
        }
    finally:
        conn.close()


def list_audit_actions() -> list:
    conn = get_conn()
    try:
        rows = conn.execute('SELECT DISTINCT action FROM audit_logs ORDER BY action').fetchall()
        return [r['action'] for r in rows]
    finally:
        conn.close()


# ── 分类规则操作 ────────────────────────────────


def get_all_classification_rules(rule_type: str | None = None) -> list[dict]:
    """获取分类规则列表，可选的 rule_type 过滤（维度键如 'content'|'region'|'media_type' 等）"""
    conn = get_conn()
    if rule_type:
        rows = conn.execute(
            'SELECT * FROM classification_rules WHERE rule_type = ? ORDER BY sort_order, priority, id',
            (rule_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM classification_rules ORDER BY rule_type, sort_order, priority, id'
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_classification_rules(rule_type: str | None = None) -> list[dict]:
    """获取活跃的分类规则（is_active=1），可选的 rule_type 过滤（维度键如 'content'|'region'|'media_type' 等）"""
    conn = get_conn()
    if rule_type:
        rows = conn.execute(
            'SELECT * FROM classification_rules WHERE is_active = 1 AND rule_type = ? ORDER BY sort_order, priority, id',
            (rule_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM classification_rules WHERE is_active = 1 ORDER BY rule_type, sort_order, priority, id'
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_classification_rule(rule_dict: dict) -> int:
    """新增分类规则，返回新记录的 ID"""
    import datetime
    import json as _json

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    name = rule_dict.get('name', '')
    rule_type = rule_dict.get('rule_type', 'category')
    keywords = rule_dict.get('keywords', [])
    if isinstance(keywords, (list, tuple)):
        keywords = _json.dumps(keywords, ensure_ascii=False)
    priority = rule_dict.get('priority', 100)
    sort_order = rule_dict.get('sort_order', 0)
    is_active = rule_dict.get('is_active', 1)
    cursor = _execute(
        'INSERT INTO classification_rules (rule_type, name, keywords, priority, sort_order, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (rule_type, name, keywords, priority, sort_order, is_active, now, now),
    )
    return cursor.lastrowid


def update_classification_rule(rule_id: int, rule_dict: dict) -> bool:
    """更新分类规则，返回是否更新成功"""
    import datetime
    import json as _json

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    updates = []
    params = []
    for key in ('rule_type', 'name', 'priority', 'sort_order', 'is_active'):
        if key in rule_dict and rule_dict[key] is not None:
            updates.append(f'{key} = ?')
            params.append(rule_dict[key])
    if 'keywords' in rule_dict and rule_dict['keywords'] is not None:
        kw = rule_dict['keywords']
        if isinstance(kw, (list, tuple)):
            kw = _json.dumps(kw, ensure_ascii=False)
        updates.append('keywords = ?')
        params.append(kw)
    if not updates:
        return False
    updates.append('updated_at = ?')
    params.append(now)
    params.append(rule_id)
    cursor = _execute(f'UPDATE classification_rules SET {", ".join(updates)} WHERE id = ?', params)
    return cursor.rowcount > 0


def delete_classification_rule(rule_id: int) -> bool:
    """删除分类规则"""
    cursor = _execute('DELETE FROM classification_rules WHERE id = ?', (rule_id,))
    return cursor.rowcount > 0


# ── 恢复默认（D-4 修复）─────────────────────────


def reset_category_dictionary_to_default():
    """D-4 修复：将分类字典恢复为系统默认种子值（先清空再种子）。"""
    yaml_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config',
        'channel_rules.yml',
    )
    conn = get_conn()
    try:
        conn.execute('DELETE FROM category_dictionary')
        _seed_category_dictionary(conn, yaml_path)
        conn.commit()
    finally:
        conn.close()


def reset_classification_rules_to_default() -> int:
    """D-4 修复：将分类规则恢复为系统默认（从 channel_rules.yml 重新导入）。"""
    yaml_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config',
        'channel_rules.yml',
    )
    if not os.path.exists(yaml_path):
        return 0
    import yaml

    with open(yaml_path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    _execute('DELETE FROM classification_rules')
    count = 0
    for sort_idx, cat in enumerate(data.get('categories') or []):
        name = cat.get('name', '')
        if not name:
            continue
        add_classification_rule(
            {
                'rule_type': 'content',
                'name': name,
                'keywords': cat.get('keywords', []),
                'priority': cat.get('priority', 100),
                'sort_order': sort_idx,
                'is_active': 1,
            }
        )
        count += 1
    for sort_idx, (ctype_name, ctype_keywords) in enumerate((data.get('channel_types') or {}).items()):
        add_classification_rule(
            {
                'rule_type': 'media_type',
                'name': ctype_name,
                'keywords': ctype_keywords,
                'priority': 50,
                'sort_order': sort_idx,
                'is_active': 1,
            }
        )
        count += 1
    _execute('DELETE FROM province_exclusion_map')
    conn = get_conn()
    try:
        _seed_from_yaml(conn)
        conn.commit()
    finally:
        conn.close()
    return count


# ── 分类维度操作 ────────────────────────────────


def get_all_dimensions() -> list[dict]:
    """获取所有维度定义"""
    conn = get_conn()
    rows = conn.execute('SELECT * FROM classification_dimensions ORDER BY sort_order, id').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_dimension(dim_key: str, dim_name: str, sort_order: int = 0) -> int:
    """新增维度"""
    cursor = _execute(
        'INSERT INTO classification_dimensions (dim_key, dim_name, sort_order) VALUES (?, ?, ?)',
        (dim_key, dim_name, sort_order),
    )
    return cursor.lastrowid


def delete_dimension(dim_key: str) -> bool:
    """删除维度（同时删除该维度的所有规则）"""
    conn = None
    try:
        with _write_lock:
            conn = get_conn()
            # 先删除该维度下的所有规则
            conn.execute('DELETE FROM classification_rules WHERE rule_type = ?', (dim_key,))
            # 再删除维度定义
            cursor = conn.execute('DELETE FROM classification_dimensions WHERE dim_key = ?', (dim_key,))
            conn.commit()
            return cursor.rowcount > 0
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception as _re:
                logger.warning(f'delete_dimension回滚异常: {_re}')
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _re:
                logger.warning(f'delete_dimension关闭连接异常: {_re}')


# ── 流源分类结果持久化操作 ────────────────────────


def save_source_categories(source_id: int, categories: dict[str, str]) -> bool:
    """批量保存某个源的各维度分类结果。

    只插入非 '未知' 且有值的维度，已有记录会 UPDATE。
    is_manual 标记为 False（自动计算）。

    Args:
        source_id: stream_sources.id
        categories: {'content': '央视频道', 'region': '境内', ...}

    Returns:
        bool: 是否成功
    """
    conn = None
    try:
        with _write_lock:
            conn = get_conn()
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for dim_key, dim_value in categories.items():
                if not dim_value or dim_value == '未知' or dim_value == 'None':
                    continue
                # 检查是否已有人工修正记录
                existing = conn.execute(
                    'SELECT is_manual FROM stream_source_categories WHERE source_id=? AND dim_key=?',
                    (source_id, dim_key),
                ).fetchone()
                if existing and existing['is_manual'] == 1:
                    continue  # 人工修正的不覆盖

                conn.execute(
                    """
                    INSERT INTO stream_source_categories (source_id, dim_key, dim_value, is_manual, updated_at)
                    VALUES (?, ?, ?, 0, ?)
                    ON CONFLICT(source_id, dim_key) DO UPDATE SET
                        dim_value = excluded.dim_value,
                        is_manual = 0,
                        updated_at = excluded.updated_at
                """,
                    (source_id, dim_key, dim_value, now),
                )
            conn.commit()
            return True
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception as _re:
                logger.warning(f'save_source_categories回滚异常: {_re}')
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _re:
                logger.warning(f'save_source_categories关闭连接异常: {_re}')


def get_source_categories(source_id: int) -> dict[str, str]:
    """获取某个源所有维度的分类结果。

    Returns:
        {'content': '央视频道', 'region': '境内', 'language': 'zh', ...}
        没有记录的维度返回 None
    """
    conn = get_conn()
    rows = conn.execute(
        'SELECT dim_key, dim_value FROM stream_source_categories WHERE source_id=?',
        (source_id,),
    ).fetchall()
    conn.close()
    result = {}
    for row in rows:
        result[row['dim_key']] = row['dim_value']
    return result


def update_source_category(source_id: int, dim_key: str, dim_value: str) -> bool:
    """人工修正某个维度的分类值，标记 is_manual=1

    Returns:
        bool: 是否成功
    """
    try:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor = _execute(
            """
            INSERT INTO stream_source_categories (source_id, dim_key, dim_value, is_manual, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(source_id, dim_key) DO UPDATE SET
                dim_value = excluded.dim_value,
                is_manual = 1,
                updated_at = excluded.updated_at
        """,
            (source_id, dim_key, dim_value, now),
        )
        return cursor.rowcount > 0
    except Exception:
        return False


def delete_source_categories(source_id: int) -> bool:
    """删除某个源的所有分类结果（例如源被删除时清理）"""
    try:
        cursor = _execute('DELETE FROM stream_source_categories WHERE source_id = ?', (source_id,))
        return cursor.rowcount > 0
    except Exception:
        return False


# ── 省份排除映射操作 ────────────────────────────


def get_all_exclusions() -> list[dict]:
    """获取所有排除映射"""
    conn = get_conn()
    rows = conn.execute('SELECT * FROM province_exclusion_map ORDER BY province_keyword, excluded_keyword').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_exclusion(province_keyword: str, excluded_keyword: str, note: str = '') -> int | None:
    """新增排除映射，成功返回 ID，重复返回 None"""
    try:
        cursor = _execute(
            'INSERT INTO province_exclusion_map (province_keyword, excluded_keyword, note) VALUES (?, ?, ?)',
            (province_keyword, excluded_keyword, note),
        )
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None


def delete_exclusion(exclusion_id: int) -> bool:
    """删除排除映射"""
    cursor = _execute('DELETE FROM province_exclusion_map WHERE id = ?', (exclusion_id,))
    return cursor.rowcount > 0


def check_exclusion(province_keyword: str, excluded_keyword: str) -> dict | None:
    """检查指定排除是否存在，存在返回记录 dict"""
    conn = get_conn()
    row = conn.execute(
        'SELECT * FROM province_exclusion_map WHERE province_keyword = ? AND excluded_keyword = ?',
        (province_keyword, excluded_keyword),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── 频道全名映射操作 ────────────────────────────────


def get_channel_name_mapping(channel_name: str) -> dict[str, str] | None:
    """查频道全名映射，返回各维度分类字典，不存在返回 None"""
    conn = get_conn()
    row = conn.execute(
        'SELECT content, region, language, quality, media_type, genre, tvg_id, tvg_logo '
        'FROM channel_name_mapping WHERE channel_name = ?',
        (channel_name,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_channel_name_mapping(channel_name: str, categories: dict[str, str]) -> bool:
    """保存或更新频道全名映射（UPSERT，保留 EPG 列 tvg_id / tvg_logo 不被抹除）"""
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO channel_name_mapping
                (channel_name, content, region, language, quality, media_type, genre, is_manual, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'))
            ON CONFLICT(channel_name) DO UPDATE SET
                content = excluded.content,
                region = excluded.region,
                language = excluded.language,
                quality = excluded.quality,
                media_type = excluded.media_type,
                genre = excluded.genre,
                is_manual = 1,
                updated_at = datetime('now')
        """,
            (
                channel_name,
                categories.get('content', '其他频道'),
                categories.get('region', '未知'),
                categories.get('language', '未知'),
                categories.get('quality', '高清'),
                categories.get('media_type', '电视节目'),
                categories.get('genre', '综合'),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f'保存频道全名映射失败 [{channel_name}]: {e}')
        if conn:
            try:
                conn.rollback()
            except Exception as _re:
                logger.warning(f'save_channel_name_mapping回滚异常: {_re}')
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _re:
                logger.warning(f'save_channel_name_mapping关闭连接异常: {_re}')


def delete_channel_name_mapping(channel_name: str) -> bool:
    """删除频道全名映射"""
    try:
        cursor = _execute('DELETE FROM channel_name_mapping WHERE channel_name = ?', (channel_name,))
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f'删除频道全名映射失败 [{channel_name}]: {e}')
        return False


def list_channel_name_mappings(page: int = 1, page_size: int = 50) -> tuple[list[dict], int]:
    """分页列出所有频道全名映射"""
    conn = get_conn()
    total = conn.execute('SELECT COUNT(*) FROM channel_name_mapping').fetchone()[0]
    offset = (page - 1) * page_size
    rows = conn.execute(
        'SELECT channel_name, content, region, language, quality, media_type, genre, '
        'tvg_id, tvg_logo, is_manual, created_at, updated_at '
        'FROM channel_name_mapping ORDER BY channel_name LIMIT ? OFFSET ?',
        (page_size, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def set_channel_tvg_info(channel_name: str, tvg_id: str = '', tvg_logo: str = '') -> bool:
    """写入/更新频道的 EPG 对齐信息（tvg_id / tvg_logo），不影响分类维度。

    传空字符串表示"不修改该字段"，便于只补 logo 或只补 id。
    """
    if not channel_name or (not tvg_id and not tvg_logo):
        return False
    try:
        _execute(
            """
            INSERT INTO channel_name_mapping (channel_name, tvg_id, tvg_logo, is_manual, created_at, updated_at)
            VALUES (?, ?, ?, 0, datetime('now'), datetime('now'))
            ON CONFLICT(channel_name) DO UPDATE SET
                tvg_id = CASE WHEN excluded.tvg_id != '' THEN excluded.tvg_id ELSE channel_name_mapping.tvg_id END,
                tvg_logo = CASE WHEN excluded.tvg_logo != '' THEN excluded.tvg_logo ELSE channel_name_mapping.tvg_logo END,
                updated_at = datetime('now')
            """,
            (channel_name, tvg_id, tvg_logo),
        )
        return True
    except Exception as e:
        logger.error(f'写入频道 EPG 映射失败 [{channel_name}]: {e}')
        return False


def get_all_channel_tvg_map() -> dict[str, dict[str, str]]:
    """一次性取全部频道的 tvg_id / tvg_logo 映射，供 M3U 生成时批量注入。

    Returns: {channel_name: {'tvg_id': ..., 'tvg_logo': ...}}
    """
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT channel_name, tvg_id, tvg_logo FROM channel_name_mapping WHERE tvg_id != '' OR tvg_logo != ''"
        ).fetchall()
        conn.close()
        return {r['channel_name']: {'tvg_id': r['tvg_id'] or '', 'tvg_logo': r['tvg_logo'] or ''} for r in rows}
    except Exception as e:
        logger.warning(f'读取频道 EPG 映射失败: {e}')
        return {}


def batch_import_mappings_from_current_sources() -> int:
    """从规则引擎跑所有已测试成功的源（从 stream_source_categories 表取频道名），
    批量写入 channel_name_mapping
    Returns: 写入条目数
    """
    try:
        from app import ChannelRules

        rules = ChannelRules()
    except Exception as e:
        logger.error(f'加载 ChannelRules 失败: {e}')
        return 0

    # 从配置或环境变量获取 m3u 输出路径（F-3: 删除模块级硬编码常量）
    m3u_output_dir = os.environ.get('M3U_OUTPUT_DIR', './www/output')
    # 将相对路径解析为绝对路径（相对于当前工作目录）
    if m3u_output_dir and not os.path.isabs(m3u_output_dir):
        m3u_output_dir = os.path.abspath(m3u_output_dir)
    m3u_qualified_path = os.path.join(m3u_output_dir, 'qualified_live.m3u')
    m3u_path = os.path.join(m3u_output_dir, 'live.m3u')

    # 尝试从 stream_sources 表读
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT name FROM stream_sources WHERE status='success' AND name IS NOT NULL AND name != ''"
        ).fetchall()
    except Exception as _:
        logger.warning('stream_sources 表不可用，回退到 m3u 文件解析')
        # stream_sources 表不存在，尝试从 m3u 文件解析（路径配置化）
        rows = []
        m3u_search_paths = [
            m3u_qualified_path,
            m3u_path,
            os.path.join(os.path.dirname(m3u_output_dir), 'sources', '*.m3u'),
        ]
        for m3u_path in m3u_search_paths:
            import glob

            files = glob.glob(m3u_path) if '*' in m3u_path else ([m3u_path] if os.path.exists(m3u_path) else [])
            for fpath in files:
                try:
                    with open(fpath, encoding='utf-8') as f:
                        for line in f:
                            if line.startswith('#EXTINF:'):
                                import re

                                m = re.search(r'channel-name="?([^",]+)', line)
                                if m:
                                    rows.append((m.group(1).strip(),))
                                else:
                                    m2 = re.search(r',([^,]+)$', line)
                                    if m2:
                                        rows.append((m2.group(1).strip(),))
                except Exception as _:
                    logger.warning(f'解析m3u文件异常({fpath}): {_}')
                    continue
            if rows:
                break
    finally:
        conn.close() if conn else None

    if not rows:
        logger.warning('没有找到已成功的源，跳过批量导入')
        return 0

    imported = 0
    seen = set()
    for row in rows:
        ch_name = row[0].strip()
        if not ch_name or ch_name in seen:
            continue
        seen.add(ch_name)
        # 检查是否已存在
        existing = get_channel_name_mapping(ch_name)
        if existing:
            continue
        cats = rules.determine_categories(ch_name)
        ok = save_channel_name_mapping(ch_name, cats)
        if ok:
            imported += 1

    logger.info(f'批量导入频道全名映射: {imported} 条')
    return imported


# ── GitHub 下载缓存（持久化下载状态） ─────────────────


def upsert_github_download_cache(repo_key: str, files: list[dict]):
    """写入/更新 GitHub 源的下载缓存。files: [{filename, file_size}, ...]"""
    try:
        conn = get_conn()
        for f in files:
            conn.execute(
                'INSERT OR REPLACE INTO github_download_cache (repo_key, filename, file_size, downloaded_at) '
                "VALUES (?, ?, ?, datetime('now'))",
                (repo_key, f['filename'], f.get('file_size', 0)),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f'写入 GitHub 下载缓存失败 [{repo_key}]: {e}')


def get_github_download_cache(repo_key: str) -> list[dict]:
    """获取某个 GitHub 源的下载缓存记录"""
    try:
        conn = get_conn()
        rows = conn.execute(
            'SELECT filename, file_size, downloaded_at FROM github_download_cache '
            'WHERE repo_key = ? ORDER BY downloaded_at DESC',
            (repo_key,),
        ).fetchall()
        conn.close()
        return [{'filename': r[0], 'file_size': r[1], 'downloaded_at': r[2]} for r in rows]
    except Exception as e:
        logger.error(f'查询 GitHub 下载缓存失败 [{repo_key}]: {e}')
        return []


def clear_github_download_cache(repo_key: str | None = None):
    """清除 GitHub 下载缓存。repo_key 为空则清空所有"""
    try:
        conn = get_conn()
        if repo_key:
            conn.execute('DELETE FROM github_download_cache WHERE repo_key = ?', (repo_key,))
        else:
            conn.execute('DELETE FROM github_download_cache')
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f'清除 GitHub 下载缓存失败: {e}')


def get_github_download_cache_summary(repo_key: str) -> dict:
    """获取统计信息：{discovered: N, matched: N, total_size: N}"""
    try:
        conn = get_conn()
        total = conn.execute('SELECT COUNT(*) FROM github_download_cache WHERE repo_key = ?', (repo_key,)).fetchone()[0]
        conn.close()
        return {'discovered': total, 'matched': total, 'total_size': 0}
    except Exception as e:
        logger.error(f'查询 GitHub 下载缓存摘要失败 [{repo_key}]: {e}')
        return {'discovered': 0, 'matched': 0, 'total_size': 0}


# ═══════════════════════════════════════════════════
# 首次登录强制修改密码（《网络安全法》第24条）
# ═══════════════════════════════════════════════════

# 默认初始管理员密码（李总指定，与 Go 版一致；首次登录后强制修改）
DEFAULT_ADMIN_PASSWORD = 'Admin@123'


def _username_for_id(user_id: int) -> str:
    """根据用户ID取用户名（用于改密后清除强制改密标记）"""
    conn = get_conn()
    try:
        row = conn.execute('SELECT username FROM users WHERE id = ?', (user_id,)).fetchone()
        return row['username'] if row else ''
    finally:
        conn.close()


def is_admin_on_default_password() -> bool:
    """判断 admin 当前是否仍在使用默认初始密码（用于决定是否置位首次强制改密）"""
    return bool(verify_password('admin', DEFAULT_ADMIN_PASSWORD))


def set_password_change_required(username: str, required: bool = True):
    """设置用户是否需要在下次登录时修改密码（《网络安全法》第24条）"""
    conn = get_conn()
    try:
        conn.execute(
            'CREATE TABLE IF NOT EXISTS password_change_required ('
            'username TEXT PRIMARY KEY, '
            'required INTEGER NOT NULL DEFAULT 1, '
            "created_at TEXT DEFAULT (datetime('now'))"
            ')'
        )
        conn.execute(
            'INSERT OR REPLACE INTO password_change_required (username, required) VALUES (?, ?)',
            (username, 1 if required else 0),
        )
        conn.commit()
    finally:
        conn.close()


def get_password_change_required(username: str) -> bool:
    """查询用户是否需要在下次登录时修改密码（表不存在时自动建表，保证幂等）"""
    conn = get_conn()
    try:
        conn.execute(
            'CREATE TABLE IF NOT EXISTS password_change_required ('
            'username TEXT PRIMARY KEY, '
            'required INTEGER NOT NULL DEFAULT 1, '
            "created_at TEXT DEFAULT (datetime('now'))"
            ')'
        )
        conn.commit()
        row = conn.execute(
            'SELECT required FROM password_change_required WHERE username = ?',
            (username,),
        ).fetchone()
        return bool(row and row['required'])
    finally:
        conn.close()


def clear_password_change_required(username: str):
    """清除修改密码标记（用户已修改密码后调用）"""
    set_password_change_required(username, False)


# ── 登录失败锁定表 ──────────────────────────────


def init_login_lockout_table():
    """初始化登录失败锁定表（《网络安全法》第24条：身份鉴别失败处理）"""
    conn = get_conn()
    conn.execute(
        'CREATE TABLE IF NOT EXISTS login_lockout ('
        'username TEXT PRIMARY KEY, '
        'attempts INTEGER NOT NULL DEFAULT 0, '
        'lockout_until REAL'
        ')'
    )
    conn.commit()
    conn.close()
    logger.info('login_lockout 表已就绪')


# ── 登录锁定操作（从 core.py 迁移，消除重复 SQL 逻辑） ──────────

LOGIN_LOCKOUT_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_DURATION = 15 * 60  # 15分钟


def check_login_lockout(username: str) -> tuple[bool, int]:
    """检查用户是否被锁定。返回 (is_locked, remaining_seconds)"""
    conn = get_conn()
    try:
        conn.execute(
            'CREATE TABLE IF NOT EXISTS login_lockout ('
            'username TEXT PRIMARY KEY, '
            'attempts INTEGER NOT NULL DEFAULT 0, '
            'lockout_until REAL'
            ')'
        )
        conn.commit()
        row = conn.execute(
            'SELECT attempts, lockout_until FROM login_lockout WHERE username = ?',
            (username,),
        ).fetchone()
        if not row:
            return False, 0
        lockout_until = row['lockout_until']
        import time

        now = time.time()
        if lockout_until and now < lockout_until:
            remaining = int(lockout_until - now)
            return True, remaining
        # 锁定已过期，重置计数器
        if lockout_until and now >= lockout_until:
            conn.execute('DELETE FROM login_lockout WHERE username = ?', (username,))
            conn.commit()
        return False, 0
    finally:
        conn.close()


def record_login_failure(username: str):
    """记录登录失败，达到阈值则锁定"""
    import time

    now = time.time()
    _execute(
        'INSERT INTO login_lockout (username, attempts, lockout_until) VALUES (?, 1, NULL) '
        'ON CONFLICT(username) DO UPDATE SET '
        'attempts = CASE WHEN attempts >= ? THEN ? ELSE attempts + 1 END, '
        'lockout_until = CASE WHEN attempts + 1 >= ? THEN ? ELSE lockout_until END',
        (
            username,
            LOGIN_LOCKOUT_MAX_ATTEMPTS - 1,
            LOGIN_LOCKOUT_MAX_ATTEMPTS,
            LOGIN_LOCKOUT_MAX_ATTEMPTS - 1,
            now + LOGIN_LOCKOUT_DURATION,
        ),
    )


def reset_login_lockout(username: str):
    """登录成功后重置锁定计数器"""
    _execute('DELETE FROM login_lockout WHERE username = ?', (username,))


def get_login_lockout_attempts(username: str) -> int:
    """返回当前累计失败尝试次数（用于给前端提示剩余可尝试次数）"""
    conn = get_conn()
    try:
        row = conn.execute(
            'SELECT attempts FROM login_lockout WHERE username = ?',
            (username,),
        ).fetchone()
        return int(row['attempts']) if row and row['attempts'] else 0
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# EPG（电子节目单）数据访问
# ══════════════════════════════════════════════════════════

EPG_SOURCE_FIELDS = (
    'id, name, url, enabled, priority, refresh_mode, refresh_at, refresh_minutes, remark, '
    'last_fetch_at, last_status, last_error, last_channel_count, last_programme_count, '
    'last_duration_ms, created_at, updated_at'
)


def list_epg_sources(enabled_only: bool = False) -> list[dict]:
    """列出 EPG 源，按 priority 升序（越小越优先）"""
    conn = get_conn()
    try:
        sql = f'SELECT {EPG_SOURCE_FIELDS} FROM epg_sources'
        if enabled_only:
            sql += ' WHERE enabled = 1'
        sql += ' ORDER BY priority ASC, id ASC'
        return [dict(r) for r in conn.execute(sql).fetchall()]
    except Exception as e:
        logger.error(f'列出 EPG 源失败: {e}')
        return []
    finally:
        conn.close()


def get_epg_source(source_id: int) -> dict | None:
    """按 ID 取单个 EPG 源"""
    conn = get_conn()
    try:
        row = conn.execute(
            f'SELECT {EPG_SOURCE_FIELDS} FROM epg_sources WHERE id = ?',
            (source_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_epg_source(
    name: str,
    url: str,
    enabled: bool = True,
    priority: int = 100,
    refresh_mode: str = '',
    refresh_at: str = '',
    refresh_minutes: int = 0,
    remark: str = '',
) -> int:
    """新增 EPG 源，返回新 ID；URL 重复返回 0"""
    url = (url or '').strip()
    if not url:
        return 0
    now = datetime.datetime.now().isoformat(timespec='seconds')
    try:
        cursor = _execute(
            'INSERT INTO epg_sources (name, url, enabled, priority, refresh_mode, refresh_at, '
            'refresh_minutes, remark, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                (name or url)[:120],
                url,
                1 if enabled else 0,
                int(priority),
                refresh_mode or '',
                refresh_at or '',
                int(refresh_minutes or 0),
                remark or '',
                now,
                now,
            ),
        )
        return int(cursor.lastrowid or 0)
    except sqlite3.IntegrityError:
        logger.warning(f'EPG 源已存在，跳过: {url}')
        return 0
    except Exception as e:
        logger.error(f'新增 EPG 源失败: {e}')
        return 0


_EPG_SOURCE_EDITABLE = {
    'name',
    'url',
    'enabled',
    'priority',
    'refresh_mode',
    'refresh_at',
    'refresh_minutes',
    'remark',
}


def update_epg_source(source_id: int, **fields) -> bool:
    """更新 EPG 源（只接受白名单字段）"""
    sets: list[str] = []
    params: list = []
    for key, value in fields.items():
        if key not in _EPG_SOURCE_EDITABLE:
            continue
        if key == 'enabled':
            value = 1 if value in (True, 1, '1', 'true', 'True', 'on') else 0
        elif key in ('priority', 'refresh_minutes'):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif isinstance(value, str):
            value = value.strip()
        sets.append(f'{key} = ?')
        params.append(value)
    if not sets:
        return False
    sets.append('updated_at = ?')
    params.append(datetime.datetime.now().isoformat(timespec='seconds'))
    params.append(source_id)
    try:
        cursor = _execute(f'UPDATE epg_sources SET {", ".join(sets)} WHERE id = ?', tuple(params))
        return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        logger.warning(f'更新 EPG 源冲突（URL 重复）: id={source_id}')
        return False
    except Exception as e:
        logger.error(f'更新 EPG 源失败 [{source_id}]: {e}')
        return False


def delete_epg_source(source_id: int) -> bool:
    """删除 EPG 源，并级联清掉其频道与节目数据"""
    conn = None
    try:
        conn = get_conn()
        conn.execute('DELETE FROM epg_programmes WHERE source_id = ?', (source_id,))
        conn.execute('DELETE FROM epg_channels WHERE source_id = ?', (source_id,))
        cursor = conn.execute('DELETE FROM epg_sources WHERE id = ?', (source_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f'删除 EPG 源失败 [{source_id}]: {e}')
        if conn:
            with suppress(Exception):
                conn.rollback()
        return False
    finally:
        if conn:
            with suppress(Exception):
                conn.close()


def mark_epg_source_result(
    source_id: int,
    status: str,
    error: str = '',
    channel_count: int = 0,
    programme_count: int = 0,
    duration_ms: int = 0,
) -> None:
    """记录一次抓取结果（成功/失败）到源上"""
    with suppress(Exception):
        _execute(
            'UPDATE epg_sources SET last_fetch_at = ?, last_status = ?, last_error = ?, '
            'last_channel_count = ?, last_programme_count = ?, last_duration_ms = ? WHERE id = ?',
            (
                datetime.datetime.now().isoformat(timespec='seconds'),
                status,
                (error or '')[:500],
                int(channel_count),
                int(programme_count),
                int(duration_ms),
                source_id,
            ),
        )


def replace_epg_data(source_id: int, channels: list[dict], programmes: list[dict]) -> tuple[int, int]:
    """整源替换 EPG 数据：先删旧数据再批量写入（单事务）。

    Args:
        source_id: 源 ID
        channels: [{'tvg_id','display_name','icon'}]
        programmes: [{'tvg_id','start_utc','stop_utc','title',...}]

    Returns:
        (写入频道数, 写入节目数)
    """
    conn = None
    try:
        conn = get_conn()
        now = datetime.datetime.now().isoformat(timespec='seconds')
        conn.execute('DELETE FROM epg_programmes WHERE source_id = ?', (source_id,))
        conn.execute('DELETE FROM epg_channels WHERE source_id = ?', (source_id,))
        ch_rows = [
            (source_id, c.get('tvg_id', ''), c.get('display_name', ''), c.get('icon', ''), now)
            for c in channels
            if c.get('tvg_id')
        ]
        if ch_rows:
            conn.executemany(
                'INSERT OR REPLACE INTO epg_channels (source_id, tvg_id, display_name, icon, updated_at) '
                'VALUES (?, ?, ?, ?, ?)',
                ch_rows,
            )
        pg_rows = [
            (
                source_id,
                p.get('tvg_id', ''),
                p.get('start_utc', ''),
                p.get('stop_utc', ''),
                p.get('title', ''),
                p.get('sub_title', ''),
                p.get('description', ''),
                p.get('category', ''),
                p.get('episode', ''),
                p.get('icon', ''),
            )
            for p in programmes
            if p.get('tvg_id') and p.get('start_utc')
        ]
        if pg_rows:
            conn.executemany(
                'INSERT OR IGNORE INTO epg_programmes (source_id, tvg_id, start_utc, stop_utc, title, '
                'sub_title, description, category, episode, icon) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                pg_rows,
            )
        conn.commit()
        return len(ch_rows), len(pg_rows)
    except Exception as e:
        logger.error(f'写入 EPG 数据失败 [source={source_id}]: {e}')
        if conn:
            with suppress(Exception):
                conn.rollback()
        return 0, 0
    finally:
        if conn:
            with suppress(Exception):
                conn.close()


def set_epg_channel_match(source_id: int, tvg_id: str, matched_channel: str) -> bool:
    """把 EPG 频道对齐到本地频道名"""
    try:
        cursor = _execute(
            'UPDATE epg_channels SET matched_channel = ? WHERE source_id = ? AND tvg_id = ?',
            (matched_channel, source_id, tvg_id),
        )
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f'设置 EPG 频道匹配失败: {e}')
        return False


def get_epg_channel(channel_id: int) -> dict | None:
    """按 epg_channels.id 取单个频道"""
    conn = get_conn()
    try:
        row = conn.execute(
            'SELECT id, source_id, tvg_id, display_name, icon, matched_channel FROM epg_channels WHERE id = ?',
            (channel_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f'读取 EPG 频道失败: {e}')
        return None
    finally:
        if conn:
            with suppress(Exception):
                conn.close()


def set_epg_channel_match_by_id(channel_id: int, matched_channel: str, tvg_id: str | None = None) -> bool:
    """按 epg_channels.id 设置对齐（matched_channel；tvg_id 可选覆盖）"""
    try:
        if tvg_id is not None:
            cursor = _execute(
                'UPDATE epg_channels SET matched_channel = ?, tvg_id = ? WHERE id = ?',
                (matched_channel, tvg_id, channel_id),
            )
        else:
            cursor = _execute(
                'UPDATE epg_channels SET matched_channel = ? WHERE id = ?',
                (matched_channel, channel_id),
            )
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f'设置 EPG 频道对齐失败: {e}')
        return False


def bulk_set_epg_channel_match(pairs: list[tuple[int, str, str]]) -> int:
    """批量对齐：[(source_id, tvg_id, matched_channel), ...]"""
    if not pairs:
        return 0
    conn = None
    try:
        conn = get_conn()
        conn.executemany(
            'UPDATE epg_channels SET matched_channel = ? WHERE source_id = ? AND tvg_id = ?',
            [(m, s, t) for s, t, m in pairs],
        )
        conn.commit()
        return len(pairs)
    except Exception as e:
        logger.error(f'批量对齐 EPG 频道失败: {e}')
        if conn:
            with suppress(Exception):
                conn.rollback()
        return 0
    finally:
        if conn:
            with suppress(Exception):
                conn.close()


def list_epg_channels(
    source_id: int | None = None,
    keyword: str = '',
    matched_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """分页列出 EPG 频道（含所属源名）"""
    conn = get_conn()
    try:
        where: list[str] = []
        params: list = []
        if source_id:
            where.append('c.source_id = ?')
            params.append(source_id)
        if keyword:
            where.append('(c.tvg_id LIKE ? OR c.display_name LIKE ? OR c.matched_channel LIKE ?)')
            like = f'%{keyword}%'
            params.extend([like, like, like])
        if matched_only:
            where.append("c.matched_channel != ''")
        clause = (' WHERE ' + ' AND '.join(where)) if where else ''
        total = conn.execute(f'SELECT COUNT(*) FROM epg_channels c{clause}', tuple(params)).fetchone()[0]
        rows = conn.execute(
            'SELECT c.id, c.source_id, c.tvg_id, c.display_name, c.icon, c.matched_channel, '
            's.name AS source_name FROM epg_channels c '
            f'LEFT JOIN epg_sources s ON s.id = c.source_id{clause} '
            'ORDER BY c.display_name LIMIT ? OFFSET ?',
            (*params, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows], total
    except Exception as e:
        logger.error(f'列出 EPG 频道失败: {e}')
        return [], 0
    finally:
        conn.close()


def query_epg_programmes(tvg_ids: list[str], start_utc: str, end_utc: str) -> list[dict]:
    """按 tvg_id 列表与时间窗查询节目（UTC 字符串）。

    重叠判定：programme.start < end AND programme.stop > start。
    多源冲突时按源 priority 保留优先级最高的一份。
    """
    if not tvg_ids:
        return []
    conn = get_conn()
    try:
        result: list[dict] = []
        seen: set[tuple[str, str]] = set()
        # SQLite 变量上限 999，分批查询
        chunk = 400
        for i in range(0, len(tvg_ids), chunk):
            batch = tvg_ids[i : i + chunk]
            placeholders = ','.join('?' * len(batch))
            rows = conn.execute(
                'SELECT p.tvg_id, p.start_utc, p.stop_utc, p.title, p.sub_title, p.description, '
                'p.category, p.episode, p.icon, p.source_id, s.priority '
                'FROM epg_programmes p LEFT JOIN epg_sources s ON s.id = p.source_id '
                f'WHERE p.tvg_id IN ({placeholders}) AND p.start_utc < ? AND p.stop_utc > ? '
                'ORDER BY p.tvg_id, p.start_utc, s.priority ASC',
                (*batch, end_utc, start_utc),
            ).fetchall()
            for r in rows:
                key = (r['tvg_id'], r['start_utc'])
                if key in seen:
                    continue
                seen.add(key)
                result.append(dict(r))
        return result
    except Exception as e:
        logger.error(f'查询 EPG 节目失败: {e}')
        return []
    finally:
        conn.close()


def cleanup_epg_programmes(before_utc: str) -> int:
    """清理 stop_utc 早于给定 UTC 时间的历史节目，返回删除条数"""
    try:
        cursor = _execute('DELETE FROM epg_programmes WHERE stop_utc < ?', (before_utc,))
        return cursor.rowcount or 0
    except Exception as e:
        logger.error(f'清理 EPG 历史节目失败: {e}')
        return 0


def get_epg_stats() -> dict:
    """EPG 概览统计"""
    conn = get_conn()
    try:
        stats: dict = {
            'sources': conn.execute('SELECT COUNT(*) FROM epg_sources').fetchone()[0],
            'sources_enabled': conn.execute('SELECT COUNT(*) FROM epg_sources WHERE enabled = 1').fetchone()[0],
            'channels': conn.execute('SELECT COUNT(*) FROM epg_channels').fetchone()[0],
            'channels_matched': conn.execute(
                "SELECT COUNT(*) FROM epg_channels WHERE matched_channel != ''"
            ).fetchone()[0],
            'programmes': conn.execute('SELECT COUNT(*) FROM epg_programmes').fetchone()[0],
        }
        row = conn.execute('SELECT MAX(last_fetch_at) FROM epg_sources').fetchone()
        stats['last_fetch_at'] = row[0] if row else None
        row = conn.execute('SELECT MIN(start_utc), MAX(stop_utc) FROM epg_programmes').fetchone()
        stats['range_start_utc'] = row[0] if row else None
        stats['range_stop_utc'] = row[1] if row else None
        return stats
    except Exception as e:
        logger.error(f'读取 EPG 统计失败: {e}')
        return {}
    finally:
        conn.close()
