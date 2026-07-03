#!/usr/bin/env python3
"""
敏感配置字段加解密模块

加密方案：cryptography.fernet.Fernet (AES-128-CBC + HMAC-SHA256)
密钥来源：
  1. 环境变量 CONFIG_ENCRYPT_KEY（优先使用，推荐生产环境）
  2. SQLite app_config System.encrypt_key（首次运行自动生成并持久化）
  3. 如果两者都未设置，ensure_key_initialized() 自动生成随机密钥并持久化
"""

import base64
import logging
import os
import platform
import secrets
import subprocess
import sys
import threading

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger('web.crypto_utils')

# 确保 ensure_key_initialized 是线程安全的
_initialized_lock = threading.Lock()
_initialized_flag = False

# PBKDF2 盐值（固定，用于从原始密钥派生 Fernet 密钥）
_BUILTIN_SALT = b'liv3_s0urc3_m4n4g'

# 机器绑定加密的盐值（与常规加密隔离）
_MACHINE_SALT = b'm4ch1n3_b0und_t0k'

# 敏感配置项集合（key 的完整点分名称）
SENSITIVE_KEYS = frozenset(
    {
        'Network.proxy_username',
        'Network.proxy_password',
        'GitHub.api_token',
    }
)

# 需要机器绑定的敏感字段（复制到其他机器后无法解密）
MACHINE_BOUND_KEYS = frozenset(
    {
        'GitHub.api_token',
    }
)

_fernet_instance: Fernet = None
_machine_fernet_instance: Fernet = None


def _get_raw_key() -> bytes:
    """获取原始密钥字节（优先环境变量，其次 SQLite）"""
    raw = os.environ.get('CONFIG_ENCRYPT_KEY', '')
    if raw:
        return raw.encode()
    # 环境变量未设置，尝试从 SQLite 读取已持久化的密钥
    try:
        from web import models

        stored = models.get_app_config_raw('System.encrypt_key')
        if stored:
            return stored.encode()
    except Exception as _:
        logger.debug(f'从SQLite读取密钥失败(首次启动时正常): {_}')
    raise RuntimeError(
        '加密密钥未设置！请通过环境变量 CONFIG_ENCRYPT_KEY 设置，或确保 ensure_key_initialized() 已在启动时被调用'
    )


def _get_fernet_from_key(key_bytes: bytes) -> Fernet:
    """从原始密钥字节派生 Fernet 实例"""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_BUILTIN_SALT, iterations=600000)
    derived_key = base64.urlsafe_b64encode(kdf.derive(key_bytes))
    return Fernet(derived_key)


def _reset_fernet():
    """重置 Fernet 实例（密钥轮换时调用）"""
    global _fernet_instance, _machine_fernet_instance, _initialized_flag
    _fernet_instance = None
    _machine_fernet_instance = None
    _initialized_flag = False  # 下次 get_cipher 重新初始化


# ══════════════════════════════════════════════════════
# 机器绑定加密
# ══════════════════════════════════════════════════════


def get_machine_id() -> str:
    """获取当前机器的唯一标识符

    跨平台方案：
    - Windows: HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid
    - Linux:   /etc/machine-id 或 /var/lib/dbus/machine-id
    - macOS:   IOPlatformUUID

    返回的 ID 在同一台机器上是稳定的，换机器后不同。
    """
    # Windows
    if sys.platform == 'win32':
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\\Microsoft\\Cryptography') as key:
                guid, _ = winreg.QueryValueEx(key, 'MachineGuid')
                if guid:
                    return str(guid)
        except Exception as e:
            logger.warning(f'获取 Windows MachineGuid 失败: {e}')

    # Linux
    if sys.platform.startswith('linux'):
        for path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
            try:
                with open(path) as f:
                    mid = f.read().strip()
                    if mid:
                        return mid
            except Exception:
                pass

    # macOS
    if sys.platform == 'darwin':
        try:
            result = subprocess.run(
                ['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if 'IOPlatformUUID' in line:
                    parts = line.split('"')
                    if len(parts) >= 4:
                        return parts[3]
        except Exception as e:
            logger.warning(f'获取 macOS UUID 失败: {e}')

    # 兜底：使用 hostname + platform 信息（不如系统 ID 稳定，但总比没有好）
    fallback = f'{platform.node()}-{platform.machine()}-{platform.processor()}'
    logger.warning(f'无法获取系统 Machine ID，使用兜底方案: {fallback}')
    return fallback


def _get_machine_fernet() -> Fernet:
    """获取基于机器 ID 派生的 Fernet 实例（懒加载）"""
    global _machine_fernet_instance
    if _machine_fernet_instance is not None:
        return _machine_fernet_instance
    machine_id = get_machine_id()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_MACHINE_SALT, iterations=600000)
    derived_key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode('utf-8')))
    _machine_fernet_instance = Fernet(derived_key)
    return _machine_fernet_instance


