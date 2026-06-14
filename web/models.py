#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite ORM — 用户表、审计日志表
"""

import os
import sqlite3
import threading
import bcrypt
from datetime import datetime
from typing import Optional, List, Dict


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'data')
DB_PATH = os.path.join(DATA_DIR, 'web.db')

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """每个线程独立连接（加 row_factory）"""
    if not hasattr(_local, 'conn') or _local.conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def init_db():
    """建表 + 默认用户"""
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
    """)
    conn.commit()

    # 检查是否有用户，无则创建默认用户
    cursor = conn.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        admin_pw = os.environ.get('WEB_ADMIN_PASSWORD', 'admin123')
        viewer_pw = os.environ.get('WEB_VIEWER_PASSWORD', 'viewer123')
        admin_hash = bcrypt.hashpw(admin_pw.encode(), bcrypt.gensalt()).decode()
        viewer_hash = bcrypt.hashpw(viewer_pw.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            ('admin', admin_hash, 'admin', '管理员')
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
            ('viewer', viewer_hash, 'viewer', '查看者')
        )
        conn.commit()


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
    conn = get_conn()
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cursor = conn.execute(
        "INSERT INTO users (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)",
        (username, pw_hash, role, display_name)
    )
    conn.commit()
    return cursor.lastrowid


def delete_user(user_id: int) -> bool:
    conn = get_conn()
    cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return cursor.rowcount > 0


def update_user(user_id: int, **kwargs) -> bool:
    """更新用户信息（role, display_name, password等）"""
    conn = get_conn()
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
    cursor = conn.execute(
        f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
        params
    )
    conn.commit()
    return cursor.rowcount > 0


def toggle_user(user_id: int) -> Optional[bool]:
    """切换用户启用/禁用状态，返回新状态或None（用户不存在）"""
    conn = get_conn()
    user = get_user_by_id(user_id)
    if not user:
        return None
    new_status = 0 if user['is_active'] else 1
    conn.execute("UPDATE users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (new_status, user_id))
    conn.commit()
    return bool(new_status)


def update_user_password(user_id: int, new_password: str) -> bool:
    conn = get_conn()
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    cursor = conn.execute(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (pw_hash, user_id)
    )
    conn.commit()
    return cursor.rowcount > 0


# ── 审计日志 ──────────────────────────────────────

def add_audit_log(user_id: int, username: str, action: str, target: str = '', detail: str = '', ip_address: str = ''):
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_logs (user_id, username, action, target, detail, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, action, target, detail, ip_address)
    )
    conn.commit()


def list_audit_logs(page: int = 1, size: int = 50, action_filter: str = '') -> Dict:
    conn = get_conn()
    offset = (page - 1) * size
    if action_filter:
        total = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = ?",
            (action_filter,)
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
