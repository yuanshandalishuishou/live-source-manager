#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局错误处理模块
提供异常捕获装饰器、错误统计收集、统一日志格式
"""

import asyncio
import functools
import logging
import sys
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, Type
from collections import defaultdict

from exceptions import BaseAppException


# 统一日志格式: [YYYY-MM-DD HH:MM:SS] [LEVEL] [Module] message
UNIFIED_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
UNIFIED_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """创建统一格式的日志记录器
    
    Args:
        name: 模块名
        level: 日志级别
        
    Returns:
        logging.Logger: 配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 如果已经有处理器，不重复添加
    if logger.handlers:
        return logger
    
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(UNIFIED_LOG_FORMAT, datefmt=UNIFIED_LOG_DATE_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


class ErrorStats:
    """错误统计收集器
    
    按类型、模块统计错误计数
    
    Attributes:
        window_minutes: 统计时间窗口（分钟）
        _error_counts: {(error_code, module): count}
    """
    
    def __init__(self, window_minutes: int = 60):
        self.window_minutes = window_minutes
        # {(error_code, module): [(timestamp, message), ...]}
        self._errors = defaultdict(list)
        self._total_count = 0
    
    def record(
        self,
        error: Exception,
        module: str = "",
        context: Optional[Dict[str, Any]] = None,
    ):
        """记录一个错误
        
        Args:
            error: 异常对象
            module: 来源模块名
            context: 附加上下文信息
        """
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
        """清理超过时间窗口的错误记录"""
        cutoff = datetime.now() - timedelta(minutes=self.window_minutes)
        for key in list(self._errors.keys()):
            self._errors[key] = [
                e for e in self._errors[key]
                if e["timestamp"] > cutoff
            ]
            if not self._errors[key]:
                del self._errors[key]
    
    def get_summary(self) -> Dict[str, Any]:
        """获取错误统计摘要"""
        self._prune()
        summary = {
            "total_count": self._total_count,
            "window_minutes": self.window_minutes,
            "by_type_and_module": {},
        }
        for (code, module), records in self._errors.items():
            key = f"code={code}, module={module}"
            summary["by_type_and_module"][key] = {
                "error_code": code,
                "module": module,
                "count": len(records),
                "last_occurred": max(e["timestamp"] for e in records).isoformat(),
            }
        return summary
    
    def get_count_by_type(self) -> Dict[int, int]:
        """按错误码统计"""
        counts = defaultdict(int)
        for (code, _), records in self._errors.items():
            counts[code] += len(records)
        return dict(counts)
    
    def get_count_by_module(self) -> Dict[str, int]:
        """按模块统计"""
        counts = defaultdict(int)
        for (_, module), records in self._errors.items():
            counts[module] += len(records)
        return dict(counts)
    
    def reset(self):
        """重置所有统计"""
        self._errors.clear()
        self._total_count = 0


# 全局错误统计实例
global_error_stats = ErrorStats()


def catch_exception(
    logger: Optional[logging.Logger] = None,
    module_name: str = "",
    raise_original: bool = False,
    fallback_return: Any = None,
    capture_stats: bool = True,
):
    """全局异常捕获装饰器
    
    自动捕获、记录异常，并根据配置决定是否重新抛出或返回默认值。
    日志格式: [YYYY-MM-DD HH:MM:SS] [LEVEL] [Module] message
    
    Args:
        logger: 日志记录器（默认使用 logging.getLogger）
        module_name: 来源模块名
        raise_original: 是否重新抛出原始异常
        fallback_return: 异常时的缺省返回值
        capture_stats: 是否纳入错误统计
    
    Usage:
        @catch_exception(logger=my_logger, module_name="my_module")
        def risky_function():
            ...
    """
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


def _log_exception(
    logger: logging.Logger,
    error: Exception,
    module: str,
    func_name: str,
):
    """统一记录异常日志
    
    日志格式: [YYYY-MM-DD HH:MM:SS] [LEVEL] [Module] message
    """
    if isinstance(error, BaseAppException):
        logger.error(
            "[%s] %s | 建议: %s",
            error.message,
            error.suggestion,
        )
        if error.original:
            logger.debug("原始异常: %s", traceback.format_exc())
    else:
        logger.error(
            "%s\n%s",
            str(error),
            traceback.format_exc(),
        )


def _wrap_exception(error: Exception, module: str) -> BaseAppException:
    """将普通异常包装为 BaseAppException"""
    return BaseAppException(
        error_code=0,
        message=str(error),
        suggestion="请联系管理员查看详细日志",
        original=error,
    )


def format_error_response(
    error: BaseAppException,
    include_traceback: bool = False,
) -> Dict[str, Any]:
    """生成统一的错误响应格式
    
    Args:
        error: 异常对象
        include_traceback: 是否包含调用栈
    
    Returns:
        统一格式的错误字典
    """
    response = {
        "success": False,
        "error": error.to_dict(),
    }
    if include_traceback:
        response["traceback"] = error.traceback_str
    return response


def setup_global_exception_hook(logger: Optional[logging.Logger] = None):
    """设置全局未捕获异常处理器
    
    Args:
        logger: 日志记录器
    """
    if logger is None:
        logger = logging.getLogger("GlobalHook")
    
    def global_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, BaseAppException):
            logger.critical(
                "[%s] %s | 建议: %s",
                exc_value.message,
                exc_value.suggestion,
            )
        else:
            logger.critical(
                "未捕获的异常 (类型: %s): %s\n%s",
                exc_type.__name__,
                str(exc_value),
                "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            )
    
    sys.excepthook = global_excepthook
