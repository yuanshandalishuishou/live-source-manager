# -*- coding: utf-8 -*-
"""
测试配置管理模块（config_manager模块）
"""

import os
import sys
import tempfile
import logging
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from config_manager import Config, Logger


# ========== Config 测试 ==========

class TestConfigLoading:
    """测试配置加载"""

    def test_config_load_normal(self):
        """测试正常加载配置文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, encoding='utf-8') as f:
            f.write("""[Sources]
local_dirs = /config/sources
online_urls = https://example.com/list.m3u

[Testing]
timeout = 15
concurrent_threads = 20
cache_ttl = 60
enable_speed_test = True
speed_test_duration = 5

[Filter]
max_latency = 3000
min_bitrate = 200
must_hd = False
must_4k = False
min_speed = 50
min_resolution = 1080p
max_resolution = 4k
resolution_filter_mode = min_only

[Output]
filename = tv.m3u
group_by = country
include_failed = False
max_sources_per_channel = 5
enable_filter = True

[Logging]
level = DEBUG
file = /tmp/test_app.log
max_size = 20
backup_count = 10

[UserAgents]
ua_position = extinf
ua_enabled = False

[Network]
proxy_enabled = False
ipv6_enabled = False
""")
            tmp_path = f.name

        try:
            config = Config(config_path=tmp_path)
            assert config is not None

            # 验证各配置项
            testing = config.get_testing_params()
            assert testing['timeout'] == 15
            assert testing['concurrent_threads'] == 20
            assert testing['cache_ttl'] == 60
            assert testing['enable_speed_test'] is True
            assert testing['speed_test_duration'] == 5

            filter_params = config.get_filter_params()
            assert filter_params['max_latency'] == 3000
            assert filter_params['min_bitrate'] == 200
            assert filter_params['min_speed'] == 50
            assert filter_params['min_resolution'] == '1080p'
            assert filter_params['resolution_filter_mode'] == 'min_only'

            output = config.get_output_params()
            assert output['filename'] == 'tv.m3u'
            assert output['group_by'] == 'country'
            assert output['max_sources_per_channel'] == 5
            assert output['enable_filter'] is True

            logging_cfg = config.get_logging_config()
            assert logging_cfg['level'] == 'DEBUG'
            assert logging_cfg['backup_count'] == 10

            assert config.get_ua_position() == 'extinf'
            assert config.is_ua_enabled() is False

            network = config.get_network_config()
            assert network['proxy_enabled'] is False
            assert network['ipv6_enabled'] is False

            sources = config.get_sources()
            assert 'local_dirs' in sources
            assert 'online_urls' in sources
        finally:
            os.unlink(tmp_path)

    def test_config_load_default_on_missing_file(self):
        """测试文件不存在时创建默认配置"""
        tmp_path = "/tmp/test_nonexistent_config_xyz.ini"
        # 确保文件不存在
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

        config = Config(config_path=tmp_path)
        assert config is not None
        # 应自动创建默认配置
        assert os.path.exists(tmp_path)

        # 验证默认值
        testing = config.get_testing_params()
        assert testing['timeout'] == 10
        assert testing['concurrent_threads'] == 30

        filter_params = config.get_filter_params()
        assert filter_params['min_speed'] == 40

        # 清理
        os.unlink(tmp_path)

    def test_config_load_with_bom(self):
        """测试带BOM头的配置文件"""
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.ini', delete=False) as f:
            f.write(b'\xef\xbb\xbf')  # BOM
            f.write(b"""[Sources]
local_dirs = /config/sources

[Testing]
timeout = 20
""")
            tmp_path = f.name

        try:
            config = Config(config_path=tmp_path)
            assert config.get_testing_params()['timeout'] == 20
        finally:
            os.unlink(tmp_path)

    def test_config_load_empty_file_creates_default(self):
        """测试空配置文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, encoding='utf-8') as f:
            f.write("")
            tmp_path = f.name

        try:
            config = Config(config_path=tmp_path)
            # 空文件 -> 尝试解析失败 -> create_default_config 覆盖默认值
            assert config.get_testing_params()['timeout'] == 10
        finally:
            os.unlink(tmp_path)

    def test_config_load_partial_sections(self):
        """测试部分配置存在"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, encoding='utf-8') as f:
            f.write("""[Testing]
