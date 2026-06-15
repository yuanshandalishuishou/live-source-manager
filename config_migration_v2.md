# 架构设计方案 v2 — 全量配置SQLite化 + app/config_manager.py改造

> **背景**：第一轮迭代（commit 2f943c9）已实现 `web/webapp.py` 的 `read_config()/write_config()` 切换到 SQLite `app_config` 表，但 config.ini 仍保留双写。后台模块（`app/main.py`、`app/source_manager.py`、`app/stream_tester.py` 等）仍从 config.ini 读取。
>
> **目标**：全部配置存入 SQLite，彻底废弃 config.ini 的运行时依赖。包括用户名密码等敏感配置进入 SQLite（加密存储）。`app/config_manager.py` 的 `Config` 类改为 SQLite 读取。

---

## Part 1: app_config 表 — 敏感字段加密支持

### 当前表结构

```sql
CREATE TABLE IF NOT EXISTS app_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 设计决策：字段级加密

**方案选择**：字段级加密，仅对敏感字段做加密，非全表加密。

原因：
1. **可搜索性**：非敏感字段可直接查询和展示，无需解密
2. **性能**：避免不必要的加解密开销
3. **可维护性**：`SENSITIVE_KEYS` 集合清晰定义了哪些字段需要保护

### 加密方案

**算法**：`cryptography.fernet.Fernet`（AES-128-CBC + HMAC-SHA256），对称加密。

**密钥来源**：
1. 优先使用环境变量 `CONFIG_ENCRYPT_KEY`（32位 hex 字符串，即 16 字节，匹配 Fernet 的 32 位 base64 编码密钥）
2. 若未设置，使用内置固定密钥（至少 16 字节，用于开发/测试环境）
3. **生产环境必须设置** `CONFIG_ENCRYPT_KEY`

### 新增模块：`web/crypto_utils.py`

```python
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
    'Auth.admin_password',   # 预留：将来可能从环境变量迁移到 app_config
    'Auth.viewer_password',  # 同上
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
        logger.warning("CONFIG_ENCRYPT_KEY 未设置，使用内置固定密钥（不安全！生产环境请设置）")
        fernet_key = _derive_fallback_key()

    _fernet_instance = Fernet(fernet_key)
    return _fernet_instance


def _derive_fallback_key() -> bytes:
    """从内置固定密钥派生稳定的 Fernet 密钥"""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=100000)
    return base64.urlsafe_b64encode(kdf.derive(_FALLBACK_KEY))


def encrypt_value(plaintext: str) -> str:
    """加密明文字符串，返回 base64 编码的密文字符串（带 ENC: 前缀）"""
    if not plaintext:
        return plaintext
    f = _get_fernet()
    ciphertext = f.encrypt(plaintext.encode('utf-8'))
    return 'ENC:' + ciphertext.decode('utf-8')


def decrypt_value(ciphertext: str) -> str:
    """解密 ENC: 前缀的密文字符串，返回明文"""
    if not ciphertext or not ciphertext.startswith('ENC:'):
        return ciphertext  # 未加密的原样返回
    f = _get_fernet()
    try:
        payload = ciphertext[4:]  # 去掉 ENC: 前缀
        return f.decrypt(payload.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"解密失败: {e}")
        return ''


def is_sensitive_key(key: str) -> bool:
    """判断 config key 是否属于敏感字段"""
    return key in SENSITIVE_KEYS
```

### 修改 `web/models.py`

在 `set_app_config` 和 `get_app_config` 中注入加密/解密逻辑：

```python
from .crypto_utils import is_sensitive_key, encrypt_value, decrypt_value


def set_app_config(key: str, value: str):
    """INSERT OR REPLACE 写入单个配置值（敏感字段自动加密）"""
    if is_sensitive_key(key):
        value = encrypt_value(value)
    _execute(
        "INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value)
    )


def get_app_config(key: str) -> Optional[str]:
    """读取单个配置值（敏感字段自动解密）"""
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    if row:
        value = row['value']
        if is_sensitive_key(key):
            value = decrypt_value(value)
        return value
    return None
