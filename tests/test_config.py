"""
app.config 模块单元测试（纯 SQLite 版）

覆盖：Config 类初始化、配置读写、默认值、类型转换。
不再依赖 config.ini，所有配置读写直接走 SQLite app_config 表。
"""

import datetime
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def _setup_temp_db(tmpdir: str) -> None:
    """在临时目录创建最小化的测试数据库。

    覆写 models.DATA_DIR 和 models.DB_PATH，然后调用 init_db 建表。
    需要先设置 WEB_ADMIN_PASSWORD 环境变量。
    """
    import web.models as _m

    _m.DATA_DIR = tmpdir
    _m.DB_PATH = os.path.join(tmpdir, 'web.db')
    if 'WEB_ADMIN_PASSWORD' not in os.environ:
        os.environ['WEB_ADMIN_PASSWORD'] = 'TestAdminPw1!'
    _m.init_db(admin_password=os.environ['WEB_ADMIN_PASSWORD'])


def _seed_app_config(entries: dict[str, str]) -> None:
    """将配置键值对写入 app_config 表"""
    import web.models as _m

    conn = _m.get_conn()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for key, value in entries.items():
        conn.execute(
            'INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, ?)',
            (key, value, now),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def temp_db(tmp_path):
    """创建临时数据库并种子测试配置"""
    tmpdir = str(tmp_path)
    _setup_temp_db(tmpdir)

    # 种子测试配置数据
    _seed_app_config(
        {
            'Sources.local_dirs': './config/sources',
            'Sources.online_urls': '',
            'Sources.github_sources': '',
            'Sources.max_concurrent': '20',
            'Logging.level': 'INFO',
            'Logging.file': './logs/app.log',
            'Logging.max_size_mb': '10',
            'Logging.backup_count': '5',
            'Network.proxy_enabled': 'False',
            'Network.proxy_url': '',
            'Network.proxy_username': '',
            'Network.proxy_password': '',
            'Test.timeout': '10',
            'Test.max_connections': '50',
            'Test.ffmpeg_path': 'ffprobe',
            'Output.output_dir': './www/output',
            'Output.m3u_filename': 'playlist.m3u',
            'Output.txt_filename': 'playlist.txt',
            'Output.generate_m3u': 'True',
            'Output.generate_txt': 'True',
            'Output.ua_position': 'm3u',
            'UserAgents.list': 'Mozilla/5.0',
            'UserAgents.ua_position': 'extinf',
            'UserAgents.ua_enabled': 'False',
            'Testing.timeout': '10',
            'Testing.concurrent_threads': '40',
            'Testing.cache_ttl': '120',
            'Testing.enable_speed_test': 'True',
            'Testing.speed_test_duration': '6',
            'Filter.max_latency': '4000',
            'Filter.min_bitrate': '80',
            'Filter.must_hd': 'False',
            'Filter.must_4k': 'False',
            'Filter.min_speed': '50',
            'Filter.min_resolution': '360p',
            'Filter.max_resolution': '4k',
            'Filter.resolution_filter_mode': 'range',
            'HTTPServer.enabled': 'True',
            'HTTPServer.host': '0.0.0.0',
            'HTTPServer.fileshare_port': '12345',
            'HTTPServer.manager_port': '23456',
            'HTTPServer.document_root': './www/output',
            'GitHub.api_url': 'https://api.github.com',
            'GitHub.api_token': '',
            'GitHub.rate_limit': '5000',
        }
    )
    return tmpdir


@pytest.fixture
def config_instance(temp_db):
    """创建 Config 实例（纯 SQLite 模式）"""
    from app.config import Config

    return Config()


# ── Config 初始化 ──────────────────────────────


class TestConfigInit:
    """Config 类初始化"""

    def test_init_without_path(self):
        """Config() 不需参数（纯 SQLite）"""
        from app.config import Config

        cfg = Config()
        assert cfg is not None


# ── 配置读取 ──────────────────────────────────


class TestConfigRead:
    """配置读取"""

    def test_get_string_value(self, config_instance):
        val = config_instance.get('Sources', 'local_dirs', './fallback')
        assert val == './config/sources'

    def test_getint_value(self, config_instance):
        val = config_instance.getint('Sources', 'max_concurrent', 10)
        assert val == 20

    def test_getboolean_value(self, config_instance):
        val = config_instance.getboolean('Network', 'proxy_enabled', True)
        assert val is False

    def test_getboolean_true(self, temp_db):
        _seed_app_config({'Test.enabled': 'True'})
        from app.config import Config

        cfg = Config()
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

    def test_set_value(self, config_instance):
        config_instance.set('Sources', 'max_concurrent', '50')
        # 重新读取确认
        from app.config import Config

        cfg2 = Config()
        assert cfg2.getint('Sources', 'max_concurrent', 10) == 50

    def test_set_creates_key(self, config_instance):
        """设置不存在的 key 时自动创建"""
        config_instance.set('NewSection', 'key', 'value')
        val = config_instance.get('NewSection', 'key', '')
        assert val == 'value'

    def test_set_persists(self, config_instance):
        config_instance.set('Test', 'timeout', '30')
        from app.config import Config

        cfg2 = Config()
        assert cfg2.getint('Test', 'timeout', 10) == 30


# ── 类型转换 ──────────────────────────────────


class TestConfigTypeConversion:
    """配置值类型转换"""

    def test_getint_from_string(self, temp_db):
        _seed_app_config({'Test.value': '123'})
        from app.config import Config

        cfg = Config()
        assert cfg.getint('Test', 'value', 0) == 123

    def test_getint_invalid_returns_default(self, temp_db):
        _seed_app_config({'Test.value': 'not_a_number'})
        from app.config import Config

        cfg = Config()
        assert cfg.getint('Test', 'value', 99) == 99

    def test_getboolean_various_values(self, temp_db):
        _seed_app_config(
            {
                'Test.v_true': 'True',
                'Test.v_false': 'False',
                'Test.v_1': '1',
                'Test.v_0': '0',
                'Test.v_yes': 'yes',
                'Test.v_no': 'no',
            }
        )
        from app.config import Config

        cfg = Config()
        assert cfg.getboolean('Test', 'v_true', False) is True
        assert cfg.getboolean('Test', 'v_false', True) is False
        assert cfg.getboolean('Test', 'v_1', False) is True
        assert cfg.getboolean('Test', 'v_0', True) is False


# ── UserAgents ────────────────────────────────


class TestConfigUserAgents:
    """UserAgents 配置"""

    def test_get_user_agents(self, config_instance):
        uas = config_instance.get_user_agents()
        assert isinstance(uas, (dict, list))
        if isinstance(uas, dict):
            assert len(uas) >= 1
        else:
            assert len(uas) >= 1
