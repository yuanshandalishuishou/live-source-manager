# -*- coding: utf-8 -*-
"""
配置端加密测试 — 从 test_encrypt_key.py 拆分

覆盖范围：
  1. models 层与加密的集成（敏感配置加密存储）
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
# 全局 fixture：保存/恢复环境变量
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
# 配置加密集成测试
# ═══════════════════════════════════════════════════════════════

class TestModelsEncryption:
    """测试 models 层读写敏感配置时自动加解密"""

    # 注意：这些测试依赖于 conftest 设置的共享临时 DB
    # conftest 已初始化数据库并覆写 models.DATA_DIR / models.DB_PATH

    def _ensure_key_for_models(self):
        """确保加密密钥就绪"""
        import web.crypto_utils as cu
        cu.ensure_key_initialized()

    def test_models_set_sensitive_stores_encrypted(self):
        """敏感配置项在数据库中以加密形式存储"""
        from web import models
        self._ensure_key_for_models()

        test_val = "ProxyPass!ModelTest"
        models.set_app_config('Network.proxy_password', test_val)

        retrieved = models.get_app_config('Network.proxy_password')
        assert retrieved == test_val, f"读取应返回明文: {retrieved} != {test_val}"

        # 验证数据库存的是加密值
        conn = models.get_conn()
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'Network.proxy_password'"
        ).fetchone()
        assert row is not None, "数据库应有记录"
        stored_raw = row['value']
        assert stored_raw.startswith('ENC:'), f"数据库应存加密值: {stored_raw[:30]}"

        conn.execute("DELETE FROM app_config WHERE key = 'Network.proxy_password'")
        conn.commit()

    def test_models_non_sensitive_plain(self):
        """非敏感配置项不加密"""
        from web import models

        test_val = "plain_value_for_test"
        models.set_app_config('Sources.local_dirs', test_val)

        retrieved = models.get_app_config('Sources.local_dirs')
        assert retrieved == test_val

        conn = models.get_conn()
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'Sources.local_dirs'"
        ).fetchone()
        assert row is not None
        assert not row['value'].startswith('ENC:'), "非敏感值不应加密"

        conn.execute("DELETE FROM app_config WHERE key = 'Sources.local_dirs'")
        conn.commit()

    def test_models_get_all_config_decrypts(self):
        """get_all_config 对敏感字段解密"""
        from web import models
        self._ensure_key_for_models()

        test_val = "AllConfigDecryptTest!"
        models.set_app_config('Network.proxy_password', test_val)
        models.set_app_config('Sources.local_dirs', 'plain_dir')

        config = models.get_all_config()
        network = config.get('Network', {})
        assert network.get('proxy_password') == test_val, \
            f"get_all_config 应返回解密后的值: {network.get('proxy_password')}"

        # 清理
        conn = models.get_conn()
        conn.execute("DELETE FROM app_config WHERE key = 'Network.proxy_password'")
        conn.execute("DELETE FROM app_config WHERE key = 'Sources.local_dirs'")
        conn.commit()
