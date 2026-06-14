import pytest
import sys
sys.path.insert(0, 'app')
from error_handler import ErrorStats, catch_exception, format_error_response
from exceptions import LsmError, ConfigError

class TestErrorStats:
    def test_record_and_summary(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("测试错误"), module="test_module")
        summary = stats.get_summary()
        assert summary["total_count"] > 0
    
    def test_record_multiple(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("错误1"), module="m1")
        stats.record(ConfigError("错误2"), module="m2")
        assert stats.get_summary()["total_count"] == 2
    
    def test_reset(self):
        stats = ErrorStats(window_minutes=60)
        stats.record(ConfigError("错误"), module="m")
        stats.reset()
        assert stats.get_summary()["total_count"] == 0

class TestFormatErrorResponse:
    def test_format_basic(self):
        err = ConfigError("配置错误", suggestion="检查config.ini")
        resp = format_error_response(err)
        assert resp["success"] == False
        assert resp["error"]["error_code"] == 1001
        assert resp["error"]["suggestion"] == "检查config.ini"
    
    def test_format_with_traceback(self):
        try:
            raise ValueError("原始错误")
        except ValueError as e:
            err = ConfigError("包装错误", original=e)
            resp = format_error_response(err, include_traceback=True)
            assert resp["success"] == False
            assert "traceback" in resp
