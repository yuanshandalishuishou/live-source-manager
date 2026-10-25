/* ================================================================
 * pages/users.js — 用户管理页面脚本
 * 用户 CRUD（全部写操作经 LSM.ensureCsrfToken 带 X-CSRF-Token，F2）；
 * 删除/启用/禁用走 LSM.confirm；重置密码走 LSM.promptPassword；
 * 反馈统一 LSM.showToast；静态弹窗 .is-open 类驱动 + Esc/焦点管理。
 * ================================================================ */
(function() {
    'use strict';

    /* ── 静态弹窗：类驱动显隐 + Esc + 焦点管理 ────── */
    var modalReturnFocus = null;

    function openModal(el) {
        modalReturnFocus = document.activeElement;
        el.classList.add('is-open');
        var first = el.querySelector('input:not([disabled]), select, textarea');
        if (first) first.focus();
    }

    function closeModal(el) {
        el.classList.remove('is-open');
        if (modalReturnFocus && typeof modalReturnFocus.focus === 'function') {
            modalReturnFocus.focus();
        }
        modalReturnFocus = null;
    }

    // Esc 关闭当前打开的静态弹窗；backdrop/关闭钮经 data-modal-close 绑定
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        var open = document.querySelector('.modal.is-open');
        if (open) closeModal(open);
    });

    document.querySelectorAll('.modal [data-modal-close]').forEach(function(el) {
        el.addEventListener('click', function() {
            var modal = el.closest('.modal');
            if (modal) closeModal(modal);
        });
    });

    function showAddUserForm() {
        document.getElementById('new-username').value = '';
        document.getElementById('new-password').value = '';
        document.getElementById('new-display-name').value = '';
        document.getElementById('new-role').value = 'viewer';
        document.getElementById('add-user-result').innerHTML = '';
        openModal(document.getElementById('add-user-modal'));
    }

    function hideAddUserForm() {
        document.getElementById('add-user-result').innerHTML = '';
        closeModal(document.getElementById('add-user-modal'));
    }

    function showEditUserForm(id, username, displayName, role) {
        document.getElementById('edit-user-id').value = id;
        document.getElementById('edit-username').value = username;
        document.getElementById('edit-display-name').value = displayName || '';
        document.getElementById('edit-role').value = role;
        document.getElementById('edit-password').value = '';
        document.getElementById('edit-user-result').innerHTML = '';
        openModal(document.getElementById('edit-user-modal'));
    }

    function hideEditUserForm() {
        document.getElementById('edit-user-result').innerHTML = '';
        closeModal(document.getElementById('edit-user-modal'));
    }

    /* ── 表单提交 ───────────────────────────────── */
    function bindForm(formId, resultId, buildPayload, url, method, onSuccess) {
        var form = document.getElementById(formId);
        if (!form) return;
        form.addEventListener('submit', async function(evt) {
            evt.preventDefault();
            var result = document.getElementById(resultId);
            var btn = form.querySelector('button[type="submit"]');
            LSM.setBtnLoading(btn, true);
            try {
                var payload = buildPayload();
                var csrfToken = await LSM.ensureCsrfToken();
                var resp = await fetch(url(payload), {
                    method: method,
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify(payload)
                });
                var data = {};
                try { data = await resp.json(); } catch (e) { /* 非 JSON 响应走状态码兜底 */ }
                if (resp.ok) {
                    onSuccess();
                } else {
                    result.innerHTML = '<div class="alert alert-error">' +
                        LSM.escapeHtml(data.detail || '操作失败（HTTP ' + resp.status + '）') + '</div>';
                }
            } catch (e) {
                // catch 文案规约（U-21）：禁止 '' + e
                result.innerHTML = '<div class="alert alert-error">' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误，请重试') + '</div>';
            } finally {
                LSM.setBtnLoading(btn, false);
            }
        });
    }

    /* ── 用户列表渲染（事件委托，无内联 onclick） ── */
    function loadUsers() {
        fetch('/api/users', { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var users = data.users || [];
                if (users.length === 0) {
                    LSM.showEmptyState(document.getElementById('users-list'), {
                        icon: 'users', text: '暂无用户'
                    });
                    return;
                }
                var html = '<table class="data-table"><thead><tr>' +
                    '<th>用户名</th><th>角色</th><th>显示名称</th><th>状态</th><th>创建时间</th><th>操作</th>' +
                    '</tr></thead><tbody>';
                for (var i = 0; i < users.length; i++) {
                    var u = users[i];
                    var statusCls = u.is_active ? 'badge badge-green' : 'badge badge-gray';
                    var statusText = u.is_active ? '正常' : '已禁用';
                    var roleText = u.role === 'admin' ? '管理员' : '查看者';
                    html += '<tr>';
                    html += '<td>' + LSM.escapeHtml(u.username) + '</td>';
                    html += '<td>' + roleText + '</td>';
                    html += '<td>' + LSM.escapeHtml(u.display_name || '') + '</td>';
                    html += '<td><span class="' + statusCls + '">' + statusText + '</span></td>';
                    // created_at 为 TEXT datetime（models.py: datetime('now')），非时间戳，原样转义展示
                    html += '<td class="text-sm">' + LSM.escapeHtml(u.created_at || '-') + '</td>';
                    html += '<td class="actions">';
                    html += '<button class="btn btn-sm btn-outline" data-action="edit" data-id="' + u.id + '"' +
                        ' data-username="' + LSM.escapeHtml(u.username) + '"' +
                        ' data-display-name="' + LSM.escapeHtml(u.display_name || '') + '"' +
                        ' data-role="' + LSM.escapeHtml(u.role) + '">编辑</button>';
                    html += '<button class="btn btn-sm btn-outline" data-action="reset-pwd" data-id="' + u.id + '"' +
                        ' data-username="' + LSM.escapeHtml(u.username) + '">重置密码</button>';
                    html += '<button class="btn btn-sm btn-outline" data-action="toggle" data-id="' + u.id + '"' +
                        ' data-username="' + LSM.escapeHtml(u.username) + '"' +
                        ' data-active="' + (u.is_active ? '1' : '0') + '">' + (u.is_active ? '禁用' : '启用') + '</button>';
                    html += '<button class="btn btn-sm btn-danger" data-action="delete" data-id="' + u.id + '"' +
                        ' data-username="' + LSM.escapeHtml(u.username) + '">删除</button>';
                    html += '</td></tr>';
                }
                html += '</tbody></table>';
                document.getElementById('users-list').innerHTML = html;
            })
            .catch(function(e) {
                document.getElementById('users-list').innerHTML = '<div class="alert alert-error">加载用户失败: ' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误') + '</div>';
            });
    }

    function bindListActions() {
        var list = document.getElementById('users-list');
        if (!list) return;
        list.addEventListener('click', function(e) {
            var btn = e.target.closest('button[data-action]');
            if (!btn) return;
            var id = btn.dataset.id;
            var username = btn.dataset.username;
            switch (btn.dataset.action) {
                case 'edit':
                    showEditUserForm(id, username, btn.dataset.displayName, btn.dataset.role);
                    break;
                case 'reset-pwd':
                    resetUserPassword(id, username);
                    break;
                case 'toggle':
                    toggleUser(id, username, btn.dataset.active === '1');
                    break;
                case 'delete':
                    deleteUser(id, username);
                    break;
            }
        });
    }

    /* ── 破坏性操作：LSM.confirm + Toast 反馈 ────── */
    async function deleteUser(userId, username) {
        var ok = await LSM.confirm('确定删除用户 "' + username + '" 吗？此操作不可恢复。', '删除用户');
        if (!ok) return;
        try {
            var csrfToken = await LSM.ensureCsrfToken();
            var resp = await fetch('/api/users/' + encodeURIComponent(userId), {
                method: 'DELETE',
                headers: { 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin'
            });
            var data = {};
            try { data = await resp.json(); } catch (e) { /* 非 JSON 响应走状态码兜底 */ }
            if (resp.ok && data.status === 'deleted') {
                LSM.showToast('用户 "' + username + '" 已删除', 'success');
                loadUsers();
            } else {
                LSM.showToast(data.detail || '删除失败', 'error');
            }
        } catch (e) {
            LSM.showToast((e && e.message) ? e.message : '网络错误，请重试', 'error');
        }
    }

    async function toggleUser(userId, username, currentStatus) {
        var action = currentStatus ? '禁用' : '启用';
        var ok = await LSM.confirm('确定' + action + '用户 "' + username + '" 吗？', action + '用户');
        if (!ok) return;
        try {
            var csrfToken = await LSM.ensureCsrfToken();
            var resp = await fetch('/api/users/' + encodeURIComponent(userId) + '/toggle', {
                method: 'PATCH',
                headers: { 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin'
            });
            var data = {};
            try { data = await resp.json(); } catch (e) { /* 非 JSON 响应走状态码兜底 */ }
            if (resp.ok && data.status === 'toggled') {
                LSM.showToast('用户 "' + username + '" 已' + action, 'success');
                loadUsers();
            } else {
                LSM.showToast(data.detail || '操作失败', 'error');
            }
        } catch (e) {
            LSM.showToast((e && e.message) ? e.message : '网络错误，请重试', 'error');
        }
    }

    /* ── 重置密码：LSM.promptPassword（显隐+强度+确认） ── */
    async function resetUserPassword(userId, username) {
        var newPassword = await LSM.promptPassword({
            title: '重置密码',
            label: '为 "' + username + '" 设置新密码',
            confirmText: '重置'
        });
        if (!newPassword) return;   // 取消
        try {
            var csrfToken = await LSM.ensureCsrfToken();
            var resp = await fetch('/api/users/' + encodeURIComponent(userId) + '/password', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken
                },
                credentials: 'same-origin',
                body: JSON.stringify({ new_password: newPassword })
            });
            var data = {};
            try { data = await resp.json(); } catch (e) { /* 非 JSON 响应走状态码兜底 */ }
            if (resp.ok) {
                LSM.showToast('用户 "' + username + '" 密码已重置', 'success');
            } else {
                LSM.showToast(data.detail || '重置失败', 'error');
            }
        } catch (e) {
            LSM.showToast((e && e.message) ? e.message : '网络错误，请重试', 'error');
        }
    }

    /* ── 初始化 ─────────────────────────────────── */
    document.addEventListener('DOMContentLoaded', function() {
        var addBtn = document.getElementById('add-user-btn');
        if (addBtn) addBtn.addEventListener('click', showAddUserForm);
        bindListActions();
        loadUsers();

        bindForm('add-user-form', 'add-user-result', function() {
            return {
                username: document.getElementById('new-username').value.trim(),
                password: document.getElementById('new-password').value,
                display_name: document.getElementById('new-display-name').value.trim(),
                role: document.getElementById('new-role').value
            };
        }, function() { return '/api/users'; }, 'POST', function() {
            hideAddUserForm();
            LSM.showToast('用户创建成功', 'success');
            loadUsers();
        });

        bindForm('edit-user-form', 'edit-user-result', function() {
            var data = {};
            var displayName = document.getElementById('edit-display-name').value.trim();
            var password = document.getElementById('edit-password').value;
            data.role = document.getElementById('edit-role').value;
            if (displayName) data.display_name = displayName;
            if (password) data.password = password;
            return data;
        }, function() { return '/api/users/' + encodeURIComponent(document.getElementById('edit-user-id').value); },
           'PUT', function() {
            hideEditUserForm();
            LSM.showToast('用户已更新', 'success');
            loadUsers();
        });
    });
})();