```

**⚠️ 注意**：`import_from_ini_file` 也需处理加密 —— 导入时通过 `set_app_config`（已在其中加密），所以无需额外改动。

---

## Part 2: app/config_manager.py 全面重写

### 核心目标

- 保持与现有调用方完全兼容的接口（特别是 `Config.get(section, key)` 模式）
- `Config` 类默认从 SQLite 读取
- 保留 INI 读取能力用于首次导入场景
- 保持 `get_testing_params()` / `get_network_config()` 等便捷方法的返回值格式不变

### 新 Config 类设计

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块 — 全SQLite版（v2）
所有运行时配置从 SQLite app_config 表读取，config.ini 仅作为首次导入源。
"""
import os
import sys
import time
import logging
import logging.handlers
import configparser
from typing import Dict, List, Any, Optional
from app.utils import ConfigError


class Config:
    """配置管理类 — 全SQLite版

    接口完全兼容旧版 Config:
    - __init__(from_sqlite=True) / __init__(config_path, reload_interval) — 默认走 SQLite
    - get(section, key, default=None) — 读取配置
    - get_xxx() 便捷方法 — 返回格式与前版完全一致
    - create_default_config() — 保留（首次导入使用）
    """

    def __init__(self, config_path: str = "/config/config.ini", reload_interval: int = 60):
        """
        参数向后兼容：
        - config_path: 仅首次导入时使用，运行时读取走 SQLite
        - reload_interval: 保留但不再使用（SQLite 始终最新）
        """
        self.config_path = config_path
        self.config = configparser.ConfigParser()  # 仅用于 create_default_config
        self._from_sqlite = True  # 默认走 SQLite
        self._check_reload_interval = reload_interval
        self._last_check_time = 0.0

        # 尝试导入 web.models（可能失败，例如在测试环境中）
        try:
            from web import models as _m
            self._models = _m
        except ImportError:
            self._models = None
            self._from_sqlite = False
            # 回退到 INI 文件
            if os.path.exists(config_path):
                self.config.read(config_path, encoding='utf-8')
            else:
                self.create_default_config()
                self.config.read(config_path, encoding='utf-8')

    def _get_models(self):
        """延迟获取 models 引用"""
        if self._models is None:
            try:
                from web import models as _m
                self._models = _m
            except ImportError:
                raise ConfigError("无法加载 web.models 模块，请确保 web 包可导入")
        return self._models

    # ── 核心 get/set 接口（兼容旧版） ──────────────

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """统一读取入口 — 优先 SQLite，回退 INI"""
        if self._from_sqlite and self._models:
            try:
                val = self._get_models().get_app_config(f"{section}.{key}")
                if val is not None:
                    return val
            except Exception:
                pass  # 回退到 INI
        # INI 回退
        if self.config.has_section(section):
            return self.config.get(section, key, fallback=default)
        return default

    def set(self, section: str, key: str, value: str):
        """统一写入入口 — 写入 SQLite"""
        if self._from_sqlite and self._models:
            try:
                self._get_models().set_app_config(f"{section}.{key}", value)
                return
            except Exception:
                pass
        # INI 回退
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, value)

    def save(self):
        """保存回 INI（兼容旧版，保留但不推荐使用）"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)

    # ── 便捷获取方法（保持返回值格式完全一致） ──────

    def get_logging_config(self) -> Dict:
        """获取日志配置"""
        return {
            'level': self.get('Logging', 'level', 'INFO'),
            'file': self.get('Logging', 'file', '/log/app.log'),
            'max_size': self._getint('Logging', 'max_size', 10),
            'backup_count': self._getint('Logging', 'backup_count', 5),
            'enable_console': True,
        }

    def get_network_config(self) -> Dict:
        return {
            'proxy_enabled': self._getbool('Network', 'proxy_enabled', False),
            'proxy_type': self.get('Network', 'proxy_type', 'socks5'),
            'proxy_host': self.get('Network', 'proxy_host', '192.168.1.211'),
            'proxy_port': self._getint('Network', 'proxy_port', 1800),
            'proxy_username': self.get('Network', 'proxy_username', ''),
            'proxy_password': self.get('Network', 'proxy_password', ''),
            'ipv6_enabled': self._getbool('Network', 'ipv6_enabled', False),
        }

    def get_github_config(self) -> Dict:
        return {
            'api_url': self.get('GitHub', 'api_url', 'https://api.github.com'),
            'api_token': self.get('GitHub', 'api_token', ''),
            'rate_limit': self._getint('GitHub', 'rate_limit', 5000),
        }

    def get_testing_params(self) -> Dict:
        return {
            'timeout': self._getint('Testing', 'timeout', 10),
            'concurrent_threads': self._getint('Testing', 'concurrent_threads', 30),
            'cache_ttl': self._getint('Testing', 'cache_ttl', 120),
            'enable_speed_test': self._getbool('Testing', 'enable_speed_test', True),
            'speed_test_duration': self._getint('Testing', 'speed_test_duration', 6),
            'max_workers': 50,  # 固定值，与旧版一致
        }

    def get_filter_params(self) -> Dict:
        return {
            'max_latency': self._getint('Filter', 'max_latency', 5000),
            'min_bitrate': self._getint('Filter', 'min_bitrate', 100),
            'must_hd': self._getbool('Filter', 'must_hd', False),
            'must_4k': self._getbool('Filter', 'must_4k', False),
            'min_speed': self._getint('Filter', 'min_speed', 40),
            'min_resolution': self.get('Filter', 'min_resolution', '720p'),
            'max_resolution': self.get('Filter', 'max_resolution', '4k'),
            'resolution_filter_mode': self.get('Filter', 'resolution_filter_mode', 'range'),
        }

    def get_output_params(self) -> Dict:
        return {
            'filename': self.get('Output', 'filename', 'live.m3u'),
            'group_by': self.get('Output', 'group_by', 'category'),
            'include_failed': self._getbool('Output', 'include_failed', False),
            'max_sources_per_channel': self._getint('Output', 'max_sources_per_channel', 3),
            'enable_filter': self._getbool('Output', 'enable_filter', False),
            'output_dir': '/www/output',
        }

    def get_http_server_config(self) -> Dict:
        return {
            'enabled': self._getbool('HTTPServer', 'enabled', False),
            'host': self.get('HTTPServer', 'host', '0.0.0.0'),
            'port': self._getint('HTTPServer', 'port', 12345),
            'document_root': self.get('HTTPServer', 'document_root', '/www/output'),
        }

    def get_ua_position(self) -> str:
        return self.get('UserAgents', 'ua_position', 'extinf')

    def is_ua_enabled(self) -> bool:
        return self._getbool('UserAgents', 'ua_enabled', True)

    def get_user_agents(self) -> Dict:
        """从 UserAgents section 获取非标准字段（作为 header 来源）"""
        ua_config = {}
        # 从 SQLite 获取全部 UserAgents 配置
        if self._from_sqlite and self._models:
            try:
                all_cfg = self._get_models().get_all_config()
                section_data = all_cfg.get('UserAgents', {})
                for key, value in section_data.items():
                    if key not in ('ua_position', 'ua_enabled'):
                        ua_config[key] = str(value)
                return ua_config
            except Exception:
                pass
        # INI 回退
        if self.config.has_section('UserAgents'):
            for key, value in self.config.items('UserAgents'):
                if key not in ['ua_position', 'ua_enabled']:
                    ua_config[key] = value
        return ua_config

    def get_sources(self) -> Dict:
        local_dirs_raw = self.get('Sources', 'local_dirs', '/config/sources')
        if isinstance(local_dirs_raw, str):
            local_dirs = [d.strip() for d in local_dirs_raw.split(',')]
        else:
            local_dirs = local_dirs_raw

        online_urls_raw = self.get('Sources', 'online_urls', '')
        if online_urls_raw:
            online_urls = [url.strip() for url in online_urls_raw.split('\n') if url.strip()]
        else:
            online_urls = []

        return {'local_dirs': local_dirs, 'online_urls': online_urls}

    # ── 辅助方法 ──────────────────────────────

    def _getint(self, section: str, key: str, default: int) -> int:
        val = self.get(section, key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _getbool(self, section: str, key: str, default: bool) -> bool:
        val = self.get(section, key)
        if val is None:
            return default
        return str(val).lower() in ('true', '1', 'yes', 'on')

    def check_reload(self) -> bool:
        """兼容旧版接口 — SQLite 模式永远返回 False（无需重载）"""
        return False

    # ── INI 相关（仅首次导入 / 测试使用） ──────

    def load_config(self):
        """兼容旧版 — 读取 INI（仅非 SQLite 模式使用）"""
        if not self._from_sqlite:
            if os.path.exists(self.config_path):
                self.config.read(self.config_path, encoding='utf-8')
            else:
                self.create_default_config()

    def create_default_config(self):
        """创建默认 INI 文件"""
        self.config['Sources'] = {
            'local_dirs': '/config/sources',
            'online_urls': 'https://live.zbds.org/tv/iptv4.m3u\n'
                           'https://raw.githubusercontent.com/YueChan/Live/main/APTV.m3u',
        }
        self.config['Network'] = {
            'proxy_enabled': 'False',
            'proxy_type': 'socks5',
            'proxy_host': '192.168.1.211',
            'proxy_port': '1800',
            'proxy_username': '',
            'proxy_password': '',
            'ipv6_enabled': 'False',
        }
        self.config['HTTPServer'] = {
            'enabled': 'True',
            'host': '0.0.0.0',
            'port': '12345',
            'document_root': '/www/output',
        }
        self.config['GitHub'] = {
            'api_url': 'https://api.github.com',
            'api_token': '',
            'rate_limit': '5000',
        }
        self.config['Testing'] = {
            'timeout': '10',
            'concurrent_threads': '30',
            'cache_ttl': '120',
            'enable_speed_test': 'True',
            'speed_test_duration': '6',
        }
        self.config['Output'] = {
            'filename': 'live.m3u',
            'group_by': 'category',
            'include_failed': 'False',
            'max_sources_per_channel': '3',
            'enable_filter': 'False',
        }
        self.config['Logging'] = {
            'level': 'INFO',
            'file': '/log/app.log',
            'max_size': '10',
            'backup_count': '5',
        }
        self.config['Filter'] = {
            'max_latency': '5000',
            'min_bitrate': '100',
            'must_hd': 'False',
            'must_4k': 'False',
            'min_speed': '40',
            'min_resolution': '720p',
            'max_resolution': '4k',
            'resolution_filter_mode': 'range',
        }
        self.config['UserAgents'] = {
            'ua_position': 'extinf',
            'ua_enabled': 'True',
        }

        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)

    @staticmethod
    def create_default_at(config_path: str):
        """在指定路径创建默认配置文件（用于首次运行初始化）"""
        config = Config.__new__(Config)
        config.config_path = config_path
        config.config = configparser.ConfigParser()
        config.create_default_config()
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            config.config.write(f)


# ── Logger 类保持不变 ──────────────────────────────

class Logger:
    """日志管理类 — 保持原样（不依赖 Config 的 INI 读取方式）"""
    # ... 完整实现见原文件，此处略去重复 ...
```

