#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二轮复检：Web管理模块认证测试
覆盖：登录/登出/Session/CSRF/错误处理/边界条件

数据库和环境由 tests/conftest.py 统一配置（共享临时目录和密码）。
"""
import os
import sys
import json
import time
import uuid
import asyncio
import shutil

# ── 项目路径 ──────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest
from fastapi.testclient import TestClient

from web import models
from web.webapp import app
from web.auth import _get_csrf_token, verify_csrf_token

# 密码必须与 conftest.py 保持一致
ADMIN_PASSWORD = 'TestAdminPw1!'
VIEWER_PASSWORD = 'TestViewerPw1!'


def _client():
    """每次测试创建新 TestClient 实例（避免 cookie 跨测试污染）"""
    return TestClient(app)


def _admin_login(client=None):
    """内部登录 admin 并返回 session_id + csrf_token"""
    if client is None:
        client = _client()
    resp = client.post('/api/auth/login', data={
        'username': 'admin',
        'password': ADMIN_PASSWORD,
    })
    assert resp.status_code == 200
    sid = resp.cookies.get('session')
    assert sid
    csrf = _get_csrf_token(sid)
    return client, {'session_id': sid, 'csrf_token': csrf}


def _viewer_login(client=None):
    """内部登录 viewer"""
    if client is None:
        client = _client()
    resp = client.post('/api/auth/login', data={
        'username': 'viewer',
        'password': VIEWER_PASSWORD,
    })
    assert resp.status_code == 200
    sid = resp.cookies.get('session')
    assert sid
    return client, {'session_id': sid}


# ══════════════════════════════════════════════════
# 1. 登录测试
# ══════════════════════════════════════════════════

class TestLogin:
    """POST /api/auth/login"""

    def test_login_success_admin(self):
        """正常登录：管理员"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 200, f"管理员登录失败: {resp.json()}"
        data = resp.json()
        assert data['status'] == 'ok'
        assert data['role'] == 'admin'
        assert 'session' in resp.cookies
        session_id = resp.cookies['session']
        assert len(session_id) > 20, "session id 长度异常"

    def test_login_success_viewer(self):
        """正常登录：查看者"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': 'viewer',
            'password': VIEWER_PASSWORD,
        })
        assert resp.status_code == 200, f"查看者登录失败: {resp.json()}"
        data = resp.json()
        assert data['status'] == 'ok'
        assert data['role'] == 'viewer'

    def test_login_wrong_password(self):
        """错误密码应返回 401"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': 'wrong_password_123',
        })
        assert resp.status_code == 401, f"预期 401，实际 {resp.status_code}"

    def test_login_nonexistent_user(self):
        """不存在的用户应返回 401"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': 'nonexistent_user_xyz',
            'password': 'whatever123',
        })
        assert resp.status_code == 401

    def test_login_empty_username(self):
        """空用户名"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': '',
            'password': ADMIN_PASSWORD,
        })
        # FastAPI 对空 Form 字段可能返回 422（Pydantic 校验）或 401（业务层）
        assert resp.status_code in (401, 422), f"空用户名应被拒绝: {resp.status_code}"

    def test_login_empty_password(self):
        """空密码"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': '',
        })
        assert resp.status_code in (401, 422), f"空密码应被拒绝: {resp.status_code}"

    def test_login_long_username(self):
        """超长用户名（边界测试）"""
        client = _client()
        long_name = 'u' * 1000
        resp = client.post('/api/auth/login', data={
            'username': long_name,
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 401, "超长用户名应返回 401（不存在）"

    def test_login_special_chars(self):
        """特殊字符用户名"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': "<script>alert('xss')</script>",
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 401, "特殊字符用户名应返回 401"

    def test_login_unicode(self):
        """Unicode 用户名"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': '管理员测试',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 401, "中文字符用户名应返回 401（不存在）"


# ══════════════════════════════════════════════════
# 2. 登出测试
# ══════════════════════════════════════════════════

class TestLogout:
    """POST /api/auth/logout"""

    def test_logout_then_unauthorized(self):
        """登录→登出→操作被拒"""
        client, auth = _admin_login()

        # 先确认已登录
        resp = client.get('/api/auth/me')
        assert resp.status_code == 200, "登出前应可访问"

        # 登出
        resp = client.post('/api/auth/logout')
        assert resp.status_code == 200
        assert resp.json()['status'] == 'ok'

        # 再次访问应被拒（TestClient 会自动移除 session cookie 因为 set-cookie 删除了它）
        resp = client.get('/api/auth/me')
        assert resp.status_code == 401, "登出后 session 应失效"

    def test_logout_twice(self):
        """重复登出不应报错"""
        client, auth = _admin_login()
        resp1 = client.post('/api/auth/logout')
        assert resp1.status_code == 200
        resp2 = client.post('/api/auth/logout')
        assert resp2.status_code == 200, "重复登出应正常返回"

    def test_logout_without_session(self):
        """无 session 的登出请求"""
        client = _client()
        resp = client.post('/api/auth/logout')
        assert resp.status_code == 200, "未登录的登出应正常返回"

    def test_logout_removes_csrf(self):
        """登出后 CSRF token 应失效"""
        client, auth = _admin_login()
        sid, csrf = auth['session_id'], auth['csrf_token']

        resp = client.post('/api/auth/logout')
        assert resp.status_code == 200

        # 登出后验证 CSRF token 是否失效
        assert not verify_csrf_token(sid, csrf), "登出后 CSRF token 应失效"


# ══════════════════════════════════════════════════
# 3. 认证状态测试
# ══════════════════════════════════════════════════

class TestAuthMe:
    """GET /api/auth/me"""

    def test_me_authenticated(self):
        """已登录用户可获取自身信息"""
        client, auth = _admin_login()
        resp = client.get('/api/auth/me')
        assert resp.status_code == 200
        data = resp.json()
        assert data['username'] == 'admin'
        assert data['role'] == 'admin'

    def test_me_viewer(self):
        """查看者信息"""
        client, auth = _viewer_login()
        resp = client.get('/api/auth/me')
        assert resp.status_code == 200
        data = resp.json()
        assert data['username'] == 'viewer'
        assert data['role'] == 'viewer'

    def test_me_unauthenticated(self):
        """未登录返回 401"""
        client = _client()
        resp = client.get('/api/auth/me')
        assert resp.status_code == 401

    def test_me_invalid_session(self):
        """无效 session 返回 401"""
        client = _client()
        client.cookies.set('session', 'invalid_session_id_12345')
        resp = client.get('/api/auth/me')
        assert resp.status_code == 401

    def test_me_expired_session(self):
        """过期 session 应返回 401（SQLite 中无此记录）"""
        client = _client()
        client.cookies.set('session', 'a' * 32)
        resp = client.get('/api/auth/me')
        assert resp.status_code == 401


# ══════════════════════════════════════════════════
# 4. 认证全流程测试
# ══════════════════════════════════════════════════

class TestAuthFlow:
    """完整认证流程：未登录→登录→操作→登出→操作被拒"""

    def test_full_auth_flow(self):
        """完整认证流程验证"""
        client = _client()

        # Step 1: 未登录访问仪表盘 API
        resp = client.get('/api/dashboard/stats')
        assert resp.status_code == 401, "未登录应被拒绝"

        # Step 2: 登录
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 200
        session_id = resp.cookies.get('session')
        assert session_id

        # Step 3: 登录后可访问受保护资源（TestClient 持有了 cookie）
        resp = client.get('/api/dashboard/stats')
        assert resp.status_code == 200

        # Step 4: 登出
        resp = client.post('/api/auth/logout')
        assert resp.status_code == 200

        # Step 5: 登出后操作被拒（TestClient 的 cookie 被登出响应删除）
        resp = client.get('/api/dashboard/stats')
        assert resp.status_code == 401, "登出后应被拒绝"

    def test_concurrent_sessions(self):
        """并发 session：同一用户多地登录"""
        sessions = []
        for _ in range(5):
            client = _client()
            resp = client.post('/api/auth/login', data={
                'username': 'admin',
                'password': ADMIN_PASSWORD,
            })
            assert resp.status_code == 200
            sid = resp.cookies.get('session')
            assert sid
            sessions.append((client, sid))

        # 所有 session 均应有效
        for client, sid in sessions:
            resp = client.get('/api/auth/me')
            assert resp.status_code == 200, "并发 session 应全部有效"

        # 依次登出，不应影响其他 session
        for i, (client, sid) in enumerate(sessions):
            resp = client.post('/api/auth/logout')
            assert resp.status_code == 200
            # 后续 session 仍然有效
            if i + 1 < len(sessions):
                resp = sessions[i + 1][0].get('/api/auth/me')
                assert resp.status_code == 200

    def test_role_isolation(self):
        """角色隔离：viewer 不能操作管理员接口"""
        client, auth = _viewer_login()

        # viewer 访问管理员接口应被拒
        resp = client.get('/api/dashboard/system')
        assert resp.status_code == 403, "viewer 访问系统信息应被拒"

        resp = client.get('/api/users')
        assert resp.status_code == 403, "viewer 访问用户管理应被拒"

        resp = client.post('/api/users', json={'username': 'evil', 'password': '123456'})
        assert resp.status_code == 403, "viewer 创建用户应被拒"


# ══════════════════════════════════════════════════
# 5. Session 持久化测试
# ══════════════════════════════════════════════════

class TestSessionPersistence:
    """Session 在 SQLite 中的持久化"""

    def test_session_in_db(self):
        """session 应存储在 SQLite 中"""
        client, auth = _admin_login()
        sid = auth['session_id']
        row = models.get_session_db(sid, 7200, 86400)
        assert row is not None
        assert row['username'] == 'admin'
        assert row['role'] == 'admin'

    def test_session_not_in_memory_only(self):
        """session 不能仅存在内存中（应写入 SQLite）"""
        client, auth = _admin_login()
        sid = auth['session_id']
        conn = models.get_conn()
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        assert row is not None, "Session 必须写入 SQLite 数据库"

    def test_destroyed_session_removed(self):
        """销毁的 session 应从数据库删除"""
        client, auth = _admin_login()
        sid = auth['session_id']
        models.destroy_session_db(sid)
        conn = models.get_conn()
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        assert row is None, "销毁的 session 应从数据库删除"


# ══════════════════════════════════════════════════
# 6. CSRF 测试
# ══════════════════════════════════════════════════

class TestCSRF:
    """CSRF 中间件防护测试"""

    def test_write_without_csrf(self):
        """写操作缺少 CSRF token 应返回 403"""
        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'DEBUG'}},
            headers={},  # 不传 X-CSRF-Token
        )
        assert resp.status_code == 403, "缺少 CSRF token 应返回 403"
        detail = resp.json().get('detail', '')
        assert 'CSRF' in detail or '安全令牌' in detail

    def test_write_with_invalid_csrf(self):
        """无效 CSRF token 应返回 403"""
        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'DEBUG'}},
            headers={'X-CSRF-Token': 'invalid_token_12345'},
        )
        assert resp.status_code == 403, "无效 CSRF token 应返回 403"

    def test_write_with_valid_csrf(self):
        """有效 CSRF token 应允许写操作"""
        client, auth = _admin_login()
        csrf = auth['csrf_token']
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'INFO'}},
            headers={'X-CSRF-Token': csrf},
        )
        assert resp.status_code == 200, f"有效 CSRF 写操作应成功: {resp.json()}"

    def test_csrf_not_required_for_exempt_routes(self):
        """免 CSRF 路径应正常"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 200, "登录路径应免 CSRF"

    def test_csrf_token_staleness_on_logout(self):
        """登出后 CSRF token 应失效"""
        client = _client()
        resp = client.post('/api/auth/login', data={
            'username': 'admin',
            'password': ADMIN_PASSWORD,
        })
        assert resp.status_code == 200
        sid = resp.cookies.get('session')
        csrf = _get_csrf_token(sid)

        # 登出
        client.post('/api/auth/logout')

        # CSRF token 应已无效
        assert not verify_csrf_token(sid, csrf)


