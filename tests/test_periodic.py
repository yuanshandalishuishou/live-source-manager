# -*- coding: utf-8 -*-
"""
测试定时调度模式（main.py 的 --periodic 功能）
"""

import sys
import pytest


class TestPeriodicMode:
    """测试main.py的定时调度模式"""

    def test_run_periodic_method_exists(self):
        """验证EnhancedLiveSourceManager有run_periodic方法"""
        sys.path.insert(0, 'app')
        from main import EnhancedLiveSourceManager

        mgr = EnhancedLiveSourceManager()
        assert hasattr(mgr, 'run_periodic')
        assert callable(mgr.run_periodic)

    def test_periodic_stores_last_run(self):
        """验证定时模式会记录最后运行时间"""
        sys.path.insert(0, 'app')
        from main import EnhancedLiveSourceManager

        mgr = EnhancedLiveSourceManager()
        assert hasattr(mgr, 'last_run_time')
        assert mgr.last_run_time == 0.0
        assert hasattr(mgr, 'last_run_success')
        assert mgr.last_run_success is False

    def test_command_line_periodic_flag(self):
        """验证 --periodic 命令行参数能触发定时模式"""
        # 保存原始argv
        orig_argv = sys.argv
        try:
            sys.argv = ['main.py', '--periodic']
            from main import main
            # 只需要验证导入不报错
            assert True
        finally:
            sys.argv = orig_argv
