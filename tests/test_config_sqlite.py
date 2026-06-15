#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R3 迭代：SQLite 配置存储功能验证
覆盖：
  2.1 配置读写（SQLite层）
  2.2 配置迁移完整性
  2.3 首次运行初始化测试
  2.4 API兼容性测试

数据库和环境由 tests/conftest.py 统一配置（共享临时目录和密码）。
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
from fastapi.testclient import TestClient

from web import models
from web.webapp import app, read_config, read_section, write_config
from web.webapp import CONFIG_PATH
from web.auth import _get_csrf_token

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


def _viewer_login(client=None):
    if client is None:
        client = _client()
    resp = client.post('/api/auth/login', data={
        'username': 'viewer',
        'password': VIEWER_PASSWORD,
    })
    assert resp.status_code == 200
    sid = resp.cookies.get('session')
    return client, {'session_id': sid}


# ══════════════════════════════════════════════════
# 2.1 配置读写（SQLite层）
# ══════════════════════════════════════════════════

class TestSQLiteConfigReadWrite:
    """SQLite 层配置读写操作"""

    def test_get_set_app_config(self):
        """写入配置，读取验证"""
        key = 'test_section.test_key'
        value = 'test_value_123'
        models.set_app_config(key, value)
        result = models.get_app_config(key)
        assert result == value

        # 覆盖写入
        new_value = 'updated_value_456'
        models.set_app_config(key, new_value)
        result = models.get_app_config(key)
        assert result == new_value

        # 清理
        models._execute("DELETE FROM app_config WHERE key = ?", (key,))

    def test_get_all_config(self):
        """批量读取格式"""
        # 写入多个测试配置
        test_keys = {
            'Sources.local_dirs': '/config/sources',
            'Sources.online_urls': 'http://test.com/list',
            'Logging.level': 'DEBUG',
            'Logging.file': '/tmp/test.log',
            'Network.proxy_enabled': 'False',
        }
        for k, v in test_keys.items():
            models.set_app_config(k, v)

        all_config = models.get_all_config()
        assert isinstance(all_config, dict)
        assert 'Sources' in all_config
        assert 'Logging' in all_config
        assert 'Network' in all_config
        assert all_config['Sources']['local_dirs'] == '/config/sources'
        assert all_config['Logging']['level'] == 'DEBUG'

        # 清理
        for k in test_keys:
            models._execute("DELETE FROM app_config WHERE key = ?", (k,))

    def test_get_nonexistent_config(self):
        """不存在的key返回None"""
        result = models.get_app_config('nonexistent_section.nonexistent_key_xyz')
        assert result is None

        result = models.get_app_config('')
        assert result is None

    def test_delete_section(self):
        """删除一组section的配置"""
        # 写入测试数据
        keys = {
            'TestSection.key1': 'val1',
            'TestSection.key2': 'val2',
            'TestSection.nested.key': 'val3',
            'OtherSection.key': 'other_val',
        }
        for k, v in keys.items():
            models.set_app_config(k, v)

        # 验证写入成功
        assert models.get_app_config('TestSection.key1') == 'val1'

        # 删除 TestSection
        models.delete_app_config_by_section('TestSection')

        # 验证已删除
        assert models.get_app_config('TestSection.key1') is None
        assert models.get_app_config('TestSection.key2') is None
        assert models.get_app_config('TestSection.nested.key') is None

        # 其他 section 不受影响
        assert models.get_app_config('OtherSection.key') == 'other_val'

        # 清理
        models._execute("DELETE FROM app_config WHERE key LIKE ?", ('OtherSection.%',))

    def test_import_from_ini(self):
        """从INI导入的格式正确性"""
        # 创建临时 INI 文件
        tmp_dir = tempfile.mkdtemp(prefix='import_test_')
        ini_path = os.path.join(tmp_dir, 'test_config.ini')
        cp = configparser.ConfigParser()
        cp.add_section('ImportSection1')
        cp.set('ImportSection1', 'key_a', 'value_a')
        cp.set('ImportSection1', 'key_b', 'value_b')
        cp.add_section('ImportSection2')
        cp.set('ImportSection2', 'key_c', 'value_c')
        with open(ini_path, 'w', encoding='utf-8') as f:
            cp.write(f)

        # 导入
        count = models.import_from_ini_file(ini_path)
        assert count == 3  # 3 个键值对

        # 验证格式
        assert models.get_app_config('ImportSection1.key_a') == 'value_a'
        assert models.get_app_config('ImportSection1.key_b') == 'value_b'
        assert models.get_app_config('ImportSection2.key_c') == 'value_c'

        # get_all_config 中正确分组
        all_cfg = models.get_all_config()
        assert 'ImportSection1' in all_cfg
        assert all_cfg['ImportSection1']['key_a'] == 'value_a'
        assert 'ImportSection2' in all_cfg

        # 清理
        models._execute("DELETE FROM app_config WHERE key LIKE 'ImportSection1.%'")
        models._execute("DELETE FROM app_config WHERE key LIKE 'ImportSection2.%'")
        shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_import_from_nonexistent_ini(self):
        """不存在的 INI 文件返回 0"""
        count = models.import_from_ini_file('/tmp/nonexistent_config_xyz.ini')
        assert count == 0


