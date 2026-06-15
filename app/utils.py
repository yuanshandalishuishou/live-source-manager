#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live Source Manager 工具模块
合并自: exceptions.py, error_handler.py, file_utils.py
功能不减少，仅合并文件
"""

import os
import sys
import tempfile
import shutil
import time
import asyncio
import functools
import logging
import traceback
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable
from collections import defaultdict


# ═══════════════════════════════════════════════════
# 异常层次体系 (原 exceptions.py)
# ═══════════════════════════════════════════════════

class BaseAppException(Exception):
    """应用异常基类 - 带错误码和修复建议"""
    def __init__(self, error_code: int, message: str, suggestion: str = "",
                 details: Optional[Dict[str, Any]] = None, original: Optional[Exception] = None):
        self.error_code = error_code
        self.message = message
        self.suggestion = suggestion
        self.details = details or {}
        self.original = original
        super().__init__(str(self))

    @property
    def traceback_str(self) -> str:
        if self.original:
            return "".join(traceback.format_exception(
                type(self.original), self.original, self.original.__traceback__))
        return "".join(traceback.format_exception(type(self), self, self.__traceback__))

    def to_dict(self) -> Dict[str, Any]:
        return {"error_code": self.error_code, "message": self.message,
                "suggestion": self.suggestion, "details": self.details}

    def __str__(self) -> str:
        base = f"[{self.error_code}] {self.message}"
        if self.suggestion:
            base += f" | 建议: {self.suggestion}"
        return base


class LsmError(BaseAppException):
    """lsm 基类"""
    def __init__(self, message: str, suggestion: str = "",
                 details: Optional[Dict[str, Any]] = None, original: Optional[Exception] = None):
        super().__init__(error_code=0, message=message, suggestion=suggestion,
                         details=details, original=original)


class ConfigError(LsmError):
    """配置相关错误"""
    def __init__(self, message: str, suggestion: str = "请检查配置文件格式和内容是否正确",
                 details=None, original=None):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 1001


class SourceError(LsmError):
    """源处理错误"""
    def __init__(self, message: str, suggestion: str = "请检查源文件格式或URL",
                 details=None, original=None):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 3001


class SourceDownloadError(SourceError):
    """源下载失败"""
    def __init__(self, message: str, suggestion: str = "请检查网络连接和源URL",
                 details=None, original=None):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 3002


class SourceParseError(SourceError):
    """源解析失败"""
    def __init__(self, message: str, suggestion: str = "请检查文件格式是否为有效的M3U/TXT",
                 details=None, original=None):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 3003


class StreamTestError(LsmError):
    """流测试错误"""
    def __init__(self, message: str, suggestion: str = "请检查测试配置或FFprobe工具可用性",
                 details=None, original=None):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 4001


class FileException(LsmError):
    """文件操作异常"""
    def __init__(self, message: str, suggestion: str = "请检查文件路径和权限，确保目录存在且可读写",
                 details=None, original=None):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 5002


class OutputError(LsmError):
    """输出/文件写入错误"""
    def __init__(self, message: str, suggestion: str = "请检查输出目录权限和磁盘空间",
                 details=None, original=None):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 5001


ERROR_CODE_SUGGESTIONS = {
    1001: "配置错误，请检查配置文件",
    3001: "源处理异常，请检查源文件或URL",
    3002: "源下载失败，请检查网络连接",
    3003: "源解析失败，请检查文件格式",
    4001: "流测试异常，请检查测试参数",
    5001: "文件操作异常，请检查文件权限和磁盘空间",
    5002: "文件操作异常，请检查文件路径和权限",
}


# ═══════════════════════════════════════════════════
# 全局错误处理 (原 error_handler.py)
# ═══════════════════════════════════════════════════

# 统一日志格式
UNIFIED_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
UNIFIED_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """创建统一格式的日志记录器"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(UNIFIED_LOG_FORMAT, datefmt=UNIFIED_LOG_DATE_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


