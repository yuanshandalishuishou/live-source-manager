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
"""
import os
import sys
import tempfile
import shutil
import configparser

# ── 项目路径 ──────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ── 共享临时目录（在 pytest 收集测试前创建） ────
SHARED_TMP_DIR = tempfile.mkdtemp(prefix='shared_web_test_')
SHARED_ADMIN_PW = 'TestAdminPw1!'
SHARED_VIEWER_PW = 'TestViewerPw1!'

# 设置环境变量（webapi 启动事件可能读取）
os.environ['WEB_ADMIN_PASSWORD'] = SHARED_ADMIN_PW
os.environ['WEB_VIEWER_PASSWORD'] = SHARED_VIEWER_PW

# ── 覆写 models 全局变量 ────────────────────────
from web import models
models.DATA_DIR = SHARED_TMP_DIR
models.DB_PATH = os.path.join(SHARED_TMP_DIR, 'web.db')

# ── 覆写 web.webapp.CONFIG_PATH (原 config_proxy) ─
import web.webapp
web.webapp.CONFIG_PATH = os.path.join(SHARED_TMP_DIR, 'config.ini')

# 写最小测试配置
_cp = configparser.ConfigParser()
_cp.add_section('Logging')
_cp.set('Logging', 'level', 'INFO')
_cp.set('Logging', 'file', os.path.join(SHARED_TMP_DIR, 'app.log'))
_cp.add_section('Sources')
_cp.set('Sources', 'local_dirs', '/config/sources')
_cp.add_section('Network')
_cp.set('Network', 'proxy_enabled', 'False')
with open(web.webapp.CONFIG_PATH, 'w') as _f:
    _cp.write(_f)

# 创建 app.log 文件（test_logs_download_admin 需要文件存在）
open(os.path.join(SHARED_TMP_DIR, 'app.log'), 'a').close()

# ── 初始化数据库（统一密码）──────────────────────
models.init_db(admin_password=SHARED_ADMIN_PW, viewer_password=SHARED_VIEWER_PW)

# 注意：webapi app 由各测试文件在需要时导入（from web.webapi import app）


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
