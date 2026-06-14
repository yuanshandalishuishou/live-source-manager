#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
URL安全审查模块

核心功能:
- validate_url(url): 格式校验、scheme白名单、XSS/命令注入/路径遍历检测
- sanitize_url(url): URL规范化（移除多余参数、统一编码）
- is_safe_url(url): 返回 (safe: bool, reason: str)
- 域名黑名单（可配置列表）
- 不安全协议阻断（file://, data://, javascript: 等）
"""

import re
import logging
import ipaddress
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, quote

from exceptions import BaseAppException


# ============================================================
# 默认配置
# ============================================================

# 允许的 URL scheme 白名单
ALLOWED_SCHEMES = frozenset({
    "http",
    "https",
})

# 不安全协议（直接阻断）
BLOCKED_SCHEMES = frozenset({
    "file",
    "data",
    "javascript",
    "vbscript",
    "jar",
    "ftp",
    "chrome",
    "chrome-extension",
    "edge",
    "safari-extension",
    "view-source",
    "about",
})

# 默认域名黑名单
DEFAULT_DOMAIN_BLACKLIST = frozenset({
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "255.255.255.255",
    "internal.example.com",
})

# 私有 IP 前缀（快速匹配）
PRIVATE_IP_PREFIXES = [
    "10.",
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.",
    "127.",
    "169.254.",
]

# XSS 注入检测模式
XSS_PATTERNS = [
    re.compile(r"<script[^>]*>", re.I),
    re.compile(r"</script>", re.I),
    re.compile(r"javascript\s*:", re.I),
    re.compile(r"on\w+\s*=", re.I),
    re.compile(r"<iframe[^>]*>", re.I),
    re.compile(r"<embed[^>]*>", re.I),
    re.compile(r"<object[^>]*>", re.I),
    re.compile(r"alert\s*\(", re.I),
    re.compile(r"eval\s*\(", re.I),
    re.compile(r"document\.cookie", re.I),
    re.compile(r"window\.location", re.I),
    re.compile(r"expression\s*\(", re.I),
    re.compile(r"vbscript\s*:", re.I),
    re.compile(r"data\s*:", re.I),
]

# 命令注入检测模式
CMD_INJECTION_PATTERNS = [
    re.compile(r"[;&|`$]"),
    re.compile(r"\$\{.*?\}"),
    re.compile(r"`[^`]*`"),
    re.compile(r"\$\(.*?\)"),
    re.compile(r"\|\s*[a-z]+"),
    re.compile(r";\s*[a-z]+"),
]

# 路径遍历检测模式
PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./\.\.", re.I),
    re.compile(r"\.\.\\\.\."),
    re.compile(r"%2e%2e%2f", re.I),
    re.compile(r"%2e%2e/", re.I),
]


# ============================================================
# 核心函数
# ============================================================

def validate_url(url: str) -> Dict[str, Any]:
    """URL 格式校验与安全检查
    
    包括：格式校验、scheme白名单、XSS/命令注入/路径遍历检测
    
    Args:
        url: 待检查的 URL
        
    Returns:
        Dict with keys:
            - valid: bool 格式是否合法
            - safe: bool 是否安全
            - reason: str 不安全原因（若 safe=False）
            - normalized_url: str 规范化后的 URL
    """
    result = {
        "valid": False,
        "safe": False,
        "reason": "",
        "normalized_url": url,
    }
    
    if not url or not url.strip():
        result["reason"] = "URL 为空"
        return result
    
    url = url.strip()
    
    # 提取实际 URL（去除 User-Agent 尾缀）
    clean_url = url.split("|")[0].split("#")[0]
    
    # 解析 URL
    try:
        parsed = urlparse(clean_url)
    except Exception as e:
        result["reason"] = f"URL 解析失败: {e}"
        return result
    
    # 检查 scheme
    scheme = parsed.scheme.lower()
    if not scheme:
        result["reason"] = "URL 缺少协议 scheme"
        return result
    
    if scheme in BLOCKED_SCHEMES:
        result["reason"] = f"不安全的协议: {scheme}"
        return result
    
    if scheme not in ALLOWED_SCHEMES:
        result["reason"] = f"不支持的协议: {scheme}（仅支持 http/https）"
        return result
    
    # 检查 host
    host = parsed.hostname or parsed.netloc
    if not host:
        result["reason"] = "URL 缺少主机地址"
        return result
    
    if not _is_valid_host(host):
        result["reason"] = f"无效的主机地址格式: {host}"
        return result
    
    # 域名黑名单检查
    if _is_blacklisted_domain(host):
        result["reason"] = f"域名在黑名单中: {host}"
        return result
    
    # 私有 IP 阻断
    if _is_private_ip(host):
        result["reason"] = f"私有 IP 地址被拒绝: {host}"
        return result
    
    url_content = parsed.path + "?" + (parsed.query or "")
    
    # XSS 注入检测
    xss_reason = _check_xss(url)
    if xss_reason:
        result["reason"] = xss_reason
        return result
    
    # 命令注入检测
    cmd_reason = _check_command_injection(url_content)
    if cmd_reason:
        result["reason"] = cmd_reason
        return result
    
    # 路径遍历检测
    traversal_reason = _check_path_traversal(parsed.path)
    if traversal_reason:
        result["reason"] = traversal_reason
        return result
    
    # 全部检查通过
    result["valid"] = True
    result["safe"] = True
    result["normalized_url"] = clean_url  # 原始 clean_url 通过检查
    
    # 额外执行规范化（修改 normalized_url）
    result["normalized_url"] = sanitize_url(clean_url)
    
    return result


def sanitize_url(url: str) -> str:
    """URL 规范化
    
    移除多余参数、统一编码、过滤不安全查询参数
    
    Args:
        url: 原始 URL
        
    Returns:
        str: 规范化后的 URL
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    
    # 统一 netloc 小写
    netloc = parsed.netloc.lower()
    
    # 规范化路径：消除多余的 /
    path = parsed.path
    if path:
        while "//" in path:
            path = path.replace("//", "/")
    
    # 过滤可能不安全的查询参数
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    unsafe_params = {"cmd", "exec", "command", "shell", "debug", "eval", "callback", "jsonp"}
    safe_params = {
        k: v for k, v in query_params.items()
        if k.lower() not in unsafe_params
    }
    
    new_query = urlencode(safe_params, doseq=True) if safe_params else ""
    
    normalized = urlunparse((
        parsed.scheme,
        netloc,
        path,
        parsed.params,
        new_query,
        "",
    ))
    
    return normalized