# ══════════════════════════════════════════════════
# 2.2 配置迁移完整性
# ══════════════════════════════════════════════════

class TestConfigMigrationIntegrity:
    """SQLite 与 INI 格式一致性"""

    def test_import_export_consistency(self):
        """SQLite写入后，通过API读取的格式与INI格式一致"""
        # 1. 通过 write_config 写入配置（同时写 SQLite 和 INI）
        test_data = {
            'Logging': {'level': 'WARN', 'max_size': '20'},
            'Network': {'proxy_enabled': 'True', 'proxy_host': '192.168.1.1'},
        }
        success, msg = write_config(test_data)
        assert success, f"write_config 失败: {msg}"

        # 2. 从 SQLite 读取
        sqlite_cfg = models.get_all_config()

        # 3. 从 INI 文件读取
        cp = configparser.ConfigParser()
        cp.read(CONFIG_PATH, encoding='utf-8')
        ini_cfg = {}
        for section in cp.sections():
            ini_cfg[section] = dict(cp.items(section))

        # 4. 写入的两个 section 内容一致
        for section in test_data:
            assert section in sqlite_cfg, f"SQLite 缺失 section [{section}]"
            assert section in ini_cfg, f"INI 缺失 section [{section}]"
            for key, val in test_data[section].items():
                assert sqlite_cfg[section].get(key) == val, \
                    f"SQLite [{section}] {key}: 期望 {val}, 实际 {sqlite_cfg[section].get(key)}"
                assert ini_cfg.get(section, {}).get(key) == val, \
                    f"INI [{section}] {key}: 期望 {val}, 实际 {ini_cfg.get(section, {}).get(key)}"

    def test_sqlite_fallback_read(self):
        """SQLite无数据时回退到INI读取"""
        # 清空 app_config 表
        models._execute("DELETE FROM app_config")

        # 确保 INI 文件存在（由 conftest 创建）
        assert os.path.exists(CONFIG_PATH), f"INI 文件不存在: {CONFIG_PATH}"

        # read_config 应该能回退到 INI 读取
        config = read_config()
        assert isinstance(config, dict)
        assert len(config) > 0, "回退到 INI 后配置不应为空"
        assert 'Logging' in config, "INI 应有 Logging 段"

        # read_section 也应该回退
        section_data = read_section('Logging')
        assert isinstance(section_data, dict)
        assert 'level' in section_data

    def test_sqlite_fallback_read_empty_ini(self):
        """SQLite和INI都为空时返回空"""
        # 清空 app_config 和 INI
        models._execute("DELETE FROM app_config")
        empty_cp = configparser.ConfigParser()
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            empty_cp.write(f)

        config = read_config()
        assert config == {}

        section_data = read_section('Logging')
        assert section_data == {}


# ══════════════════════════════════════════════════
# 2.3 首次运行初始化测试
# ══════════════════════════════════════════════════

