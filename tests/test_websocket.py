#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket 功能测试

覆盖：
- ConnectionManager 基本功能（连接跟踪）
- 模块导入与全局单例验证
- WebSocket 端点认证（拒绝未认证连接）
- WebSocket 端点 ping/pong 交互
"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient

from web.webapp import app, manager, ConnectionManager


ADMIN_PASSWORD = 'TestAdminPw1!'


class TestConnectionManager:
    """ConnectionManager 单元测试（不依赖 TestClient）"""

    def test_count_initial_zero(self):
        """初始 connection count 为 0"""
        m = ConnectionManager()
        assert m.count == 0

    def test_importable(self):
        """ConnectionManager 可导入"""
        assert ConnectionManager is not None

    def test_global_manager_instance(self):
        """全局 manager 单例存在且有关键方法"""
        assert hasattr(manager, 'connect')
        assert hasattr(manager, 'disconnect')
        assert hasattr(manager, 'broadcast')
        assert hasattr(manager, 'count')


class TestWebSocketEndpoint:
    """WebSocket 端点集成测试"""

    def test_ws_unauthorized_closes(self):
        """未认证连接 accept 后会被 close(code=4001)"""
        client = TestClient(app)
        with client.websocket_connect('/ws/test') as ws:
            # 握手后 accept 成功，但认证失败会在接收时触发 close
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4001

    def test_ws_ping_pong(self):
        """认证后 ping → {'type': 'pong'}"""
        client = TestClient(app)
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 200
        sid = resp.cookies.get('session')
        assert sid

        with client.websocket_connect('/ws/test', cookies={'session': sid}) as ws:
            ws.send_text('ping')
            data = ws.receive_json()
            assert data == {'type': 'pong'}