timeout = 8
concurrent_threads = 10
""")
            tmp_path = f.name

        try:
            config = Config(config_path=tmp_path)
            # 明确配置的用配置值
            assert config.get_testing_params()['timeout'] == 8
            # 缺失的用默认值
            filter_params = config.get_filter_params()
            assert filter_params['max_latency'] == 5000
            assert filter_params['min_speed'] == 40
        finally:
            os.unlink(tmp_path)


class TestLoggingConfig:
    """测试日志配置"""

    def test_logging_config_default(self):
        """测试默认日志配置"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, encoding='utf-8') as f:
            f.write("[Logging]\n")
            f.write("level = INFO\n")
            f.write("file = /tmp/test_app.log\n")
            f.write("max_size = 10\n")
            f.write("backup_count = 5\n")
            tmp_path = f.name

        try:
            config = Config(config_path=tmp_path)
            logging_cfg = config.get_logging_config()
            assert logging_cfg['level'] == 'INFO'
            assert logging_cfg['max_size'] == 10
            assert logging_cfg['backup_count'] == 5
            assert logging_cfg['enable_console'] is True
        finally:
            os.unlink(tmp_path)

    def test_logging_config_missing_section(self):
        """测试缺少Logging section"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, encoding='utf-8') as f:
            f.write("[Testing]\ntimeout=10\n")
            tmp_path = f.name

        try:
            config = Config(config_path=tmp_path)
            logging_cfg = config.get_logging_config()
            # 应返回默认值
            assert 'level' in logging_cfg
            assert logging_cfg['level'] == 'INFO'
            assert logging_cfg['enable_console'] is True
        finally:
            os.unlink(tmp_path)

    def test_logger_setup(self):
        """测试Logger初始化"""
        log_cfg = {
            'level': 'DEBUG',
            'file': '/tmp/test_lsm_log.log',
            'max_size': 5,
            'backup_count': 3,
            'enable_console': True
        }
        logger_obj = Logger(log_cfg)
        assert logger_obj.logger is not None
        assert logger_obj.logger.level == logging.DEBUG
        assert logger_obj.logger.name == 'LiveSourceManager'

    def test_logger_setup_invalid_level(self):
        """测试无效的日志级别"""
        log_cfg = {
            'level': 'INVALID_LEVEL',
            'file': '',
            'enable_console': True
        }
        logger_obj = Logger(log_cfg)
        assert logger_obj.logger is not None
        # 应使用默认INFO级别
        assert logger_obj.logger.level == logging.INFO


class TestConfigItemParsing:
    """测试配置项解析"""

    @pytest.fixture
    def sample_config(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False, encoding='utf-8') as f:
            f.write("""[Testing]
timeout = 30
concurrent_threads = 50
cache_ttl = 300
enable_speed_test = false
speed_test_duration = 10

[Filter]
max_latency = 2000
min_bitrate = 500
must_hd = true
must_4k = false
min_speed = 100
min_resolution = 720p
max_resolution = 1080p
resolution_filter_mode = range

[Output]
filename = mylist.m3u
group_by = source
include_failed = true
max_sources_per_channel = 10
enable_filter = true

[UserAgents]
ua_position = url
ua_enabled = true

[Network]
proxy_enabled = true
proxy_type = http
proxy_host = 10.0.0.1
proxy_port = 3128
proxy_username = user
proxy_password = pass
ipv6_enabled = true

[GitHub]
api_url = https://api.github.com/
api_token = mytoken123
rate_limit = 1000
""")
            tmp_path = f.name
        config = Config(config_path=tmp_path)
        yield config
        os.unlink(tmp_path)

    def test_boolean_parsing_must_hd(self, sample_config):
        """布尔值解析：must_hd=true"""
        params = sample_config.get_filter_params()
        assert params['must_hd'] is True

    def test_boolean_parsing_must_4k(self, sample_config):
        """布尔值解析：must_4k=false"""
        params = sample_config.get_filter_params()
        assert params['must_4k'] is False

    def test_boolean_parsing_speed_test_disabled(self, sample_config):
        """布尔值解析：enable_speed_test=false"""
        params = sample_config.get_testing_params()
        assert params['enable_speed_test'] is False

    def test_int_parsing_concurrent_threads(self, sample_config):
        """整型值解析"""
        params = sample_config.get_testing_params()
        assert params['concurrent_threads'] == 50
        assert params['timeout'] == 30

    def test_string_parsing_resolutions(self, sample_config):
        """字符串值解析"""
        params = sample_config.get_filter_params()
        assert params['min_resolution'] == '720p'
        assert params['max_resolution'] == '1080p'
        assert params['resolution_filter_mode'] == 'range'

    def test_string_parsing_ua_position(self, sample_config):
        """UA位置解析"""
        assert sample_config.get_ua_position() == 'url'
        assert sample_config.is_ua_enabled() is True

    def test_network_config_parsing(self, sample_config):
        """网络配置解析"""
        network = sample_config.get_network_config()
        assert network['proxy_enabled'] is True
        assert network['proxy_type'] == 'http'
        assert network['proxy_host'] == '10.0.0.1'
        assert network['proxy_port'] == 3128
        assert network['proxy_username'] == 'user'
        assert network['proxy_password'] == 'pass'
        assert network['ipv6_enabled'] is True

    def test_github_config_parsing(self, sample_config):
        """GitHub配置解析"""
        gh = sample_config.get_github_config()
        assert gh['api_token'] == 'mytoken123'
        assert gh['rate_limit'] == 1000

    def test_sources_parsing(self, sample_config):
        """源配置解析"""
        sources = sample_config.get_sources()
        assert 'local_dirs' in sources
        assert 'online_urls' in sources
        # 没有配置Sources段时，使用默认值
        assert isinstance(sources['online_urls'], list)

    def test_output_parsing_include_failed(self, sample_config):
        """输出配置：include_failed"""
        output = sample_config.get_output_params()
        assert output['include_failed'] is True
        assert output['max_sources_per_channel'] == 10
        assert output['filename'] == 'mylist.m3u'
        assert output['group_by'] == 'source'
        assert output['enable_filter'] is True

    def test_http_server_config(self, sample_config):
        """HTTP服务器配置"""
        http = sample_config.get_http_server_config()
        # Nginx版默认禁用
        assert http['enabled'] is False
