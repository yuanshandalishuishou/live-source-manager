#!/usr/bin/env python3
"""
异常体系与全局错误处理
======================

包含:
  - 异常层次体系 (BaseAppException / LsmError / ConfigError / ...)
  - 错误统计收集器 (ErrorStats)
  - 全局异常捕获装饰器 (catch_exception)
  - 错误响应格式化 (format_error_response)
"""

import asyncio
import functools
import logging
import sys
import traceback
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

# ═══════════════════════════════════════════════════
# 异常层次体系
# ═══════════════════════════════════════════════════


class BaseAppException(Exception):
    """应用异常基类 - 带错误码和修复建议"""

    def __init__(
        self,
        error_code: int,
        message: str,
        suggestion: str = '',
        details: dict[str, Any] | None = None,
        original: Exception | None = None,
    ):
        self.error_code = error_code
        self.message = message
        self.suggestion = suggestion
        self.details = details or {}
        self.original = original
        super().__init__(str(self))

    @property
    def traceback_str(self) -> str:
        if self.original:
            return ''.join(traceback.format_exception(type(self.original), self.original, self.original.__traceback__))
        return ''.join(traceback.format_exception(type(self), self, self.__traceback__))

    def to_dict(self) -> dict[str, Any]:
        return {
            'error_code': self.error_code,
            'message': self.message,
            'suggestion': self.suggestion,
            'details': self.details,
        }

    def __str__(self) -> str:
        base = f'[{self.error_code}] {self.message}'
        if self.suggestion:
            base += f' | 建议: {self.suggestion}'
        return base


class LsmError(BaseAppException):
    """lsm 基类"""

    def __init__(
        self,
        message: str,
        suggestion: str = '',
        details: dict[str, Any] | None = None,
        original: Exception | None = None,
    ):
        super().__init__(
            error_code=0,
            message=message,
            suggestion=suggestion,
            details=details,
            original=original,
        )


class ConfigError(LsmError):
    """配置相关错误"""

    def __init__(
        self,
        message: str,
        suggestion: str = '请检查配置文件格式和内容是否正确',
        details=None,
        original=None,
    ):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 1001


class SourceError(LsmError):
    """源处理错误"""

    def __init__(
        self,
        message: str,
        suggestion: str = '请检查源文件格式或URL',
        details=None,
        original=None,
    ):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 3001


class SourceDownloadError(SourceError):
    """源下载失败"""

    def __init__(
        self,
        message: str,
        suggestion: str = '请检查网络连接和源URL',
        details=None,
        original=None,
    ):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 3002


class SourceParseError(SourceError):
    """源解析失败"""

    def __init__(
        self,
        message: str,
        suggestion: str = '请检查文件格式是否为有效的M3U/TXT',
        details=None,
        original=None,
    ):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 3003


class StreamTestError(LsmError):
    """流测试错误"""

    def __init__(
        self,
        message: str,
        suggestion: str = '请检查测试配置或FFprobe工具可用性',
        details=None,
        original=None,
    ):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 4001


class FileException(LsmError):
    """文件操作异常"""

    def __init__(
        self,
        message: str,
        suggestion: str = '请检查文件路径和权限，确保目录存在且可读写',
        details=None,
        original=None,
    ):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 5002


class OutputError(LsmError):
    """输出/文件写入错误"""

    def __init__(
        self,
        message: str,
        suggestion: str = '请检查输出目录权限和磁盘空间',
        details=None,
        original=None,
    ):
        super().__init__(message=message, suggestion=suggestion, details=details, original=original)
        self.error_code = 5001


ERROR_CODE_SUGGESTIONS = {
    1001: '配置错误，请检查配置文件',
    3001: '源处理异常，请检查源文件或URL',
    3002: '源下载失败，请检查网络连接',
    3003: '源解析失败，请检查文件格式',
    4001: '流测试异常，请检查测试参数',
    5001: '文件操作异常，请检查文件权限和磁盘空间',
    5002: '文件操作异常，请检查文件路径和权限',
}


# ═══════════════════════════════════════════════════
# 全局错误处理
# ═══════════════════════════════════════════════════