### 关键兼容性说明

1. **`Config()` 无参构造**：现在自动走 SQLite，`__init__` 签名不变（`config_path` 和 `reload_interval` 仅用于首次导入场景）
2. **`Config(config_path)` 带参构造**：向后兼容，但 config_path 仅作首次导入使用（可通过 `_from_sqlite` 切换）
3. **`config.config` 属性**：旧版代码中存在 `self.config.get('GitHub', 'api_token', fallback='')` 直接访问 `configparser.ConfigParser` 实例的写法（`app/source_manager.py:44`）—— 需修改为 `self.config.get('GitHub', 'api_token', '')` 通过新接口访问

---

## Part 3: 后台模块的配置读取分析

### 完整调用列表

| 文件 | 当前用法 | 是否兼容新 Config | 备注 |
|------|---------|------------------|------|
| `app/main.py` | `self.config = Config()` → `config.get_testing_params()` 等 | ✅ 完全兼容 | 无参构造 |
| `app/source_manager.py` | `config: Config` + `config.get_network_config()` + `config.config.get('GitHub','api_token')` | ⚠️ **需修改 direct configparser 访问** | 见下方说明 |
| `app/stream_tester.py` | `config: Config` + `config.get_testing_params()` + `config.config.getint('Testing',...)` | ⚠️ **需修改 direct configparser 访问** | 同上 |
| `app/m3u_generator.py` | `config: Config` + `config.get_output_params()` + `config.get()` | ✅ 完全兼容 | |
| `app/scripts.py` | `config = Config()` → `config.get_network_config()` | ✅ 完全兼容 | |
| `web/webapp.py` | `Config(CONFIG_PATH)` + `Config.create_default_at()` | ✅ 完全兼容 | lifespan 中调用 |

