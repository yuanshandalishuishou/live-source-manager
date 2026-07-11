#!/usr/bin/env python3
"""
Web 管理服务 — 精简入口

架构重构：原 3711 行巨型文件已拆分为：
  web/core.py          — 共享基础设施（app、中间件、lifespan、认证、配置代理、辅助函数）
  web/routes/pages.py     — HTML 页面路由
  web/routes/auth.py      — 认证 + 用户管理 API
  web/routes/dashboard.py — Dashboard 统计 API
  web/routes/sources.py   — 源管理 API
  web/routes/config_api.py— 配置中心 API
  web/routes/rules.py     — 规则 + 频道映射 + 分类字典 API
  web/routes/system.py    — 测试 / WebSocket / 日志 / 审计 / GitHub API

本文件职责：
  1. 从 web.core 导入 app 实例
  2. 从各路由模块导入 router 并挂载到 app
  3. 保留 uvicorn 启动代码
  4. 确保 from web.webapp import app 仍然可用（向后兼容）
  5. 保留 conftest.py 需要的 CSRF_EXEMPT_PATHS / _auth_sessions / _auth_csrf_tokens
"""

import os
import socket
import sys

import uvicorn

# ── 从 core 导入 app 实例和共享状态 ──────────────
from web.core import (
    app,
    logger,
)
from web.routes.auth import router as auth_router
from web.routes.config_api import router as config_router
from web.routes.dashboard import router as dashboard_router

# ── 导入路由模块并挂载到 app ──────────────────────
from web.routes.pages import router as pages_router
from web.routes.rules import router as rules_router
from web.routes.sources import router as sources_router
from web.routes.system import router as system_router

app.include_router(pages_router)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(sources_router)
app.include_router(config_router)
app.include_router(rules_router)
app.include_router(system_router)


# ══════════════════════════════════════════════════
# 端口检测
# ══════════════════════════════════════════════════


def check_port(host: str = '0.0.0.0', port: int = 23456) -> bool:
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
    port = int(os.environ.get('WEB_PORT', '23456'))

    if not check_port(host, port):
        logger.error(f'错误: 端口 {port} 已被占用，无法启动 Web 服务')
        sys.exit(1)

    logger.info(f'🌐 Web 管理界面启动: http://{host}:{port}')
    logger.info('   首次启动密码由 WEB_ADMIN_PASSWORD / WEB_VIEWER_PASSWORD 环境变量设置')
    logger.info('   未设置时自动生成随机密码，请查看启动日志')
    uvicorn.run(app, host=host, port=port, log_level='info')


if __name__ == '__main__':
    main()
