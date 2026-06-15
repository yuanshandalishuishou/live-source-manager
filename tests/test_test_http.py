"""测试 app/scripts.py 中的 HTTP 测试函数

注意：原 test_http.py 已被合并到 app/scripts.py。
"""

import sys
import os
from app.scripts import test_http_service as scripts_test_http_service


def test_test_http_module_import():
    """验证 test_http 函数可以从 app.scripts 导入"""
    from app.scripts import test_http as module
    assert module is not None


def test_test_http_has_expected_functions():
    """验证 test_http 包含预期的函数"""
    from app.scripts import test_http_service as _srv
    assert callable(_srv)
