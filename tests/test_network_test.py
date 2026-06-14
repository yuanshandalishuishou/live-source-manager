"""测试 app/network_test.py 模块的导入和基本结构

注意：network_test.py 是运维诊断脚本，内建 sys.path.insert(0, '/app')，
在 pytest 环境下需要调整 PYTHONPATH 才能正确导入。
"""

import sys
import os


def _import_network_test():
    """辅助：将 app 目录插入路径后导入 network_test"""
    app_dir = os.path.join(os.path.dirname(__file__), '..', 'app')
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    import network_test
    return network_test


def test_network_test_module_import():
    """验证 network_test.py 可以被成功导入"""
    nwt = _import_network_test()
    assert nwt is not None


def test_network_test_has_expected_functions():
    """验证 network_test 包含预期的函数"""
    nwt = _import_network_test()
    assert hasattr(nwt, 'test_basic_connectivity')
    assert callable(nwt.test_basic_connectivity)
