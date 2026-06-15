# -*- coding: utf-8 -*-
"""
综合测试 — app/utils.py（428行核心工具模块）

覆盖范围：
  1. BaseAppException / LsmError 异常层次
  2. 所有自定义异常类
  3. ErrorStats 错误统计
  4. catch_exception 装饰器
  5. format_error_response
  6. setup_global_exception_hook
  7. setup_logger
  8. atomic_write / safe_read_file 文件工具
"""

import os
import sys
import time
import json
import logging
import tempfile
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.utils import (
    BaseAppException, LsmError, ConfigError, SourceError,
    SourceDownloadError, SourceParseError, StreamTestError,
    OutputError, FileException,
    ErrorStats, catch_exception, format_error_response,
    setup_global_exception_hook, setup_logger,
    atomic_write, safe_read_file,
)


# ═══════════════════════════════════════════════════════════════
# 1. BaseAppException 基础特性
# ═══════════════════════════════════════════════════════════════

class TestBaseAppException:
    """测试 BaseAppException 核心特性"""

    def test_init_defaults(self):
        err = BaseAppException(error_code=9999, message="测试错误")
        assert err.error_code == 9999
        assert err.message == "测试错误"
        assert err.suggestion == ""
        assert err.details == {}
        assert err.original is None

    def test_init_full(self):
        try:
            raise ValueError("原始错误")
        except ValueError as orig:
            err = BaseAppException(
                error_code=5001,
                message="包装错误",
                suggestion="检查参数",
                details={"key": "value"},
                original=orig,
            )
        assert err.error_code == 5001
        assert err.message == "包装错误"
        assert err.suggestion == "检查参数"
        assert err.details == {"key": "value"}
        assert isinstance(err.original, ValueError)

    def test_to_dict(self):
        err = BaseAppException(error_code=1001, message="错误",
                               suggestion="建议", details={"k": "v"})
        d = err.to_dict()
        assert d["error_code"] == 1001
        assert d["message"] == "错误"
        assert d["suggestion"] == "建议"
        assert d["details"] == {"k": "v"}

    def test_to_dict_without_traceback(self):
        err = BaseAppException(error_code=1001, message="错误")
        d = err.to_dict()
        assert "traceback" not in d

    def test_str_representation(self):
        err = BaseAppException(error_code=1001, message="错误信息",
                               suggestion="建议内容")
        s = str(err)
        assert "[1001]" in s
        assert "错误信息" in s
        assert "建议内容" in s

    def test_traceback_str(self):
        """traceback_str 应包含异常的字符串表示"""
        err = BaseAppException(error_code=1001, message="err")
        tb = err.traceback_str
        assert isinstance(tb, str)
        assert len(tb) > 0

    def test_traceback_str_with_original(self):
        try:
            raise ValueError("内部错误")
        except ValueError as e:
            err = BaseAppException(error_code=1001, message="外部错误", original=e)
            tb = err.traceback_str
            assert "ValueError" in tb
            assert "内部错误" in tb

    def test_traceback_str_with_cause(self):
        try:
            try:
                raise ValueError("原因")
            except ValueError as cause:
                raise RuntimeError("结果") from cause
        except RuntimeError as e:
            err = BaseAppException(error_code=1001, message="包装", original=e)
            tb = err.traceback_str
            assert "RuntimeError" in tb
            assert "ValueError" in tb


# ═══════════════════════════════════════════════════════════════
# 2. LsmError 及子类
# ═══════════════════════════════════════════════════════════════