### 需修改的 direct configparser 访问

**`app/source_manager.py` 第 44 行**：
```python
# 当前代码（直接访问 config.config ConfigParser 实例）：
self.api_token = (
    config.config.get('GitHub', 'api_token', fallback='')  # ❌ config.config 在新版中不存在
    or os.environ.get('GITHUB_TOKEN', '')
)

# 改为：
self.api_token = (
    config.get('GitHub', 'api_token', '')  # ✅ 通过统一接口
    or os.environ.get('GITHUB_TOKEN', '')
)
```

**`app/stream_tester.py` 中 `_get_config_timeout` 方法**：
```python
# 当前代码：
def _get_config_timeout(self, key: str, default: int) -> int:
    config = self.config.config  # ❌ direct access
    if config.has_section('Testing'):
        return config.getint('Testing', key, fallback=default)
    return default

# 改为：
def _get_config_timeout(self, key: str, default: int) -> int:
    return self.config._getint('Testing', key, default)  # ✅ 通过新接口
```

---

## Part 4: 首次运行初始化升级

### 当前 lifespan 逻辑（`web/webapp.py`）

```python
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # 1. init_db() — 创建用户表，admin/viewer 密码从环境变量或随机生成
    # 2. init_db() 中密码不会持久化到 app_config —— 用户密码由 bcrypt hash 存 users 表
    # 3. 检查 config.ini，不存在则创建默认
    # 4. 检查 app_config 是否有数据，无则从 INI 导入
```

