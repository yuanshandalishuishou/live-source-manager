# -*- coding: utf-8 -*-
"""
测试 .gitignore 文件
"""

import os
import pytest


class TestGitignore:
    """测试 .gitignore 文件内容"""

    PROJECT_ROOT = '/opt/dev/lsm/live-source-manager-main'

    def test_gitignore_exists(self):
        """.gitignore 需存在于项目根目录"""
        assert os.path.exists(os.path.join(self.PROJECT_ROOT, '.gitignore'))

    def test_gitignore_contains_pycache(self):
        """.gitignore 应包含 __pycache__"""
        content = open(os.path.join(self.PROJECT_ROOT, '.gitignore')).read()
        assert '__pycache__' in content
