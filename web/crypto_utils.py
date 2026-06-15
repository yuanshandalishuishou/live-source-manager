#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
敏感配置字段加解密模块

加密方案：cryptography.fernet.Fernet (AES-128-CBC + HMAC-SHA256)
密钥来源：
  1. 环境变量 CONFIG_ENCRYPT_KEY（优先使用）
  2. 内置固定密钥（仅用于开发/测试环境）
"""

import os
import base64
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger('web.crypto_utils')

# 内置固定密钥（仅用于开发/测试、运行环境未设置 CONFIG_ENCRYPT_KEY 时）
_FALLBACK_KEY = b'LiveSourceManagerDefaultKey16!'  # 至少 16 字节
_SALT = b'LiveSourceMgrSalt2024'  # PBKDF2 盐值

# 敏感配置项集合（key 的完整点分名称）
SENSITIVE_KEYS = frozenset({
    'Network.proxy_password',
    'GitHub.api_token',
})

_fernet_instance = None


def _get_fernet() -> Fernet:
    """获取 Fernet 加密实例（懒加载）"""
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    env_key = os.environ.get('CONFIG_ENCRYPT_KEY', '')
    if env_key:
        try:
            # 环境变量为 32 位 hex -> bytes
            key_bytes = bytes.fromhex(env_key)
            if len(key_bytes) < 16:
                raise ValueError(f"CONFIG_ENCRYPT_KEY 不足 16 字节 (当前 {len(key_bytes)} 字节)")
            # Fernet 要求 32 位 base64 编码的密钥
            if len(key_bytes) != 32:
                # 用 PBKDF2 派生为 32 字节
                kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=100000)
                key_bytes = kdf.derive(key_bytes)
            fernet_key = base64.urlsafe_b64encode(key_bytes)
        except Exception as e:
            logger.error(f"CONFIG_ENCRYPT_KEY 格式错误: {e}，将使用内置密钥")
            fernet_key = _derive_fallback_key()
    else:
        logger.debug("CONFIG_ENCRYPT_KEY 未设置，使用内置固定密钥")
        fernet_key = _derive_fallback_key()

    _fernet_instance = Fernet(fernet_key)
    return _fernet_instance


def _derive_fallback_key() -> bytes:
    """从内置固定密钥派生稳定的 Fernet 密钥"""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=100000)
    return base64.urlsafe_b64encode(kdf.derive(_FALLBACK_KEY))


def encrypt_value(plaintext: str) -> str:
    """加密明文字符串，返回 'ENC:' 前缀的 base64 密文字符串

    幂等性：已加密的值不会再次加密（前缀检测）
    """
    if not plaintext or plaintext.startswith('ENC:'):
        return plaintext
    f = _get_fernet()
    ciphertext = f.encrypt(plaintext.encode('utf-8'))
    return 'ENC:' + ciphertext.decode('utf-8')


def decrypt_value(ciphertext: str) -> str:
    """解密 'ENC:' 前缀的密文，返回明文

    非 'ENC:' 前缀的原样返回（幂等、兼容未加密值）
    """
    if not ciphertext or not ciphertext.startswith('ENC:'):
        return ciphertext
    f = _get_fernet()
    try:
        payload = ciphertext[4:]  # 去掉 ENC: 前缀
        return f.decrypt(payload.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"解密失败: {e}")
        # 解密失败时返回空字符串，避免破坏应用
        return ''


def is_encrypted(value: str) -> bool:
    """判断值是否已加密"""
    return value.startswith('ENC:')


def is_sensitive_key(key: str) -> bool:
    """判断 config key 是否属于敏感字段"""
    return key in SENSITIVE_KEYS
