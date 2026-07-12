#!/usr/bin/env python3
"""
配置管理模块
============

Config 类 — 纯 SQLite 版配置管理。
所有配置读写直接走 SQLite app_config 表，无 INI 文件依赖。
"""

import logging
import os
from typing import Any, ClassVar

from app.exceptions import ConfigError


class Config:
    """配置管理类 — 纯 SQLite 版

    所有配置读写直接走 SQLite app_config 表，无 INI 文件依赖。
    对外接口:
    - __init__() — 初始化（无需路径参数）
    - get(section, key, default=None) — 从 SQLite 读取配置
    - set(section, key, value) — 写入 SQLite
    - get_xxx() 便捷方法 — 覆盖常用配置段
    """

    # 统一默认值 — 与 web/core.py 中的 SECTION_SCHEMA 保持权威一致。
    # 同时也是 seed_app_config_defaults() 的种子来源。
    _DEFAULT_VALUES: ClassVar[dict[str, str]] = {
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
        'Testing.max_concurrent_ffprobe': '16',
        'Testing.cache_ttl': '120',
        'Testing.enable_speed_test': 'True',
        'Testing.speed_test_duration': '6',
        'Testing.auto_scan_enabled': 'False',
        'Testing.auto_scan_mode': 'interval',
        'Testing.auto_scan_interval_hours': '24',
        'Testing.auto_scan_daily_time': '03:00',
        # 性能优化（对标 Guovin/iptv-api P0）
        'Testing.enable_host_speed_share': 'True',  # 同 Host 测速复用：同 CDN 只 ffprobe 一次
        'Testing.enable_source_freeze': 'True',  # 失败源指数退避冻结：死源拉黑冷却省资源
        'Testing.freeze_fail_threshold': '3',  # 连续失败几次后开始冻结
        'Testing.freeze_base_seconds': '60',  # 退避基数：2^n × base 秒
        'Testing.freeze_max_hours': '24',  # 冻结上限小时
        # 质量与过滤增强（对标 Guovin/iptv-api P1/P2）
        'Testing.enable_ad_detect': 'True',  # 广告/循环占位源检测：拉 playlist 查关键字+循环标志
        'Testing.ad_keywords': 'no_signal,/ad/,advertisement,测试卡,无信号,test_pattern,colorbar,broadcast_test,signal_lost',
        'Testing.ad_max_duration': '90',  # 含 #EXT-X-ENDLIST 且累计时长<=该值(秒)判为循环占位
        'Testing.global_blacklist': '',  # 全局黑名单：URL/host，逗号或换行分隔，命中则跳过测试
        'Testing.global_whitelist': '',  # 全局白名单：URL/host，逗号或换行分隔（豁免黑名单）
        'Testing.output_sort_by': 'speed',  # 输出排序方式：speed(默认,快源在前)/name/resolution
        'Testing.max_test_attempts': '1',  # 实时测试次数(总): 1=每个地址测一次; 2=测两次(含1次重试); 默认1
        # [Output]
        'Output.filename': 'live.m3u',
        'Output.group_by': 'category',
        'Output.include_failed': 'False',
        'Output.max_sources_per_channel': '8',
        'Output.enable_filter': 'False',
        'Output.whitelist_force_keep': 'False',  # 白名单源即使未通过质量过滤也强制保留到输出
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

    def __init__(self):
        """初始化 Config（纯 SQLite 版，配置读写直接走 SQLite app_config 表）。"""
        self._models = None
        self._loaded = False

    def _get_models(self):
        """懒加载 web.models 模块"""
        if self._models is not None:
            return self._models
        try:
            from web import models as _m

            self._models = _m
            return self._models
        except ImportError:
            raise ConfigError('无法加载 web.models 模块，SQLite 配置不可用')

    def _get_config_dict(self) -> dict[str, dict[str, str]]:
        """从 SQLite 读取全量配置"""
        return self._get_models().get_all_config()

    def get(self, section: str, key: str, default: Any = None) -> str | None:
        """从 SQLite 读取单个配置值"""
        try:
            val = self._get_models().get_app_config(f'{section}.{key}')
            if val is not None:
                return val
        except Exception as e:
            logging.warning(f'Config.get SQLite 读取失败 ({section}.{key}): {e}')
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
        """写入配置到 SQLite"""
        try:
            self._get_models().set_app_config(f'{section}.{key}', value)
        except Exception as e:
            logging.warning(f'Config.set SQLite 写入失败: {e}')

    def save(self):
        """保留兼容（原用于 INI 保存），SQLite 模式无操作"""
        pass

    def check_reload(self) -> bool:
        """保留兼容（原用于 INI mtime 检查），SQLite 模式始终返回 False"""
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
            # 性能优化（对标 Guovin/iptv-api P0）
            'enable_host_speed_share': self.getboolean(
                'Testing', 'enable_host_speed_share', self._default_bool('Testing', 'enable_host_speed_share')
            ),
            'enable_source_freeze': self.getboolean(
                'Testing', 'enable_source_freeze', self._default_bool('Testing', 'enable_source_freeze')
            ),
            'freeze_fail_threshold': self.getint(
                'Testing', 'freeze_fail_threshold', self._default_int('Testing', 'freeze_fail_threshold')
            ),
            'freeze_base_seconds': self.getint(
                'Testing', 'freeze_base_seconds', self._default_int('Testing', 'freeze_base_seconds')
            ),
            'freeze_max_hours': self.getint(
                'Testing', 'freeze_max_hours', self._default_int('Testing', 'freeze_max_hours')
            ),
            # 质量与过滤增强（对标 Guovin/iptv-api P1/P2）
            'enable_ad_detect': self.getboolean(
                'Testing', 'enable_ad_detect', self._default_bool('Testing', 'enable_ad_detect')
            ),
            'ad_keywords': self.get('Testing', 'ad_keywords', self._default('Testing', 'ad_keywords')),
            'ad_max_duration': self.getint(
                'Testing', 'ad_max_duration', self._default_int('Testing', 'ad_max_duration')
            ),
            'global_blacklist': self.get('Testing', 'global_blacklist', self._default('Testing', 'global_blacklist')),
            'global_whitelist': self.get('Testing', 'global_whitelist', self._default('Testing', 'global_whitelist')),
            'output_sort_by': self.get('Testing', 'output_sort_by', self._default('Testing', 'output_sort_by')),
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
            'whitelist_force_keep': self.getboolean(
                'Output', 'whitelist_force_keep', self._default_bool('Output', 'whitelist_force_keep')
            ),
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
        """从 SQLite 读取 UserAgents 段的所有 UA 配置"""
        ua_config: dict[str, str] = {}
        try:
            all_cfg = self._get_models().get_all_config()
            section_data = all_cfg.get('UserAgents', {})
            for key, value in section_data.items():
                if key not in ('ua_position', 'ua_enabled'):
                    ua_config[key] = str(value)
        except Exception:
            logging.warning('get_user_agents 从 SQLite 读取失败')
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