def is_safe_url(url: str) -> Tuple[bool, str]:
    """快捷函数：判断 URL 是否安全
    
    Args:
        url: 待检查的 URL
        
    Returns:
        Tuple[bool, str]: (safe, reason)
    """
    result = validate_url(url)
    return result["safe"], result["reason"]


# ============================================================
# 域名黑名单
# ============================================================

# 全局可配置域名黑名单
_global_domain_blacklist = set(DEFAULT_DOMAIN_BLACKLIST)


def get_domain_blacklist() -> set:
    """获取当前的域名黑名单"""
    return _global_domain_blacklist.copy()


def add_domain_blacklist(domains: List[str]):
    """添加域名到黑名单
    
    Args:
        domains: 域名列表
    """
    _global_domain_blacklist.update(
        d.lower().strip() for d in domains if d.strip()
    )


def clear_domain_blacklist():
    """清空域名黑名单（恢复默认）"""
    _global_domain_blacklist.clear()
    _global_domain_blacklist.update(DEFAULT_DOMAIN_BLACKLIST)


# ============================================================
# 内部辅助函数
# ============================================================

def _is_blacklisted_domain(host: str) -> bool:
    """检查域名是否在黑名单中"""
    return host.lower() in _global_domain_blacklist


def _is_private_ip(host: str) -> bool:
    """检查是否为私有 IP 地址"""
    # 快速前缀匹配
    for prefix in PRIVATE_IP_PREFIXES:
        if host.startswith(prefix):
            return True
    
    # 用 ipaddress 库精确检测
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _is_valid_host(host: str) -> bool:
    """验证 host 格式合法性"""
    host_lower = host.lower()
    if not re.match(r'^[a-z0-9:._\-\[\]]+$', host_lower):
        return False
    
    # IP 地址直接通过
    try:
        ipaddress.ip_address(host_lower)
        return True
    except ValueError:
        pass
    
    # 域名至少包含一个点，或为 localhost
    if "." not in host_lower and host_lower != "localhost":
        return False
    
    return True


def _check_xss(url: str) -> Optional[str]:
    """检查 URL 中是否包含 XSS 注入 payload
    
    Returns:
        str | None: 检测到的问题描述，None 表示安全
    """
    for pattern in XSS_PATTERNS:
        match = pattern.search(url)
        if match:
            return f"检测到 XSS 注入 payload: '{match.group()[:50]}'"
    return None


def _check_command_injection(url_content: str) -> Optional[str]:
    """检查 URL 中是否包含命令注入 payload
    
    Returns:
        str | None: 检测到的问题描述，None 表示安全
    """
    for pattern in CMD_INJECTION_PATTERNS:
        match = pattern.search(url_content)
        if match:
            return f"检测到命令注入 payload: '{match.group()[:50]}'"
    return None


def _check_path_traversal(path: str) -> Optional[str]:
    """检查路径遍历攻击
    
    Returns:
        str | None: 检测到的问题描述，None 表示安全
    """
    for pattern in PATH_TRAVERSAL_PATTERNS:
        match = pattern.search(path)
        if match:
            return f"检测到路径遍历 payload: '{match.group()[:50]}'"
    return None
