#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite ORM — 用户表、审计日志表、Session表
"""
import os
import sqlite3
import threading
import bcrypt
import logging
from typing import Optional, List, Dict

logger = logging.getLogger('web.models')

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'data')
DB_PATH = os.path.join(DATA_DIR, 'web.db')

# 写锁 — 保护并发写入（check_same_thread=False + WAL 下仍可能竞态）
_write_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    """返回独立连接（避免 async 环境下 threading.local 导致连接状态竞态）"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


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
                except Exception:
                    pass
            if 'locked' in str(e) and attempt < max_retries - 1:
                import time
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


def init_db(admin_password: str, viewer_password: str):
    """建表 + 默认用户（密码由调用方提供，非硬编码）"""
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
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_username ON audit_logs(username);

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            created_at REAL NOT NULL,
            last_active REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
    """)
    conn.commit()

    # 检查是否有用户，无则创建管理员 viewer（密码从环境变量或随机生成）
    cursor = conn.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        admin_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
        viewer_hash = bcrypt.hashpw(viewer_password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            ('admin', admin_hash, 'admin', '管理员')
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            ('viewer', viewer_hash, 'viewer', '查看者')
        )
        conn.commit()
        logger.info(f"默认用户已创建: admin(管理员) / viewer(查看者)")


def cleanup_audit_logs(max_days: int = 90):
    """清理旧审计日志（启动时调用一次，不频繁操作）"""
    try:
        conn = get_conn()
        conn.execute("DELETE FROM audit_logs WHERE created_at < datetime('now', ? || ' days')", (str(-max_days),))
        conn.commit()
    except Exception as e:
        logger.warning(f"审计日志清理失败: {e}")


# ── 用户操作 ──────────────────────────────────────

def get_user_by_username(username: str) -> Optional[Dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)).fetchone()
    if row:
        return dict(row)
    return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        return dict(row)
    return None


def verify_password(username: str, password: str) -> Optional[Dict]:
    """验证密码，成功返回用户 dict，失败返回 None"""
    user = get_user_by_username(username)
    if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        return user
    return None


def list_users() -> List[Dict]:
    conn = get_conn()
    rows = conn.execute("SELECT id, username, role, display_name, is_active, created_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def create_user(username: str, password: str, role: str = 'viewer', display_name: str = '') -> int:
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cursor = _execute(
        "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
        (username, pw_hash, role, display_name)
    )
    return cursor.lastrowid


def delete_user(user_id: int) -> bool:
    cursor = _execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cursor.rowcount > 0


def update_user(user_id: int, **kwargs) -> bool:
    """更新用户信息（role, display_name, password等）"""
    updates = []
    params = []
    for key in ('role', 'display_name'):
        if key in kwargs and kwargs[key] is not None:
            updates.append(f"{key} = ?")
            params.append(kwargs[key])
    if 'password' in kwargs and kwargs['password']:
        pw_hash = bcrypt.hashpw(kwargs['password'].encode(), bcrypt.gensalt()).decode()
        updates.append("password_hash = ?")
        params.append(pw_hash)
    if not updates:
        return False
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(user_id)
    cursor = _execute(
        f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
        params
    )
    return cursor.rowcount > 0


def toggle_user(user_id: int) -> Optional[bool]:
    """切换用户启用/禁用状态，返回新状态或None（用户不存在）"""
    user = get_user_by_id(user_id)
    if not user:
        return None
    new_status = 0 if user['is_active'] else 1
    _execute("UPDATE users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
             (new_status, user_id))
    return bool(new_status)


def update_user_password(user_id: int, new_password: str) -> bool:
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    cursor = _execute(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (pw_hash, user_id)
    )
    return cursor.rowcount > 0


# ── Session 操作 ──────────────────────────────────

def create_session_db(user_id: int, username: str, role: str, ttl: float = 86400) -> str:
    """创建 session 并存入 SQLite"""
    import uuid
    import time
    session_id = uuid.uuid4().hex
    now = time.time()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, user_id, username, role, created_at, last_active) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, user_id, username, role, now, now)
    )
    conn.commit()
    return session_id


def get_session_db(session_id: str, idle_timeout: int = 7200, session_ttl: int = 86400) -> Optional[Dict]:
    """从 SQLite 获取 session"""
    import time
    conn = get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    s = dict(row)
    now = time.time()
    if now - s['created_at'] > session_ttl or now - s['last_active'] > idle_timeout:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return None
    # 刷新最后活跃时间
    conn.execute("UPDATE sessions SET last_active = ? WHERE id = ?", (now, session_id))
    conn.commit()
    s['last_active'] = now
    return s


def destroy_session_db(session_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def cleanup_expired_sessions():
    """清理过期 session（使用写锁保护并发）"""
    import time
    now = time.time()
    _execute("DELETE FROM sessions WHERE last_active < ? OR created_at < ?",
             (now - 7200, now - 86400))


# ── 审计日志 ──────────────────────────────────────

def add_audit_log(user_id: int, username: str, action: str, target: str = '', detail: str = '', ip_address: str = ''):
    _execute(
        "INSERT INTO audit_logs (user_id, username, action, target, detail, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, action, target, detail, ip_address)
    )


def list_audit_logs(page: int = 1, size: int = 50, action_filter: str = '') -> Dict:
    conn = get_conn()
    offset = (page - 1) * size
    if action_filter:
        total = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = ?", (action_filter,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE action = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (action_filter, size, offset)
        ).fetchall()
    else:
        total = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (size, offset)
        ).fetchall()
    return {'total': total, 'page': page, 'size': size, 'logs': [dict(r) for r in rows]}


def list_audit_actions() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action").fetchall()
    return [r['action'] for r in rows]
