#!/usr/bin/env python3
"""
直播源管理工具 — 核心包入口
===========================

本模块为 app 包的统一入口，从各子模块 re-export 全部公开接口，
保持向后兼容（外部代码 `from app import XXX` 无需修改）。

模块架构（依赖方向: 上层 → 下层）:
  L0 基础层:  exceptions / logger / utils
  L1 配置层:  config / security
  L2 业务层:  rules / source_manager / stream_tester
  L3 输出层:  m3u_generator
  L4 协调层:  manager (EnhancedLiveSourceManager)
"""

# ═══════════════════════════════════════════════════
# L0 基础层
# ═══════════════════════════════════════════════════

# --- exceptions ---
# ═══════════════════════════════════════════════════
# L1 配置层
# ═══════════════════════════════════════════════════
# --- config ---
from app.config import Config
from app.exceptions import (
    ERROR_CODE_SUGGESTIONS,
    BaseAppException,
    ConfigError,
    ErrorStats,
    FileException,
    LsmError,
    OutputError,
    SourceDownloadError,
    SourceError,
    SourceParseError,
    StreamTestError,
    _log_exception,
    _wrap_exception,
    catch_exception,
    format_error_response,
    global_error_stats,
    setup_global_exception_hook,
)

# --- logger ---
from app.logger import (
    UNIFIED_LOG_DATE_FORMAT,
    UNIFIED_LOG_FORMAT,
    Logger,
    setup_logger,
)

# ═══════════════════════════════════════════════════
# L3 输出层
# ═══════════════════════════════════════════════════
# --- m3u_generator ---
from app.m3u_generator import M3UGenerator

# ═══════════════════════════════════════════════════
# L4 协调层
# ═══════════════════════════════════════════════════
# --- manager ---
from app.manager import EnhancedLiveSourceManager, main

# ═══════════════════════════════════════════════════
# L2 业务层
# ═══════════════════════════════════════════════════
# --- rules ---
from app.rules import (
    _DEFAULT_NEGATIVE_KEYWORDS,
    ChannelRules,
    _get_rule_models,
    check_exclusion_for_app,
    get_active_classification_rules_for_app,
    get_all_exclusions_for_app,
    get_channel_name_mapping_for_app,
    get_source_categories_for_app,
    save_source_categories_for_app,
)

# --- security ---
from app.security import (
    ALLOWED_SCHEMES,
    BLOCKED_SCHEMES,
    CMD_INJECTION_PATTERNS,
    DEFAULT_DOMAIN_BLACKLIST,
    PATH_TRAVERSAL_PATTERNS,
    PRIVATE_IP_PREFIXES,
    XSS_PATTERNS,
    SourceData,
    _check_command_injection,
    _check_path_traversal,
    _check_xss,
    _global_domain_blacklist,
    _is_blacklisted_domain,
    _is_private_ip,
    _is_valid_host,
    add_domain_blacklist,
    clear_domain_blacklist,
    get_domain_blacklist,
    is_safe_url,
    sanitize_url,
    validate_url,
)

# --- source_manager ---
from app.source_manager import SourceManager

# --- stream_tester ---
from app.stream_tester import StreamTester

# --- utils ---
from app.utils import (
    _backup_file,
    _do_atomic_write,
    _get_fallback_logger,
    _verify_write,
    atomic_write,
    safe_read_file,
)

# ═══════════════════════════════════════════════════
# 全局常量（向后兼容 — 第三方库可用性标志）
# ═══════════════════════════════════════════════════

try:
    import aiofiles
    import aiohttp
    import aiohttp_socks

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None
    aiofiles = None
    aiohttp_socks = None

try:
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    tqdm = None

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None


# ═══════════════════════════════════════════════════
# __all__ — 显式声明公开接口
# ═══════════════════════════════════════════════════

__all__ = [
    # exceptions
    'BaseAppException',
    'LsmError',
    'ConfigError',
    'SourceError',
    'SourceDownloadError',
    'SourceParseError',
    'StreamTestError',
    'FileException',
    'OutputError',
    'ERROR_CODE_SUGGESTIONS',
    'ErrorStats',
    'global_error_stats',
    'catch_exception',
    'format_error_response',
    'setup_global_exception_hook',
    # logger
    'UNIFIED_LOG_FORMAT',
    'UNIFIED_LOG_DATE_FORMAT',
    'setup_logger',
    'Logger',
    # utils
    'atomic_write',
    'safe_read_file',
    # config
    'Config',
    # security
    'SourceData',
    'validate_url',
    'sanitize_url',
    'is_safe_url',
    'ALLOWED_SCHEMES',
    'BLOCKED_SCHEMES',
    'DEFAULT_DOMAIN_BLACKLIST',
    'get_domain_blacklist',
    'add_domain_blacklist',
    'clear_domain_blacklist',
    # rules
    'ChannelRules',
    'get_active_classification_rules_for_app',
    'check_exclusion_for_app',
    'get_all_exclusions_for_app',
    'get_channel_name_mapping_for_app',
    'save_source_categories_for_app',
    'get_source_categories_for_app',
    # source_manager
    'SourceManager',
    # stream_tester
    'StreamTester',
    # m3u_generator
    'M3UGenerator',
    # manager
    'EnhancedLiveSourceManager',
    'main',
    # 全局常量
    'AIOHTTP_AVAILABLE',
    'TQDM_AVAILABLE',
    'YAML_AVAILABLE',
]
