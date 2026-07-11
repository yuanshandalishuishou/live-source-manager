"""
web.core 模块单元测试 — 共享基础设施

覆盖：read_config, write_config, sanitize_config_data, validate_and_coerce,
sanitize_config_data, get_field_meta, ConnectionManager
"""

import os
import sys

# 设置测试环境
os.environ['WEB_ADMIN_PASSWORD'] = 'TestAdminPw1!'
os.environ['CONFIG_ENCRYPT_KEY'] = 'test-key-not-valid-for-prod-only-testing'

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from web.core import (
    get_field_meta,
    read_config,
    read_section,
    sanitize_config_data,
    validate_and_coerce,
)


class TestValidateAndCoerce:
    """validate_and_coerce 类型转换与校验"""

    def test_int_valid(self):
        coerced, err = validate_and_coerce('Testing', 'timeout', '15', ('int', '10', '测试超时(秒)'))
        assert coerced == 15
        assert err == ''

    def test_int_invalid(self):
        coerced, err = validate_and_coerce('Testing', 'timeout', 'abc', ('int', '10', '测试超时(秒)'))
        assert coerced == '10'
        assert '必须是整数' in err

    def test_bool_true(self):
        coerced, err = validate_and_coerce('Network', 'proxy_enabled', 'true', ('bool', 'False', '启用代理'))
        assert coerced == 'True'
        assert err == ''

    def test_bool_false(self):
        coerced, err = validate_and_coerce('Network', 'proxy_enabled', '0', ('bool', 'False', '启用代理'))
        assert coerced == 'False'
        assert err == ''

    def test_str_value(self):
        coerced, err = validate_and_coerce('Sources', 'local_dirs', '/some/path', ('str', '.', '本地源目录'))
        assert coerced == '/some/path'
        assert err == ''

    def test_textarea_value(self):
        """textarea 类型的字段保持原样返回"""
        coerced, err = validate_and_coerce('Sources', 'online_urls', 'http://example.com', ('textarea', '', '在线源'))
        assert coerced == 'http://example.com'
        assert err == ''


class TestSanitizeConfigData:
    """sanitize_config_data 脱敏处理"""

    def test_hides_proxy_password(self):
        data = {'Network': {'proxy_password': 'secret123', 'proxy_host': '1.2.3.4'}}
        safe = sanitize_config_data(data)
        assert safe['Network']['proxy_password'] == '***'
        # proxy_host 现在也属于敏感字段，会被脱敏
        assert safe['Network']['proxy_host'] == '***'

    def test_hides_api_token(self):
        data = {'GitHub': {'api_token': 'ghp_xxx123'}}
        safe = sanitize_config_data(data)
        assert safe['GitHub']['api_token'] == '***'

    def test_empty_values_not_masked(self):
        data = {'Network': {'proxy_password': ''}}
        safe = sanitize_config_data(data)
        assert safe['Network']['proxy_password'] == ''


class TestGetFieldMeta:
    """get_field_meta 返回字段元信息"""

    def test_returns_dict(self):
        meta = get_field_meta()
        assert isinstance(meta, dict)

    def test_contains_sections(self):
        meta = get_field_meta()
        assert 'Sources' in meta
        assert 'Network' in meta

    def test_field_def_format(self):
        meta = get_field_meta()
        field = meta['Testing']['timeout']
        assert len(field) >= 3  # (type, default, label, ...)
        assert field[0] == 'int'


class TestReadConfig:
    """read_config 读取配置"""

    def test_returns_dict(self):
        config = read_config()
        assert isinstance(config, dict)

    def test_returns_non_empty(self):
        # 种子测试数据确保 read_config 能返回内容
        from web import models

        models.set_app_config('Test.core_value', '42')
        config = read_config()
        assert len(config) > 0


class TestReadSection:
    """read_section 读取指定段配置"""

    def test_read_existing_section(self):
        section_data = read_section('Logging')
        assert isinstance(section_data, dict)

    def test_read_nonexistent_section(self):
        section_data = read_section('NonExistent')
        assert section_data == {}


class TestConnectionManager:
    """ConnectionManager WebSocket 连接管理"""

    def test_init(self):
        from web.core import ConnectionManager

        cm = ConnectionManager(max_connections=10)
        assert cm.count == 0
        assert cm.max_connections == 10

    def test_count_property(self):
        from web.core import ConnectionManager

        cm = ConnectionManager()
        assert cm.count == 0