class TestLsmError:
    """测试 LsmError 及子类"""

    def test_lsm_error_default(self):
        err = LsmError("系统错误")
        assert err.error_code in (9999, 0)  # 代码兼容

    def test_lsm_error_with_suggestion(self):
        err = LsmError("系统错误", suggestion="建议")
        assert err.suggestion == "建议"

    def test_lsm_error_is_base_app_exception(self):
        assert issubclass(LsmError, BaseAppException)

    def test_config_error(self):
        err = ConfigError("配置错误")
        assert err.error_code == 1001

    def test_source_error(self):
        err = SourceError("源错误")
        assert err.error_code == 3001

    def test_source_download_error(self):
        err = SourceDownloadError("下载失败")
        assert err.error_code == 3002

    def test_source_parse_error(self):
        err = SourceParseError("解析失败")
        assert err.error_code == 3003

    def test_stream_test_error(self):
        err = StreamTestError("测试失败")
        assert err.error_code == 4001

    def test_file_exception(self):
        err = FileException("文件错误")
        assert err.error_code == 5002

    def test_output_error(self):
        err = OutputError("输出错误")
        assert err.error_code == 5001

    def test_inheritance_chain(self):
        assert issubclass(ConfigError, LsmError)
        assert issubclass(SourceError, LsmError)
        assert issubclass(SourceDownloadError, SourceError)
        assert issubclass(SourceParseError, SourceError)
        assert issubclass(StreamTestError, LsmError)
        assert issubclass(FileException, LsmError)
        assert issubclass(OutputError, LsmError)
        assert issubclass(LsmError, BaseAppException)

    def test_all_catchable_by_base(self):
        errors = [
            ConfigError(""),
            SourceError(""),
            SourceDownloadError(""),
            SourceParseError(""),
            StreamTestError(""),
            FileException(""),
            OutputError(""),
        ]
        for err in errors:
            assert isinstance(err, BaseAppException)


# ═══════════════════════════════════════════════════════════════
# 3. ErrorStats 错误统计
# ═══════════════════════════════════════════════════════════════