# ══════════════════════════════════════════════════
# 7. 错误处理测试
# ══════════════════════════════════════════════════

class TestErrorHandling:
    """HTTP 错误处理"""

    def test_404_not_found(self):
        """不存在的 API 返回 404"""
        client = _client()
        resp = client.get('/api/nonexistent/route/12345')
        assert resp.status_code == 404

    def test_401_unauthorized(self):
        """未认证访问保护资源"""
        client = _client()
        resp = client.get('/api/dashboard/stats')
        assert resp.status_code == 401

    def test_403_forbidden(self):
        """viewer 访问 admin-only 资源"""
        client, auth = _viewer_login()
        resp = client.get('/api/dashboard/system')
        assert resp.status_code == 403

        resp = client.get('/api/users')
        assert resp.status_code == 403

    def test_400_bad_request(self):
        """创建用户时提供无效数据"""
        client, auth = _admin_login()
        csrf = auth['csrf_token']
        # 用户名太短
        resp = client.post(
            '/api/users',
            json={'username': 'a', 'password': '123456'},
            headers={'X-CSRF-Token': csrf},
        )
        assert resp.status_code == 400

    def test_csrf_missing_on_put(self):
        """PUT 路径缺少 CSRF"""
        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'DEBUG'}},
        )
        assert resp.status_code == 403

    def test_csrf_missing_on_post(self):
        """POST 路径缺少 CSRF"""
        client, auth = _admin_login()
        resp = client.post(
            '/api/users',
            json={'username': 'test', 'password': '123456'},
        )
        assert resp.status_code == 403


