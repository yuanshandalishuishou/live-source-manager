#!/usr/bin/env python3
"""
URL 安全审查模块
================

包含:
  - SourceData TypedDict — 直播源数据结构定义
  - URL 格式校验与安全检查 (validate_url / sanitize_url / is_safe_url)
  - 域名黑名单管理
  - XSS / 命令注入 / 路径遍历检测
"""

import ipaddress
import re
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class SourceData(TypedDict, total=False):
    """直播源数据结构定义"""

    name: str
    url: str
    url_original: str
    logo: str
    user_agent: str
    ua_position: str
    group: str
    status: str
    response_time: float
    download_speed: float
    resolution: str
    bitrate: int
    fps: float
    media_type: str
    category: str
    province: str
    country: str
    is_qualified: bool


# ============================================================
# 默认配置
# ============================================================

ALLOWED_SCHEMES = frozenset(
    {
        'http',
        'https',
    }
)

BLOCKED_SCHEMES = frozenset(
    {
        'file',
        'data',
        'javascript',
        'vbscript',
        'jar',
        'ftp',
        'chrome',
        'chrome-extension',
        'edge',
        'safari-extension',
        'view-source',
        'about',
    }
)

DEFAULT_DOMAIN_BLACKLIST = frozenset(
    {
        'localhost',
        '127.0.0.1',
        '0.0.0.0',
        '255.255.255.255',
        'internal.example.com',
    }
)

PRIVATE_IP_PREFIXES = [
    '10.',
    '172.16.',
    '172.17.',
    '172.18.',
    '172.19.',
    '172.20.',
    '172.21.',
    '172.22.',
    '172.23.',
    '172.24.',
    '172.25.',
    '172.26.',
    '172.27.',
    '172.28.',
    '172.29.',
    '172.30.',
    '172.31.',
    '192.168.',
    '127.',
    '169.254.',
]

XSS_PATTERNS = [
    re.compile(r'<script[^>]*>', re.I),
    re.compile(r'</script>', re.I),
    re.compile(r'javascript\s*:', re.I),
    re.compile(r'on\w+\s*=', re.I),
    re.compile(r'<iframe[^>]*>', re.I),
    re.compile(r'<embed[^>]*>', re.I),
    re.compile(r'<object[^>]*>', re.I),
    re.compile(r'alert\s*\(', re.I),
    re.compile(r'eval\s*\(', re.I),
    re.compile(r'document\.cookie', re.I),
    re.compile(r'window\.location', re.I),
    re.compile(r'expression\s*\(', re.I),
    re.compile(r'vbscript\s*:', re.I),
    re.compile(r'data\s*:', re.I),
]

CMD_INJECTION_PATTERNS = [
    re.compile(r'[;&|`$]'),
    re.compile(r'\$\{.*?\}'),
    re.compile(r'`[^`]*`'),
    re.compile(r'\$\(.*?\)'),
    re.compile(r'\|\s*[a-z]+'),
    re.compile(r';\s*[a-z]+'),
]

PATH_TRAVERSAL_PATTERNS = [
    re.compile(r'\.\./\.\.', re.I),
    re.compile(r'\.\.\\\.\.'),
    re.compile(r'%2e%2e%2f', re.I),
    re.compile(r'%2e%2e/', re.I),
]


# ============================================================
# 核心函数
# ============================================================