def encrypt_machine_bound(plaintext: str) -> str:
    """使用机器 ID 加密明文，返回 'MENC:' 前缀的密文

    密文只能在本机解密。复制程序到其他机器后，因 Machine ID 不同，
    解密会失败，从而保护敏感数据。
    幂等性：已加密的值不会再次加密。
    """
    if not plaintext or plaintext.startswith('MENC:'):
        return plaintext
    cipher = _get_machine_fernet()
    encrypted = cipher.encrypt(plaintext.encode('utf-8'))
    return 'MENC:' + encrypted.decode('utf-8')


def decrypt_machine_bound(ciphertext: str) -> str | None:
    """解密 'MENC:' 前缀的机器绑定密文

    非 'MENC:' 前缀的原样返回（兼容旧数据）。
    解密失败（换机器）返回 None。
    """
    if not ciphertext:
        return ciphertext
    if not ciphertext.startswith('MENC:'):
        # 兼容：可能是由旧版 ENC: 加密的，尝试常规解密
        if ciphertext.startswith('ENC:'):
            return decrypt_value(ciphertext)
        return ciphertext
    cipher = _get_machine_fernet()
    try:
        payload = ciphertext[5:]  # 去掉 MENC: 前缀
        return cipher.decrypt(payload.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f'机器绑定解密失败（可能已换机器）: {e}')
        return None


def is_machine_bound_encrypted(value: str) -> bool:
    """判断值是否已使用机器绑定加密"""
    return value.startswith('MENC:')


def is_machine_bound_key(key: str) -> bool:
    """判断 config key 是否属于机器绑定字段"""
    return key in MACHINE_BOUND_KEYS


def get_cipher() -> Fernet:
    """获取 Fernet 加密实例（懒加载）

    如果密钥未设置，自动调用 ensure_key_initialized() 初始化。
    """
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance
    try:
        key = _get_raw_key()
    except RuntimeError:
        # 密钥未设置，自动初始化（首次运行 / 测试场景）
        ensure_key_initialized()
        key = _get_raw_key()
    _fernet_instance = _get_fernet_from_key(key)
    return _fernet_instance


def encrypt_value(plaintext: str) -> str:
    """加密明文字符串，返回 'ENC:' 前缀的 base64 密文字符串

    幂等性：已加密的值不会再次加密（前缀检测）
    """
    if not plaintext or plaintext.startswith('ENC:'):
        return plaintext
    cipher = get_cipher()
    encrypted = cipher.encrypt(plaintext.encode('utf-8'))
    return 'ENC:' + encrypted.decode('utf-8')


def decrypt_value(ciphertext: str) -> str | None:
    """解密 'ENC:' 前缀的密文，返回明文

    非 'ENC:' 前缀的原样返回（幂等、兼容未加密值）
    纪码修复 P0-3: 解密失败返回 None 而非 ''，让调用方可以区分"解密失败"与"空字符串"。
    """
    if not ciphertext or not ciphertext.startswith('ENC:'):
        return ciphertext
    cipher = get_cipher()
    try:
        payload = ciphertext[4:]  # 去掉 ENC: 前缀
        return cipher.decrypt(payload.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f'解密失败: {e}')
        # 纪码修复 P0-3: 返回 None 让调用方能区分解密失败和空字符串
        return None


def is_encrypted(value: str) -> bool:
    """判断值是否已加密（前缀检测）

    注意：此函数仅做前缀检查，不验证 Fernet token 格式有效性。
    严格校验由 _is_valid_fernet_token 完成。
    """
    return value.startswith('ENC:')


def _is_valid_fernet_token(value: str) -> bool:
    """判断 ENC: 前缀后的内容是否为有效 Fernet token

    修复 P2-新-3: 防止 ENC: 前缀误判导致字面字符串 'ENC:hello' 被跳过加密，
    导致后续解密失败时配置值被无声吞没。
    """
    if not value.startswith('ENC:'):
        return False
    payload = value[4:]
    # Fernet token 经 base64 编码后标准长度为 44 字节（不带 padding）
    if len(payload) < 44:
        return False
    try:
        # 尝试 base64 解码验证（不触发解密）
        base64.urlsafe_b64decode(payload + '==')
        return True
    except Exception:
        # base64解码失败 = 不是有效Fernet token，返回False是预期行为
        return False


def is_sensitive_key(key: str) -> bool:
    """判断 config key 是否属于敏感字段"""
    return key in SENSITIVE_KEYS