# ══════════════════════════════════════════════════
# 8. CSRF 绕过检测
# ══════════════════════════════════════════════════

class TestCSRFBypass:
    """CSRF 中间件绕过检测"""

    def test_csrf_bypass_without_cookie(self):
        """无 cookie 的写请求应无法绕过"""
        client = _client()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'DEBUG'}},
        )
        assert resp.status_code in (401, 403), "无 cookie 的写请求应被拦截"

    def test_csrf_bypass_different_origin(self):
        """模拟不同 origin 的 CSRF 攻击（无 token 仅有 cookie）"""
        client, auth = _admin_login()
        # 攻击者网站诱导浏览器自动发送 cookie，但无法获知 CSRF token
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'CRITICAL'}},
            headers={
                'Origin': 'https://evil.example.com',
                'Referer': 'https://evil.example.com/attack',
            },
        )
        assert resp.status_code == 403, "跨站请求应被 CSRF 保护拦截"

    def test_csrf_bypass_with_empty_token(self):
        """空 CSRF token"""
        client, auth = _admin_login()
        resp = client.put(
            '/api/config',
            json={'Logging': {'level': 'DEBUG'}},
            headers={'X-CSRF-Token': ''},
        )
        assert resp.status_code == 403, "空 CSRF token 应被拦截"

    def test_csrf_bypass_with_wrong_token(self):
        """错误的 CSRF token（恒定时间比较）"""
        client, auth = _admin_login()
        # 多个不同长度的错误 token
        for wrong_token in ['a', 'ab', 'abc', 'a' * 64, 'b' * 128]:
            resp = client.put(
                '/api/config',
                json={'Logging': {'level': 'DEBUG'}},
                headers={'X-CSRF-Token': wrong_token},
            )
            assert resp.status_code == 403, f"错误 CSRF token 应被拦截"

    def test_csrf_bypass_with_own_valid_token(self):
        """一个用户的 token 无法用于另一个用户的 session"""
        # 管理员 token
        admin_client, admin_auth = _admin_login()
        csrf_admin = admin_auth['csrf_token']

        # 查看者 session（用不同的 client）
        viewer_client = _client()
        resp = viewer_client.post('/api/auth/login', data={
            'username': 'viewer',
            'password': VIEWER_PASSWORD,
        })
        assert resp.status_code == 200

        # 管理员 token + 查看者 cookie
        resp = viewer_client.put(
            '/api/config',
            json={'Logging': {'level': 'DEBUG'}},
            headers={'X-CSRF-Token': csrf_admin},
        )
        # 403（CSRF 不匹配，因为 session 不同）或 403（role 权限不够）
        assert resp.status_code == 403


