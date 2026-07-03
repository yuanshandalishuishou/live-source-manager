#!/usr/bin/env python3
"""
配置管理模块
============

Config 类 — 全 SQLite 版配置管理，INI 文件作为回退。
接口完全兼容旧版 Config。
"""

import configparser
import logging
import os
import time
from typing import Any

from app.exceptions import ConfigError


class Config:
    """配置管理类 — 全SQLite版

    接口完全兼容旧版 Config:
    - __init__(config_path, reload_interval) — 默认走 SQLite
    - get(section, key, default=None) — 读取配置
    - get_xxx() 便捷方法 — 返回格式与前版完全一致
    - create_default_config() — 保留（首次导入使用）
    """

    # 统一默认值 — 与 web/webapp.py 中的 SECTION_SCHEMA 保持权威一致。
    _DEFAULT_VALUES = {
        # [Sources]
        'Sources.local_dirs': './config/sources',
        'Sources.online_urls': (
            'https://live.zbds.org/tv/iptv4.m3u\n'
            'https://myernestlu.github.io/zby.txt\n'
            'https://raw.githubusercontent.com/Rivens7/Livelist/main/CCTV.m3u\n'
            'https://raw.githubusercontent.com/Rivens7/Livelist/main/CNTV.m3u\n'
            'https://raw.githubusercontent.com/Rivens7/Livelist/main/IPTV.m3u\n'
            'https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/result.m3u\n'
            'https://raw.githubusercontent.com/suxuang/myIPTV/refs/heads/main/ipv4.m3u\n'
            'https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8\n'
            'https://raw.githubusercontent.com/zwc456baby/iptv_alive/refs/heads/master/live.m3u\n'
            'https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u\n'
            'https://raw.githubusercontent.com/vamoschuck/TV/main/M3U\n'
            'https://raw.githubusercontent.com/BigBigGrandG/IPTV-URL/release/Gather.m3u\n'
            'https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u\n'
            'https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u\n'
            'https://raw.githubusercontent.com/huang770101/my-iptv/main/IPTV-ipv4.m3u\n'
            'https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u\n'
            'https://live.fanmingming.cn/tv/m3u/ipv6.m3u\n'
            'https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u\n'
            'https://iptv-org.github.io/iptv/countries/tw.m3u\n'
            'https://iptv-org.github.io/iptv/index.m3u'
        ),
        'Sources.github_sources': (
            'wcb1969/iptv/main\n'
            'joevess/IPTV/main\n'
            'suxuang/myIPTV/main\n'
            'YueChan/Live\n'
            'YanG-1989/m3u\n'
            'qwerttvv/Beijing-IPTV\n'
            'joevess/IPTV\n'
            'cymz6/AutoIPTV-Hotel\n'
            'Rivens7/Livelist'
        ),
        'Sources.github_source_settings': '{}',
        'Sources.source_file_ua_settings': '{}',
        'Sources.channel_ua_overrides': '{}',
        # [Network]
        'Network.proxy_enabled': 'False',
        'Network.proxy_type': 'socks5',
        'Network.proxy_host': '192.168.1.46',
        'Network.proxy_port': '1800',
        'Network.proxy_username': '',
        'Network.proxy_password': '',
        'Network.github_mirror': 'https://ghproxy.com/',
        'Network.ipv6_enabled': 'True',
        # [HTTPServer]
        'HTTPServer.enabled': 'True',
        'HTTPServer.host': '0.0.0.0',
        'HTTPServer.fileshare_port': '12345',
        'HTTPServer.manager_port': '23456',
        'HTTPServer.document_root': './www/output',
        # [GitHub]
        'GitHub.api_url': 'https://api.github.com',
        'GitHub.api_token': '',
        'GitHub.rate_limit': '5000',
        # [Testing]
        'Testing.timeout': '10',
        'Testing.concurrent_threads': '40',
        'Testing.cache_ttl': '120',
        'Testing.enable_speed_test': 'True',
        'Testing.speed_test_duration': '6',
        # [Output]
        'Output.filename': 'live.m3u',
        'Output.group_by': 'category',
        'Output.include_failed': 'False',
        'Output.max_sources_per_channel': '8',
        'Output.enable_filter': 'False',
        # [Logging]
        'Logging.level': 'INFO',
        'Logging.file': './log/app.log',
        'Logging.max_size': '10',
        'Logging.backup_count': '5',
        # [Filter]
        'Filter.max_latency': '4000',
        'Filter.min_bitrate': '80',
        'Filter.must_hd': 'False',
        'Filter.must_4k': 'False',
        'Filter.min_speed': '50',
        'Filter.min_resolution': '360p',
        'Filter.max_resolution': '4k',
        'Filter.resolution_filter_mode': 'range',
        # [UserAgents]
        'UserAgents.ua_position': 'extinf',
        'UserAgents.ua_enabled': 'False',
    }

    def __init__(self, config_path: str = None, reload_interval: int = 60):
        self.config_path = config_path or os.environ.get('CONFIG_PATH', '/config/config.ini')
        self.config = configparser.ConfigParser()
        self.reload_interval = reload_interval
        self._last_mtime = 0.0
        self._last_check_time = 0.0
        self._from_sqlite = True
        self._loaded = False

        try:
            from web import models as _m

            self._models = _m
        except ImportError:
            self._models = None
            self._from_sqlite = False

        self._ensure_ini_loaded()

    def _get_models(self):
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
        if os.path.exists(self.config_path):
            encodings = ['utf-8', 'gbk', 'gb2312', 'latin1', 'utf-8-sig']
            for encoding in encodings:
                try:
                    with open(self.config_path, encoding=encoding) as f:
                        content = f.read()
                        if content.startswith('\ufeff'):
                            content = content[1:]
                        self.config.read_string(content)
                    return
                except (UnicodeDecodeError, configparser.Error):
                    continue
            try:
                self.config.read(self.config_path)
            except configparser.Error as e:
                raise ConfigError(f'配置文件格式错误: {e}') from e
        else:
            self.create_default_config()

    def _get_config_dict(self) -> dict[str, dict[str, str]]:
        try:
            return self._get_models().get_all_config()
        except Exception as e:
            logging.warning(f'SQLite读取失败，回退到INI: {e}')
            return self._load_from_ini()

    def _load_from_ini(self) -> dict[str, dict[str, str]]:
        if os.path.exists(self.config_path):
            cp = configparser.ConfigParser()
            cp.read(self.config_path, encoding='utf-8')
            result = {}
            for section in cp.sections():
                result[section] = dict(cp.items(section))
            return result
        return {}

    def get(self, section: str, key: str, default: Any = None) -> str | None:
        if self._from_sqlite and self._models:
            try:
                val = self._get_models().get_app_config(f'{section}.{key}')
                if val is not None:
                    return val
            except Exception:
                logging.warning(f'Config.get 从SQLite读取失败({section}.{key})，回退到INI')
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

    def items(self, section: str) -> dict[str, str]:
        config_dict = self._get_config_dict()
        return config_dict.get(section, {})

    def sections(self) -> list[str]:
        config_dict = self._get_config_dict()
        return list(config_dict.keys())

    def set(self, section: str, key: str, value: str):
        try:
            self._get_models().set_app_config(f'{section}.{key}', value)
            self._sync_to_ini(section, key, value)
        except Exception as e:
            logging.warning(f'Config.set 写入SQLite失败: {e}')

    def _sync_to_ini(self, section: str, key: str, value: str):
        if not os.path.exists(self.config_path):
            return
        try:
            cp = configparser.ConfigParser()
            cp.read(self.config_path, encoding='utf-8')
            if section not in cp:
                cp[section] = {}
            cp[section][key] = value
            with open(self.config_path, 'w', encoding='utf-8') as f:
                cp.write(f)
        except Exception as _:
            logging.warning(f'INI备份写入失败(不阻塞主流程): {_}')

    def save(self):
        pass

    def check_reload(self) -> bool:
        now = time.time()
        if now - self._last_check_time < self.reload_interval:
            return False
        self._last_check_time = now
        if not os.path.exists(self.config_path):
            logging.warning(f'INI文件不存在 ({self.config_path})，check_reload 跳过')
            return False
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
        self._loaded = True

    def _default(self, section: str, key: str) -> str:
        return self._DEFAULT_VALUES.get(f'{section}.{key}', '')

    def _default_int(self, section: str, key: str) -> int:
        try:
            return int(self._default(section, key))
        except (ValueError, TypeError):
            return 0

    def _default_bool(self, section: str, key: str) -> bool:
        return str(self._default(section, key)).lower() in ('true', '1', 'yes', 'on')

    def get_logging_config(self) -> dict:
        return {
            'level': self.get('Logging', 'level', self._default('Logging', 'level')),
            'file': self.get('Logging', 'file', self._default('Logging', 'file')),
            'max_size': self.getint('Logging', 'max_size', self._default_int('Logging', 'max_size')),
            'backup_count': self.getint('Logging', 'backup_count', self._default_int('Logging', 'backup_count')),
            'enable_console': True,
        }

    def get_network_config(self) -> dict:
        return {
            'proxy_enabled': self.getboolean(
                'Network', 'proxy_enabled', self._default_bool('Network', 'proxy_enabled')
            ),
            'proxy_type': self.get('Network', 'proxy_type', self._default('Network', 'proxy_type')),
            'proxy_host': self.get('Network', 'proxy_host', self._default('Network', 'proxy_host')),
            'proxy_port': self.getint('Network', 'proxy_port', self._default_int('Network', 'proxy_port')),
            'proxy_username': self.get('Network', 'proxy_username', self._default('Network', 'proxy_username')),
            'proxy_password': self.get('Network', 'proxy_password', self._default('Network', 'proxy_password')),
            'github_mirror': self.get('Network', 'github_mirror', self._default('Network', 'github_mirror')),
            'ipv6_enabled': self.getboolean('Network', 'ipv6_enabled', self._default_bool('Network', 'ipv6_enabled')),
        }

    def get_github_config(self) -> dict:
        return {
            'api_url': self.get('GitHub', 'api_url', self._default('GitHub', 'api_url')),
            'api_token': self.get('GitHub', 'api_token', self._default('GitHub', 'api_token')),
            'rate_limit': self.getint('GitHub', 'rate_limit', self._default_int('GitHub', 'rate_limit')),
        }

    def get_testing_params(self) -> dict:
        return {
            'timeout': self.getint('Testing', 'timeout', self._default_int('Testing', 'timeout')),
            'concurrent_threads': self.getint(
                'Testing', 'concurrent_threads', self._default_int('Testing', 'concurrent_threads')
            ),
            'cache_ttl': self.getint('Testing', 'cache_ttl', self._default_int('Testing', 'cache_ttl')),
            'enable_speed_test': self.getboolean(
                'Testing', 'enable_speed_test', self._default_bool('Testing', 'enable_speed_test')
            ),
            'speed_test_duration': self.getint(
                'Testing', 'speed_test_duration', self._default_int('Testing', 'speed_test_duration')
            ),
            'max_workers': 50,
        }

    def get_filter_params(self) -> dict:
        return {
            'max_latency': self.getint('Filter', 'max_latency', self._default_int('Filter', 'max_latency')),
            'min_bitrate': self.getint('Filter', 'min_bitrate', self._default_int('Filter', 'min_bitrate')),
            'must_hd': self.getboolean('Filter', 'must_hd', self._default_bool('Filter', 'must_hd')),
            'must_4k': self.getboolean('Filter', 'must_4k', self._default_bool('Filter', 'must_4k')),
            'min_speed': self.getint('Filter', 'min_speed', self._default_int('Filter', 'min_speed')),
            'min_resolution': self.get('Filter', 'min_resolution', self._default('Filter', 'min_resolution')),
            'max_resolution': self.get('Filter', 'max_resolution', self._default('Filter', 'max_resolution')),
            'resolution_filter_mode': self.get(
                'Filter', 'resolution_filter_mode', self._default('Filter', 'resolution_filter_mode')
            ),
        }

    def get_output_params(self) -> dict:
        output_dir = self.get('Output', 'output_dir', './www/output')
        if output_dir and not os.path.isabs(output_dir):
            output_dir = os.path.abspath(output_dir)
        return {
            'filename': self.get('Output', 'filename', self._default('Output', 'filename')),
            'group_by': self.get('Output', 'group_by', self._default('Output', 'group_by')),
            'include_failed': self.getboolean(
                'Output', 'include_failed', self._default_bool('Output', 'include_failed')
            ),
            'max_sources_per_channel': self.getint(
                'Output', 'max_sources_per_channel', self._default_int('Output', 'max_sources_per_channel')
            ),
            'enable_filter': self.getboolean('Output', 'enable_filter', self._default_bool('Output', 'enable_filter')),
            'output_dir': output_dir,
        }

    def get_http_server_config(self) -> dict:
        config = {
            'enabled': self.getboolean('HTTPServer', 'enabled', self._default_bool('HTTPServer', 'enabled')),
            'host': self.get('HTTPServer', 'host', self._default('HTTPServer', 'host')),
            'fileshare_port': self.getint(
                'HTTPServer', 'fileshare_port', self._default_int('HTTPServer', 'fileshare_port')
            ),
            'manager_port': self.getint('HTTPServer', 'manager_port', self._default_int('HTTPServer', 'manager_port')),
            'document_root': self.get('HTTPServer', 'document_root', self._default('HTTPServer', 'document_root')),
        }
        if config['document_root'] and not os.path.isabs(config['document_root']):
            config['document_root'] = os.path.abspath(config['document_root'])
        return config

    def get_ua_position(self) -> str:
        return self.get('UserAgents', 'ua_position', self._default('UserAgents', 'ua_position'))

    def is_ua_enabled(self) -> bool:
        return self.getboolean('UserAgents', 'ua_enabled', self._default_bool('UserAgents', 'ua_enabled'))

    def get_user_agents(self) -> dict:
        ua_config = {}
        if self._from_sqlite and self._models:
            try:
                all_cfg = self._get_models().get_all_config()
                section_data = all_cfg.get('UserAgents', {})
                for key, value in section_data.items():
                    if key not in ('ua_position', 'ua_enabled'):
                        ua_config[key] = str(value)
                return ua_config
            except Exception:
                logging.warning('get_user_agents 从SQLite读取失败，回退到INI')
        if self.config.has_section('UserAgents'):
            for key, value in self.config.items('UserAgents'):
                if key not in ['ua_position', 'ua_enabled']:
                    ua_config[key] = value
        return ua_config

    def get_source_file_ua_settings(self) -> dict:
        import json as _json

        raw = self.get('Sources', 'source_file_ua_settings', '{}')
        try:
            settings = _json.loads(raw) if raw else {}
            return settings if isinstance(settings, dict) else {}
        except Exception:
            return {}

    def get_channel_ua_overrides(self) -> dict:
        import json as _json

        raw = self.get('Sources', 'channel_ua_overrides', '{}')
        try:
            overrides = _json.loads(raw) if raw else {}
            return overrides if isinstance(overrides, dict) else {}
        except Exception:
            return {}

    def get_sources(self) -> dict:
        local_dirs_raw = self.get('Sources', 'local_dirs', './config/sources')
        if isinstance(local_dirs_raw, str):
            local_dirs = [d.strip() for d in local_dirs_raw.split(',')]
        else:
            local_dirs = local_dirs_raw

        online_urls_raw = self.get('Sources', 'online_urls', '')
        if online_urls_raw:
            online_urls = [url.strip() for url in online_urls_raw.split('\n') if url.strip()]
        else:
            online_urls = []

        github_sources_raw = self.get('Sources', 'github_sources', '')
        if github_sources_raw:
            github_sources = [s.strip() for s in github_sources_raw.split('\n') if s.strip()]
        else:
            github_sources = []

        return {'local_dirs': local_dirs, 'online_urls': online_urls, 'github_sources': github_sources}

    def create_default_config(self):
        self.config['Sources'] = {
            'local_dirs': self._DEFAULT_VALUES.get('Sources.local_dirs', '/config/sources'),
            'online_urls': self._DEFAULT_VALUES.get('Sources.online_urls', ''),
        }
        self.config['Network'] = {
            'proxy_enabled': self._DEFAULT_VALUES.get('Network.proxy_enabled', 'False'),
            'proxy_type': self._DEFAULT_VALUES.get('Network.proxy_type', 'socks5'),
            'proxy_host': self._DEFAULT_VALUES.get('Network.proxy_host', ''),
            'proxy_port': self._DEFAULT_VALUES.get('Network.proxy_port', '1800'),
            'proxy_username': self._DEFAULT_VALUES.get('Network.proxy_username', ''),
            'proxy_password': self._DEFAULT_VALUES.get('Network.proxy_password', ''),
            'ipv6_enabled': self._DEFAULT_VALUES.get('Network.ipv6_enabled', 'False'),
        }
        self.config['HTTPServer'] = {
            'enabled': self._DEFAULT_VALUES.get('HTTPServer.enabled', 'False'),
            'host': self._DEFAULT_VALUES.get('HTTPServer.host', '0.0.0.0'),
            'fileshare_port': self._DEFAULT_VALUES.get('HTTPServer.fileshare_port', '12345'),
            'manager_port': self._DEFAULT_VALUES.get('HTTPServer.manager_port', '23456'),
            'document_root': self._DEFAULT_VALUES.get('HTTPServer.document_root', './www/output'),
        }
        self.config['GitHub'] = {
            'api_url': self._DEFAULT_VALUES.get('GitHub.api_url', 'https://api.github.com'),
            'api_token': self._DEFAULT_VALUES.get('GitHub.api_token', ''),
            'rate_limit': self._DEFAULT_VALUES.get('GitHub.rate_limit', '5000'),
        }
        self.config['Testing'] = {
            'timeout': self._DEFAULT_VALUES.get('Testing.timeout', '10'),
            'concurrent_threads': self._DEFAULT_VALUES.get('Testing.concurrent_threads', '40'),
            'cache_ttl': self._DEFAULT_VALUES.get('Testing.cache_ttl', '120'),
            'enable_speed_test': self._DEFAULT_VALUES.get('Testing.enable_speed_test', 'True'),
            'speed_test_duration': self._DEFAULT_VALUES.get('Testing.speed_test_duration', '6'),
        }
        self.config['Output'] = {
            'filename': self._DEFAULT_VALUES.get('Output.filename', 'live.m3u'),
            'group_by': self._DEFAULT_VALUES.get('Output.group_by', 'category'),
            'include_failed': self._DEFAULT_VALUES.get('Output.include_failed', 'False'),
            'max_sources_per_channel': self._DEFAULT_VALUES.get('Output.max_sources_per_channel', '8'),
            'enable_filter': self._DEFAULT_VALUES.get('Output.enable_filter', 'False'),
        }
        self.config['Logging'] = {
            'level': self._DEFAULT_VALUES.get('Logging.level', 'INFO'),
            'file': self._DEFAULT_VALUES.get('Logging.file', '/log/app.log'),
            'max_size': self._DEFAULT_VALUES.get('Logging.max_size', '10'),
            'backup_count': self._DEFAULT_VALUES.get('Logging.backup_count', '5'),
        }
        self.config['Filter'] = {
            'max_latency': self._DEFAULT_VALUES.get('Filter.max_latency', '4000'),
            'min_bitrate': self._DEFAULT_VALUES.get('Filter.min_bitrate', '80'),
            'must_hd': self._DEFAULT_VALUES.get('Filter.must_hd', 'False'),
            'must_4k': self._DEFAULT_VALUES.get('Filter.must_4k', 'False'),
            'min_speed': self._DEFAULT_VALUES.get('Filter.min_speed', '50'),
            'min_resolution': self._DEFAULT_VALUES.get('Filter.min_resolution', '360p'),
            'max_resolution': self._DEFAULT_VALUES.get('Filter.max_resolution', '4k'),
            'resolution_filter_mode': self._DEFAULT_VALUES.get('Filter.resolution_filter_mode', 'range'),
        }
        self.config['UserAgents'] = {
            'ua_position': self._DEFAULT_VALUES.get('UserAgents.ua_position', 'extinf'),
            'ua_enabled': self._DEFAULT_VALUES.get('UserAgents.ua_enabled', 'True'),
        }

        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)

    @staticmethod
    def create_default_at(config_path: str):
        config = Config.__new__(Config)
        config.config_path = config_path
        config.config = configparser.ConfigParser()
        config.create_default_config()
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            config.config.write(f)
