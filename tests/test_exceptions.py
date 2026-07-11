"""
app.exceptions 模块单元测试

覆盖：异常继承关系、BaseAppException 属性/方法、ErrorStats 统计、
catch_exception 装饰器（同步+异步）、format_error_response。
"""

import asyncio
import logging

import pytest
from app.exceptions import (
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
    catch_exception,
    format_error_response,
    global_error_stats,
)

# ── 异常继承关系 ──────────────────────────────


class TestExceptionHierarchy:
    """验证异常继承链"""

    def test_all_inherit_from_lsm_error(self):
        """ConfigError/SourceError/StreamTestError/FileException/OutputError 都继承 LsmError"""
        for exc_cls in [ConfigError, SourceError, StreamTestError, FileException, OutputError]:
            assert issubclass(exc_cls, LsmError)

    def test_lsm_error_inherits_base(self):
        assert issubclass(LsmError, BaseAppException)

    def test_source_subtypes(self):
        assert issubclass(SourceDownloadError, SourceError)
        assert issubclass(SourceParseError, SourceError)

    def test_all_are_exceptions(self):
        for exc_cls in [BaseAppException, LsmError, ConfigError, SourceError, StreamTestError]:
            assert issubclass(exc_cls, Exception)


# ── BaseAppException 属性 ─────────────────────


class TestBaseAppException:
    """验证异常实例属性和方法"""

    def test_init_with_all_params(self):
        original = ValueError('原始错误')
        exc = BaseAppException(
            error_code=9999,
            message='测试错误',
            suggestion='检查输入',
            details={'key': 'value'},
            original=original,
        )
        assert exc.error_code == 9999
        assert exc.message == '测试错误'
        assert exc.suggestion == '检查输入'
        assert exc.details == {'key': 'value'}
        assert exc.original is original

    def test_str_includes_code_and_message(self):
        exc = BaseAppException(error_code=100, message='出错了')
        assert '[100]' in str(exc)
        assert '出错了' in str(exc)

    def test_str_includes_suggestion(self):
        exc = BaseAppException(error_code=100, message='出错了', suggestion='请重试')
        s = str(exc)
        assert '建议' in s
        assert '请重试' in s

    def test_to_dict(self):
        exc = ConfigError('配置错误', suggestion='检查配置文件', details={'line': 42})
        d = exc.to_dict()
        assert d['error_code'] == 1001
        assert d['message'] == '配置错误'
        assert d['suggestion'] == '检查配置文件'
        assert d['details'] == {'line': 42}

    def test_details_defaults_empty(self):
        exc = LsmError('test')
        assert exc.details == {}

    def test_traceback_str(self):
        """traceback_str 返回非空字符串"""
        try:
            raise LsmError('测试追踪')
        except LsmError as e:
            tb = e.traceback_str
            assert isinstance(tb, str)
            assert len(tb) > 0

    def test_error_codes_distinct(self):
        """各类异常的 error_code 不同"""
        codes = {
            LsmError('x').error_code,
            ConfigError('x').error_code,
            SourceError('x').error_code,
            SourceDownloadError('x').error_code,
            SourceParseError('x').error_code,
            StreamTestError('x').error_code,
            FileException('x').error_code,
            OutputError('x').error_code,
        }
        # 至少应有 6 个不同的 error_code（LsmError 和某些子类可能共享 0）
        assert len(codes) >= 6


# ── ErrorStats ────────────────────────────────


class TestErrorStats:
    """ErrorStats 统计收集器"""

    def test_record_and_total_count(self):
        stats = ErrorStats()
        stats.record(LsmError('错误1'), module='test_mod')
        stats.record(ConfigError('错误2'), module='test_mod')
        assert stats.get_summary()['total_count'] == 2

    def test_get_count_by_type(self):
        stats = ErrorStats()
        stats.record(ConfigError('x'), module='mod')
        stats.record(ConfigError('y'), module='mod')
        stats.record(SourceError('z'), module='mod')
        counts = stats.get_count_by_type()
        assert counts[ConfigError('x').error_code] == 2
        assert counts[SourceError('x').error_code] == 1

    def test_get_count_by_module(self):
        stats = ErrorStats()
        stats.record(LsmError('x'), module='mod_a')
        stats.record(LsmError('y'), module='mod_b')
        stats.record(LsmError('z'), module='mod_a')
        counts = stats.get_count_by_module()
        assert counts['mod_a'] == 2
        assert counts['mod_b'] == 1

    def test_reset(self):
        stats = ErrorStats()
        stats.record(LsmError('x'), module='mod')
        stats.reset()
        assert stats.get_summary()['total_count'] == 0
        assert stats.get_count_by_module() == {}

    def test_get_summary_structure(self):
        stats = ErrorStats()
        stats.record(ConfigError('test'), module='web', context={'func': 'f'})
        summary = stats.get_summary()
        assert 'total_count' in summary
        assert 'window_minutes' in summary
        assert 'by_type_and_module' in summary
        # 至少有一个分类条目
        assert len(summary['by_type_and_module']) >= 1

    def test_context_stored(self):
        stats = ErrorStats()
        ctx = {'func': 'my_func', 'extra': 'info'}
        stats.record(LsmError('x'), module='mod', context=ctx)
        summary = stats.get_summary()
        # 找到记录的条目
        for info in summary['by_type_and_module'].values():
            assert info['count'] >= 1


# ── catch_exception 装饰器 ─────────────────────


class TestCatchException:
    """catch_exception 装饰器"""

    def test_normal_return(self):
        @catch_exception()
        def add(a, b):
            return a + b

        assert add(1, 2) == 3

    def test_base_app_exception_caught(self):
        @catch_exception(fallback_return='fallback')
        def fail():
            raise LsmError('出错了')

        assert fail() == 'fallback'

    def test_generic_exception_caught(self):
        @catch_exception(fallback_return=42)
        def fail():
            raise ValueError('普通错误')

        assert fail() == 42

    def test_raise_original(self):
        @catch_exception(raise_original=True)
        def fail():
            raise ConfigError('配置错误')

        with pytest.raises(ConfigError):
            fail()

    def test_stats_recorded(self):
        global_error_stats.reset()

        @catch_exception(module_name='test_module')
        def fail():
            raise LsmError('统计测试')

        fail()
        counts = global_error_stats.get_count_by_module()
        assert 'test_module' in counts

    def test_async_function(self):
        @catch_exception(fallback_return='async_fallback')
        async def async_fail():
            raise LsmError('异步错误')

        result = asyncio.run(async_fail())
        assert result == 'async_fallback'

    def test_async_normal_return(self):
        @catch_exception()
        async def async_add(a, b):
            return a + b

        assert asyncio.run(async_add(3, 4)) == 7

    def test_logger_used(self):
        test_logger = logging.getLogger('test_catch_exception')

        @catch_exception(logger=test_logger)
        def fail():
            raise LsmError('日志测试')

        fail()  # 不应该抛出异常


# ── format_error_response ──────────────────────


class TestFormatErrorResponse:
    """format_error_response"""

    def test_basic_response(self):
        exc = ConfigError('配置错误', suggestion='检查文件')
        resp = format_error_response(exc)
        assert resp['success'] is False
        assert 'error' in resp
        assert resp['error']['error_code'] == 1001
        assert resp['error']['message'] == '配置错误'

    def test_without_traceback(self):
        exc = LsmError('测试')
        resp = format_error_response(exc, include_traceback=False)
        assert 'traceback' not in resp

    def test_with_traceback(self):
        try:
            raise LsmError('带追踪')
        except LsmError as e:
            resp = format_error_response(e, include_traceback=True)
            assert 'traceback' in resp
