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

import concurrent.futures
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

logger = logging.getLogger('app.security')


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

# ════════════════════════════════════════════════════════════
# 境外流媒体域名默认拦截（《网络安全法》第24条）
# ════════════════════════════════════════════════════════════

KNOWN_OVERSEAS_STREAMING_DOMAINS = frozenset(
    {
        # YouTube 系列
        'youtube.com',
        'www.youtube.com',
        'm.youtube.com',
        'youtu.be',
        'ytimg.com',
        'googlevideo.com',
        'youtube.googleapis.com',
        # Netflix
        'netflix.com',
        'www.netflix.com',
        'nflxvideo.net',
        'nflximg.net',
        'nflxext.com',
        'nflxso.net',
        # HBO Max / Max
        'hbomax.com',
        'www.hbomax.com',
        'hbogo.com',
        'max.com',
        'www.max.com',
        # Disney+
        'disneyplus.com',
        'www.disneyplus.com',
        'dssott.com',
        'disney-plus.net',
        'disney.api.edge.bamgrid.com',
        # Amazon Prime Video
        'primevideo.com',
        'www.primevideo.com',
        'amazonaws.com',
        'amazonvideo.com',
        # Apple TV+
        'tv.apple.com',
        'apple.com',
        # Hulu
        'hulu.com',
        'www.hulu.com',
        'hulustream.com',
        # Paramount+
        'paramountplus.com',
        'cbs.com',
        'cbsi.com',
        # Peacock
        'peacocktv.com',
        'nbc.com',
        # Spotify / 境外流媒体音频
        'spotify.com',
        'open.spotify.com',
        'tidal.com',
        'pandora.com',
        'deezer.com',
        # Twitch
        'twitch.tv',
        'www.twitch.tv',
        'jtvnw.net',
        # Vimeo
        'vimeo.com',
        'vimeocdn.com',
        # Dailymotion
        'dailymotion.com',
        'dmcdn.net',
    }
)

# 已知境外 CDN / 流媒体 IP 段（CIDR 格式，用于 IP 级别拦截）
KNOWN_OVERSEAS_CDN_CIDR = [
    # Google/YouTube
    '74.125.0.0/16',
    '172.217.0.0/16',
    '216.58.0.0/16',
    '108.177.0.0/17',
    # Netflix (AWS + 自有)
    '52.0.0.0/8',
    # Cloudflare (用于多家境外流媒体)
    '104.16.0.0/12',
    '172.64.0.0/13',
    # Akamai
    '23.0.0.0/12',
    '96.0.0.0/12',
    '184.24.0.0/13',
]

# 内容特征码匹配（违规内容检测）
CONTENT_FINGERPRINTS: list[str] = []

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
    # 注意：不拦截单个 shell 元字符（& ; | ` $）。
    # 它们在 URL query 分隔符中大量合法出现（如 http://host/stream?a=1&b=2），
    # 误杀会导致海量合法直播源被判为"不安全"被丢弃。
    # 且 ffprobe/ffmpeg 以参数列表方式调用（绝不 shell=True），元字符不会被执行，
    # 故仅拦截明确的命令替换/执行模式。
    re.compile(r'\$\{.*?\}'),  # ${...} 变量展开
    re.compile(r'`[^`]*`'),  # 反引号命令替换
    re.compile(r'\$\(.*?\)'),  # $(...) 命令替换
    re.compile(r'\|\s*[a-z]+'),  # 管道接命令
    re.compile(r';\s*[a-z]+'),  # 分号接命令
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

    # ---- 白名单默认拒绝模式 ----
    whitelist = get_domain_whitelist()
    if whitelist:  # 白名单非空时启用
        if not _is_whitelisted(host, whitelist):
            result['reason'] = f'域名不在白名单中: {host}（白名单默认拒绝模式已启用）'
            return result

    # ---- 境外流媒体检查 ----
    if _is_overseas_streaming(host, parsed.hostname):
        result['reason'] = f'域名在境外流媒体拦截列表中: {host}（《网络安全法》第38条）'
        return result

    if _is_private_ip(host):
        result['reason'] = f'私有 IP 地址被拒绝: {host}'
        return result

    # ---- IP 级别黑名单（含 ASN 级别） ----
    ip_reason = _check_ip_blacklist(host)
    if ip_reason:
        result['reason'] = ip_reason
        return result

    # ---- DNS 解析验证 ----
    dns_reason = _check_dns_resolution(host)
    if dns_reason:
        result['reason'] = dns_reason
        return result

    # ---- 内容特征码匹配 ----
    content_reason = _check_content_fingerprint(url)
    if content_reason:
        result['reason'] = content_reason
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
# 域名白名单（默认拒绝模式）
# ============================================================

_global_domain_whitelist: set[str] = set()


def get_domain_whitelist() -> set[str]:
    """获取域名白名单"""
    return _global_domain_whitelist.copy()


def add_domain_whitelist(domains: list[str]):
    """向白名单添加域名"""
    _global_domain_whitelist.update(d.lower().strip() for d in domains if d.strip())


