"""测试 app/test_http.py 模块的导入和基本结构

注意：test_http.py 是运维诊断脚本，内建 sys.path.insert(0, '/app')，
在 pytest 环境下需要调整 PYTHONPATH 才能正确导入。
"""

import sys
import os


def _import_test_http():
    """辅助：将 app 目录插入路径后导入 test_http"""
    app_dir = os.path.join(os.path.dirname(__file__), '..', 'app')
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    import test_http
    return test_http


def test_test_http_module_import():
    """验证 test_http.py 可以被成功导入"""
    th = _import_test_http()
    assert th is not None


def test_test_http_has_expected_functions():
    """验证 test_http 包含预期的函数"""
    th = _import_test_http()
    assert hasattr(th, 'test_http_service')
    assert callable(th.test_http_service)
