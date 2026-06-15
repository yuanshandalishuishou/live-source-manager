#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块 — 全SQLite版（v2）
所有运行时配置从 SQLite app_config 表读取，config.ini 仅作为首次导入源。

接口完全兼容旧版 Config:
  - Config(config_path) 实例化
  - .get(section, key, default=None) 读取
  - .get_xxx() 便捷方法 — 返回格式与前版完全一致
  - .create_default_config() 保留（首次导入使用）
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
    - __init__(config_path, reload_interval) — 默认走 SQLite
    - get(section, key, default=None) — 读取配置
    - get_xxx() 便捷方法 — 返回格式与前版完全一致
    - create_default_config() — 保留（首次导入使用）
    """

    def __init__(self, config_path: str = "/config/config.ini", reload_interval: int = 60):
        """
        参数向后兼容：
        - config_path: 仅首次导入时使用，运行时读取走 SQLite
        - reload_interval: 保留但 SQLite 模式无需重载
        """
        self.config_path = config_path
        self.config = configparser.ConfigParser()  # 仅用于 create_default_config / INI回退
        self.reload_interval = reload_interval
        self._last_mtime = 0.0
        self._last_check_time = 0.0
        self._from_sqlite = True  # 默认走 SQLite
        self._loaded = False

        # 尝试导入 web.models（可能失败，例如在测试环境中）
        try:
            from web import models as _m
            self._models = _m
        except ImportError:
            self._models = None
            self._from_sqlite = False

        # 加载 INI 文件内容供回退使用
        self._ensure_ini_loaded()

    def _get_models(self):
        """延迟获取 models 引用（支持测试重导）"""
        if self._models is not None:
            return self._models
        try:
            from web import models as _m
            self._models = _m
            self._from_sqlite = True
            return self._models
        except ImportError:
            self._from_sqlite = False
            raise

    def _ensure_ini_loaded(self):
        """加载 INI 文件内容供回退使用"""
        if os.path.exists(self.config_path):
            encodings = ['utf-8', 'gbk', 'gb2312', 'latin1', 'utf-8-sig']
            for encoding in encodings:
                try:
                    with open(self.config_path, 'r', encoding=encoding) as f:
                        content = f.read()
                        if content.startswith('\ufeff'):
                            content = content[1:]
                        self.config.read_string(content)
                    return
                except (UnicodeDecodeError, configparser.Error):
                    continue
            # 所有编码均失败，兜底读取
            try:
                self.config.read(self.config_path)
            except configparser.Error as e:
                raise ConfigError(f"配置文件格式错误: {e}") from e
        else:
            # 文件不存在时创建默认 INI（供首次导入或回退使用）
            self.create_default_config()

    def _get_config_dict(self) -> Dict[str, Dict[str, str]]:
        """从 SQLite 获取全量配置，格式为 {section: {key: value}}"""
        try:
            return self._get_models().get_all_config()
        except Exception as e:
            logging.warning(f"SQLite读取失败，回退到INI: {e}")
            return self._load_from_ini()

    def _load_from_ini(self) -> Dict[str, Dict[str, str]]:
        """从 INI 文件读取（回退路径）"""
        if os.path.exists(self.config_path):
            cp = configparser.ConfigParser()
            cp.read(self.config_path, encoding='utf-8')
            result = {}
            for section in cp.sections():
                result[section] = dict(cp.items(section))
            return result
        return {}

    # ── 核心 get/set 接口（兼容旧版） ──────────────

    def get(self, section: str, key: str, default: Any = None) -> Optional[str]:
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

    def getint(self, section: str, key: str, default: int = 0) -> int:
        val = self.get(section, key, str(default))
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def getboolean(self, section: str, key: str, default: bool = False) -> bool:
        val = self.get(section, key, 'True' if default else 'False')
        return str(val).lower() in ('true', '1', 'yes', 'on')

    def getfloat(self, section: str, key: str, default: float = 0.0) -> float:
        val = self.get(section, key, str(default))
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def items(self, section: str) -> Dict[str, str]:
        """获取某个 section 的所有配置项"""
        config_dict = self._get_config_dict()
        return config_dict.get(section, {})

    def sections(self) -> List[str]:
        """获取所有 section 名称"""
        config_dict = self._get_config_dict()
        return list(config_dict.keys())

    def set(self, section: str, key: str, value: str):
        """写入配置到 SQLite"""
        try:
            self._get_models().set_app_config(f"{section}.{key}", value)
        except Exception as e:
            logging.warning(f"Config.set 写入SQLite失败: {e}")

    def save(self):
        """兼容接口（SQLite 模式无需保存到文件）"""
        pass

    def check_reload(self) -> bool:
        """检查配置文件是否变更（兼容旧版接口）

        检测 INI 文件 mtime 变化，如有更新则重新加载 INI 配置。
        SQLite 模式也保留此能力，以便 INI 回退数据保持最新。
        """
        now = time.time()
        if now - self._last_check_time < self.reload_interval:
            return False
        self._last_check_time = now
        try:
            current_mtime = os.path.getmtime(self.config_path)
            if current_mtime != self._last_mtime:
                self._last_mtime = current_mtime
                self._ensure_ini_loaded()
                return True
        except OSError:
            pass
        return False

    def load_config(self):
        """兼容旧版接口 — SQLite 模式无需加载"""
        self._loaded = True

    # ── 便捷获取方法（保持返回值格式完全一致） ──────

    def get_logging_config(self) -> Dict:
        """获取日志配置"""
        return {
            'level': self.get('Logging', 'level', 'INFO'),
            'file': self.get('Logging', 'file', '/log/app.log'),
            'max_size': self.getint('Logging', 'max_size', 10),
            'backup_count': self.getint('Logging', 'backup_count', 5),
            'enable_console': True,
        }

    def get_network_config(self) -> Dict:
        return {
            'proxy_enabled': self.getboolean('Network', 'proxy_enabled', False),
            'proxy_type': self.get('Network', 'proxy_type', 'socks5'),
            'proxy_host': self.get('Network', 'proxy_host', '192.168.1.211'),
            'proxy_port': self.getint('Network', 'proxy_port', 1800),
            'proxy_username': self.get('Network', 'proxy_username', ''),
            'proxy_password': self.get('Network', 'proxy_password', ''),
            'ipv6_enabled': self.getboolean('Network', 'ipv6_enabled', False),
        }

    def get_github_config(self) -> Dict:
        return {
            'api_url': self.get('GitHub', 'api_url', 'https://api.github.com'),
            'api_token': self.get('GitHub', 'api_token', ''),
            'rate_limit': self.getint('GitHub', 'rate_limit', 5000),
        }

    def get_testing_params(self) -> Dict:
        return {
            'timeout': self.getint('Testing', 'timeout', 10),
            'concurrent_threads': self.getint('Testing', 'concurrent_threads', 30),
            'cache_ttl': self.getint('Testing', 'cache_ttl', 120),
            'enable_speed_test': self.getboolean('Testing', 'enable_speed_test', True),
            'speed_test_duration': self.getint('Testing', 'speed_test_duration', 6),
            'max_workers': 50,  # 固定值，与旧版一致
        }

    def get_filter_params(self) -> Dict:
        return {
            'max_latency': self.getint('Filter', 'max_latency', 5000),
            'min_bitrate': self.getint('Filter', 'min_bitrate', 100),
            'must_hd': self.getboolean('Filter', 'must_hd', False),
            'must_4k': self.getboolean('Filter', 'must_4k', False),
            'min_speed': self.getint('Filter', 'min_speed', 40),
            'min_resolution': self.get('Filter', 'min_resolution', '720p'),
            'max_resolution': self.get('Filter', 'max_resolution', '4k'),
            'resolution_filter_mode': self.get('Filter', 'resolution_filter_mode', 'range'),
        }

    def get_output_params(self) -> Dict:
        return {
            'filename': self.get('Output', 'filename', 'live.m3u'),
            'group_by': self.get('Output', 'group_by', 'category'),
            'include_failed': self.getboolean('Output', 'include_failed', False),
            'max_sources_per_channel': self.getint('Output', 'max_sources_per_channel', 3),
            'enable_filter': self.getboolean('Output', 'enable_filter', False),
            'output_dir': '/www/output',
        }

    def get_http_server_config(self) -> Dict:
        return {
            'enabled': self.getboolean('HTTPServer', 'enabled', False),
            'host': self.get('HTTPServer', 'host', '0.0.0.0'),
            'port': self.getint('HTTPServer', 'port', 12345),
            'document_root': self.get('HTTPServer', 'document_root', '/www/output'),
        }

    def get_ua_position(self) -> str:
        return self.get('UserAgents', 'ua_position', 'extinf')

    def is_ua_enabled(self) -> bool:
        return self.getboolean('UserAgents', 'ua_enabled', True)

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

    # ── INI 相关（首次导入 / 测试使用） ──────────

    def create_default_config(self):
        """创建默认 INI 配置（用于首次导入）"""
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


class Logger:
    """日志管理类 — 增强错误处理（与原版完全一致）"""

    def __init__(self, config: Dict):
        self.logger = self.setup_logging(config)

    def setup_logging(self, config: Dict):
        """配置日志系统 - 增强错误处理"""
        logger = logging.getLogger('LiveSourceManager')

        # 设置日志级别
        log_level = getattr(logging, config.get('level', 'INFO').upper(), logging.INFO)
        logger.setLevel(log_level)

        # 清除现有处理器
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        # 创建格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 文件处理器（如果配置了文件路径）
        log_file = config.get('file', '/log/app.log')
        file_handler = None

        if log_file:
            try:
                log_dir = os.path.dirname(log_file)
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)

                if config.get('clear_on_startup', False) and os.path.exists(log_file):
                    try:
                        os.remove(log_file)
                    except Exception as e:
                        print(f"无法清空日志文件: {e}")

                max_size = config.get('max_size', 10) * 1024 * 1024
                backup_count = config.get('backup_count', 5)

                file_handler = logging.handlers.RotatingFileHandler(
                    log_file,
                    maxBytes=max_size,
                    backupCount=backup_count,
                    encoding='utf-8'
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            except Exception as e:
                print(f"创建文件日志处理器失败: {e}")

        # 控制台处理器（如果启用）
        if config.get('enable_console', True):
            try:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setFormatter(formatter)
                logger.addHandler(console_handler)
            except Exception as e:
                print(f"创建控制台日志处理器失败: {e}")

        # 如果没有任何处理器，添加一个基本的控制台处理器
        if not logger.handlers:
            print("警告: 无日志处理器，创建基本控制台处理器")
            basic_handler = logging.StreamHandler(sys.stdout)
            basic_handler.setFormatter(formatter)
            logger.addHandler(basic_handler)

        try:
            logger.info("日志系统初始化完成")
        except Exception:
            pass

        return logger
