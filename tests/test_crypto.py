"""
web.crypto_utils 模块单元测试 — 加解密

覆盖：加密/解密、机器绑定加密/解密、密钥初始化、get_machine_id、
is_sensitive_key, is_machine_bound_key, encrypt_value 幂等性
"""

import os
import sys

# 设置测试环境
os.environ['WEB_ADMIN_PASSWORD'] = 'TestAdminPw1!'
os.environ['CONFIG_ENCRYPT_KEY'] = 'test-key-not-valid-for-prod-only-test'

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from web import crypto_utils as cu


class TestEncryptDecrypt:
    """Fernet 加解密"""

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = 'hello-world-123'
        encrypted = cu.encrypt_value(plaintext)
        assert encrypted.startswith('ENC:')
        decrypted = cu.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_encrypt_empty_string(self):
        encrypted = cu.encrypt_value('')
        assert encrypted == ''  # 空字符串应原样返回

    def test_decrypt_non_encrypted(self):
        result = cu.decrypt_value('plain-text')
        assert result == 'plain-text'  # 非 ENC: 前缀应原样返回

    def test_encrypt_idempotent(self):
        """已加密的值不应再次加密"""
        plaintext = 'secret-value'
        encrypted = cu.encrypt_value(plaintext)
        double_encrypted = cu.encrypt_value(encrypted)
        assert double_encrypted == encrypted

    def test_decrypt_none_input(self):
        result = cu.decrypt_value(None)
        assert result is None

    def test_encrypt_none_input(self):
        result = cu.encrypt_value(None)
        assert result is None

    def test_decrypt_failed_returns_none(self):
        """解密失败应返回 None 而非空字符串"""
        result = cu.decrypt_value('ENC:invalid-token-format')
        # 无效 token 解码失败返回 None
        assert result is None

    def test_long_string_encrypt_decrypt(self):
        plaintext = 'A' * 10000
        encrypted = cu.encrypt_value(plaintext)
        decrypted = cu.decrypt_value(encrypted)
        assert decrypted == plaintext


class TestMachineBoundEncryption:
    """机器绑定加解密"""

    def test_encrypt_decrypt_machine_bound(self):
        plaintext = 'machine-bound-secret'
        encrypted = cu.encrypt_machine_bound(plaintext)
        assert encrypted.startswith('MENC:')
        decrypted = cu.decrypt_machine_bound(encrypted)
        assert decrypted == plaintext

    def test_machine_bound_idempotent(self):
        plaintext = 'secret'
        encrypted = cu.encrypt_machine_bound(plaintext)
        double_encrypted = cu.encrypt_machine_bound(encrypted)
        assert double_encrypted == encrypted

    def test_decrypt_non_menc(self):
        """非 MENC: 前缀的值原样返回"""
        result = cu.decrypt_machine_bound('plain-text')
        assert result == 'plain-text'

    def test_is_machine_bound_encrypted(self):
        assert cu.is_machine_bound_encrypted('MENC:xxx') is True
        assert cu.is_machine_bound_encrypted('ENC:xxx') is False
        assert cu.is_machine_bound_encrypted('plain') is False

    def test_decrypt_machine_bound_failure(self):
        """机器绑定解密失败（如换机器后）应返回 None"""
        result = cu.decrypt_machine_bound('MENC:invalid-token')
        assert result is None


class TestMachineId:
    """机器 ID 获取"""

    def test_get_machine_id_returns_string(self):
        mid = cu.get_machine_id()
        assert isinstance(mid, str)
        assert len(mid) > 0

    def test_get_machine_id_stable(self):
        """同一机器多次调用应返回相同的 ID（兜底方案也应稳定）"""
        mid1 = cu.get_machine_id()
        mid2 = cu.get_machine_id()
        assert mid1 == mid2


class TestKeyFunctions:
    """密钥相关辅助函数"""

    def test_is_sensitive_key(self):
        assert cu.is_sensitive_key('Network.proxy_password') is True
        assert cu.is_sensitive_key('Network.proxy_host') is False

    def test_is_machine_bound_key(self):
        assert cu.is_machine_bound_key('GitHub.api_token') is True
        assert cu.is_machine_bound_key('Network.proxy_password') is False

    def test_is_encrypted(self):
        assert cu.is_encrypted('ENC:xxx') is True
        assert cu.is_encrypted('MENC:xxx') is False
        assert cu.is_encrypted('plain') is False

    def test_is_valid_fernet_token(self):
        # 真正的 Fernet token 由 encrypt_value 生成
        encrypted = cu.encrypt_value('test')
        assert cu._is_valid_fernet_token(encrypted) is True

    def test_is_valid_fernet_token_invalid(self):
        assert cu._is_valid_fernet_token('ENC:hello') is False
        assert cu._is_valid_fernet_token('plain') is False

    def test_generate_key_returns_base64(self):
        key = cu.generate_key()
        assert len(key) > 0
        import base64

        base64.urlsafe_b64decode(key)  # 不应抛出异常


class TestEnsureKeyInitialized:
    """密钥初始化"""

    def test_ensure_key_initialized(self):
        cu.ensure_key_initialized()
        # 不应抛出异常
        assert True

    def test_cipher_after_ensure(self):
        cipher = cu.get_cipher()
        assert cipher is not None
        test_val = cu.encrypt_value('test-after-init')
        assert test_val.startswith('ENC:')