def validate_url(url: str) -> dict[str, Any]:
    """URL 格式校验与安全检查"""
    result = {
        'valid': False,
        'safe': False,
        'reason': '',
        'normalized_url': url,
    }

    if not url or not url.strip():
        result['reason'] = 'URL 为空'
        return result

    url = url.strip()

    clean_url = url.split('|')[0].split('#')[0]

    try:
        parsed = urlparse(clean_url)
    except Exception as e:
        result['reason'] = f'URL 解析失败: {e}'
        return result

    scheme = parsed.scheme.lower()
    if not scheme:
        result['reason'] = 'URL 缺少协议 scheme'
        return result

    if scheme in BLOCKED_SCHEMES:
        result['reason'] = f'不安全的协议: {scheme}'
        return result

    if scheme not in ALLOWED_SCHEMES:
        result['reason'] = f'不支持的协议: {scheme}（仅支持 http/https）'
        return result

    host = parsed.hostname or parsed.netloc
    if not host:
        result['reason'] = 'URL 缺少主机地址'
        return result

    if not _is_valid_host(host):
        result['reason'] = f'无效的主机地址格式: {host}'
        return result

    if _is_blacklisted_domain(host):
        result['reason'] = f'域名在黑名单中: {host}'
        return result

    if _is_private_ip(host):
        result['reason'] = f'私有 IP 地址被拒绝: {host}'
        return result

    url_content = parsed.path + '?' + (parsed.query or '')

    xss_reason = _check_xss(url)
    if xss_reason:
        result['reason'] = xss_reason
        return result

    cmd_reason = _check_command_injection(url_content)
    if cmd_reason:
        result['reason'] = cmd_reason
        return result

    traversal_reason = _check_path_traversal(parsed.path)
    if traversal_reason:
        result['reason'] = traversal_reason
        return result

    result['valid'] = True
    result['safe'] = True
    result['normalized_url'] = clean_url

    result['normalized_url'] = sanitize_url(clean_url)

    return result


def sanitize_url(url: str) -> str:
    """URL 规范化"""
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    netloc = parsed.netloc.lower()

    path = parsed.path
    if path:
        while '//' in path:
            path = path.replace('//', '/')

    query_params = parse_qs(parsed.query, keep_blank_values=True)
    unsafe_params = {'cmd', 'exec', 'command', 'shell', 'debug', 'eval', 'callback', 'jsonp'}
    safe_params = {k: v for k, v in query_params.items() if k.lower() not in unsafe_params}

    new_query = urlencode(safe_params, doseq=True) if safe_params else ''

    normalized = urlunparse(
        (
            parsed.scheme,
            netloc,
            path,
            parsed.params,
            new_query,
            '',
        )
    )

    return normalized


def is_safe_url(url: str) -> tuple[bool, str]:
    """快捷函数：判断 URL 是否安全"""
    result = validate_url(url)
    return result['safe'], result['reason']


# ============================================================
# 域名黑名单
# ============================================================

_global_domain_blacklist = set(DEFAULT_DOMAIN_BLACKLIST)


def get_domain_blacklist() -> set:
    return _global_domain_blacklist.copy()


def add_domain_blacklist(domains: list[str]):
    _global_domain_blacklist.update(d.lower().strip() for d in domains if d.strip())


def clear_domain_blacklist():
    _global_domain_blacklist.clear()
    _global_domain_blacklist.update(DEFAULT_DOMAIN_BLACKLIST)


# ============================================================
# 内部辅助函数
# ============================================================


def _is_blacklisted_domain(host: str) -> bool:
    return host.lower() in _global_domain_blacklist


def _is_private_ip(host: str) -> bool:
    for prefix in PRIVATE_IP_PREFIXES:
        if host.startswith(prefix):
            return True

    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _is_valid_host(host: str) -> bool:
    host_lower = host.lower()
    if not re.match(r'^[a-z0-9:._\-\[\]]+$', host_lower):
        return False

    try:
        ipaddress.ip_address(host_lower)
        return True
    except ValueError:
        pass

    if '.' not in host_lower and host_lower != 'localhost':
        return False

    return True


def _check_xss(url: str) -> str | None:
    for pattern in XSS_PATTERNS:
        match = pattern.search(url)
        if match:
            return f"检测到 XSS 注入 payload: '{match.group()[:50]}'"
    return None


def _check_command_injection(url_content: str) -> str | None:
    for pattern in CMD_INJECTION_PATTERNS:
        match = pattern.search(url_content)
        if match:
            return f"检测到命令注入 payload: '{match.group()[:50]}'"
    return None


def _check_path_traversal(path: str) -> str | None:
    for pattern in PATH_TRAVERSAL_PATTERNS:
        match = pattern.search(path)
        if match:
            return f"检测到路径遍历 payload: '{match.group()[:50]}'"
    return None