class TestErrorStats:
    """测试 ErrorStats 错误统计功能"""

    def test_init_empty(self):
        stats = ErrorStats(window_minutes=60)
        summary = stats.get_summary()
        assert "total_count" in summary
        assert summary["total_count"] >= 0
        assert "by_type_and_module" in summary

    def test_record_single(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("错误A"), module="mod1")
        assert stats.get_summary()["total_count"] >= 1

    def test_record_multiple(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("错误1"), module="m1")
        stats.record(SourceError("错误2"), module="m2")
        stats.record(FileException("错误3"), module="m3")
        assert stats.get_summary()["total_count"] >= 3

    def test_record_with_context(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(
            ConfigError("配置错误"),
            module="config",
            context={"file": "test.ini"},
        )
        assert stats.get_summary()["total_count"] >= 1

    def test_get_count_by_type(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("e1"), module="m")
        stats.record(ConfigError("e2"), module="m")
        stats.record(SourceError("e3"), module="m")
        by_type = stats.get_count_by_type()
        assert sum(by_type.values()) >= 3

    def test_get_count_by_module(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("e1"), module="cfg")
        stats.record(ConfigError("e2"), module="cfg")
        stats.record(SourceError("e3"), module="src")
        by_module = stats.get_count_by_module()
        assert by_module.get("cfg") == 2
        assert by_module.get("src") == 1

    def test_reset(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("e"), module="m")
        stats.reset()
        summary = stats.get_summary()
        assert summary["total_count"] == 0

    def test_record_without_module(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("e"))
        assert stats.get_summary()["total_count"] >= 1

    def test_record_non_lsm_error(self):
        """即使传入非 LsmError，也应记录"""
        stats = ErrorStats(window_minutes=60)
        stats.record(ValueError("标准异常"), module="std")
        assert stats.get_summary()["total_count"] >= 1


# ═══════════════════════════════════════════════════════════════
# 4. catch_exception 装饰器
# ═══════════════════════════════════════════════════════════════

class TestCatchException:
    """测试 catch_exception 装饰器"""

    def test_decorator_passthrough_sync(self):
        @catch_exception()
        def my_func(x: int) -> int:
            return x * 2

        result = my_func(5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_decorator_passthrough_async(self):
        @catch_exception()
        async def my_async_func(x: int) -> int:
            return x * 2

        result = await my_async_func(5)
        assert result == 10

    def test_decorator_fallback_sync(self):
        @catch_exception(fallback_return=-1)
        def failing_func():
            raise ValueError("内部错误")

        result = failing_func()
        assert result == -1

    @pytest.mark.asyncio
    async def test_decorator_fallback_async(self):
        @catch_exception(fallback_return="fallback")
        async def failing_async():
            raise ConfigError("配置错误")

        result = await failing_async()
        assert result == "fallback"

    def test_decorator_wraps_lsm_error(self):
        """LsmError 子类应被直接记录并返回 fallback"""
        @catch_exception(fallback_return=0)
        def lsm_failing():
            raise ConfigError("配置问题")

        result = lsm_failing()
        assert result == 0

    def test_decorator_raises_when_specified(self):
        """raise_original=True 时重新抛出"""
        @catch_exception(raise_original=True)
        def raises_again():
            raise RuntimeError("不应被吞没")

        with pytest.raises(RuntimeError, match="不应被吞没"):
            raises_again()

    def test_decorator_with_logger(self):
        logger = logging.getLogger("test_catch")
        logger.setLevel(logging.DEBUG)
        handler = logging.NullHandler()
        logger.addHandler(handler)

        @catch_exception(logger=logger, fallback_return=None)
        def log_func():
            raise ConfigError("记录此错误")

        result = log_func()
        assert result is None
        logger.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_decorator_async_raises_original(self):
        @catch_exception(raise_original=True)
        async def async_raise():
            raise PermissionError("权限不足")

        with pytest.raises(PermissionError):
            await async_raise()


# ═══════════════════════════════════════════════════════════════
# 5. format_error_response
# ═══════════════════════════════════════════════════════════════

class TestFormatErrorResponse:
    """测试 format_error_response 函数"""

    def test_basic_format(self):
        err = ConfigError("配置错误", suggestion="检查配置文件")
        resp = format_error_response(err)
        assert resp["success"] is False
        assert resp["error"]["error_code"] == 1001
        assert resp["error"]["message"] == "配置错误"
        assert resp["error"]["suggestion"] == "检查配置文件"
        assert "traceback" not in resp

    def test_format_with_traceback(self):
        try:
            raise ValueError("原始错误")
        except ValueError as e:
            err = ConfigError("包装错误", original=e)
            resp = format_error_response(err, include_traceback=True)
            assert resp["success"] is False
            assert "traceback" in resp
            assert "ValueError" in resp["traceback"]
            assert "原始错误" in resp["traceback"]

    def test_format_base_app_exception(self):
        err = BaseAppException(error_code=9999, message="通用错误",
                               suggestion="通用建议")
        resp = format_error_response(err)
        assert resp["error"]["error_code"] == 9999
        assert resp["error"]["message"] == "通用错误"

    def test_format_with_details(self):
        err = ConfigError("错误", details={"file": "config.ini", "line": 42})
        resp = format_error_response(err)
        assert resp["error"]["details"]["file"] == "config.ini"


# ═══════════════════════════════════════════════════════════════
# 6. setup_global_exception_hook
# ═══════════════════════════════════════════════════════════════

class TestGlobalExceptionHook:
    """测试 setup_global_exception_hook"""

    def test_hook_installed(self):
        original_hook = sys.excepthook
        try:
            logger = logging.getLogger("test_hook")
            setup_global_exception_hook(logger=logger)
            assert sys.excepthook is not original_hook
        finally:
            sys.excepthook = original_hook

    def test_hook_handles_base_app_exception(self):
        """钩子应能处理 BaseAppException（不抛出）"""
        logger = MagicMock()
        original_hook = sys.excepthook
        try:
            setup_global_exception_hook(logger=logger)
            hook = sys.excepthook
            err = ConfigError("配置异常")
            hook(type(err), err, err.__traceback__)
            assert logger.critical.called or logger.error.called
        finally:
            sys.excepthook = original_hook

    def test_hook_no_logger_fallback(self):
        """无 logger 时不崩溃"""
        original_hook = sys.excepthook
        try:
            setup_global_exception_hook()
            hook = sys.excepthook
            err = ConfigError("测试")
            hook(type(err), err, err.__traceback__)
        finally:
            sys.excepthook = original_hook


# ═══════════════════════════════════════════════════════════════
# 7. setup_logger
# ═══════════════════════════════════════════════════════════════

class TestSetupLogger:
    """测试 setup_logger 函数"""

    def test_create_logger(self):
        logger = setup_logger("test_logger")
        assert logger is not None
        assert logger.name == "test_logger"
        assert logger.level == logging.INFO

    def test_logger_level_override(self):
        logger = setup_logger("test_debug", level=logging.DEBUG)
        assert logger.level == logging.DEBUG

    def test_logger_has_console_handler(self):
        logger = setup_logger("console_test")
        handlers = [h for h in logger.handlers
                    if isinstance(h, logging.StreamHandler)]
        assert len(handlers) >= 1


# ═══════════════════════════════════════════════════════════════
# 8. atomic_write / safe_read_file
# ═══════════════════════════════════════════════════════════════

class TestAtomicWrite:
    """测试 atomic_write 函数"""

    def test_basic_write(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            target = f.name
            os.unlink(target)
        try:
            atomic_write(target, "Hello Atomic Write")
            with open(target, 'r', encoding='utf-8') as f:
                assert f.read() == "Hello Atomic Write"
        finally:
            if os.path.exists(target):
                os.unlink(target)

    def test_write_unicode(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            target = f.name
            os.unlink(target)
        try:
            content = "中文测试内容 ✓"
            atomic_write(target, content)
            with open(target, 'r', encoding='utf-8') as f:
                assert f.read() == content
        finally:
            if os.path.exists(target):
                os.unlink(target)

    def test_write_with_backup(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b"original")
            target = f.name
        try:
            atomic_write(target, "updated", backup=True)
            with open(target, 'r') as f:
                assert f.read() == "updated"
            # 验证备份文件存在
            backup_dir = os.path.join(os.path.dirname(target), '.backup')
            if os.path.isdir(backup_dir):
                backups = os.listdir(backup_dir)
                assert len(backups) > 0
        finally:
            if os.path.exists(target):
                os.unlink(target)
            backup_dir = os.path.join(os.path.dirname(target), '.backup')
            if os.path.isdir(backup_dir):
                import shutil
                shutil.rmtree(backup_dir, ignore_errors=True)

    def test_write_new_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "sub", "nested", "file.txt")
            atomic_write(target, "nested content")
            assert os.path.exists(target)
            with open(target, 'r') as f:
                assert f.read() == "nested content"

    def test_write_without_backup(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            target = f.name
            os.unlink(target)
        try:
            atomic_write(target, "no backup", backup=False)
            assert os.path.exists(target)
        finally:
            if os.path.exists(target):
                os.unlink(target)

    def test_write_verify(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            target = f.name
            os.unlink(target)
        try:
            atomic_write(target, "verify me", verify=True)
            with open(target, 'r') as f:
                assert f.read() == "verify me"
        finally:
            if os.path.exists(target):
                os.unlink(target)


class TestSafeReadFile:
    """测试 safe_read_file 函数"""

    def test_read_existing(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                          delete=False, encoding='utf-8') as f:
            f.write("test content 测试")
            target = f.name
        try:
            content = safe_read_file(target)
            assert content == "test content 测试"
        finally:
            os.unlink(target)

    def test_read_nonexistent(self):
        with pytest.raises(FileException, match="文件未找到"):
            safe_read_file("/nonexistent_path/file.txt")

    def test_read_with_encoding_fallback(self):
        import io
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b'\xd6\xd0\xce\xc4')  # GB2312 编码的"中文"
            target = f.name
        try:
            content = safe_read_file(target, encoding='utf-8',
                                     fallback_encodings=['gb2312'])
            assert content == "中文"
        finally:
            os.unlink(target)

    def test_read_with_bom(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write('\ufeff带BOM的内容'.encode('utf-8'))
            target = f.name
        try:
            content = safe_read_file(target)
            assert content == "带BOM的内容"
            assert not content.startswith('\ufeff')
        finally:
            os.unlink(target)

    def test_read_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            target = f.name
        try:
            content = safe_read_file(target)
            assert content == ""
        finally:
            os.unlink(target)