class ErrorStats:
    """错误统计收集器"""
    def __init__(self, window_minutes: int = 60):
        self.window_minutes = window_minutes
        self._errors = defaultdict(list)
        self._total_count = 0

    def record(self, error: Exception, module: str = "", context: Optional[Dict[str, Any]] = None):
        now = datetime.now()
        error_code = getattr(error, "error_code", 0)
        self._errors[(error_code, module)].append({
            "timestamp": now,
            "message": str(error),
            "context": context or {},
        })
        self._total_count += 1
        self._prune()

    def _prune(self):
        cutoff = datetime.now() - timedelta(minutes=self.window_minutes)
        for key in list(self._errors.keys()):
            self._errors[key] = [e for e in self._errors[key] if e["timestamp"] > cutoff]
            if not self._errors[key]:
                del self._errors[key]

    def get_summary(self) -> Dict[str, Any]:
        self._prune()
        summary = {"total_count": self._total_count, "window_minutes": self.window_minutes,
                    "by_type_and_module": {}}
        for (code, module), records in self._errors.items():
            key = f"code={code}, module={module}"
            summary["by_type_and_module"][key] = {
                "error_code": code, "module": module, "count": len(records),
                "last_occurred": max(e["timestamp"] for e in records).isoformat(),
            }
        return summary

    def get_count_by_type(self) -> Dict[int, int]:
        counts = defaultdict(int)
        for (code, _), records in self._errors.items():
            counts[code] += len(records)
        return dict(counts)

    def get_count_by_module(self) -> Dict[str, int]:
        counts = defaultdict(int)
        for (_, module), records in self._errors.items():
            counts[module] += len(records)
        return dict(counts)

    def reset(self):
        self._errors.clear()
        self._total_count = 0


global_error_stats = ErrorStats()