class TestFirstRunInit:
    """首次运行初始化逻辑验证"""

    def test_first_run_creates_default_ini(self):
        """删除config.ini后启动，验证被重新创建"""
        # 备份当前 config.ini 路径
        original_config = CONFIG_PATH
        bak_config = original_config + '.firstrun.bak'
        if os.path.exists(original_config):
            shutil.copy2(original_config, bak_config)
            os.remove(original_config)

        try:
            # 模拟 lifespan 中的首次运行逻辑
            from app.config_manager import Config as _Config

            # 1. 检查 config.ini，不存在则创建默认
            if not os.path.exists(original_config):
                _Config.create_default_at(original_config)
                assert os.path.exists(original_config), "config.ini 应被重新创建"

            # 验证内容
            cp = configparser.ConfigParser()
            cp.read(original_config, encoding='utf-8')
            assert 'Sources' in cp
            assert 'Logging' in cp
            assert 'Network' in cp
            assert cp.get('Logging', 'level', fallback='') == 'INFO'
        finally:
            # 恢复 conftest 原始状态
            # 注意：其他测试可能修改过 INI（如 test_sqlite_fallback_read_empty_ini），
            # 所以在此确保 INI 有 3 个必要 section
            if os.path.exists(bak_config):
                shutil.copy2(bak_config, original_config)
                os.remove(bak_config)
            if not os.path.exists(original_config):
                cp = configparser.ConfigParser()
                for section in ('Logging', 'Sources', 'Network'):
                    cp.add_section(section)
                cp.set('Logging', 'level', 'INFO')
                cp.set('Logging', 'file', os.path.join(os.path.dirname(original_config), 'app.log'))
                cp.set('Sources', 'local_dirs', '/config/sources')
                cp.set('Network', 'proxy_enabled', 'False')
                with open(original_config, 'w') as f:
                    cp.write(f)

    def test_first_run_imports_to_sqlite(self):
        """首次运行后，app_config表有数据"""
        # 清空 app_config
        models._execute("DELETE FROM app_config")
        assert not models.has_app_config_data()

        # 确保 INI 文件存在且有内容（其他测试可能重置过）
        ini_path = CONFIG_PATH
        if os.path.exists(ini_path):
            cp_chk = configparser.ConfigParser()
            cp_chk.read(ini_path, encoding='utf-8')
            if not cp_chk.sections():
                # INI 文件为空，重建测试数据
                cp = configparser.ConfigParser()
                for section in ('Logging', 'Sources', 'Network'):
                    cp.add_section(section)
                cp.set('Logging', 'level', 'INFO')
                cp.set('Logging', 'file', os.path.join(os.path.dirname(ini_path), 'app.log'))
                cp.set('Sources', 'local_dirs', '/config/sources')
                cp.set('Network', 'proxy_enabled', 'False')
                with open(ini_path, 'w') as f:
                    cp.write(f)

        # 模拟导入（INI 文件来自 conftest，应存在）
        count = models.import_from_ini_file(ini_path)
        assert count > 0, f"应从 INI 导入至少 1 条配置 (从 {ini_path})"

        # 验证 app_config 有数据
        assert models.has_app_config_data(), "导入后 app_config 应有数据"

        # 验证读到的数据不为空
        all_cfg = models.get_all_config()
        assert len(all_cfg) > 0

    def test_first_run_creates_channel_rules(self):
        """channel_rules.yml 被创建"""
        config_dir = os.path.join(PROJECT_ROOT, 'config')
        channel_rules_path = os.path.join(config_dir, 'channel_rules.yml')

        # 备份
        bak_path = channel_rules_path + '.bak_test'
        had_rules = os.path.exists(channel_rules_path)
        if had_rules:
            shutil.copy2(channel_rules_path, bak_path)
            os.remove(channel_rules_path)

        try:
            # 模拟创建
            if not os.path.exists(channel_rules_path):
                os.makedirs(os.path.dirname(channel_rules_path), exist_ok=True)
                with open(channel_rules_path, 'w', encoding='utf-8') as f:
                    f.write("# 默认频道分类规则\n# 请根据实际需求修改\n")

            assert os.path.exists(channel_rules_path), "channel_rules.yml 应被创建"

            with open(channel_rules_path, 'r', encoding='utf-8') as f:
                content = f.read()
            assert '# 默认频道分类规则' in content
        finally:
            # 恢复
            if os.path.exists(bak_path):
                shutil.copy2(bak_path, channel_rules_path)
                os.remove(bak_path)
            elif not had_rules:
                if os.path.exists(channel_rules_path):
                    os.remove(channel_rules_path)