def remove_domain_whitelist(domains: list[str]):
    """从白名单移除域名"""
    for d in domains:
        _global_domain_whitelist.discard(d.lower().strip())


def clear_domain_whitelist():
    """清空白名单"""
    _global_domain_whitelist.clear()


def set_domain_whitelist(domains: list[str]):
    """设置白名单（全量替换）"""
    _global_domain_whitelist.clear()
    add_domain_whitelist(domains)


def _is_whitelisted(host: str, whitelist: set[str]) -> bool:
    """检查域名是否在白名单中（支持子域名匹配）"""
    host = host.lower()
    if host in whitelist:
        return True
    # 子域名匹配：xxx.example.com 匹配 *.example.com
    parts = host.split('.')
    for i in range(1, len(parts)):
        wildcard = '*.' + '.'.join(parts[i:])
        if wildcard in whitelist:
            return True
    return False


# ============================================================
# 境外流媒体检测
# ============================================================


def _is_overseas_streaming(host: str, parsed_host: str | None) -> bool:
    """判断是否为已知境外流媒体域名"""
    host = host.lower()
    if host in KNOWN_OVERSEAS_STREAMING_DOMAINS:
        return True
    return any(host.endswith('.' + known) for known in KNOWN_OVERSEAS_STREAMING_DOMAINS)


def get_overseas_streaming_domains() -> frozenset:
    """获取境外流媒体域名列表"""
    return KNOWN_OVERSEAS_STREAMING_DOMAINS


# ============================================================
# IP 黑名单（含 ASN 级别）
# ============================================================


def _check_ip_blacklist(host: str) -> str | None:
    """检查 IP 是否在黑名单中（含境外 CDN 段）"""
    try:
        ip_obj = ipaddress.ip_address(host)
        for cidr_str in KNOWN_OVERSEAS_CDN_CIDR:
            if ip_obj in ipaddress.ip_network(cidr_str, strict=False):
                return f'IP 在境外 CDN 黑名单中: {host} ({cidr_str})'
    except ValueError:
        pass
    return None


# ============================================================
# DNS 解析验证
# ============================================================


# 复用的 DNS 解析线程池，避免每次校验都新建 executor 的开销
_DNS_RESOLVER = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='dns-resolver')


def _check_dns_resolution(host: str) -> str | None:
    """验证域名 DNS 解析结果是否安全

    检查：
    - 解析结果指向内网地址则拒绝
    - 解析失败返回警告
    - 解析超时（5s）返回警告，防止异常 DNS 服务器导致永久挂起（DoS）
    """
    try:
        addrs = _DNS_RESOLVER.submit(socket.getaddrinfo, host, 80).result(timeout=5)
        for addr in addrs:
            ip_str = addr[4][0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if ip_obj.is_private or ip_obj.is_loopback:
                    return f'DNS 解析到内网地址: {host} → {ip_str}'
            except ValueError:
                continue
    except concurrent.futures.TimeoutError:
        return f'DNS 解析超时: {host}（域名解析超过 5 秒）'
    except socket.gaierror:
        return f'DNS 解析失败: {host}（域名可能不存在或网络不可达）'
    except Exception as e:
        logger.debug(f'DNS 解析异常({host}): {e}')
    return None


# ============================================================
# 内容特征码匹配
# ============================================================


def _check_content_fingerprint(url: str) -> str | None:
    """检查 URL 内容特征码（URL hash 匹配已知违规内容库）"""
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    url_hash_prefix = url_hash[:16]
    for fingerprint in CONTENT_FINGERPRINTS:
        if url_hash_prefix.startswith(fingerprint) or fingerprint.startswith(url_hash_prefix):
            return f'URL 哈希匹配已知违规内容: 前16位={url_hash_prefix}'
    return None


def load_cnnic_blacklist(filepath: str) -> int:
    """加载 CNNIC / 公安部通报的恶意 URL 库

    文件格式（JSON Lines）:
      {"type": "domain", "value": "malware.example.com", "source": "cnnic", "category": "malware"}
      {"type": "ip", "value": "1.2.3.4", "source": "cnnic", "category": "malware"}
      {"type": "url_hash", "value": "ab12cd34...", "source": "cnnic", "category": "phishing"}

    Returns:
        int: 加载的条目数
    """
    if not os.path.exists(filepath):
        logger.warning(f'CNNIC 黑名单文件不存在: {filepath}')
        return 0

    count = 0
    with open(filepath, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                etype = entry.get('type', '')
                evalue = entry.get('value', '')
                if not evalue:
                    continue
                if etype == 'domain':
                    add_domain_blacklist([evalue])
                    count += 1
                elif etype == 'url_hash':
                    global CONTENT_FINGERPRINTS
                    CONTENT_FINGERPRINTS = [*list(CONTENT_FINGERPRINTS), evalue]
                    count += 1
            except (json.JSONDecodeError, KeyError):
                continue

    logger.info(f'已加载 {count} 条 CNNIC/公安部通报恶意条目')
    return count


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

    return not ('.' not in host_lower and host_lower != 'localhost')


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
