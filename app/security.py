#!/usr/bin/env python3
"""
URL 安全审查模块（解析阶段窄门禁）
=================================

本模块只包含解析阶段**真正生效**的「窄门禁」逻辑：

  - SourceData TypedDict —— 直播源数据结构定义
  - is_static_safe(url) —— 解析阶段 URL 门禁（协议白名单 + 主机格式 + SSRF）

设计哲学（详见 PROJECT_DESIGN_AND_DEV_GUIDE / 安全审计 S1/S2）：
直播源是给用户看的，用户网络 ≠ 服务器网络；可达性交由 StreamTester 在测试阶段
判定。解析阶段只拒绝「确定有害」的输入——不安全协议、非法主机格式、内网 / 回环 /
链路本地 / 自引用主机（SSRF）——**绝不**做 DNS 预筛、境外流媒体拦截、域名黑白名单、
URL 级 XSS / 命令注入 / 路径遍历检测。后者一旦接线会静默蒸发「服务器不可达但用户可看」
的合法源，且对以参数列表调用的 ffprobe 不构成威胁（渲染侧由 Jinja2 自动转义保障）。

历史说明：本模块曾包含 validate_url / is_safe_url / sanitize_url 及域名黑白名单、
境外流媒体拦截、DNS 解析、CNNIC 黑名单、内容指纹等「全量审查」逻辑。经安全审计
（S1/S2）确认其为生产零调用的死代码，且接线会造成误杀与虚假安全，故整体移除，仅
保留 is_static_safe 这一真实可用的窄门禁。
"""

import ipaddress
import logging
import re
from typing import TypedDict
from urllib.parse import urlparse

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
# 解析阶段窄门禁配置
# ============================================================

ALLOWED_SCHEMES = frozenset(
    {
        'http',
        'https',
        'rtmp',
        'rtsp',
        'rtp',
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


def is_static_safe(url: str) -> tuple[bool, str, str]:
    """解析阶段用的「窄门禁」：只保服务器，不做联网 / 内容策略检查。

    直播源是给用户看的，用户网络 ≠ 服务器网络；可达性应由 StreamTester 在测试
    阶段判定（解析不了 → connection_failed 分类），不应在解析阶段用 DNS 预筛把
    合法源静默蒸发。URL 字符串级的 XSS / 命令注入 / 路径穿越对以参数列表调用的
    ffprobe 不构成威胁，且 UI 渲染由 Jinja2 自动转义保障。

    本函数仅做三件事：
      1. 协议白名单 (http/https/rtmp/rtsp/rtp)
      2. 主机格式合法
      3. SSRF：拒绝私有 / 回环 / 链路本地 / 元数据 IP 以及 localhost/internal 等自引用主机

    相对原 is_safe_url / validate_url 故意移除：
      - DNS 解析验证（会误杀本机不可达但用户可看的源）
      - URL 级 XSS / 命令注入 / 路径遍历
      - 境外流媒体拦截 / 域名黑白名单（内容策略，不应静默丢弃）

    Returns:
        (safe, reason, category)  category ∈ {'ok', 'scheme', 'host', 'ssrf'}
    """
    if not url or not url.strip():
        return False, 'URL 为空', 'host'

    clean_url = url.strip().split('|')[0].split('#')[0]
    try:
        parsed = urlparse(clean_url)
    except Exception as e:
        return False, f'URL 解析失败: {e}', 'host'

    scheme = (parsed.scheme or '').lower()
    if not scheme:
        return False, 'URL 缺少协议 scheme', 'scheme'
    if scheme not in ALLOWED_SCHEMES:
        return (
            False,
            f'不支持的协议: {scheme}（仅支持 http/https/rtmp/rtsp/rtp）',
            'scheme',
        )

    host = (parsed.hostname or parsed.netloc or '').strip().lower()
    if not host:
        return False, 'URL 缺少主机地址', 'host'
    if not _is_valid_host(host):
        return False, f'无效的主机地址格式: {host}', 'host'

    # ---- SSRF 防护：拒绝服务器自引用 / 内网地址 ----
    if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local') or host.endswith('.internal'):
        return False, f'拒绝自引用主机(SSRF): {host}', 'ssrf'
    if _is_private_ip(host):
        return False, f'私有/内网 IP 被拒绝(SSRF): {host}', 'ssrf'

    return True, '', 'ok'


def _is_private_ip(host: str) -> bool:
    """判断 host 是否为私有 / 回环 / 链路本地地址（SSRF 防护用）"""
    for prefix in PRIVATE_IP_PREFIXES:
        if host.startswith(prefix):
            return True

    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _is_valid_host(host: str) -> bool:
    """校验 host 字符串格式是否合法（IPv4 / IPv6 / 域名）"""
    host_lower = host.lower()
    if not re.match(r'^[a-z0-9:._\-\[\]]+$', host_lower):
        return False

    try:
        ipaddress.ip_address(host_lower)
        return True
    except ValueError:
        pass

    return not ('.' not in host_lower and host_lower != 'localhost')