### 密码管理方案（解决「鸡生蛋」问题）

**用户密码**（`Auth.admin_password`, `Auth.viewer_password`）已由 `init_db()` 通过 bcrypt 存储在 `users` 表中。不存在鸡生蛋问题，无需额外写入 `app_config`。

**环境变量密码**（`WEB_ADMIN_PASSWORD` / `WEB_VIEWER_PASSWORD`）是首次运行的首选方式。如果用户未设置，则生成随机密码并打印到日志 —— 这与当前行为一致。

**建议**：短期内保持现状（密码由环境变量驱动 → 写入 users 表）。长期考虑在 Web 前端添加强制修改密码流程，或在 `init_db()` 写入默认密码到 users 表并在首次登录时强制修改。

### 增加的 lifecycle 逻辑

在现有 lifespan 基础上，需增加一步：

```python
    # ── startup ────────────────────────────────
    # 1. init_db() — 建表 + 创建默认用户（保持现有逻辑）
    # ...
    
    # 2. 检查 config.ini 和导入（保持现有逻辑）
    # ...
    
    # 3. ✅ 新增：将环境变量密码写入 app_config（便于通过配置API读取）
    import os
    admin_pw_env = os.environ.get('WEB_ADMIN_PASSWORD')
    if admin_pw_env:
        models.set_app_config('Auth.admin_password_hint', 'set_via_env')
    viewer_pw_env = os.environ.get('WEB_VIEWER_PASSWORD')
    if viewer_pw_env:
        models.set_app_config('Auth.viewer_password_hint', 'set_via_env')
```

**注意**：用户明文密码本身**不应**写入 `app_config` —— 因为密码已通过 bcrypt 存储在 `users` 表。`Auth.admin_password` 和 `Auth.viewer_password` 虽然被 `SENSITIVE_KEYS` 定义，但仅作为预留，实际写入由管理员在 Web 界面修改密码时通过 API 写入。

---

## Part 5: 变更影响清单

### 需要修改的文件

| # | 文件 | 修改内容 | 风险等级 |
|---|------|---------|---------|
| 1 | `web/crypto_utils.py` | **新建** — 加密工具模块 | 低 |
| 2 | `web/models.py` | 修改 `set_app_config`/`get_app_config`，引入加密 | 中 |
| 3 | `app/config_manager.py` | **全面重写** `Config` 类 | 高 |
| 4 | `app/source_manager.py` | 修改 direct configparser 访问 → 统一接口 | 低 |
| 5 | `app/stream_tester.py` | 修改 `_get_config_timeout` 方法 | 低 |
| 6 | `web/webapp.py` | lifespan 中补充环境变量密码记录（可选） | 低 |
| 7 | `tests/test_config_manager.py` | 更新测试用例以适配新 Config 行为 | 中 |
| 8 | `tests/test_config_sqlite.py` | 新增加密相关测试 | 低 |
| 9 | `tests/test_config_reload.py` | 更新/移除 `check_reload` 相关测试 | 低 |

### 不需要修改的文件

| 文件 | 原因 |
|------|------|
| `app/main.py` | 仅使用 `config.get_xxx()` 便捷方法，接口不变 |
| `app/m3u_generator.py` | 仅使用 `config.get()` 和 `config.get_output_params()` |
| `app/scripts.py` | 仅使用 `config.get_network_config()` 等便捷方法 |
| `app/channel_rules.py` | 不依赖 Config |
| `app/m3u_generator.py` | 仅使用 `config.get()` 和 `config.get_output_params()` |
| `web/auth.py` | 不依赖 Config |
| `app/utils.py` | 仅定义异常类，不依赖 Config |

