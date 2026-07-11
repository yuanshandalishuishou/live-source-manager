# -*- coding: utf-8 -*-
"""
pytest 共享环境配置
解决 test_web_auth.py 和 test_web_api.py 测试隔离冲突。

此文件在 pytest 收集测试前被导入（先于任何测试文件的模块级代码），
在此初始化共享的临时目录和数据库，两个测试文件共用。

核心思路：
1. 两个测试文件使用相同的临时目录和数据库
2. 两个测试文件使用相同的密码
3. 数据库只初始化一次

修复（审计 P2-16A）：添加 clean_db_before_each fixture，每个测试前清理数据库
避免测试间数据污染。
"""

import os
import sys
import tempfile
import shutil
import logging

# ── 项目路径 ──────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ── 共享临时目录（在 pytest 收集测试前创建） ────
SHARED_TMP_DIR = tempfile.mkdtemp(prefix='shared_web_test_')
SHARED_ADMIN_PW = 'TestAdminPw1!'

# 设置环境变量（webapi 启动事件可能读取）
os.environ['WEB_ADMIN_PASSWORD'] = SHARED_ADMIN_PW

# ── 覆写 models 全局变量 ────────────────────────
from web import models

models.DATA_DIR = SHARED_TMP_DIR
models.DB_PATH = os.path.join(SHARED_TMP_DIR, 'web.db')

# ── 覆写 web.webapp 下的 CSRF_EXEMPT_PATHS 确保一致 ──
import web.webapp

web.webapp.CSRF_EXEMPT_PATHS = {'/api/auth/login', '/api/auth/logout', '/login', '/health'}

# 创建 app.log 文件（test_logs_download_admin 需要文件存在）
open(os.path.join(SHARED_TMP_DIR, 'app.log'), 'a').close()

# ── 初始化数据库（统一密码）──────────────────────
models.init_db(admin_password=SHARED_ADMIN_PW)

# 导入加密工具并初始化密钥（确保测试前加密体系就绪）
import web.crypto_utils as _cu

try:
    _cu.ensure_key_initialized()
except Exception:
    pass

# ── 每个测试前清理数据库（P2-16A 修复） ──────────

import pytest


# 注意：Session 存在全局内存 dict 中，每个测试前清理 DB 不自动清除 session
# 因此我们同时清理内存中的 session


@pytest.fixture(autouse=True)
def clean_db_before_each():
    """每个测试函数前清理数据库和会话状态，防止测试间数据污染"""
    # 清理所有数据表
    try:
        from web import models

        conn = models.get_conn()
        conn.executescript("""
            DELETE FROM audit_logs;
            DELETE FROM app_config;
            DELETE FROM sessions;
            DELETE FROM users WHERE username NOT IN ('admin');
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass

    # 清理内存中的 session 和 CSRF token
    try:
        from web.webapp import _auth_sessions, _auth_csrf_tokens

        _auth_sessions.clear()
        _auth_csrf_tokens.clear()
    except Exception:
        pass

    # 重置加密密钥初始化标志，允许测试重新初始化
    # （P2-16B 修复：conftest 初始化的 _initialized_flag 会阻止 test_encrypt_key 的重新初始化）
    try:
        import web.crypto_utils as _cu2

        _cu2._initialized_flag = False
        _cu2._fernet_instance = None
    except Exception:
        pass


# ── 双保险清理 ─────────────────────────────────
# 1) pytest_sessionfinish – pytest 框架内触发，避免并发 xdist 竞态
# 2) atexit – Python 进程退出时兜底


def pytest_sessionfinish(session, exitstatus):
    _cleanup_tmpdir()


def _cleanup_tmpdir():
    if os.path.isdir(SHARED_TMP_DIR):
        shutil.rmtree(SHARED_TMP_DIR, ignore_errors=True)


import atexit

atexit.register(_cleanup_tmpdir)