def generate_key() -> str:
    """生成新的32字节随机密钥（base64编码）"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def re_encrypt_all(new_key: str) -> int:
    """用新密钥重新加密所有敏感配置，返回重新加密的记录数

    两阶段提交实现（原子性）：
    Phase 1: 内存中完成全部解密 + 新加密
    Phase 2: 单事务批量写入（全部成功或全部回滚）
    中途 crash 不会导致数据不一致。
    """
    from web import models

    # Phase 1: 读取所有原始加密值
    encrypted_old = models.get_all_sensitive_raw()
    if not encrypted_old:
        return 0

    # 用旧密钥逐个解密（全部在内存中完成）
    old_cipher = get_cipher()
    plain_values = {}
    for key, enc_val in encrypted_old.items():
        if enc_val.startswith('ENC:'):
            try:
                plain = old_cipher.decrypt(enc_val[4:].encode()).decode()
            except Exception as e:
                logger.error(f'解密 {key} 失败: {e}')
                raise RuntimeError(f'解密 {key} 失败（密钥不匹配）: {e}')
        else:
            plain = enc_val
        plain_values[key] = plain

    # 用新密钥全部重新加密（内存中完成）
    new_kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_BUILTIN_SALT, iterations=600000)
    new_derived = base64.urlsafe_b64encode(new_kdf.derive(new_key.encode()))
    new_cipher = Fernet(new_derived)

    new_encrypted = {}
    for key, plain in plain_values.items():
        new_encrypted[key] = 'ENC:' + new_cipher.encrypt(plain.encode()).decode()

    # Phase 2: 单事务批量写入（原子提交）
    conn = models.get_conn()
    try:
        with conn:  # 自动事务管理
            for key, enc_val in new_encrypted.items():
                conn.execute(
                    "INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    (key, enc_val),
                )
            # 更新密钥记录
            conn.execute(
                "INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                ('System.encrypt_key', new_key),
            )
    finally:
        conn.close()

    # 更新环境变量和重置 Fernet 实例
    os.environ['CONFIG_ENCRYPT_KEY'] = new_key
    _reset_fernet()

    return len(plain_values)


def ensure_key_initialized():
    """确保加密密钥已初始化（首次运行时自动生成）

    线程安全：使用 _initialized_lock + 双重检查锁定模式。

    行为：
    - 环境变量 CONFIG_ENCRYPT_KEY 已设置 → 直接使用（用户自定义）
    - SQLite 已存储 System.encrypt_key → 恢复到环境变量
    - 两者都未设置 → 自动生成随机密钥并持久化到 SQLite + 输出到日志

    纪码修复 P0-3: 在自动生成密钥前输出明确警告，提示旧加密数据不可恢复。
    """
    global _initialized_flag
    if _initialized_flag:
        return

    with _initialized_lock:
        if _initialized_flag:
            return  # 双重检查

        raw = os.environ.get('CONFIG_ENCRYPT_KEY', '')
        if raw:
            # 环境变量已设置，使用用户自定义密钥
            # 设置标记，表示密钥来源为环境变量（用户自定义）
            os.environ['CONFIG_ENCRYPT_KEY_SET_MANUALLY'] = '1'
            logger.info('CONFIG_ENCRYPT_KEY 已从环境变量读取（用户自定义）')
            _initialized_flag = True
            return

        # 尝试从 SQLite 恢复
        try:
            from web import models

            stored = models.get_app_config_raw('System.encrypt_key')
            if stored:
                os.environ['CONFIG_ENCRYPT_KEY'] = stored
                # 不设置 CONFIG_ENCRYPT_KEY_SET_MANUALLY — 密钥为自动生成
                logger.info('加密密钥已从 SQLite 恢复')
                _initialized_flag = True
                return
        except Exception as _:
            logger.warning(f'从SQLite恢复密钥失败(将自动生成): {_}')

        # 纪码修复 P0-3: 环境变量和 SQLite 均无密钥时，先警告旧数据不可恢复再自动生成
        logger.warning(
            '⚠️  加密密钥未在环境变量或 SQLite 中找到！正在自动生成新密钥。\n'
            '    旧加密数据（如有）将因密钥丢失而不可恢复。\n'
            '    建议：设置环境变量 CONFIG_ENCRYPT_KEY 使用自定义密钥以避免此情况。'
        )

        # 首次运行：自动生成随机密钥
        new_key = generate_key()
        os.environ['CONFIG_ENCRYPT_KEY'] = new_key

        # 持久化到 SQLite
        try:
            from web import models

            models.set_app_config_raw('System.encrypt_key', new_key)
            logger.info('加密密钥已持久化到 SQLite (System.encrypt_key)')
        except Exception as e:
            logger.warning(f'密钥持久化到 SQLite 失败（不影响运行）: {e}')

        # 以醒目边框格式打印密钥到日志
        _log_key_to_console(new_key)
        _initialized_flag = True


def is_custom_key() -> bool:
    """判断当前密钥是否为用户通过环境变量自定义设置"""
    return os.environ.get('CONFIG_ENCRYPT_KEY_SET_MANUALLY', '') == '1'


def _log_key_to_console(key_value: str):
    """以醒目边框格式打印密钥到日志"""
    box_width = 66

    title = '  加密密钥（请保存至安全位置，丢失后加密数据无法恢复）'
    key_line = f'  CONFIG_ENCRYPT_KEY={key_value}  '

    logger.warning('')
    logger.warning('╔' + '═' * (box_width - 2) + '╗')
    logger.warning('║' + title.ljust(box_width - 4) + '  ║')
    logger.warning('║' + ' ' * (box_width - 4) + '  ║')
    logger.warning('║' + key_line.ljust(box_width - 4) + '  ║')
    logger.warning('╚' + '═' * (box_width - 2) + '╝')
    logger.warning('')
    logger.warning(
        '💡 建议：设置环境变量 CONFIG_ENCRYPT_KEY 使用自定义密钥\n'
        '   例如: docker run -e CONFIG_ENCRYPT_KEY=<您的密钥> ...'
    )
