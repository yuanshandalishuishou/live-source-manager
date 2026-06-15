# -*- coding: utf-8 -*-
"""
测试自定义异常层次体系（exceptions模块）
"""

import pytest
import sys

sys.path.insert(0, 'app')

from app.utils import LsmError, ConfigError, SourceError, \
    SourceDownloadError, SourceParseError, StreamTestError, OutputError


class TestLsmErrorHierarchy:
    """测试异常继承层次是否正确"""

    def test_lsm_error_is_base(self):
        """验证所有异常都继承自LsmError"""
        assert issubclass(ConfigError, LsmError)
        assert issubclass(SourceError, LsmError)
        assert issubclass(StreamTestError, LsmError)
        assert issubclass(OutputError, LsmError)

    def test_source_error_hierarchy(self):
        """验证SourceError子类层次"""
        assert issubclass(SourceDownloadError, SourceError)
        assert issubclass(SourceParseError, SourceError)

    def test_exception_has_message(self):
        """异常应能携带错误消息和错误码"""
        e = ConfigError("配置加载失败")
        assert "[1001] 配置加载失败" in str(e)
        assert "建议" in str(e)
        assert e.error_code == 1001

    def test_all_exceptions_catchable_by_base(self):
        """所有异常都应以LsmError捕获"""
        errors = [
            ConfigError("cfg"),
            SourceDownloadError("dl"),
            SourceParseError("parse"),
            StreamTestError("test"),
            OutputError("out"),
        ]
        for err in errors:
            assert isinstance(err, LsmError)

    def test_exception_chaining(self):
        """异常链应保持原始异常"""
        try:
            try:
                raise ValueError("原始错误")
            except ValueError as orig:
                raise SourceDownloadError("下载失败") from orig
        except LsmError as e:
            assert isinstance(e.__cause__, ValueError)


class TestExceptionIntegration:
    """测试自定义异常在各模块中的使用"""

    def test_config_manager_raises_config_error(self):
        """测试config_manager在配置损坏时抛出ConfigError"""
        import sys
        sys.path.insert(0, 'app')
        import tempfile
        import os
        from config_manager import Config
        from app.utils import ConfigError

        # 创建一个格式错误的配置文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, encoding='utf-8') as f:
            f.write("这不是合法的INI格式\n{{{bad\n")
            bad_path = f.name

        try:
            with pytest.raises(ConfigError):
                cfg = Config(config_path=bad_path)
        finally:
            os.unlink(bad_path)

    def test_source_parse_error_raised(self):
        """确认SourceParseError可被抛出具链式异常"""
        from app.utils import SourceParseError
        try:
            try:
                raise ValueError("bad format")
            except ValueError as orig:
                raise SourceParseError("无法解析M3U文件") from orig
        except SourceParseError as e:
            assert "无法解析M3U文件" in str(e)
            assert isinstance(e.__cause__, ValueError)

    def test_output_error_in_file_write(self):
        """OutputError在文件写入失败时使用"""
        from app.utils import OutputError
        try:
            try:
                raise PermissionError("Permission denied")
            except PermissionError as orig:
                raise OutputError("无法写入输出文件") from orig
        except OutputError as e:
            assert "无法写入输出文件" in str(e)

class TestBaseAppExceptionFeatures:
    """测试BaseAppException（方案B合并后）的特性"""
    
    def test_error_code_present(self):
        from app.utils import ConfigError, SourceDownloadError, StreamTestError, OutputError
        assert ConfigError("test").error_code == 1001
        assert SourceDownloadError("test").error_code == 3002
        assert StreamTestError("test").error_code == 4001
        assert OutputError("test").error_code == 5001
    
    def test_suggestion_present(self):
        from app.utils import ConfigError
        err = ConfigError("配置错误", suggestion="检查config.ini")
        assert err.suggestion == "检查config.ini"
    
    def test_to_dict(self):
        from app.utils import ConfigError
        err = ConfigError("配置错误", suggestion="检查config.ini")
        d = err.to_dict()
        assert d["error_code"] == 1001
        assert d["message"] == "配置错误"
        assert d["suggestion"] == "检查config.ini"
    
    def test_traceback_str_with_original(self):
        from app.utils import ConfigError
        try:
            raise ValueError("原始错误")
        except ValueError as e:
            err = ConfigError("包装错误", original=e)
            tb = err.traceback_str
            assert "ValueError" in tb
            assert "原始错误" in tb
    
    def test_str_representation(self):
        from app.utils import ConfigError
        err = ConfigError("配置错误", suggestion="检查config.ini")
        s = str(err)
        assert "1001" in s
        assert "配置错误" in s
        assert "检查config.ini" in s
