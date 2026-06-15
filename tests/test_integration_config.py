#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集成测试：配置文件加解密 + 启动流程 + WebSocket

覆盖场景（审计 P2-16C / 集成测试要求）：
  1. test_encrypted_config_api_read — 通过 API 写入加密值，通过 Config 类读取到解密后的原文
  2. test_full_startup_flow — 模拟完整启动流程：DB不存在→首次运行→初始化→配置可用
  3. test_websocket_unauthorized_closed — 无session时WS被拒绝连接（提前关闭而非accept后close）
"""

import os
import sys
import json
import tempfile
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest
from fastapi.testclient import TestClient

# 密码必须与 conftest.py 保持一致
ADMIN_PASSWORD = 'TestAdminPw1!'
VIEWER_PASSWORD = 'TestViewerPw1!'


class TestConfigEncryptionIntegration:
    """配置加解密集成测试"""

    def test_encrypted_config_api_read(self):
        """通过 API 写入加密值，通过 Config 类读取到解密后的原文"""
        from web.webapp import app
        from web.auth import _get_csrf_token
        from app.config_manager import Config
        import web.crypto_utils as cu

        # 确保密钥就绪
        cu.ensure_key_initialized()

        client = TestClient(app)

        # 1. 登录 admin
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 200
        sid = resp.cookies.get('session')
        csrf_resp = client.get('/api/auth/csrf-token', cookies={'session': sid})
        assert csrf_resp.status_code == 200
        csrf_token = csrf_resp.json()['csrf_token']

        # 2. 通过 API 写入加密配置
        test_password = "SecretTestProxyPass#123!"
        put_resp = client.put(
            '/api/config',
            json={
                'Network': {
                    'proxy_password': test_password,
                    'proxy_host': '192.168.1.100',
                    'proxy_port': '3128',
                }
            },
            cookies={'session': sid},
            headers={'X-CSRF-Token': csrf_token},
        )
        assert put_resp.status_code == 200, f"写入配置失败: {put_resp.text}"

        # 3. 通过 Config 类读取——应得到解密后的原文
        config = Config()
        # Config 读取走 models.get_app_config，应自动解密
        from web import models
        stored = models.get_app_config('Network.proxy_password')
        assert stored == test_password, \
            f"Config 读取应返回解密后的原文: {stored} != {test_password}"

        # 4. 验证数据库中实际存储的是加密值
        raw = models.get_app_config_raw('Network.proxy_password')
        assert raw is not None
        assert raw.startswith('ENC:'), f"数据库应存储加密值: {raw[:30]}"

        # 5. 通过 API 读取也返回明文
        get_resp = client.get('/api/config', cookies={'session': sid})
        assert get_resp.status_code == 200
        config_data = get_resp.json()
        assert config_data.get('Network', {}).get('proxy_password') == test_password, \
            f"API 应返回解密后的明文: {config_data.get('Network', {})}"

    def test_full_startup_flow(self):
        """模拟完整启动流程：DB不存在→首次运行→初始化→配置可用

        此测试创建一个全新的临时目录，模拟应用首次启动的场景。
        """
        # 创建全新的临时目录
        startup_dir = tempfile.mkdtemp(prefix='startup_flow_test_')
        db_path = os.path.join(startup_dir, 'web.db')
        ini_path = os.path.join(startup_dir, 'config.ini')

        try:
            # 1. 保存 models 原始变量
            import web.models as web_models
            old_models_db = web_models.DB_PATH
            old_models_data = web_models.DATA_DIR

            # 2. 覆写 models 指向新目录
            web_models.DB_PATH = db_path
            web_models.DATA_DIR = startup_dir
            os.makedirs(startup_dir, exist_ok=True)

            # 3. 模拟首次运行：数据库文件不存在
            assert not os.path.exists(db_path), "测试开始前 DB 不应存在"

            # 4. 初始化数据库
            web_models.init_db(admin_password=ADMIN_PASSWORD, viewer_password=VIEWER_PASSWORD)
            assert os.path.exists(db_path), "init_db 应创建数据库文件"

            # 5. 验证 admin 用户存在
            admin_user = web_models.get_user_by_username('admin')
            assert admin_user is not None
            assert admin_user['role'] == 'admin'

            # 6. 写入一条非加密配置
            web_models.set_app_config('Sources.local_dirs', '/test/sources')
            retrieved = web_models.get_app_config('Sources.local_dirs')
            assert retrieved == '/test/sources', f"配置写入/读取: {retrieved}"

            # 7. 验证加密体系可用
            import web.crypto_utils as cu
            old_initialized = cu._initialized_flag
            old_key = os.environ.get('CONFIG_ENCRYPT_KEY')
            # 清除环境变量，模拟自动生成密钥
            os.environ.pop('CONFIG_ENCRYPT_KEY', None)
            os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
            cu._fernet_instance = None
            cu._initialized_flag = False

            cu.ensure_key_initialized()
            gen_key = os.environ.get('CONFIG_ENCRYPT_KEY')
            assert gen_key, "ensure_key_initialized 应生成密钥"

            # 8. 写入加密配置并验证
            test_secret = "StartupFlowSecret#789"
            web_models.set_app_config('Network.proxy_password', test_secret)
            stored = web_models.get_app_config('Network.proxy_password')
            assert stored == test_secret, f"加密配置: {stored} != {test_secret}"

            # 验证数据库中存的是加密值
            raw = web_models.get_app_config_raw('Network.proxy_password')
            assert raw is not None
            assert raw.startswith('ENC:'), f"数据库应存储加密值: {raw[:30]}"

            # 9. 模拟重启：清除环境变量 + 缓存，从 SQLite 恢复密钥
            os.environ.pop('CONFIG_ENCRYPT_KEY', None)
            os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
            cu._fernet_instance = None
            cu._initialized_flag = False

            cu.ensure_key_initialized()
            # 从 SQLite 恢复的密钥应与之前相同
            restored_key = os.environ.get('CONFIG_ENCRYPT_KEY')
            assert restored_key == gen_key, f"重启应恢复相同密钥: {restored_key[:20]} != {gen_key[:20]}"

            # 恢复的密钥应能解密之前加密的数据
            cu._fernet_instance = None
            restored = web_models.get_app_config('Network.proxy_password')
            assert restored == test_secret, f"重启后读取: {restored} != {test_secret}"

            # 清理测试数据
            conn = web_models.get_conn()
            conn.execute("DELETE FROM app_config WHERE key = 'Network.proxy_password'")
            conn.execute("DELETE FROM app_config WHERE key = 'Sources.local_dirs'")
            conn.commit()
            conn.close()

        finally:
            # 恢复 models 原始变量
            import web.models as web_models_restore
            web_models_restore.DB_PATH = old_models_db
            web_models_restore.DATA_DIR = old_models_data

            # 恢复环境变量
            import web.crypto_utils as _cu_restore
            if old_key:
                os.environ['CONFIG_ENCRYPT_KEY'] = old_key
            else:
                os.environ.pop('CONFIG_ENCRYPT_KEY', None)
            os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
            _cu_restore._fernet_instance = None
            _cu_restore._initialized_flag = old_initialized

            # 清理临时目录
            if os.path.isdir(startup_dir):
                shutil.rmtree(startup_dir, ignore_errors=True)


class TestWebSocketIntegration:
    """WebSocket 集成测试"""

    def test_websocket_unauthorized_closed(self):
        """无session时WS被拒绝连接（审计积分测试要求——提前关闭）"""
        from starlette.websockets import WebSocketDisconnect
        from web.webapp import app

        client = TestClient(app)
        with client.websocket_connect('/ws/test') as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4001, \
                f"未认证 WebSocket 应返回 code 4001, got {exc_info.value.code}"

    def test_websocket_authorized_succeeds(self):
        """已登录用户 WebSocket 连接成功"""
        from web.webapp import app

        client = TestClient(app)
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 200
        sid = resp.cookies.get('session')

        with client.websocket_connect('/ws/test', cookies={'session': sid}) as ws:
            ws.send_text('ping')
            data = ws.receive_json()
            assert data == {'type': 'pong'}, f"ping/pong 失败: {data}"

    def test_websocket_invalid_session_closed(self):
        """无效 session 连接被拒绝"""
        from starlette.websockets import WebSocketDisconnect
        from web.webapp import app

        client = TestClient(app)
        with client.websocket_connect('/ws/test', cookies={'session': 'invalid-session-id'}) as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4001, \
                f"无效 session 应返回 4001, got {exc_info.value.code}"
