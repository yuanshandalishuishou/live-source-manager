#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件工具模块
提供原子写入、写入后校验、自动重试、自动创建目录等功能
"""

import os
import tempfile
import shutil
import time
import logging
from typing import Optional, Callable

from exceptions import FileException


def atomic_write(
    filepath: str,
    content: str,
    encoding: str = "utf-8",
    retries: int = 3,
    retry_delay: float = 0.5,
    backup: bool = True,
    backup_dir: Optional[str] = None,
    verify: bool = True,
    logger: Optional[logging.Logger] = None,
):
    """原子写入文件内容
    
    使用临时文件 + os.replace 实现原子写入，避免写入过程中程序崩溃导致文件损坏。
    
    Args:
        filepath: 目标文件路径
        content: 要写入的内容（字符串）
        encoding: 文件编码
        retries: 最大重试次数
        retry_delay: 重试间隔（秒）
        backup: 是否在写入前备份原文件
        backup_dir: 备份文件存放目录（默认与原文件同目录的 .backup/ 子目录）
        verify: 是否在写入后校验内容长度
        logger: 日志记录器
    
    Raises:
        FileException: 所有重试均失败时抛出
    """
    _log = logger or _get_fallback_logger()
    
    # 确保目标目录存在
    dirpath = os.path.dirname(filepath)
    try:
        os.makedirs(dirpath, exist_ok=True)
    except OSError as e:
        raise FileException(
            message=f"无法创建目录: {dirpath}",
            suggestion="请检查文件系统权限",
            details={"dirpath": dirpath},
            original=e,
        )
    
    # 写入前备份（如果需要）
    if backup and os.path.exists(filepath):
        _backup_file(filepath, backup_dir, _log)
    
    # 带重试的原子写入
    last_exception = None
    
    for attempt in range(1, retries + 1):
        try:
            _do_atomic_write(filepath, content, encoding, _log)
            
            # 写入后校验
            if verify:
                _verify_write(filepath, content, encoding, _log)
            
            _log.debug("原子写入成功: %s (尝试 %d/%d)", filepath, attempt, retries)
            return  # 成功，退出
            
        except (OSError, IOError) as e:
            last_exception = e
            if attempt < retries:
                _log.warning(
                    "写入失败 (尝试 %d/%d): %s - %s",
                    attempt, retries, filepath, e,
                )
                time.sleep(retry_delay)
            else:
                raise FileException(
                    message=f"文件写入失败（已重试 {retries} 次）: {filepath}",
                    suggestion=f"请检查目录权限: {os.path.dirname(filepath)}",
                    details={
                        "filepath": filepath,
                        "attempts": retries,
                        "last_error": str(e),
                    },
                    original=e,
                )


def _do_atomic_write(filepath: str, content: str, encoding: str, logger: logging.Logger):
    """执行一次原子写入
    
    步骤:
    1. 在目标文件同目录创建临时文件
    2. 写入内容
    3. 刷新并关闭
    4. os.replace 原子替换
    """
    dirpath = os.path.dirname(filepath) or "."
    
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=".atomic_",
        dir=dirpath,
    )
    
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
    """验证写入的内容完整性（长度比对）"""
    try:
        with open(filepath, "r", encoding=encoding) as f:
            written = f.read()
        
        expected_len = len(content)
        actual_len = len(written)
        
        if expected_len != actual_len:
            logger.warning(
                "写入校验不一致: 期望 %d 字节, 实际 %d 字节",
                expected_len, actual_len,
            )
        else:
            logger.debug("写入校验通过: %d 字节", actual_len)
            
    except OSError as e:
        logger.warning("写入校验失败 (文件无法读取): %s", e)


def _backup_file(
    filepath: str,
    backup_dir: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
):
    """备份原文件
    
    Args:
        filepath: 原文件路径
        backup_dir: 备份目录（默认与原文件同目录的 .backup/ 子目录）
        logger: 日志记录器
    """
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


def safe_read_file(
    filepath: str,
    encoding: str = "utf-8",
    fallback_encodings: Optional[list] = None,
    logger: Optional[logging.Logger] = None,
) -> str:
    """安全读取文件内容（支持多编码回退）
    
    Args:
        filepath: 文件路径
        encoding: 首选编码
        fallback_encodings: 回退编码列表
        logger: 日志记录器
    
    Returns:
        文件内容字符串
    
    Raises:
        FileException: 读取失败时抛出
    """
    _log = logger or _get_fallback_logger()
    
    if not os.path.exists(filepath):
        raise FileException(
            message=f"文件未找到: {filepath}",
            suggestion="请检查文件路径是否正确",
            details={"filepath": filepath, "reason": "file_not_found"},
        )
    
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
    
    # 最后尝试二进制读取
    try:
        with open(filepath, "rb") as f:
            raw = f.read()
        return raw.decode("utf-8", errors="replace")
    except OSError as e:
        raise FileException(
            message=f"文件读取失败: {filepath}",
            suggestion="请检查文件是否存在且可读",
            details={"filepath": filepath},
            original=e,
        )


def _get_fallback_logger() -> logging.Logger:
    """获取备用日志记录器"""
    return logging.getLogger("FileUtils")