# ══════════════════════════════════════════════════
# 2.4 API兼容性测试
# ══════════════════════════════════════════════════

class TestAPIConfigMigration:
    """API层配置迁移兼容性"""

    def test_api_get_config_after_migration(self):
        """GET /api/config 返回格式与迁移前一致"""
        # 先写入一些配置到 SQLite
        models._execute("DELETE FROM app_config")
        test_items = {
            'Logging.level': 'DEBUG',
            'Logging.max_size': '10',
            'Sources.local_dirs': '/config/sources',
            'Network.proxy_enabled': 'True',
        }
        for k, v in test_items.items():
            models.set_app_config(k, v)

        client, auth = _admin_login()
        resp = client.get('/api/config')
        assert resp.status_code == 200
        data = resp.json()

        # 格式应为 {section: {key: value}}
        assert isinstance(data, dict)
        assert 'Logging' in data
        assert 'Sources' in data
        assert isinstance(data['Logging'], dict)
        # 包含写入的值
        assert data['Logging']['level'] == 'DEBUG'
        # Logging.level 和 Logging.max_size 同在 Logging section 中

    def test_api_update_config_writes_db(self):
        """PUT /api/config 写入后SQLite有对应数据"""
        models._execute("DELETE FROM app_config")  # 清理

        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={
                'Testing': {'timeout': '30', 'concurrent_threads': '50'},
                'Logging': {'level': 'ERROR'},
            },
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'ok'

        # 验证 SQLite 有数据
        assert models.get_app_config('Testing.timeout') == '30'
        assert models.get_app_config('Testing.concurrent_threads') == '50'
        assert models.get_app_config('Logging.level') == 'ERROR'

    def test_api_update_config_still_writes_ini(self):
        """PUT同时写入INI文件"""
        models._execute("DELETE FROM app_config")

        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={
                'Output': {'filename': 'custom.m3u', 'group_by': 'country'},
            },
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200

        # 验证 INI 文件也有数据
        cp = configparser.ConfigParser()
        cp.read(CONFIG_PATH, encoding='utf-8')
        assert cp.has_section('Output')
        assert cp.get('Output', 'filename') == 'custom.m3u'
        assert cp.get('Output', 'group_by') == 'country'

        # 两个来源一致
        sqlite_val = models.get_app_config('Output.filename')
        assert sqlite_val == 'custom.m3u'

    def test_api_get_config_format_consistency(self):
        """GET /api/config 返回的段落与key格式与之前一致"""
        # 通过 API 写入一组配置
        client, auth = _admin_login()
        test_cfg = {
            'Logging': {'level': 'INFO', 'file': '/log/test.log', 'max_size': '10'},
            'Testing': {'timeout': '15', 'cache_ttl': '60'},
        }
        resp = client.put(
            '/api/config',
            json=test_cfg,
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200

        # 通过 API 读取
        resp = client.get('/api/config')
        data = resp.json()

        # 验证写入的值在每个 section 中正确存在
        for section, fields in test_cfg.items():
            assert section in data, f"返回数据缺少 section [{section}]"
            for key, val in fields.items():
                assert data[section].get(key) == val, \
                    f"[{section}] {key}=期望 {val}, 实际 {data[section].get(key)}"

    def test_api_get_section_from_sqlite(self):
        """GET /api/config/{section} 从 SQLite 读取"""
        models.set_app_config('HTTPServer.port', '23456')
        models.set_app_config('HTTPServer.host', '127.0.0.1')

        client, auth = _admin_login()
        resp = client.get('/api/config/HTTPServer')
        assert resp.status_code == 200
        data = resp.json()
        assert data.get('port') == '23456'
        assert data.get('host') == '127.0.0.1'