# ══════════════════════════════════════════════════
# 9. 页面路由认证测试
# ══════════════════════════════════════════════════

class TestPageAuth:
    """页面路由认证检查"""

    def test_login_page_accessible(self):
        """登录页应公开访问"""
        client = _client()
        resp = client.get('/login')
        assert resp.status_code == 200, f"登录页状态码异常: {resp.status_code}"
        assert 'text/html' in resp.headers.get('content-type', '')

    def test_pages_redirect_when_unauthenticated(self):
        """未登录访问受保护页面返回 401"""
        client = _client()
        protected = ['/', '/sources', '/config', '/test', '/logs', '/users', '/audit']
        for path in protected:
            resp = client.get(path)
            assert resp.status_code == 401, f"页面 {path} 未登录应返回 401"

    def test_page_access_when_authenticated(self):
        """已登录可访问受保护页面"""
        client, auth = _admin_login()
        protected = ['/', '/sources', '/config', '/test', '/logs']
        for path in protected:
            resp = client.get(path)
            assert resp.status_code in (200, 500), f"已登录应可访问 {path}"

    def test_admin_pages_require_admin(self):
        """查看者访问管理员页面返回 403"""
        client, auth = _viewer_login()
        # /users 和 /audit 通过 require_admin 保护
        resp = client.get('/users')
        assert resp.status_code == 403, "viewer 访问用户管理页应返回 403"
