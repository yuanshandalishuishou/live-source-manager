# -*- coding: utf-8 -*-
"""
测试配置热加载机制
"""

import pytest
import os
import tempfile
import time
import sys

sys.path.insert(0, 'app')

from config_manager import Config


class TestConfigReload:
    """测试Config.check_reload热加载功能"""

    @pytest.fixture
    def temp_config(self):
        """创建临时配置文件用于测试"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini',
                                          delete=False, encoding='utf-8') as f:
            f.write("[Settings]\nkey1=value1\n")
            config_path = f.name
        yield config_path
        os.unlink(config_path)

    def test_config_has_reload_method(self, temp_config):
        """Config应有check_reload方法"""
        cfg = Config(config_path=temp_config, reload_interval=0)
        assert hasattr(cfg, 'check_reload')
        assert callable(cfg.check_reload)

    def test_reload_detects_change(self, temp_config):
        """文件修改后check_reload应返回True并更新配置"""
        cfg = Config(config_path=temp_config, reload_interval=0)
        # 初始值
        assert cfg.config['Settings']['key1'] == 'value1'
        # 修改文件
        time.sleep(0.1)  # 确保mtime变化
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.write("[Settings]\nkey1=value2\n")
        # 触发重载
        reloaded = cfg.check_reload()
        assert reloaded is True
        assert cfg.config['Settings']['key1'] == 'value2'

    def test_reload_no_change(self, temp_config):
        """文件未修改时check_reload应返回False"""
        cfg = Config(config_path=temp_config, reload_interval=0)
        time.sleep(0.1)
        # 先确认重载以记录mtime
        cfg.check_reload()  # 第一次
        # 不修改，再调用
        reloaded = cfg.check_reload()
        assert reloaded is False

    def test_reload_respects_interval(self, temp_config):
        """在reload_interval内不应重复重载"""
        cfg = Config(config_path=temp_config, reload_interval=999)
        # 先触发一次刷新令_last_mtime同步
        cfg.check_reload()
        time.sleep(0.1)
        with open(temp_config, 'w', encoding='utf-8') as f:
            f.write("[Settings]\nkey1=value3\n")
        # 间隔内不应重载
        reloaded = cfg.check_reload()
        assert reloaded is False  # 因为还没到reload_interval
