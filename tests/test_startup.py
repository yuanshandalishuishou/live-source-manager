# -*- coding: utf-8 -*-
"""
测试健康检查脚本和Dockerfile相关
"""

import os
import pytest


class TestHealthCheck:
    """测试healthcheck.sh脚本"""

    PROJECT_ROOT = '/opt/dev/lsm/live-source-manager-main'

    def test_healthcheck_script_exists(self):
        """healthcheck.sh 需存在于项目根目录"""
        assert os.path.exists(os.path.join(self.PROJECT_ROOT, 'healthcheck.sh'))

    def test_healthcheck_script_is_executable(self):
        """healthcheck.sh 应可执行"""
        path = os.path.join(self.PROJECT_ROOT, 'healthcheck.sh')
        assert os.access(path, os.X_OK)

    def test_healthcheck_syntax(self):
        """检查shell脚本语法"""
        import subprocess
        result = subprocess.run(
            ['bash', '-n', os.path.join(self.PROJECT_ROOT, 'healthcheck.sh')],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"Shell syntax error: {result.stderr}"
