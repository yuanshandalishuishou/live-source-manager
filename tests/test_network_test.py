"""测试 app/scripts.py 中的网络测试函数

注意：原 network_test.py 已被合并到 app/scripts.py。
"""

import sys
import os
from app.scripts import test_basic_connectivity, test_proxy_connection
from app.scripts import test_container_network, test_nginx_service


def test_network_test_module_import():
    """验证 network_test 函数可以从 app.scripts 导入"""
    from app.scripts import network_test as module
    assert module is not None


def test_network_test_has_expected_functions():
    """验证 network_test 包含预期的函数"""
    assert callable(test_basic_connectivity)
    assert callable(test_proxy_connection)
    assert callable(test_container_network)
    assert callable(test_nginx_service)