class ErrorStats:
    """错误统计收集器"""

    def __init__(self, window_minutes: int = 60):
        self.window_minutes = window_minutes
        self._errors = defaultdict(list)
        self._total_count = 0

    def record(self, error: Exception, module: str = '', context: dict[str, Any] | None = None):
        now = datetime.now()
        error_code = getattr(error, 'error_code', 0)
        self._errors[(error_code, module)].append(
            {
                'timestamp': now,
                'message': str(error),
                'context': context or {},
            }
        )
        self._total_count += 1
        self._prune()

    def _prune(self):
        cutoff = datetime.now() - timedelta(minutes=self.window_minutes)
        for key in list(self._errors.keys()):
            self._errors[key] = [e for e in self._errors[key] if e['timestamp'] > cutoff]
            if not self._errors[key]:
                del self._errors[key]

    def get_summary(self) -> dict[str, Any]:
        self._prune()
        summary: dict = {
            'total_count': self._total_count,
            'window_minutes': self.window_minutes,
            'by_type_and_module': {},
        }
        for (code, module), records in self._errors.items():
            key = f'code={code}, module={module}'
            summary['by_type_and_module'][key] = {
                'error_code': code,
                'module': module,
                'count': len(records),
                'last_occurred': max(e['timestamp'] for e in records).isoformat(),
            }
        return summary

    def get_count_by_type(self) -> dict[int, int]:
        counts = defaultdict(int)
        for (code, _), records in self._errors.items():
            counts[code] += len(records)
        return dict(counts)

    def get_count_by_module(self) -> dict[str, int]:
        counts = defaultdict(int)
        for (_, module), records in self._errors.items():
            counts[module] += len(records)
        return dict(counts)

    def reset(self):
        self._errors.clear()
        self._total_count = 0


global_error_stats = ErrorStats()


def catch_exception(
    logger: logging.Logger | None = None,
    module_name: str = '',
    raise_original: bool = False,
    fallback_return: Any = None,
    capture_stats: bool = True,
):
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
                    global_error_stats.record(e, module_name, {'func': func.__name__})
                if raise_original:
                    raise
                return fallback_return
            except Exception as e:
                wrapped = _wrap_exception(e, module_name)
                _log_exception(logger, wrapped, module_name, func.__name__)
                if capture_stats:
                    global_error_stats.record(wrapped, module_name, {'func': func.__name__})
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
                    global_error_stats.record(e, module_name, {'func': func.__name__})
                if raise_original:
                    raise
                return fallback_return
            except Exception as e:
                wrapped = _wrap_exception(e, module_name)
                _log_exception(logger, wrapped, module_name, func.__name__)
                if capture_stats:
                    global_error_stats.record(wrapped, module_name, {'func': func.__name__})
                if raise_original:
                    raise
                return fallback_return

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator


def _log_exception(logger: logging.Logger, error: Exception, module: str, func_name: str):
    if isinstance(error, BaseAppException):
        logger.error('[%s] %s | 建议: %s', error.error_code, error.message, error.suggestion)
        if error.original:
            logger.debug('原始异常: %s', traceback.format_exc())
    else:
        logger.error('%s\n%s', str(error), traceback.format_exc())


def _wrap_exception(error: Exception, module: str) -> BaseAppException:
    return BaseAppException(
        error_code=0,
        message=str(error),
        suggestion='请联系管理员查看详细日志',
        original=error,
    )


def format_error_response(error: BaseAppException, include_traceback: bool = False) -> dict[str, Any]:
    response = {'success': False, 'error': error.to_dict()}
    if include_traceback:
        response['traceback'] = error.traceback_str
    return response


def setup_global_exception_hook(logger: logging.Logger | None = None):
    if logger is None:
        logger = logging.getLogger('GlobalHook')

    def global_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, BaseAppException):
            logger.critical(
                '[%s] %s | 建议: %s',
                exc_value.error_code,
                exc_value.message,
                exc_value.suggestion,
            )
        else:
            logger.critical(
                '未捕获的异常 (类型: %s): %s\n%s',
                exc_type.__name__,
                str(exc_value),
                ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            )

    sys.excepthook = global_excepthook
