"""
app.config 模块单元测试

覆盖：Config 类初始化、配置读写、默认值、类型转换、配置文件解析。
"""

import os

import pytest
from app.config import Config


@pytest.fixture
def temp_config_file(tmp_path):
    """创建临时配置文件"""
    config_path = str(tmp_path / 'test_config.ini')
    content = """
[Sources]
local_dirs = ./config/sources
online_urls = 
github_sources = 
max_concurrent = 20

[Logging]
level = INFO
file = ./logs/app.log
max_size_mb = 10
backup_count = 5

[Network]
proxy_enabled = False
proxy_url = 
proxy_username = 
proxy_password = 

[Test]
timeout = 10
max_connections = 50
ffmpeg_path = ffprobe

[Output]
output_dir = ./www/output
m3u_filename = playlist.m3u
txt_filename = playlist.txt
generate_m3u = True
generate_txt = True
ua_position = m3u

[UserAgents]
list = Mozilla/5.0
"""
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return config_path


@pytest.fixture
def config_instance(temp_config_file):
    """创建 Config 实例（禁用 SQLite，直接从 INI 读取）"""
    cfg = Config(config_path=temp_config_file)
    # 测试环境禁用 SQLite，确保从 INI 文件读取
    cfg._from_sqlite = False
    cfg._models = None
    # 覆盖 _get_config_dict，跳过 SQLite 直接读 INI
    cfg._get_config_dict = cfg._load_from_ini
    return cfg


# ── Config 初始化 ──────────────────────────────


class TestConfigInit:
    """Config 类初始化"""

    def test_init_with_config_file(self, temp_config_file):
        cfg = Config(config_path=temp_config_file)
        assert cfg is not None

    def test_init_creates_default_if_missing(self, tmp_path):
        """配置文件不存在时创建默认配置"""
        config_path = str(tmp_path / 'nonexistent.ini')
        cfg = Config(config_path=config_path)
        # 应该创建了默认配置文件
        assert os.path.exists(config_path)

    def test_config_path_stored(self, temp_config_file):
        cfg = Config(config_path=temp_config_file)
        assert cfg.config_path == temp_config_file


# ── 配置读取 ──────────────────────────────────


class TestConfigRead:
    """配置读取"""

    def test_get_string_value(self, config_instance):
        val = config_instance.get('Sources', 'local_dirs', './config/sources')
        assert val == './config/sources'

    def test_getint_value(self, config_instance):
        val = config_instance.getint('Sources', 'max_concurrent', 10)
        assert val == 20

    def test_getboolean_value(self, config_instance):
        val = config_instance.getboolean('Network', 'proxy_enabled', True)
        assert val is False

    def test_getboolean_true(self, tmp_path):
        config_path = str(tmp_path / 'bool_test.ini')
        with open(config_path, 'w') as f:
            f.write('[Test]\nenabled = True\n')
        cfg = Config(config_path=config_path)
        assert cfg.getboolean('Test', 'enabled', False) is True

    def test_get_with_default(self, config_instance):
        """不存在的键返回默认值"""
        val = config_instance.get('Sources', 'nonexistent_key', 'default_val')
        assert val == 'default_val'

    def test_getint_with_default(self, config_instance):
        val = config_instance.getint('Sources', 'nonexistent', 42)
        assert val == 42

    def test_getboolean_with_default(self, config_instance):
        val = config_instance.getboolean('Sources', 'nonexistent', True)
        assert val is True

    def test_items_returns_dict(self, config_instance):
        """items() 返回 section 的键值对"""
        section = config_instance.items('Logging')
        assert section is not None
        assert 'level' in section
        assert section['level'] == 'INFO'


# ── 配置写入 ──────────────────────────────────


class TestConfigWrite:
    """配置写入"""

    def test_set_value(self, config_instance, temp_config_file):
        config_instance.set('Sources', 'max_concurrent', '50')
        # 重新读取确认
        cfg2 = Config(config_path=temp_config_file)
        assert cfg2.getint('Sources', 'max_concurrent', 10) == 50

    def test_set_creates_section(self, config_instance):
        """设置不存在的 section 时自动创建"""
        config_instance.set('NewSection', 'key', 'value')
        val = config_instance.get('NewSection', 'key', '')
        assert val == 'value'

    def test_set_persists_to_file(self, config_instance, temp_config_file):
        config_instance.set('Test', 'timeout', '30')
        # 文件中应该有更新后的值
        cfg2 = Config(config_path=temp_config_file)
        assert cfg2.getint('Test', 'timeout', 10) == 30


# ── 类型转换 ──────────────────────────────────


class TestConfigTypeConversion:
    """配置值类型转换"""

    def test_getint_from_string(self, tmp_path):
        config_path = str(tmp_path / 'types.ini')
        with open(config_path, 'w') as f:
            f.write('[Test]\nvalue = 123\n')
        cfg = Config(config_path=config_path)
        assert cfg.getint('Test', 'value', 0) == 123

    def test_getint_invalid_returns_default(self, tmp_path):
        config_path = str(tmp_path / 'types.ini')
        with open(config_path, 'w') as f:
            f.write('[Test]\nvalue = not_a_number\n')
        cfg = Config(config_path=config_path)
        assert cfg.getint('Test', 'value', 99) == 99

    def test_getboolean_various_values(self, tmp_path):
        config_path = str(tmp_path / 'bools.ini')
        with open(config_path, 'w') as f:
            f.write('[Test]\nv_true = True\nv_false = False\nv_1 = 1\nv_0 = 0\nv_yes = yes\nv_no = no\n')
        cfg = Config(config_path=config_path)
        assert cfg.getboolean('Test', 'v_true', False) is True
        assert cfg.getboolean('Test', 'v_false', True) is False
        assert cfg.getboolean('Test', 'v_1', False) is True
        assert cfg.getboolean('Test', 'v_0', True) is False


# ── UserAgents ────────────────────────────────


class TestConfigUserAgents:
    """UserAgents 配置"""

    def test_get_user_agents(self, config_instance):
        uas = config_instance.get_user_agents()
        # get_user_agents 返回 dict（键为 UA 名称/索引，值为 UA 字符串）
        assert isinstance(uas, (dict, list))
        if isinstance(uas, dict):
            assert len(uas) >= 1
        else:
            assert len(uas) >= 1
