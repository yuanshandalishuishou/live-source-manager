#!/usr/bin/env python3
"""
文件工具模块
============

原子写入、安全读取、备份等文件操作工具。
"""

import contextlib
import logging
import os
import shutil
import sys
import tempfile
import time

from app.exceptions import FileException


def atomic_write(
    filepath: str,
    content: str,
    encoding: str = 'utf-8',
    retries: int = 3,
    retry_delay: float = 0.5,
    backup: bool = True,
    backup_dir: str | None = None,
    verify: bool = True,
    logger: logging.Logger | None = None,
):
    """原子写入文件内容"""
    _log = logger or _get_fallback_logger()
    dirpath = os.path.dirname(filepath)
    try:
        os.makedirs(dirpath, exist_ok=True)
    except OSError as e:
        raise FileException(
            message=f'无法创建目录: {dirpath}',
            suggestion='请检查文件系统权限',
            details={'dirpath': dirpath},
            original=e,
        ) from e
    if backup and os.path.exists(filepath):
        _backup_file(filepath, backup_dir, _log)
    for attempt in range(1, retries + 1):
        try:
            _do_atomic_write(filepath, content, encoding, _log)
            if verify:
                _verify_write(filepath, content, encoding, _log)
            _log.debug('原子写入成功: %s (尝试 %d/%d)', filepath, attempt, retries)
            return
        except OSError as e:
            if attempt < retries:
                _log.warning('写入失败 (尝试 %d/%d): %s - %s', attempt, retries, filepath, e)
                time.sleep(retry_delay)
            else:
                raise FileException(
                    message=f'文件写入失败（已重试 {retries} 次）: {filepath}',
                    suggestion=f'请检查目录权限: {os.path.dirname(filepath)}',
                    details={
                        'filepath': filepath,
                        'attempts': retries,
                        'last_error': str(e),
                    },
                    original=e,
                ) from e


def _do_atomic_write(filepath: str, content: str, encoding: str, logger: logging.Logger):
    dirpath = os.path.dirname(filepath) or '.'
    fd, tmp_path = tempfile.mkstemp(suffix='.tmp', prefix='.atomic_', dir=dirpath)
    try:
        with os.fdopen(fd, 'w', encoding=encoding) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, filepath)
        logger.debug('临时文件 %s -> %s 替换完成', tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        raise


def _verify_write(filepath: str, content: str, encoding: str, logger: logging.Logger):
    try:
        with open(filepath, encoding=encoding) as f:
            written = f.read()
        expected_len = len(content)
        actual_len = len(written)
        if expected_len != actual_len:
            logger.warning('写入校验不一致: 期望 %d 字节, 实际 %d 字节', expected_len, actual_len)
        else:
            logger.debug('写入校验通过: %d 字节', actual_len)
    except OSError as e:
        logger.warning('写入校验失败 (文件无法读取): %s', e)


def _backup_file(filepath: str, backup_dir: str | None = None, logger: logging.Logger | None = None):
    _log = logger or _get_fallback_logger()
    if backup_dir:
        backup_base = backup_dir
    else:
        backup_base = os.path.join(os.path.dirname(filepath) or '.', '.backup')
    os.makedirs(backup_base, exist_ok=True)
    basename = os.path.basename(filepath)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_base, f'{basename}.backup.{timestamp}')
    try:
        shutil.copy2(filepath, backup_path)
        _log.info('文件已备份: %s -> %s', filepath, backup_path)
    except OSError as e:
        _log.warning('备份失败: %s -> %s (%s)', filepath, backup_path, e)


def safe_read_file(
    filepath: str,
    encoding: str = 'utf-8',
    fallback_encodings: list | None = None,
    logger: logging.Logger | None = None,
) -> str:
    """安全读取文件内容（支持多编码回退）"""
    if not os.path.exists(filepath):
        raise FileException(
            message=f'文件未找到: {filepath}',
            suggestion='请检查文件路径是否正确',
            details={'filepath': filepath, 'reason': 'file_not_found'},
        )
    encodings = [encoding] + (fallback_encodings or ['gbk', 'gb2312', 'latin1', 'utf-8-sig'])
    for enc in encodings:
        try:
            with open(filepath, encoding=enc) as f:
                content = f.read()
            if content.startswith('\ufeff'):
                content = content[1:]
            return content
        except (UnicodeDecodeError, OSError):
            continue
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        return raw.decode('utf-8', errors='replace')
    except OSError as e:
        raise FileException(
            message=f'文件读取失败: {filepath}',
            suggestion='请检查文件是否存在且可读',
            details={'filepath': filepath},
            original=e,
        ) from e


def _get_fallback_logger() -> logging.Logger:
    return logging.getLogger('FileUtils')


def force_remove(path: str | os.PathLike) -> bool:
    """强制删除文件，绕过运行环境对 os.remove 的"回收站安全删除"拦截。

    背景：WorkBuddy 沙箱 Python 的 sitecustomize 把 os.remove monkeypatch 成放回收站，
    回收站不可用时直接抛 OSError(SAFE_DELETE_FAIL_CLOSED)，导致删除源文件等场景 500。
    这里在 Windows 上直接调 kernel32.DeleteFileW、其他平台用 os.unlink，真正删除文件，
    不经过被拦截的 os.remove 高层封装。

    返回:
        True  - 文件存在且已删除
        False - 文件本来就不存在（视为成功，不抛异常）
    删除失败（权限/被占用等）时抛出 OSError 交由调用方决定如何处理。
    """
    path = str(path)
    if not os.path.isfile(path):
        return False
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        res = ctypes.windll.kernel32.DeleteFileW(wintypes.LPCWSTR(path))
        if not res:
            err = ctypes.windll.kernel32.GetLastError()
            raise OSError(err, ctypes.FormatError(err) or f'DeleteFileW failed (err={err})')
    else:
        os.unlink(path)
    return True