### 兼容性保护措施

1. **降级策略**：如果 `web.models` 模块无法导入（例如测试环境或未初始化），`Config` 自动回退 INI 读取
2. **向后兼容测试**：所有现有测试用例应继续通过，测试中 `Config(config_path=tmp_path)` 带参构造仍有效

---

## Part 6: 敏感配置项列表

| 配置项 | 点分 key | 敏感等级 | 存储方式 | 加密方案 |
|--------|---------|---------|---------|---------|
| 管理员密码 | `Auth.admin_password` | 🔴 最高 | bcrypt hash 存 `users` 表；不在 app_config 存明文 | Fernet 加密（预留） |
| 查看者密码 | `Auth.viewer_password` | 🔴 最高 | 同上 | Fernet 加密（预留） |
| HTTP Proxy 密码 | `Network.proxy_password` | 🟠 高 | app_config 表，加密存储 | Fernet (AES-128-CBC + HMAC) |
| GitHub Token | `GitHub.api_token` | 🟠 高 | app_config 表，加密存储 | Fernet (AES-128-CBC + HTTP) |
| 代理用户名 | `Network.proxy_username` | 🟡 中 | app_config 表，明文存储 | 非敏感 |
| API 请求地址 | `GitHub.api_url` | 🟢 低 | app_config 表，明文存储 | 非敏感 |
| 本地源目录 | `Sources.local_dirs` | 🟢 低 | app_config 表，明文存储 | 非敏感 |
| 测试超时 | `Testing.timeout` | 🟢 低 | app_config 表，明文存储 | 非敏感 |
| 日志级别 | `Logging.level` | 🟢 低 | app_config 表，明文存储 | 非敏感 |
| 代理主机 | `Network.proxy_host` | 🟢 低 | app_config 表，明文存储 | 非敏感 |
| 代理端口 | `Network.proxy_port` | 🟢 低 | app_config 表，明文存储 | 非敏感 |

### 各敏感字段的加密/处理策略

| 字段 | 存储方式 | 读取时解密 | 展示时脱敏 | 备注 |
|------|---------|-----------|-----------|------|
| `Network.proxy_password` | Fernet 加密 → `ENC:base64...` | `decrypt_value()` 透明解密 | 前端展示 `***` | 通过 `SENSITIVE_FIELDS` 规则脱敏 |
| `GitHub.api_token` | Fernet 加密 → `ENC:base64...` | `decrypt_value()` 透明解密 | 前端展示 `***` | 同上 |

应用层（`app/config_manager.py` 的 `get()` 方法）进行透明加解密：获取 `Network.proxy_password` 时自动解密返回明文，调用方无需额外处理。

---

## 实现优先级与建议

### Phase 1（核心改造）
1. 创建 `web/crypto_utils.py`
2. 修改 `web/models.py` 加解密逻辑
3. **全面重写** `app/config_manager.py`

### Phase 2（兼容性修复）
4. 修改 `app/source_manager.py`
5. 修改 `app/stream_tester.py`
6. 更新 `web/webapp.py` lifespan

### Phase 3（测试与验证）
7. 新增加密测试 (`tests/test_crypto_utils.py`)
8. 更新 Config 测试 (`tests/test_config_manager.py`)
9. 全量测试验证

### 风险项

1. **`Config` 的热加载**：旧版通过 `check_reload()` 定时重读 INI 文件。SQLite 版返回 `False`，因为 SQLite 始终最新。如果外部进程修改了 INI 文件（极少发生的场景），新的 Config 实例不会感知。建议在 `check_reload()` 调用时改为检测 `last_updated` 时间戳。
2. **`encrypt_value` 幂等性**：调用 `set_app_config` 时如果 value 已加密，再次加密会导致双重加密。`is_sensitive_key` 配合 `ENC:` 前缀检测可以避免此问题。在 `encrypt_value` 中可以加前缀检测。
3. **加密密钥变更**：如果 `CONFIG_ENCRYPT_KEY` 变更，数据库中已加密的值无法解密。需要一个密钥轮换脚本（`scripts/rotate_encrypt_key.py`）。
