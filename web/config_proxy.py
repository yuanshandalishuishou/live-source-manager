#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.ini 安全读写代理
只读接口 + 携带 schema 校验、备份、原子写入
"""
import os
import configparser
import shutil
import fcntl
import tempfile
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger('web.config_proxy')

# 配置文件路径（相对于项目根）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get('CONFIG_PATH', os.path.join(PROJECT_ROOT, 'config', 'config.ini'))

# 字段定义：name -> (type, default, label, help)
# type: str, int, bool
SECTION_SCHEMA: Dict[str, Dict[str, tuple]] = {
    'Sources': {
        'local_dirs': ('str', '/config/sources', '本地源目录', '逗号分隔'),
        'online_urls': ('textarea', '', '在线源URL列表', '每行一个URL'),
        'github_sources': ('textarea', '', 'GitHub仓库', '格式: owner/repo'),
    },
    'Network': {
        'proxy_enabled': ('bool', 'False', '启用代理', 'True/False'),
        'proxy_type': ('str', 'socks5', '代理类型', 'http/https/socks5'),
        'proxy_host': ('str', '', '代理主机'),
        'proxy_port': ('int', '1800', '代理端口'),
        'proxy_username': ('str', '', '代理用户名'),
        'proxy_password': ('str', '', '代理密码'),
        'ipv6_enabled': ('bool', 'False', '启用IPv6', ''),
    },
    'HTTPServer': {
        'enabled': ('bool', 'False', '启用HTTP'),
        'host': ('str', '0.0.0.0', '监听地址'),
        'port': ('int', '12345', '监听端口'),
        'document_root': ('str', '/www/output', '文档根目录'),
    },
    'GitHub': {
        'api_url': ('str', 'https://api.github.com', 'API地址'),
        'api_token': ('str', '', 'API Token'),
        'rate_limit': ('int', '5000', '速率限制'),
    },
    'Testing': {
        'timeout': ('int', '10', '测试超时(秒)'),
        'concurrent_threads': ('int', '40', '并发线程数'),
        'cache_ttl': ('int', '120', '缓存有效期(分)'),
        'enable_speed_test': ('bool', 'True', '启用速率测试'),
        'speed_test_duration': ('int', '6', '速率测试时长(秒)'),
    },
    'Output': {
        'filename': ('str', 'live.m3u', '输出文件名'),
        'group_by': ('str', 'category', '分组策略'),
        'include_failed': ('bool', 'False', '包含失败源'),
        'max_sources_per_channel': ('int', '8', '每频道最大源数'),
        'enable_filter': ('bool', 'False', '启用过滤'),
    },
    'Logging': {
        'level': ('str', 'INFO', '日志级别'),
        'file': ('str', '/log/app.log', '日志文件路径'),
        'max_size': ('int', '10', '最大日志大小(MB)'),
        'backup_count': ('int', '5', '备份文件数'),
    },
    'Filter': {
        'max_latency': ('int', '4000', '最大延迟(ms)'),
        'min_bitrate': ('int', '80', '最小比特率(kbps)'),
        'must_hd': ('bool', 'False', '必须高清'),
        'must_4k': ('bool', 'False', '必须4K'),
        'min_speed': ('int', '50', '最小下载速度(KB/s)'),
        'min_resolution': ('str', '360p', '最低分辨率'),
        'max_resolution': ('str', '4k', '最高分辨率'),
        'resolution_filter_mode': ('str', 'range', '分辨率筛选模式'),
    },
    'UserAgents': {
        'ua_position': ('str', 'extinf', 'UA位置'),
        'ua_enabled': ('bool', 'True', '启用UA'),
    },
}

# 简单的字段类型信息用于前端渲染
FIELD_TYPE = {'str': 'text', 'textarea': 'textarea', 'int': 'number', 'bool': 'checkbox'}


def _read_raw() -> configparser.ConfigParser:
    """读取 config.ini，返回 ConfigParser 对象"""
    cp = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        cp.read(CONFIG_PATH, encoding='utf-8')
    return cp


def read_config() -> Dict[str, Dict[str, str]]:
    """读取全量配置，返回 {section: {key: value}}"""
    cp = _read_raw()
    result = {}
    for section in cp.sections():
        result[section] = dict(cp.items(section))
    return result


def read_section(section: str) -> Dict[str, str]:
    """读取指定段配置"""
    cp = _read_raw()
    if section in cp:
        return dict(cp.items(section))
    return {}


def get_field_meta() -> Dict:
    """返回字段元信息（type/label/help），供前端表单渲染"""
    return SECTION_SCHEMA


def validate_and_coerce(section: str, key: str, value: str, field_def: tuple) -> Tuple[Any, str]:
    """校验并转换单个字段的值，返回 (转换后的值, 错误消息)"""
    ftype, default, label, *_ = field_def
    if ftype == 'int':
        try:
            return int(value), ''
        except (ValueError, TypeError):
            return default, f"{label} 必须是整数"
    if ftype == 'bool':
        return ('True' if value and str(value).lower() in ('true', '1', 'yes', 'on') else 'False'), ''
    if ftype == 'textarea':
        # 保持原样，允许多行
        return str(value), ''
    # str
    return str(value), ''


def write_config(data: Dict[str, Dict[str, str]]) -> Tuple[bool, str]:
    """
    写入 config.ini
    data: {section: {key: value}}
    返回 (success, message)
    """
    # 1. 读取当前配置
    cp = _read_raw()

    # 2. 合并并校验
    for section, fields in data.items():
        if section not in cp and not cp.has_section(section):
            cp.add_section(section)
        for key, value in fields.items():
            # 校验
            schema = SECTION_SCHEMA.get(section, {})
            if key in schema:
                _, err = validate_and_coerce(section, key, value, schema[key])
                if err:
                    return False, f"[{section}] {key}: {err}"
            cp.set(section, key, str(value))

    # 3. 备份 + 原子写入
    config_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(config_dir, exist_ok=True)

    try:
        bak_path = CONFIG_PATH + '.bak'
        if os.path.exists(CONFIG_PATH):
            shutil.copy2(CONFIG_PATH, bak_path)

        # 使用临时文件 + rename 实现原子写入
        fd, tmp_path = tempfile.mkstemp(dir=config_dir, prefix='config_', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as tmpf:
                # 加锁
                fcntl.flock(fd, fcntl.LOCK_EX)
                cp.write(tmpf)
                tmpf.flush()
                os.fsync(fd)
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.rename(tmp_path, CONFIG_PATH)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        # 4. 回读验证
        verify_cp = configparser.ConfigParser()
        verify_cp.read(CONFIG_PATH, encoding='utf-8')
        for section in data:
            if section not in verify_cp:
                # 回滚
                if os.path.exists(bak_path):
                    shutil.copy2(bak_path, CONFIG_PATH)
                return False, f"写入验证失败: 缺少段落 [{section}]"

        return True, f"配置已保存（备份: {bak_path}）"

    except PermissionError as e:
        return False, f"权限不足: {e}"
    except Exception as e:
        return False, f"写入失败: {e}"
