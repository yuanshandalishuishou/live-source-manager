#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密密钥功能测试 — 第三轮迭代

覆盖范围：
  1. 密钥自动生成与持久化（crypto_utils 层）
  2. 密钥恢复与环境变量优先
  3. 密钥显示日志
  4. API 端点（encrypt-key-status, encrypt-key, login hint）
  5. 密钥轮换（管理员权限+重新加密）
  6. models 层与加密的集成
"""

import os
import sys
import json
import logging
import base64
import secrets
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ═══════════════════════════════════════════════════════════════
# 全局 fixture：保存/恢复环境变量（P2-16B 修复）
# 防止 CONFIG_ENCRYPT_KEY 等全局变量在测试间污染
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def save_restore_env():
    """保存恢复环境变量，防止测试间污染"""
    saved = {
        'CONFIG_ENCRYPT_KEY': os.environ.get('CONFIG_ENCRYPT_KEY'),
        'CONFIG_ENCRYPT_KEY_SET_MANUALLY': os.environ.get('CONFIG_ENCRYPT_KEY_SET_MANUALLY'),
        'CONFIG_ENCRYPT_KEY_INITIALIZED': os.environ.get('CONFIG_ENCRYPT_KEY_INITIALIZED'),
    }
    # 同时保存 crypto_utils 模块级缓存状态
    import web.crypto_utils as _cu
    saved_fernet = getattr(_cu, '_fernet_instance', None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # 恢复 Fernet 实例缓存
    _cu._fernet_instance = saved_fernet


# ═══════════════════════════════════════════════════════════════
# 1. 密钥生成测试（crypto_utils 层）
# ═══════════════════════════════════════════════════════════════

class TestKeyGeneration:
    """测试密钥自动生成机制"""

    def _clean_env(self):
        """清理环境变量、模块缓存和SQLite中存储的密钥"""
        os.environ.pop('CONFIG_ENCRYPT_KEY', None)
        os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
        # 清理 SQLite 中可能保存的密钥（避免跨测试污染）
        try:
            from web import models
            conn = models.get_conn()
            conn.execute("DELETE FROM app_config WHERE key = 'System.encrypt_key'")
            conn.commit()
            conn.close()
        except Exception:
            pass
        import web.crypto_utils as cu
        cu._fernet_instance = None
        cu._initialized_flag = False

    def test_ensure_key_initialized_generates(self):
        """test_ensure_key_initialized_generates — 首次运行时自动生成"""
        self._clean_env()

        from web import crypto_utils as cu
        cu.ensure_key_initialized()

        # 确保环境变量已设置（secret key 生成了）
        key = os.environ.get('CONFIG_ENCRYPT_KEY', '')
        assert len(key) > 0, "ensure_key_initialized 应自动生成密钥"

        # 验证密钥能正常工作（加解密）
        test_val = "secret_auto_generated_value"
        enc = cu.encrypt_value(test_val)
        assert enc.startswith('ENC:'), f"加密值应以 ENC: 开头, got: {enc[:30]}"
        dec = cu.decrypt_value(enc)
        assert dec == test_val, f"解密结果不匹配: {dec} != {test_val}"

        # 清理
        self._clean_env()

    def test_ensure_key_idempotent(self):
        """test_ensure_key_idempotent — 重复调用不改变密钥"""
        self._clean_env()

        from web import crypto_utils as cu
        cu.ensure_key_initialized()
        key1 = os.environ.get('CONFIG_ENCRYPT_KEY', '')

        # 模拟重复启动
        cu._fernet_instance = None
        cu._initialized_flag = False
        cu.ensure_key_initialized()
        key2 = os.environ.get('CONFIG_ENCRYPT_KEY', '')

        assert key1 == key2, f"幂等性：重复调用应返回相同密钥, {key1[:20]} != {key2[:20]}"

        self._clean_env()

    def test_ensure_key_persists(self):
        """test_ensure_key_persists — 重启后从SQLite恢复

        模拟：先自动生成密钥（写入 SQLite）→ 清除环境变量 + 缓存
        → 再次调用 ensure_key_initialized 应还原相同的密钥
        """
        self._clean_env()

        from web import crypto_utils as cu
        # 第1步：首次运行，自动生成密钥（写入 SQLite + 环境变量）
        cu.ensure_key_initialized()
        first_key = os.environ.get('CONFIG_ENCRYPT_KEY', '')
        assert len(first_key) > 0

        # 加密一个值做验证
        test_val = "persistence_check_value"
        enc = cu.encrypt_value(test_val)

        # 第2步：模拟重启 —— 清除环境变量和缓存
        os.environ.pop('CONFIG_ENCRYPT_KEY', None)
        os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
        cu._fernet_instance = None
        cu._initialized_flag = False

        # 第3步：再次初始化（应从 SQLite 恢复）
        cu.ensure_key_initialized()
        restored_key = os.environ.get('CONFIG_ENCRYPT_KEY', '')
        assert len(restored_key) > 0, "应从 SQLite 恢复密钥"

        # 第4步：验证恢复后的密钥能解密之前加密的值
        cu._fernet_instance = None
        cu._initialized_flag = False
        dec = cu.decrypt_value(enc)
        assert dec == test_val, f"从 SQLite 恢复密钥后应能解密, {dec} != {test_val}"

        self._clean_env()

    def test_key_from_env_var_priority(self):
        """test_key_from_env_var_priority — 环境变量优先于SQLite"""
        self._clean_env()

        # 设环境变量（hex 密钥）
        env_key = 'a1b2c3d4e5f6a7b8c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2'
        os.environ['CONFIG_ENCRYPT_KEY'] = env_key

        from web import crypto_utils as cu
        cu.ensure_key_initialized()

        # 加密一个值（用环境变量密钥）
        test_val = "env_priority_test_value"
        enc_with_env = cu.encrypt_value(test_val)

        # 此时应该标记为自定义密钥
        assert cu.is_custom_key() == True, "环境变量密钥应标记为自定义"

        self._clean_env()

    def test_key_from_sqlite_when_no_env(self):
        """无环境变量时，从 SQLite 恢复的密钥不应标记为自定义"""
        self._clean_env()

        from web import crypto_utils as cu
        # 首次生成（写入 SQLite）
        cu.ensure_key_initialized()
        assert cu.is_custom_key() == False, "自动生成的密钥不应标记为自定义"

        # 模拟重启
        os.environ.pop('CONFIG_ENCRYPT_KEY', None)
        os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
        cu._fernet_instance = None
        cu._initialized_flag = False

        cu.ensure_key_initialized()
        assert cu.is_custom_key() == False, "从 SQLite 恢复的密钥不应标记为自定义"

        self._clean_env()


# ═══════════════════════════════════════════════════════════════
# 2. 加密工具函数测试
# ═══════════════════════════════════════════════════════════════

class TestEncryptUtils:
    """测试加解密辅助函数"""

    def _setup_env_key(self):
        """设置一个稳定的密钥用于测试"""
        self._clean_env()
        raw_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        os.environ['CONFIG_ENCRYPT_KEY'] = raw_key
        from web import crypto_utils as cu
        cu._fernet_instance = None
        cu._initialized_flag = False

    def _clean_env(self):
        os.environ.pop('CONFIG_ENCRYPT_KEY', None)
        os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
        # 清理 SQLite 中可能保存的密钥（避免跨测试污染）
        try:
            from web import models
            conn = models.get_conn()
            conn.execute("DELETE FROM app_config WHERE key = 'System.encrypt_key'")
            conn.commit()
            conn.close()
        except Exception:
            pass
        import web.crypto_utils as cu
        cu._fernet_instance = None
        cu._initialized_flag = False

    def test_encrypt_decrypt_roundtrip(self):
        """加解密往返测试"""
        self._setup_env_key()
        from web import crypto_utils as cu

        test_cases = [
            "hello",
            "中文测试",
            "a" * 100,
            "special!@#$%^&*()",
            "",
        ]
        for tc in test_cases:
            enc = cu.encrypt_value(tc)
            if tc:
                assert enc.startswith('ENC:'), f"应加密: {repr(tc)}"
            dec = cu.decrypt_value(enc)
            assert dec == tc, f"往返失败: {repr(dec)} != {repr(tc)}"

        self._clean_env()

    def test_encrypt_idempotent(self):
        """已加密的值再次加密不变"""
        self._setup_env_key()
        from web import crypto_utils as cu

        plain = "some_value"
        enc1 = cu.encrypt_value(plain)
        enc2 = cu.encrypt_value(enc1)  # 再次加密
        assert enc1 == enc2, "加密幂等性：已加密值不应再次加密"
        self._clean_env()

    def test_decrypt_non_encrypted(self):
        """非加密值原样返回"""
        self._setup_env_key()
        from web import crypto_utils as cu

        assert cu.decrypt_value('hello') == 'hello'
        assert cu.decrypt_value('') == ''
        assert cu.decrypt_value('test_plain') == 'test_plain'
        self._clean_env()

    def test_is_sensitive_key(self):
        """敏感键检测"""
        import web.crypto_utils as cu
        assert cu.is_sensitive_key('Network.proxy_password') is True
        assert cu.is_sensitive_key('GitHub.api_token') is True
        assert cu.is_sensitive_key('Network.proxy_host') is False
        assert cu.is_sensitive_key('Sources.local_dirs') is False
        assert cu.is_sensitive_key('Some.random_key') is False

    def test_is_encrypted(self):
        """加密前缀检测"""
        import web.crypto_utils as cu
        assert cu.is_encrypted('ENC:xxx') is True
        assert cu.is_encrypted('ENC:') is True
        assert cu.is_encrypted('xxx') is False
        assert cu.is_encrypted('') is False

    def test_generate_key(self):
        """密钥生成"""
        import web.crypto_utils as cu
        key = cu.generate_key()
        assert len(key) > 0, "应生成密钥"
        # 验证为有效 base64
        import base64
        decoded = base64.urlsafe_b64decode(key)
        assert len(decoded) == 32, f"应生成32字节密钥, got {len(decoded)}"

    def test_is_custom_key(self):
        """test_is_custom_key — 自定义密钥检测"""
        self._clean_env()
        from web import crypto_utils as cu

        # 默认未设，不是自定义
        assert cu.is_custom_key() == False

        # 设环境变量后调用 ensure_key_initialized
        os.environ['CONFIG_ENCRYPT_KEY'] = 'testkey1234567890abcdef'
        cu.ensure_key_initialized()
        assert cu.is_custom_key() == True, "环境变量设置的密钥应标记为自定义"

        self._clean_env()


# ═══════════════════════════════════════════════════════════════
# 3. 密钥显示测试（日志输出）
# ═══════════════════════════════════════════════════════════════

class TestKeyDisplay:
    """测试密钥生成时的日志打印行为"""

    def _capture_crypto_log(self):
        """为 web.crypto_utils logger 添加内存 handler 并返回"""
        import io
        handler = logging.StreamHandler(io.StringIO())
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        cu_logger = logging.getLogger('web.crypto_utils')
        cu_logger.addHandler(handler)
        return handler

    def test_ensure_key_not_logged_if_env_set(self):
        """test_ensure_key_not_logged_if_env_set — 环境变量已设时不重新生成也不重复打印"""
        self._clean_env()

        # 预先设环境变量
        env_key = 'a1b2c3d4e5f6a7b8c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d'
        os.environ['CONFIG_ENCRYPT_KEY'] = env_key

        handler = self._capture_crypto_log()
        try:
            from web import crypto_utils as cu
            cu._fernet_instance = None
            cu._initialized_flag = False
            cu.ensure_key_initialized()

            log_text = handler.stream.getvalue()
            # 环境变量设置时，不应打印密钥框
            assert 'CONFIG_ENCRYPT_KEY=' not in log_text or '已从环境变量读取' in log_text, \
                "环境变量设置时应提示已读取，不打印框"
        finally:
            logging.getLogger('web.crypto_utils').removeHandler(handler)

    def test_key_generated_logs_key(self):
        """首次自动生成时打印密钥框"""
        self._clean_env()

        # 清除 SQLite 中已保存的密钥，模拟真正的首次运行
        from web import models
        conn = models.get_conn()
        conn.execute("DELETE FROM app_config WHERE key = 'System.encrypt_key'")
        conn.commit()

        handler = self._capture_crypto_log()
        try:
            from web import crypto_utils as cu
            cu._fernet_instance = None
            cu._initialized_flag = False
            cu.ensure_key_initialized()

            log_text = handler.stream.getvalue()
            assert 'CONFIG_ENCRYPT_KEY=' in log_text, f"首次生成应在日志中显示密钥, got: {log_text[:100]}"
            assert '╔' in log_text and '║' in log_text and '╝' in log_text, "应以边框格式输出"
        finally:
            logging.getLogger('web.crypto_utils').removeHandler(handler)

    def _clean_env(self):
        os.environ.pop('CONFIG_ENCRYPT_KEY', None)
        os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
        # 清理 SQLite 中可能保存的密钥（避免跨测试污染）
        try:
            from web import models
            conn = models.get_conn()
            conn.execute("DELETE FROM app_config WHERE key = 'System.encrypt_key'")
            conn.commit()
            conn.close()
        except Exception:
            pass
        import web.crypto_utils as cu
        cu._fernet_instance = None
        cu._initialized_flag = False


# ═══════════════════════════════════════════════════════════════
# 4. models 层 + 加密集成测试
# ═══════════════════════════════════════════════════════════════

class TestEncryptKeyAPI:
    """加密密钥 API 测试

    注意：这些测试依赖 conftest.py 的共享临时目录和数据库。
    conftest.py 已初始化数据库和 admin/viewer 用户。
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前确保密钥就绪、重置状态"""
        self._ensure_key()
        from web import crypto_utils as cu
        cu._fernet_instance = None
        cu._initialized_flag = False

    def _ensure_key(self):
        """确保加密密钥就绪（不覆盖已有环境变量）"""
        import web.crypto_utils as cu
        if not os.environ.get('CONFIG_ENCRYPT_KEY'):
            cu.ensure_key_initialized()

    def _get_client(self):
        from fastapi.testclient import TestClient
        from web.webapp import app
        return TestClient(app)

    def _login_admin(self, client):
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': 'TestAdminPw1!',
        })
        assert resp.status_code == 200, f"admin 登录失败: {resp.text}"
        return resp

    def _login_viewer(self, client):
        resp = client.post('/api/auth/login', data={
            'username': 'viewer',
            'password': 'TestViewerPw1!',
        })
        assert resp.status_code == 200, f"viewer 登录失败: {resp.text}"
        return resp

    def _get_csrf_token(self, client, resp):
        # 通过实际 /api/auth/csrf-token 路由获取 CSRF token（审计 P2-16C 修复：直接测试该路由）
        sid = resp.cookies.get('session')
        assert sid, "登录后应有 session cookie"
        csrf_resp = client.get('/api/auth/csrf-token', cookies={'session': sid})
        assert csrf_resp.status_code == 200, f"CSRF token 路由应返回200: {csrf_resp.text}"
        data = csrf_resp.json()
        assert 'csrf_token' in data, f"响应应包含 csrf_token: {data}"
        assert len(data['csrf_token']) > 0, "csrf_token 不应为空"
        return data['csrf_token']

    def test_csrf_token_endpoint_returns_200(self):
        """验证 /api/auth/csrf-token 路由返回 200 + 有效token（P2-16C）"""
        client = self._get_client()
        resp = self._login_admin(client)
        sid = resp.cookies.get('session')
        assert sid
        csrf_resp = client.get('/api/auth/csrf-token', cookies={'session': sid})
        assert csrf_resp.status_code == 200, f"CSRF token 路由应返回200: {csrf_resp.text}"
        data = csrf_resp.json()
        assert 'csrf_token' in data
        assert len(data['csrf_token']) > 0

    def test_csrf_token_unauthenticated_returns_401(self):
        """未登录用户访问 /api/auth/csrf-token 应返回 401"""
        client = self._get_client()
        csrf_resp = client.get('/api/auth/csrf-token')
        assert csrf_resp.status_code == 401

    def test_encrypt_key_status_endpoint(self):
        """test_encrypt_key_status_endpoint — GET /api/auth/encrypt-key-status 返回正确"""
        client = self._get_client()
        resp = self._login_admin(client)
        cookies = resp.cookies

        status_resp = client.get('/api/auth/encrypt-key-status', cookies=cookies)
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert 'has_custom_key' in data
        assert isinstance(data['has_custom_key'], bool)

    def test_encrypt_key_status_authenticated(self):
        """test_encrypt_key_status_authenticated — 已登录用户可访问"""
        client = self._get_client()
        resp = self._login_admin(client)

        status_resp = client.get('/api/auth/encrypt-key-status', cookies=resp.cookies)
        assert status_resp.status_code == 200

    def test_encrypt_key_status_unauthenticated(self):
        """test_encrypt_key_status_unauthenticated — 未登录用户被拒绝"""
        client = self._get_client()
        status_resp = client.get('/api/auth/encrypt-key-status')
        assert status_resp.status_code == 401, "未登录应返回 401"

    def test_update_encrypt_key(self):
        """test_update_encrypt_key — PUT /api/auth/encrypt-key 更新密钥"""
        client = self._get_client()
        resp = self._login_admin(client)
        cookies = resp.cookies
        csrf = self._get_csrf_token(client, resp)

        new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
        up_resp = client.put(
            '/api/auth/encrypt-key',
            json={'new_key': new_key},
            cookies=cookies,
            headers={'X-CSRF-Token': csrf},
        )
        assert up_resp.status_code == 200, f"更新密钥失败: {up_resp.text}"
        data = up_resp.json()
        assert data['status'] == 'ok'

    def test_update_key_reencrypts(self):
        """test_update_key_reencrypts — 更新密钥后能解密之前加密的值"""
        from web import models
        import web.crypto_utils as cu

        # 确保密钥就绪
        self._ensure_key()

        test_val = "reencrypt_test_secret"
        models.set_app_config('Network.proxy_password', test_val)
        retrieved = models.get_app_config('Network.proxy_password')
        assert retrieved == test_val, "写入和读取应一致"

        # 更新密钥
        client = self._get_client()
        resp = self._login_admin(client)
        cookies = resp.cookies
        csrf = self._get_csrf_token(client, resp)

        new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
        up_resp = client.put(
            '/api/auth/encrypt-key',
            json={'new_key': new_key},
            cookies=cookies,
            headers={'X-CSRF-Token': csrf},
        )
        assert up_resp.status_code == 200, f"密钥轮换失败: {up_resp.text}"

        # 清除缓存，确保使用新密钥
        cu._fernet_instance = None
        cu._initialized_flag = False

        # 重新读取——re_encrypt_all 应已用新密钥重新加密
        restored = models.get_app_config('Network.proxy_password')
        assert restored == test_val, f"轮换后应仍能读取明文: {restored} != {test_val}"

        # 清理
        conn = models.get_conn()
        conn.execute("DELETE FROM app_config WHERE key = 'Network.proxy_password'")
        conn.commit()

    def test_update_key_short_rejected(self):
        """test_update_key_short_rejected — 过短密钥被拒绝（400）"""
        client = self._get_client()
        resp = self._login_admin(client)
        cookies = resp.cookies
        csrf = self._get_csrf_token(client, resp)

        up_resp = client.put(
            '/api/auth/encrypt-key',
            json={'new_key': 'tooshort'},
            cookies=cookies,
            headers={'X-CSRF-Token': csrf},
        )
        assert up_resp.status_code == 400, f"过短密钥应返回400: {up_resp.status_code}"

    def test_update_key_requires_admin(self):
        """test_update_key_requires_admin — 非管理员不能修改密钥"""
        client = self._get_client()
        resp = self._login_viewer(client)
        cookies = resp.cookies
        csrf = self._get_csrf_token(client, resp)

        new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
        up_resp = client.put(
            '/api/auth/encrypt-key',
            json={'new_key': new_key},
            cookies=cookies,
            headers={'X-CSRF-Token': csrf},
        )
        assert up_resp.status_code in (401, 403), f"viewer 应被拒绝: {up_resp.status_code}"


# ═══════════════════════════════════════════════════════════════
# 6. 登录提示测试
# ═══════════════════════════════════════════════════════════════

class TestLoginKeyHint:
    """测试登录接口返回 encrypt_key_hint 字段"""

    def _get_client(self):
        from fastapi.testclient import TestClient
        from web.webapp import app
        return TestClient(app)

    def _ensure_key(self):
        import web.crypto_utils as cu
        if not os.environ.get('CONFIG_ENCRYPT_KEY'):
            cu.ensure_key_initialized()

    def test_login_returns_encrypt_key_hint(self):
        """test_login_returns_encrypt_key_hint — 登录接口返回encrypt_key_hint字段"""
        self._ensure_key()
        client = self._get_client()

        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': 'TestAdminPw1!',
        })
        assert resp.status_code == 200, f"登录失败: {resp.text}"
        data = resp.json()
        assert 'encrypt_key_hint' in data, f"登录返回体应包含 encrypt_key_hint: {data}"
        assert isinstance(data['encrypt_key_hint'], bool), "encrypt_key_hint 应为布尔值"

    def test_login_hint_true_when_auto_key(self):
        """自动生成密钥时 hint 为 True"""
        # 确保是自动密钥（无环境变量）
        os.environ.pop('CONFIG_ENCRYPT_KEY', None)
        os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
        import web.crypto_utils as cu
        cu._fernet_instance = None
        cu._initialized_flag = False
        cu.ensure_key_initialized()

        # 同步 webapp 模块级标记
        import web.webapp as ww
        ww.CONFIG_KEY_IS_MANUAL = cu.is_custom_key()

        client = self._get_client()
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': 'TestAdminPw1!',
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['encrypt_key_hint'] is True, \
            f"自动密钥时 hint 应为 True, got {data['encrypt_key_hint']}"

    def test_login_hint_false_when_custom_key(self):
        """自定义密钥时 hint 为 false"""
        # 设环境变量
        env_key = 'aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899'
        os.environ['CONFIG_ENCRYPT_KEY'] = env_key
        import web.crypto_utils as cu
        cu._fernet_instance = None
        cu._initialized_flag = False
        cu.ensure_key_initialized()

        # 同步 webapp 模块级标记
        import web.webapp as ww
        ww.CONFIG_KEY_IS_MANUAL = cu.is_custom_key()

        client = self._get_client()
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': 'TestAdminPw1!',
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['encrypt_key_hint'] is False, \
            f"自定义密钥时 hint 应为 False, got {data['encrypt_key_hint']}"

        # 清理
        os.environ.pop('CONFIG_ENCRYPT_KEY', None)
        os.environ.pop('CONFIG_ENCRYPT_KEY_SET_MANUALLY', None)
        cu._fernet_instance = None
        cu._initialized_flag = False


