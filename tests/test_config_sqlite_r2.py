#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二轮迭代 — 全量配置SQLite化验证（加强版）
覆盖 15+ 新增场景：
  2.1 加密功能测试（5项）
  2.2 Config类SQLite读取测试（4项）
  2.3 双读路径测试（2项）
  2.4 首次运行初始化测试（加强，3项）
  2.5 后台模块兼容性测试（3项）

依赖 tests/conftest.py 统一的临时目录和数据库。
"""
import os
import sys
import json
import tempfile
import shutil
import configparser

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from web import models
from web.webapp import app, read_config, read_section, write_config
from web.webapp import CONFIG_PATH
from web.auth import _get_csrf_token
from web.crypto_utils import encrypt_value, decrypt_value, is_encrypted, is_sensitive_key

# 密码必须与 conftest.py 保持一致
ADMIN_PASSWORD = 'TestAdminPw1!'
VIEWER_PASSWORD = 'TestViewerPw1!'

client_pool = []


def _client():
    c = TestClient(app)
    client_pool.append(c)
    return c


def _admin_login(client=None):
    if client is None:
        client = _client()
    resp = client.post('/api/auth/login', data={
        'username': 'admin',
        'password': ADMIN_PASSWORD,
    })
    assert resp.status_code == 200
    sid = resp.cookies.get('session')
    csrf = _get_csrf_token(sid)
    return client, {'session_id': sid, 'csrf_token': csrf}


# ══════════════════════════════════════════════════
# 2.1 加密功能测试
# ══════════════════════════════════════════════════

class TestEncryptDecrypt:
    """加解密功能验证"""

    def test_encrypt_decrypt_basic(self):
        """加密再解密得到原文"""
        plaintext = 'my_secret_password_123!'
        encrypted = encrypt_value(plaintext)
        assert encrypted != plaintext
        assert encrypted.startswith('ENC:')
        decrypted = decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_encrypt_sensitive_key(self):
        """Network.proxy_password 存入后被加密"""
        # 清除已存在数据
        models._execute("DELETE FROM app_config WHERE key = 'Network.proxy_password'")
        models._execute("DELETE FROM app_config WHERE key = 'Network.proxy_host'")

        # 写入敏感字段
        models.set_app_config('Network.proxy_password', 'my_proxy_pw_456')
        models.set_app_config('Network.proxy_host', '10.0.0.100')

        # 从数据库查询原始存储值（不经过解密）
        conn = models.get_conn()
        row_pw = conn.execute(
            "SELECT value FROM app_config WHERE key = 'Network.proxy_password'"
        ).fetchone()
        row_host = conn.execute(
            "SELECT value FROM app_config WHERE key = 'Network.proxy_host'"
        ).fetchone()

        # proxy_password 应被加密存储（以 ENC: 开头）
        assert row_pw['value'].startswith('ENC:'), \
            f"敏感字段 proxy_password 应被加密，实际: {row_pw['value']!r}"
        # proxy_host 非敏感字段不应被加密
        assert not row_host['value'].startswith('ENC:'), \
            f"非敏感字段 proxy_host 不应被加密，实际: {row_host['value']!r}"

        # 通过 get_app_config 读取到的是解密后的明文
        assert models.get_app_config('Network.proxy_password') == 'my_proxy_pw_456'
        assert models.get_app_config('Network.proxy_host') == '10.0.0.100'

        # 清理
        models._execute("DELETE FROM app_config WHERE key = 'Network.proxy_password'")
        models._execute("DELETE FROM app_config WHERE key = 'Network.proxy_host'")

    def test_decrypt_non_sensitive(self):
        """非敏感字段不加密 — 存入和取出均原样"""
        key = 'Logging.test_decrypt_ns'
        value = 'some_random_value_789'
        models.set_app_config(key, value)

        conn = models.get_conn()
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = ?", (key,)
        ).fetchone()
        # 非敏感字段不应加密
        assert not row['value'].startswith('ENC:'), \
            f"非敏感字段不应加密，实际: {row['value']!r}"
        # 读出时值一致
        assert models.get_app_config(key) == value

        models._execute("DELETE FROM app_config WHERE key = ?", (key,))

    def test_empty_encrypt(self):
        """空字符串加密"""
        # encrypt_value 空字符串返回原值
        assert encrypt_value('') == ''
        assert encrypt_value(None) is None

        # 空字符串存入 SQLite
        models.set_app_config('TestSection.empty_field', '')
        result = models.get_app_config('TestSection.empty_field')
        assert result == ''
        models._execute("DELETE FROM app_config WHERE key = 'TestSection.empty_field'")

    def test_encrypt_idempotent(self):
        """已加密的值再次写入不会被二次加密"""
        key = 'Network.proxy_password'
        original_pw = 'double_encrypt_test_pw'

        # 第一次写入
        models.set_app_config(key, original_pw)
        val_after_first = models.get_app_config(key)
        assert val_after_first == original_pw

        # 第二次写入相同值（加密函数内检测到 ENC: 前缀，不二次加密）
        models.set_app_config(key, original_pw)
        val_after_second = models.get_app_config(key)
        assert val_after_second == original_pw

        # 验证数据库里只有一个 ENC: 前缀（没有被二次包装）
        conn = models.get_conn()
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = ?", (key,)
        ).fetchone()
        enc_val = row['value']
        assert enc_val.startswith('ENC:'), "应被加密"
        # 确保不是 ENC:ENC:...（二次加密）
        assert not enc_val.startswith('ENC:ENC:'), \
            f"值被二次加密了: {enc_val!r}"

        models._execute("DELETE FROM app_config WHERE key = ?", (key,))


# ══════════════════════════════════════════════════
# 2.2 Config类SQLite读取测试
# ══════════════════════════════════════════════════

@pytest.fixture
def setup_sqlite_config():
    """向 SQLite 写入一组已知配置供测试"""
    test_data = {
        'Testing.timeout': '30',
        'Testing.concurrent_threads': '50',
        'Testing.cache_ttl': '120',
        'Testing.enable_speed_test': 'True',
        'Testing.speed_test_duration': '8',
        'Filter.max_latency': '3000',
        'Filter.min_bitrate': '200',
        'Filter.must_hd': 'True',
        'Filter.min_speed': '60',
        'Filter.min_resolution': '1080p',
        'Output.filename': 'test_output.m3u',
        'Output.group_by': 'source',
        'Logging.level': 'WARN',
        'Network.proxy_enabled': 'False',
        'Sources.local_dirs': '/opt/test/sources',
    }
    # 清空已有数据
    models._execute("DELETE FROM app_config")
    for k, v in test_data.items():
        models.set_app_config(k, v)
    yield
    # 清理
    models._execute("DELETE FROM app_config")


class TestConfigClassSQLite:
    """Config类SQLite读取测试"""

    def test_config_get_from_sqlite(self, setup_sqlite_config):
        """Config().get() 从SQLite读取值"""
        from app.config_manager import Config
        # 不需要传递真实的 config_path，因为 SQLite 优先
        config = Config(config_path="/tmp/nonexistent_test_config.ini")

        # 从 SQLite 读取
        assert config.get('Testing', 'timeout') == '30'
        assert config.get('Testing', 'concurrent_threads') == '50'
        assert config.get('Testing', 'cache_ttl') == '120'
        assert config.get('Filter', 'max_latency') == '3000'
        assert config.get('Filter', 'min_bitrate') == '200'
        assert config.get('Filter', 'must_hd') == 'True'
        assert config.get('Output', 'filename') == 'test_output.m3u'
        assert config.get('Logging', 'level') == 'WARN'
        assert config.get('Network', 'proxy_enabled') == 'False'

    def test_config_get_nonexistent(self, setup_sqlite_config):
        """不存在的配置返回默认值"""
        from app.config_manager import Config
        config = Config(config_path="/tmp/nonexistent_test_config.ini")

        # 不存在的 key 返回默认值
        assert config.get('Testing', 'nonexistent_key') is None
        assert config.get('NonexistentSection', 'any_key') is None
        assert config.get('Testing', 'nonexistent', 'my_default') == 'my_default'

        # getint 返回默认 int
        assert config.getint('Testing', 'nonexistent_int', 42) == 42
        # getboolean 返回默认 bool
        assert config.getboolean('Testing', 'nonexistent_bool', True) is True

    def test_config_items_proper(self, setup_sqlite_config):
        """Config().items(section) 返回正确格式"""
        from app.config_manager import Config
        config = Config(config_path="/tmp/nonexistent_test_config.ini")

        items = config.items('Testing')
        assert isinstance(items, dict)
        assert items['timeout'] == '30'
        assert items['concurrent_threads'] == '50'
        assert items['cache_ttl'] == '120'
        assert items['enable_speed_test'] == 'True'
        assert items['speed_test_duration'] == '8'
        assert len(items) == 5  # 全部 Testing 配置项

        # 不存在的 section 返回空字典
        assert config.items('NonexistentSection') == {}

    def test_config_sections(self, setup_sqlite_config):
        """列出所有section名称"""
        from app.config_manager import Config
        config = Config(config_path="/tmp/nonexistent_test_config.ini")

        sections = config.sections()
        assert isinstance(sections, list)
        assert 'Testing' in sections
        assert 'Filter' in sections
        assert 'Output' in sections
        assert 'Logging' in sections
        assert 'Network' in sections
        assert 'Sources' in sections

        # SQLite 无数据时 sections 也为空
        models._execute("DELETE FROM app_config")
        empty_sections = config.sections()
        assert empty_sections == []


# ══════════════════════════════════════════════════
# 2.3 双读路径测试
# ══════════════════════════════════════════════════

class TestDualReadPath:
    """API写入 + Config类读取的一致性验证"""

    def test_sqlite_config_via_api_and_config_class(self):
        """通过API写入，Config类读取到同样的值"""
        # 确保数据库干净
        models._execute("DELETE FROM app_config")

        client, auth = _admin_login()

        # 通过 API 写入配置
        api_config = {
            'Testing': {'timeout': '25', 'concurrent_threads': '100'},
            'Output': {'filename': 'api_test.m3u', 'group_by': 'category'},
        }
        resp = client.put(
            '/api/config',
            json=api_config,
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200

        # 通过 Config 类读取 — 应得到同样的值
        from app.config_manager import Config
        config = Config(config_path="/tmp/nonexistent_test.ini")

        assert config.get('Testing', 'timeout') == '25'
        assert config.get('Testing', 'concurrent_threads') == '100'
        assert config.get('Output', 'filename') == 'api_test.m3u'
        assert config.get('Output', 'group_by') == 'category'

        # get_testing_params 便捷方法也应返回正确的值
        testing = config.get_testing_params()
        assert testing['timeout'] == 25
        assert testing['concurrent_threads'] == 100

        models._execute("DELETE FROM app_config")

    def test_config_fallback_to_ini(self):
        """SQLite无数据时回退到INI"""
        # 确保 INI 文件存在且有内容（conftest.py 创建）
        assert os.path.exists(CONFIG_PATH), f"INI 文件不存在: {CONFIG_PATH}"

        from app.config_manager import Config
        # 创建一个新的 Config 实例，清空 SQLite 相关数据后检查回退
        # 由于 setup_sqlite_config 可能在之前已经写入数据，我们需要确保
        # 这个测试独立运行

        # 用 patch 让 Config._get_models 抛出异常（模拟 SQLite 不可用）
        original_get_models = Config._get_models

        def _raise_on_get_models(self):
            raise Exception("模拟 SQLite 不可用")

        Config._get_models = _raise_on_get_models

        try:
            config = Config(config_path=CONFIG_PATH)

            # SQLite 模拟不可用，应回退到 INI 读取
            val = config.get('Logging', 'level')
            assert val is not None, "回退到 INI 应能读取出 Logging.level"
            # conftest 中设置为 'INFO'
            assert val == 'INFO'

            val_dir = config.get('Sources', 'local_dirs')
            assert val_dir == '/config/sources'

            # items() 也应回退
            items = config.items('Logging')
            assert 'level' in items
            assert items['level'] == 'INFO'

            # sections() 应包含 INI 中的 sections
            sections = config.sections()
            assert 'Logging' in sections
            assert 'Sources' in sections
        finally:
            Config._get_models = original_get_models


# ══════════════════════════════════════════════════
# 2.4 首次运行初始化测试（加强）
# ══════════════════════════════════════════════════

class TestFirstRunInitEnhanced:
    """首次运行初始化逻辑加强验证"""

    def test_first_run_full_init(self):
        """完整初始化：DB不存在→init_db→创建ini→导入到SQLite"""
        # 使用独立的临时目录模拟首次运行
        tmp_dir = tempfile.mkdtemp(prefix='first_run_test_')
        db_path = os.path.join(tmp_dir, 'web.db')
        ini_path = os.path.join(tmp_dir, 'config.ini')

        # 注意：以下操作模拟生命周期

        # Step 1: 创建 config.ini 默认配置
        from app.config_manager import Config as AppConfig
        AppConfig.create_default_at(ini_path)
        assert os.path.exists(ini_path), "config.ini 应被创建"

        # 验证默认 config.ini 内容
        cp = configparser.ConfigParser()
        cp.read(ini_path, encoding='utf-8')
        sections = cp.sections()
        assert 'Sources' in sections
        assert 'Testing' in sections
        assert 'Logging' in sections
        assert 'Network' in sections
        assert 'Output' in sections
        assert 'Filter' in sections
        assert 'GitHub' in sections
        assert cp.get('Logging', 'level') == 'INFO'
        assert cp.get('Testing', 'timeout') == '10'

        # Step 2: 初始化 SQLite（使用独立的数据库文件路径）
        orig_db_path = models.DB_PATH
        orig_data_dir = models.DATA_DIR
        try:
            models.DB_PATH = db_path
            models.DATA_DIR = tmp_dir
            os.environ['WEB_ADMIN_PASSWORD'] = 'firstRunPw1!'
            os.environ['WEB_VIEWER_PASSWORD'] = 'firstRunPw2!'
            models.init_db('firstRunPw1!', 'firstRunPw2!')

            # Step 3: 导入 INI 到 SQLite
            count = models.import_from_ini_file(ini_path)
            assert count > 0, f"应从 INI 导入至少 1 条配置，实际: {count}"

            # 验证 SQLite 中有数据
            assert models.has_app_config_data()
            all_cfg = models.get_all_config()
            assert len(all_cfg) >= 7  # 至少 7 个 section
            assert all_cfg['Logging']['level'] == 'INFO'
            assert all_cfg['Testing']['timeout'] == '10'

            # 验证 Config 类可以正确读取
            config = AppConfig(config_path=ini_path)
            assert config.get('Testing', 'timeout') == '10'
            params = config.get_testing_params()
            assert params['timeout'] == 10
        finally:
            models.DB_PATH = orig_db_path
            models.DATA_DIR = orig_data_dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_first_run_idempotent(self):
        """再次启动不重复创建和导入"""
        # 使用独立临时目录
        tmp_dir = tempfile.mkdtemp(prefix='idempotent_test_')
        db_path = os.path.join(tmp_dir, 'web.db')
        ini_path = os.path.join(tmp_dir, 'config.ini')

        orig_db_path = models.DB_PATH
        orig_data_dir = models.DATA_DIR

        try:
            # ----- 第一次运行 -----
            models.DB_PATH = db_path
            models.DATA_DIR = tmp_dir

            from app.config_manager import Config as AppConfig
            # 创建 INI
            AppConfig.create_default_at(ini_path)
            # 初始化 DB
            models.init_db('testPw1!', 'testPw2!')
            # 导入
            first_count = models.import_from_ini_file(ini_path)
            assert first_count > 0, f"首次导入应为 {first_count}"

            first_config_count = models.get_all_config()
            first_total = sum(len(v) for v in first_config_count.values())

            # ----- 第二次运行（模拟再次启动）-----
            # 清空内存中的 SQLite 数据，但不修改数据库
            # 重新导入（幂等：INSERT OR REPLACE 不应增加记录数）
            second_count = models.import_from_ini_file(ini_path)
            assert second_count == first_count, \
                f"二次导入数量应与首次一致: {first_count} vs {second_count}"

            second_config = models.get_all_config()
            second_total = sum(len(v) for v in second_config.values())
            assert second_total == first_total, \
                f"二次导入后配置条目数不变: {first_total} vs {second_total}"

            # 验证 init_db 幂等（已有用户表，不重复创建）
            models.init_db('testPw1!', 'testPw2!')
            # 用户表不应重复插入
            conn = models.get_conn()
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            assert user_count == 2, f"用户数应为 2（admin + viewer），实际: {user_count}"
        finally:
            models.DB_PATH = orig_db_path
            models.DATA_DIR = orig_data_dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_config_ini_not_required_at_runtime(self):
        """删除config.ini后程序正常运行（SQLite已有数据）"""
        # 先确保 SQLite 有数据
        models._execute("DELETE FROM app_config")
        test_data = {
            'Testing.timeout': '15',
            'Logging.level': 'DEBUG',
            'Sources.local_dirs': '/mnt/sources',
            'Network.proxy_enabled': 'True',
        }
        for k, v in test_data.items():
            models.set_app_config(k, v)

        # 备份 INI 文件
        ini_backup = CONFIG_PATH + '.r2_backup'
        had_ini = os.path.exists(CONFIG_PATH)
        if had_ini:
            shutil.copy2(CONFIG_PATH, ini_backup)
            os.remove(CONFIG_PATH)

        try:
            assert not os.path.exists(CONFIG_PATH), "config.ini 已被删除"

            # Config 类应能完全从 SQLite 读取
            from app.config_manager import Config
            config = Config(config_path="/tmp/nonexistent_for_test.ini")

            # 从 SQLite 读取
            assert config.get('Testing', 'timeout') == '15'
            assert config.get('Logging', 'level') == 'DEBUG'
            assert config.get('Sources', 'local_dirs') == '/mnt/sources'
            assert config.get('Network', 'proxy_enabled') == 'True'

            # 便捷方法正常
            testing = config.get_testing_params()
            assert testing['timeout'] == 15
            logging_cfg = config.get_logging_config()
            assert logging_cfg['level'] == 'DEBUG'

            # API 正常读取
            cfg = read_config()
            assert 'Testing' in cfg
            assert cfg['Testing']['timeout'] == '15'
        finally:
            # 恢复 INI 文件
            if os.path.exists(ini_backup):
                shutil.copy2(ini_backup, CONFIG_PATH)
                os.remove(ini_backup)

        # 恢复数据库
        models._execute("DELETE FROM app_config")


# ══════════════════════════════════════════════════
# 2.5 后台模块兼容性测试
# ══════════════════════════════════════════════════

class TestBackendModuleCompatibility:
    """后台模块使用 Config 读取配置的兼容性验证"""

    @pytest.fixture(autouse=True)
    def setup_test_config(self):
        """为测试准备 SQLite 配置数据"""
        models._execute("DELETE FROM app_config")
        test_data = {
            'Testing.timeout': '20',
            'Testing.concurrent_threads': '15',
            'Testing.cache_ttl': '300',
            'Testing.enable_speed_test': 'False',
            'Testing.speed_test_duration': '10',
            'Testing.max_workers': '50',
            'Filter.max_latency': '4000',
            'Filter.min_bitrate': '300',
            'Filter.must_hd': 'True',
            'Filter.must_4k': 'False',
            'Filter.min_speed': '80',
            'Filter.min_resolution': '720p',
            'Filter.max_resolution': '1080p',
            'Filter.resolution_filter_mode': 'min_only',
            'Network.proxy_enabled': 'True',
            'Network.proxy_type': 'http',
            'Network.proxy_host': '192.168.10.1',
            'Network.proxy_port': '8080',
            'Network.proxy_username': 'test_user',
            'Network.proxy_password': 'test_proxy_pw_secret',
            'Network.ipv6_enabled': 'False',
            'Sources.local_dirs': '/data/sources',
            'Sources.online_urls': 'http://example.com/list.m3u',
            'Output.filename': 'output_compat.m3u',
            'Output.group_by': 'source',
            'Output.include_failed': 'True',
            'Output.max_sources_per_channel': '12',
            'Output.enable_filter': 'True',
            'Logging.level': 'WARN',
            'Logging.file': '/var/log/test_app.log',
            'Logging.max_size': '20',
            'Logging.backup_count': '10',
        }
        for k, v in test_data.items():
            models.set_app_config(k, v)
        yield
        models._execute("DELETE FROM app_config")

    def test_config_manager_get_testing_params(self):
        """Config.get_testing_params() 正常工作"""
        from app.config_manager import Config
        config = Config(config_path="/tmp/test_compat.ini")

        params = config.get_testing_params()
        assert isinstance(params, dict)
        assert params['timeout'] == 20
        assert params['concurrent_threads'] == 15
        assert params['cache_ttl'] == 300
        assert params['enable_speed_test'] is False
        assert params['speed_test_duration'] == 10
        assert params['max_workers'] == 50

        # 所有键都在
        expected_keys = {'timeout', 'concurrent_threads', 'cache_ttl',
                         'enable_speed_test', 'speed_test_duration', 'max_workers'}
        assert set(params.keys()) == expected_keys, \
            f"返回键集合不一致: {set(params.keys())}"

    def test_config_manager_get_network_config(self):
        """Config.get_network_config() 正常工作"""
        from app.config_manager import Config
        config = Config(config_path="/tmp/test_compat.ini")

        network = config.get_network_config()
        assert isinstance(network, dict)
        assert network['proxy_enabled'] is True
        assert network['proxy_type'] == 'http'
        assert network['proxy_host'] == '192.168.10.1'
        assert network['proxy_port'] == 8080
        assert network['proxy_username'] == 'test_user'
        assert network['proxy_password'] == 'test_proxy_pw_secret'
        assert network['ipv6_enabled'] is False

        # 验证敏感字段 proxy_password 被正确解密
        # 原始密码是 'test_proxy_pw_secret'
        assert network['proxy_password'] == 'test_proxy_pw_secret', \
            "proxy_password 应被解密为明文"

        # 所有键都在
        expected_keys = {'proxy_enabled', 'proxy_type', 'proxy_host', 'proxy_port',
                         'proxy_username', 'proxy_password', 'ipv6_enabled'}
        assert set(network.keys()) == expected_keys

    def test_stream_tester_config(self):
        """stream_tester 使用Config读取配置正常"""
        from app.config_manager import Config
        config = Config(config_path="/tmp/test_compat.ini")

        # stream_tester 使用 get_testing_params() 和 get_filter_params()
        testing = config.get_testing_params()
        filter_params = config.get_filter_params()

        # 验证 testing 参数
        assert testing['timeout'] == 20
        assert testing['concurrent_threads'] == 15
        assert testing['cache_ttl'] == 300
        assert testing['enable_speed_test'] is False
        assert testing['speed_test_duration'] == 10

        # 验证 filter 参数
        assert filter_params['max_latency'] == 4000
        assert filter_params['min_bitrate'] == 300
        assert filter_params['must_hd'] is True
        assert filter_params['must_4k'] is False
        assert filter_params['min_speed'] == 80
        assert filter_params['min_resolution'] == '720p'
        assert filter_params['max_resolution'] == '1080p'
        assert filter_params['resolution_filter_mode'] == 'min_only'

        # 模拟 StreamTester 实例化流程
        # stream_tester 需要通过 sys.path.insert('app') 导入
        import importlib
        from unittest.mock import patch
        # 确保 app 目录在 path 中
        app_dir = os.path.join(PROJECT_ROOT, 'app')
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)

        # 先导入 StreamTester
        from stream_tester import StreamTester
        # 用 patch.object 代替 path-based patch
        with patch.object(StreamTester, '_verify_ffprobe'):
            mock_logger = MagicMock()
            tester = StreamTester(config, mock_logger)
            assert tester is not None
            # 验证缓存工作
            tester._cache_result("http://test-stream.example.com/hls.m3u8", {
                'status': 'success',
                'response_time': 150,
                'resolution': '1920x1080',
                'bitrate': 5000,
            })
            cached = tester._get_cached_result("http://test-stream.example.com/hls.m3u8")
            assert cached is not None
            assert cached['status'] == 'success'