def catch_exception(logger: Optional[logging.Logger] = None, module_name: str = "",
                    raise_original: bool = False, fallback_return: Any = None,
                    capture_stats: bool = True):
    """全局异常捕获装饰器"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal logger, module_name
            if logger is None:
                logger = logging.getLogger(func.__module__)
            if not module_name:
                module_name = func.__module__
            try:
                return func(*args, **kwargs)
            except BaseAppException as e:
                _log_exception(logger, e, module_name, func.__name__)
                if capture_stats:
                    global_error_stats.record(e, module_name, {"func": func.__name__})
                if raise_original:
                    raise
                return fallback_return
            except Exception as e:
                wrapped = _wrap_exception(e, module_name)
                _log_exception(logger, wrapped, module_name, func.__name__)
                if capture_stats:
                    global_error_stats.record(wrapped, module_name, {"func": func.__name__})
                if raise_original:
                    raise
                return fallback_return

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            nonlocal logger, module_name
            if logger is None:
                logger = logging.getLogger(func.__module__)
            if not module_name:
                module_name = func.__module__
            try:
                return await func(*args, **kwargs)
            except BaseAppException as e:
                _log_exception(logger, e, module_name, func.__name__)
                if capture_stats:
                    global_error_stats.record(e, module_name, {"func": func.__name__})
                if raise_original:
                    raise
                return fallback_return
            except Exception as e:
                wrapped = _wrap_exception(e, module_name)
                _log_exception(logger, wrapped, module_name, func.__name__)
                if capture_stats:
                    global_error_stats.record(wrapped, module_name, {"func": func.__name__})
                if raise_original:
                    raise
                return fallback_return

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    return decorator


def _log_exception(logger: logging.Logger, error: Exception, module: str, func_name: str):
    if isinstance(error, BaseAppException):
        logger.error("[%s] %s | 建议: %s", error.message, error.suggestion)
        if error.original:
            logger.debug("原始异常: %s", traceback.format_exc())
    else:
        logger.error("%s\n%s", str(error), traceback.format_exc())


def _wrap_exception(error: Exception, module: str) -> BaseAppException:
    return BaseAppException(error_code=0, message=str(error),
                            suggestion="请联系管理员查看详细日志", original=error)


def format_error_response(error: BaseAppException, include_traceback: bool = False) -> Dict[str, Any]:
    response = {"success": False, "error": error.to_dict()}
    if include_traceback:
        response["traceback"] = error.traceback_str
    return response


def setup_global_exception_hook(logger: Optional[logging.Logger] = None):
    if logger is None:
        logger = logging.getLogger("GlobalHook")
    def global_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, BaseAppException):
            logger.critical("[%s] %s | 建议: %s", exc_value.message, exc_value.suggestion)
        else:
            logger.critical("未捕获的异常 (类型: %s): %s\n%s",
                            exc_type.__name__, str(exc_value),
                            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.excepthook = global_excepthook


# ═══════════════════════════════════════════════════
# 文件工具 (原 file_utils.py)
# ═══════════════════════════════════════════════════

def atomic_write(filepath: str, content: str, encoding: str = "utf-8",
                 retries: int = 3, retry_delay: float = 0.5,
                 backup: bool = True, backup_dir: Optional[str] = None,
                 verify: bool = True, logger: Optional[logging.Logger] = None):
    """原子写入文件内容"""
    _log = logger or _get_fallback_logger()
    dirpath = os.path.dirname(filepath)
    try:
        os.makedirs(dirpath, exist_ok=True)
    except OSError as e:
        raise FileException(message=f"无法创建目录: {dirpath}",
                            suggestion="请检查文件系统权限",
                            details={"dirpath": dirpath}, original=e)
    if backup and os.path.exists(filepath):
        _backup_file(filepath, backup_dir, _log)
    last_exception = None
    for attempt in range(1, retries + 1):
        try:
            _do_atomic_write(filepath, content, encoding, _log)
            if verify:
                _verify_write(filepath, content, encoding, _log)
            _log.debug("原子写入成功: %s (尝试 %d/%d)", filepath, attempt, retries)
            return
        except (OSError, IOError) as e:
            last_exception = e
            if attempt < retries:
                _log.warning("写入失败 (尝试 %d/%d): %s - %s", attempt, retries, filepath, e)
                time.sleep(retry_delay)
            else:
                raise FileException(message=f"文件写入失败（已重试 {retries} 次）: {filepath}",
                                    suggestion=f"请检查目录权限: {os.path.dirname(filepath)}",
                                    details={"filepath": filepath, "attempts": retries,
                                             "last_error": str(e)}, original=e)


def _do_atomic_write(filepath: str, content: str, encoding: str, logger: logging.Logger):
    dirpath = os.path.dirname(filepath) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", prefix=".atomic_", dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, filepath)
        logger.debug("临时文件 %s -> %s 替换完成", tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _verify_write(filepath: str, content: str, encoding: str, logger: logging.Logger):
    try:
        with open(filepath, "r", encoding=encoding) as f:
            written = f.read()
        expected_len = len(content)
        actual_len = len(written)
        if expected_len != actual_len:
            logger.warning("写入校验不一致: 期望 %d 字节, 实际 %d 字节", expected_len, actual_len)
        else:
            logger.debug("写入校验通过: %d 字节", actual_len)
    except OSError as e:
        logger.warning("写入校验失败 (文件无法读取): %s", e)


def _backup_file(filepath: str, backup_dir: Optional[str] = None,
                 logger: Optional[logging.Logger] = None):
    _log = logger or _get_fallback_logger()
    if backup_dir:
        backup_base = backup_dir
    else:
        backup_base = os.path.join(os.path.dirname(filepath) or ".", ".backup")
    os.makedirs(backup_base, exist_ok=True)
    basename = os.path.basename(filepath)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_base, f"{basename}.backup.{timestamp}")
    try:
        shutil.copy2(filepath, backup_path)
        _log.info("文件已备份: %s -> %s", filepath, backup_path)
    except OSError as e:
        _log.warning("备份失败: %s -> %s (%s)", filepath, backup_path, e)


def safe_read_file(filepath: str, encoding: str = "utf-8",
                   fallback_encodings: Optional[list] = None,
                   logger: Optional[logging.Logger] = None) -> str:
    """安全读取文件内容（支持多编码回退）"""
    _log = logger or _get_fallback_logger()
    if not os.path.exists(filepath):
        raise FileException(message=f"文件未找到: {filepath}",
                            suggestion="请检查文件路径是否正确",
                            details={"filepath": filepath, "reason": "file_not_found"})
    encodings = [encoding] + (fallback_encodings or ["gbk", "gb2312", "latin1", "utf-8-sig"])
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                content = f.read()
            if content.startswith('\ufeff'):
                content = content[1:]
            return content
        except (UnicodeDecodeError, OSError):
            continue
    try:
        with open(filepath, "rb") as f:
            raw = f.read()
        return raw.decode("utf-8", errors="replace")
    except OSError as e:
        raise FileException(message=f"文件读取失败: {filepath}",
                            suggestion="请检查文件是否存在且可读",
                            details={"filepath": filepath}, original=e)


def _get_fallback_logger() -> logging.Logger:
    return logging.getLogger("FileUtils")
