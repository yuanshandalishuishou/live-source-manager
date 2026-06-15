#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二轮复检：Web管理模块 API 功能测试
覆盖：仪表盘/源管理/配置中心/用户管理/审计日志/404/401/403

数据库和环境由 tests/conftest.py 统一配置（共享临时目录和密码）。
"""
import os
import sys
import json

# ── 项目路径 ──────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest
from fastapi.testclient import TestClient

from web import models
from web.webapp import app
from web.auth import _get_csrf_token

# 密码必须与 conftest.py 保持一致
ADMIN_PASSWORD = 'TestAdminPw1!'
VIEWER_PASSWORD = 'TestViewerPw1!'

client_pool = []  # 跟踪所有 client 以便清理


def _client():
    """每次测试创建新 TestClient 实例"""
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
# 1. 仪表盘 API
# ══════════════════════════════════════════════════

class TestDashboard:
    """GET /api/dashboard/stats"""

    def test_dashboard_stats_authenticated(self):
        """已登录用户可获取仪表盘统计"""
        client, auth = _admin_login()
        resp = client.get('/api/dashboard/stats')
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_dashboard_stats_unauthenticated(self):
        """未登录返回 401"""
        client = _client()
        resp = client.get('/api/dashboard/stats')
        assert resp.status_code == 401

    def test_dashboard_system_admin(self):
        """系统信息仅管理员可访问"""
        client, auth = _admin_login()
        resp = client.get('/api/dashboard/system')
        assert resp.status_code == 200
        data = resp.json()
        assert 'memory_usage' in data
        assert 'cpu' in data

    def test_dashboard_system_viewer_denied(self):
        """查看者无法访问系统信息"""
        client, auth = _viewer_login()
        resp = client.get('/api/dashboard/system')
        assert resp.status_code == 403

    def test_dashboard_test_info(self):
        """测试信息接口"""
        client, auth = _admin_login()
        resp = client.get('/api/dashboard/test-info')
        assert resp.status_code == 200


# ══════════════════════════════════════════════════
# 2. 源管理 API
# ══════════════════════════════════════════════════

class TestSources:
    """GET /api/sources, GET /api/sources/{id}, POST /api/sources"""

    def test_list_sources_authenticated(self):
        """已登录用户可列出源"""
        client, auth = _admin_login()
        resp = client.get('/api/sources')
        assert resp.status_code == 200
        data = resp.json()
        assert 'sources' in data
        assert 'total' in data

    def test_list_sources_unauthenticated(self):
        """未登录返回 401"""
        client = _client()
        resp = client.get('/api/sources')
        assert resp.status_code == 401

    def test_list_sources_with_params(self):
        """带参数的源列表请求"""
        client, auth = _admin_login()
        resp = client.get('/api/sources?type=all&page=1&size=10&search=')
        assert resp.status_code == 200

    def test_get_single_source_not_found(self):
        """不存在的源返回 404"""
        client, auth = _admin_login()
        resp = client.get('/api/sources/nonexistent1234')
        assert resp.status_code == 404

    def test_create_source_admin(self):
        """管理员可创建源"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/sources',
            json={'name': 'test_source', 'url': 'http://test.com/stream'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'created'

    def test_create_source_viewer_denied(self):
        """查看者无法创建源"""
        client, auth = _viewer_login()
        resp = client.post(
            '/api/sources',
            json={'name': 'evil_source', 'url': 'http://evil.com/stream'},
        )
        assert resp.status_code == 403  # viewer 无 CSRF token

    def test_update_source_admin(self):
        """管理员可更新源"""
        client, auth = _admin_login()
        resp = client.put(
            '/api/sources/fakeid123456',
            json={'name': 'updated_source', 'url': 'http://updated.com/stream'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'updated'

    def test_delete_source_admin(self):
        """管理员可删除源"""
        client, auth = _admin_login()
        resp = client.delete(
            '/api/sources/fakeid123456',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'deleted'


# ══════════════════════════════════════════════════
# 3. 配置中心 API
# ══════════════════════════════════════════════════

class TestConfig:
    """GET/PUT /api/config"""

    def test_get_config_authenticated(self):
        """已登录用户可读取配置"""
        client, auth = _admin_login()
        resp = client.get('/api/config')
        assert resp.status_code == 200
        data = resp.json()
        # 配置可能包含 Logging、System 等段落（加密密钥引入 System.encrypt_key）
        assert len(data) > 0, f"配置数据不应为空, got {data}"

    def test_get_config_unauthenticated(self):
        """未登录返回 401"""
        client = _client()
        resp = client.get('/api/config')
        assert resp.status_code == 401

    def test_get_section(self):
        """读取指定配置段落"""
        client, auth = _admin_login()
        resp = client.get('/api/config/Logging')
        assert resp.status_code == 200
        data = resp.json()
        assert 'level' in data

    def test_get_section_not_found(self):
        """不存在的段落返回 404"""
        client, auth = _admin_login()
        resp = client.get('/api/config/NonExistentSection123')
        assert resp.status_code == 404

    def test_put_config_admin(self):
        """管理员可写入配置"""
        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'DEBUG'}},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'ok'

        # 验证写入生效
        resp = client.get('/api/config/Logging')
        assert resp.json().get('level') == 'DEBUG', "配置应已更新"

    def test_put_config_viewer_denied(self):
        """查看者无法写入配置"""
        client, auth = _viewer_login()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'CRITICAL'}},
        )
        assert resp.status_code == 403

    def test_put_config_requires_csrf(self):
        """配置写入需要 CSRF token"""
        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'INFO'}},
        )
        assert resp.status_code == 403

    def test_config_fields_meta(self):
        """配置字段元信息"""
        client, auth = _admin_login()
        resp = client.get('/api/config/fields')
        assert resp.status_code == 200
        data = resp.json()
        assert 'Logging' in data
        assert 'Sources' in data

    def test_reload_config_admin(self):
        """管理员可触发配置重载"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/config/reload',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'ok'


# ══════════════════════════════════════════════════
# 4. 用户管理 API
# ══════════════════════════════════════════════════

class TestUsers:
    """GET/POST/PUT/DELETE /api/users"""

    def setup_method(self):
        """每个测试方法前确保数据库连接干净，回滚可能残留的事务"""
        import sqlite3
        try:
            conn = sqlite3.connect(models.DB_PATH, timeout=1)
            conn.execute("ROLLBACK")
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def test_list_users_admin(self):
        """管理员可列出用户"""
        client, auth = _admin_login()
        resp = client.get('/api/users')
        assert resp.status_code == 200
        data = resp.json()
        assert 'users' in data
        usernames = [u['username'] for u in data['users']]
        assert 'admin' in usernames
        assert 'viewer' in usernames

    def test_list_users_viewer_denied(self):
        """查看者无法列出用户"""
        client, auth = _viewer_login()
        resp = client.get('/api/users')
        assert resp.status_code == 403

    def test_create_user_admin(self):
        """管理员创建用户"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/users',
            json={'username': 'testuser1', 'password': 'TestPass123'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'created'

    def test_create_user_duplicate(self):
        """重复用户名应返回 409"""
        client, auth = _admin_login()
        # 先创建用户，再尝试重复创建
        resp1 = client.post(
            '/api/users',
            json={'username': 'testuser1', 'password': 'TestPass123'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp1.status_code == 200, f"首次创建应成功: {resp1.text}"
        # 重复创建
        resp2 = client.post(
            '/api/users',
            json={'username': 'testuser1', 'password': 'AnotherPass123'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp2.status_code == 409

    def test_create_user_short_username(self):
        """用户名太短"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/users',
            json={'username': 'a', 'password': 'TestPass123'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 400

    def test_create_user_short_password(self):
        """密码太短"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/users',
            json={'username': 'testuser2', 'password': '12'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 400

    def test_create_user_invalid_role(self):
        """无效角色"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/users',
            json={'username': 'testuser3', 'password': 'TestPass123', 'role': 'superadmin'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 400

    def test_create_user_with_display_name(self):
        """带显示名称创建用户"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/users',
            json={'username': 'testuser4', 'password': 'TestPass123',
                  'role': 'admin', 'display_name': '测试用户四'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200

    def test_update_user(self):
        """更新用户"""
        client, auth = _admin_login()
        # 创建新用户
        resp = client.post(
            '/api/users',
            json={'username': 'updatable_user', 'password': 'Pass123456'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        # 获取用户 ID
        resp = client.get('/api/users')
        users = resp.json()['users']
        target = [u for u in users if u['username'] == 'updatable_user'][0]
        uid = target['id']

        resp = client.put(
            f'/api/users/{uid}',
            json={'role': 'viewer', 'display_name': 'Updated Name'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'updated'

    def test_toggle_user(self):
        """启用/禁用用户"""
        client, auth = _admin_login()
        resp = client.get('/api/users')
        users = resp.json()['users']
        target = [u for u in users if u['username'] == 'viewer'][0]
        uid = target['id']

        resp = client.patch(
            f'/api/users/{uid}/toggle',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert 'is_active' in resp.json()
        # 恢复
        resp = client.patch(
            f'/api/users/{uid}/toggle',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200

    def test_cannot_toggle_self(self):
        """不能禁用自己"""
        client, auth = _admin_login()
        resp = client.get('/api/users')
        users = resp.json()['users']
        target = [u for u in users if u['username'] == 'admin'][0]
        uid = target['id']

        resp = client.patch(
            f'/api/users/{uid}/toggle',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 400

    def test_cannot_delete_self(self):
        """不能删除自己"""
        client, auth = _admin_login()
        resp = client.get('/api/users')
        users = resp.json()['users']
        target = [u for u in users if u['username'] == 'admin'][0]
        uid = target['id']

        resp = client.delete(
            f'/api/users/{uid}',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 400

    def test_delete_user(self):
        """删除用户"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/users',
            json={'username': 'delete_me_user', 'password': 'Pass123456'},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        resp = client.get('/api/users')
        users = resp.json()['users']
        target = [u for u in users if u['username'] == 'delete_me_user'][0]
        uid = target['id']

        resp = client.delete(
            f'/api/users/{uid}',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'deleted'

        # 验证已删除
        resp = client.get('/api/users')
        usernames = [u['username'] for u in resp.json()['users']]
        assert 'delete_me_user' not in usernames


# ══════════════════════════════════════════════════
# 5. 审计日志 API
# ══════════════════════════════════════════════════

class TestAuditLogs:
    """GET /api/audit"""

    def test_audit_logs_admin(self):
        """管理员可查看审计日志"""
        client, auth = _admin_login()
        resp = client.get('/api/audit')
        assert resp.status_code == 200
        data = resp.json()
        assert 'logs' in data
        assert 'total' in data

    def test_audit_logs_viewer_denied(self):
        """查看者无权查看审计日志"""
        client, auth = _viewer_login()
        resp = client.get('/api/audit')
        assert resp.status_code == 403

    def test_audit_logs_pagination(self):
        """审计日志分页"""
        client, auth = _admin_login()
        resp = client.get('/api/audit?page=1&size=10')
        assert resp.status_code == 200
        data = resp.json()
        assert data['page'] == 1
        assert data['size'] == 10

    def test_audit_logs_action_filter(self):
        """审计日志按操作类型筛选"""
        client, auth = _admin_login()
        resp = client.get('/api/audit?action=login')
        assert resp.status_code == 200
        for log in resp.json()['logs']:
            assert log['action'] == 'login'

    def test_audit_actions(self):
        """操作类型列表"""
        client, auth = _admin_login()
        resp = client.get('/api/audit/actions')
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ══════════════════════════════════════════════════
# 6. 日志 API
# ══════════════════════════════════════════════════

class TestLogs:
    """GET /api/logs"""

    def test_logs_authenticated(self):
        """已登录用户可查看日志"""
        client, auth = _admin_login()
        resp = client.get('/api/logs')
        assert resp.status_code == 200
        data = resp.json()
        assert 'logs' in data

    def test_logs_unauthenticated(self):
        """未登录返回 401"""
        client = _client()
        resp = client.get('/api/logs')
        assert resp.status_code == 401

    def test_logs_level_filter(self):
        """日志级别筛选"""
        client, auth = _admin_login()
        resp = client.get('/api/logs?level=INFO')
        assert resp.status_code == 200

    def test_logs_download_admin(self):
        """管理员可下载日志"""
        client, auth = _admin_login()
        resp = client.get('/api/logs/download')
        assert resp.status_code == 200


# ══════════════════════════════════════════════════
# 7. 测试 API
# ══════════════════════════════════════════════════

class TestTestStatus:
    """GET /api/test/status"""

    def test_test_status(self):
        """测试状态"""
        client, auth = _admin_login()
        resp = client.get('/api/test/status')
        assert resp.status_code == 200

    def test_trigger_test_admin(self):
        """管理员触发测试"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/test/trigger',
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'triggered'


# ══════════════════════════════════════════════════
# 8. 审计日志脱敏验证
# ══════════════════════════════════════════════════

class TestAuditSanitization:
    """审计日志中敏感信息脱敏"""

    def test_login_audit_no_password(self):
        """审计日志不应记录密码"""
        client, auth = _admin_login()
        resp = client.get('/api/audit?action=login')
        for log in resp.json()['logs']:
            detail = log.get('detail', '')
            assert 'password' not in detail.lower(), "审计日志不应包含密码"

    def test_config_audit_redacts_sensitive(self):
        """配置更改审计应脱敏敏感字段"""
        client, auth = _admin_login()
        # 执行一次包含敏感字段的配置写入
        resp = client.put(
            '/api/config',
            json={'Network': {'proxy_password': 'super_secret', 'proxy_host': '10.0.0.1'}},
            headers={'X-CSRF-Token': auth['csrf_token']},
        )
        assert resp.status_code == 200

        # 查看审计日志
        resp = client.get('/api/audit?action=config_update')
        for log in resp.json()['logs']:
            detail = log.get('detail', '')
            if 'proxy_password' in detail:
                assert '***' in detail, "密码字段应在审计日志中被脱敏"
                assert 'super_secret' not in detail, "敏感信息不应明文出现在审计日志"
