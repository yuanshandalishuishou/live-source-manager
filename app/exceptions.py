#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live Source Manager 异常层次体系
提供标准化的异常类，替代所有模块中的通用 except Exception

融合方案B的 BaseAppException（带错误码、suggestion、to_dict、traceback_str）
"""

import os
import traceback
from typing import Optional, Dict, Any


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
