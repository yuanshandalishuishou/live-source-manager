/* ── Live Source Manager — Web UI JS ──────────────── */

// ── HTMX 配置 ────────────────────────────────
document.body.addEventListener('htmx:configRequest', function(evt) {
    // 自动处理登录重定向
    evt.detail.headers['HX-Request'] = 'true';
});

// 全局 401 未认证处理：跳转到登录页
document.body.addEventListener('htmx:responseError', function(evt) {
    if (evt.detail.xhr.status === 401) {
        window.location.href = '/login';
    }
});

// ── 源列表渲染（HTMX 结果格式化） ────────────
document.body.addEventListener('htmx:afterSwap', function(evt) {
    const target = evt.detail.target;
    if (target && target.id === 'sources-list') {
        formatSourceList(target);
    }
    if (target && target.id === 'users-list') {
        formatUserList(target);
    }
    // 操作成功/失败提示
    showHtmxAlert(evt);
});

// ── 操作结果提示 ──────────────────────────────
function showHtmxAlert(evt) {
    const el = evt.detail.elt;
    if (!el) return;
    const status = evt.detail.xhr ? evt.detail.xhr.status : 0;
    // Show a toast-like message for form submissions
    if (el.matches('form') || el.matches('[hx-post]') || el.matches('[hx-put]') || el.matches('[hx-delete]')) {
        if (status >= 200 && status < 300) {
            // Success - existing UI handles this via swap targets
        } else if (status >= 400) {
            try {
                const data = JSON.parse(evt.detail.xhr.responseText);
                showToast(data.detail || '操作失败', 'error');
            } catch(e) { showToast('操作失败 (' + status + ')', 'error'); }
        }
    }
}

// ── Toast 消息 ────────────────────────────────
function showToast(message, type) {
    type = type || 'info';
    var existing = document.getElementById('toast-container');
    if (!existing) {
        existing = document.createElement('div');
        existing.id = 'toast-container';
        existing.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:360px';
        document.body.appendChild(existing);
    }
    var toast = document.createElement('div');
    toast.style.cssText = 'padding:12px 16px;border-radius:6px;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,.15);animation:slideIn .2s ease;display:flex;align-items:center;gap:8px';
    if (type === 'success') {
        toast.style.background = '#dcfce7';
        toast.style.color = '#166534';
        toast.style.border = '1px solid #bbf7d0';
    } else if (type === 'error') {
        toast.style.background = '#fee2e2';
        toast.style.color = '#991b1b';
        toast.style.border = '1px solid #fecaca';
    } else {
        toast.style.background = '#dbeafe';
        toast.style.color = '#1e40af';
        toast.style.border = '1px solid #bfdbfe';
    }
    toast.textContent = message;
    existing.appendChild(toast);
    setTimeout(function() {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity .3s';
        setTimeout(function() { toast.remove(); }, 300);
    }, 3000);
}

// ── 源列表格式化 ──────────────────────────────
function formatSourceList(container) {
    try {
        const data = JSON.parse(container.textContent.trim());
        if (!data.sources || !Array.isArray(data.sources)) return;
        let html = '';
        if (data.sources.length === 0) {
            html = '<div class="empty-state" style="text-align:center;padding:40px;color:var(--text-light)">暂无源数据。请先运行采集任务。</div>';
        } else {
            html = '<table><thead><tr><th>名称</th><th>类型</th><th>分组</th><th>URL</th>';
            if (document.querySelector('.user-role')?.textContent === '管理员') {
                html += '<th>操作</th>';
            }
            html += '</tr></thead><tbody>';
            data.sources.forEach(function(s) {
                const name = s.name || '未知';
                const type = s.source_type === 'online' ? '在线' : '本地';
                const group = s.group || s.category || '-';
                const url = (s.url || '').substring(0, 50) + '…';
                const sid = s.id || s.name;
                html += '<tr>';
                html += '<td><strong>' + escapeHtml(name) + '</strong></td>';
                html += '<td><span class="badge badge-' + s.source_type + '">' + type + '</span></td>';
                html += '<td>' + escapeHtml(group) + '</td>';
                html += '<td><code>' + escapeHtml(url) + '</code></td>';
                if (document.querySelector('.user-role')?.textContent === '管理员') {
                    html += '<td class="actions">';
                    html += '<a href="/sources/' + sid + '/edit" class="btn btn-sm btn-outline">编辑</a> ';
                    html += '<button class="btn btn-sm btn-outline" onclick="deleteSource(\'' + sid + '\',\'' + escapeHtml(name) + '\')">删除</button>';
                    html += '</td>';
                }
                html += '</tr>';
            });
            html += '</tbody></table>';
            // 分页
            if (data.total > data.size) {
                const pages = Math.ceil(data.total / data.size);
                html += '<div class="pagination"><span>共 ' + data.total + ' 条 </span>';
                for (let i = 1; i <= pages && i <= 10; i++) {
                    html += '<button class="' + (i === data.page ? 'active' : '') + '" onclick="loadSourcesPage(' + i + ')">' + i + '</button>';
                }
                html += '</div>';
            }
        }
        container.innerHTML = html;
    } catch(e) {
        // 不是 JSON 格式，保持原样（HTMX 直接渲染的 HTML）
    }
}

// ── 用户列表格式化 ────────────────────────────
function formatUserList(container) {
    try {
        const data = JSON.parse(container.textContent.trim());
        if (!data.users) return;
        let html = '<table><thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>显示名称</th><th>创建时间</th><th>状态</th>';
        if (document.querySelector('.user-role')?.textContent === '管理员') {
            html += '<th>操作</th>';
        }
        html += '</tr></thead><tbody>';
        data.users.forEach(function(u) {
            const roleText = u.role === 'admin' ? '管理员' : '查看者';
            const statusText = u.is_active ? '正常' : '已禁用';
            const statusClass = u.is_active ? '' : 'status-disabled';
            const createdAt = u.created_at || '-';
            html += '<tr class="' + statusClass + '">';
            html += '<td>' + u.id + '</td>';
            html += '<td>' + escapeHtml(u.username) + '</td>';
            html += '<td>' + roleText + '</td>';
            html += '<td>' + escapeHtml(u.display_name || '-') + '</td>';
            html += '<td style="font-size:13px;color:var(--text-light)">' + createdAt + '</td>';
            html += '<td><span class="status-badge status-' + (u.is_active ? 'passed' : 'failed') + '">' + statusText + '</span></td>';
            if (document.querySelector('.user-role')?.textContent === '管理员') {
                html += '<td class="actions" style="white-space:nowrap">';
                html += '<button class="btn btn-sm btn-outline" onclick="showEditUserForm(' + u.id + ',\'' + escapeHtml(u.username) + '\',\'' + escapeHtml(u.display_name || '') + '\',\'' + u.role + '\')">编辑</button> ';
                html += '<button class="btn btn-sm btn-outline" onclick="toggleUser(' + u.id + ',\'' + escapeHtml(u.username) + '\',' + (u.is_active ? 'true' : 'false') + ')">' + (u.is_active ? '禁用' : '启用') + '</button> ';
                html += '<button class="btn btn-sm btn-outline" onclick="deleteUser(' + u.id + ',\'' + escapeHtml(u.username) + '\')">删除</button>';
                html += '</td>';
            }
            html += '</tr>';
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch(e) {
        // 不是 JSON
    }
}

function loadSourcesPage(page) {
    const type = document.getElementById('source-type-filter')?.value || 'all';
    htmx.ajax('GET', '/api/sources?type=' + type + '&page=' + page + '&size=50', {
        target: '#sources-list', swap: 'innerHTML'
    });
}

function deleteSource(sid, name) {
    if (!confirm('确定删除源 "' + name + '" 吗？')) return;
    htmx.ajax('DELETE', '/api/sources/' + sid, {
        target: '#sources-list', swap: 'innerHTML',
        handler: function() {
            loadSourcesPage(1);
        }
    });
}

// ── 工具函数 ────────────────────────────────
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

console.log('LSM Web UI loaded');

// ── 加密密钥提示 ───────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
    if (localStorage.getItem('encrypt_key_dismissed')) return;
    fetch('/api/auth/encrypt-key-status', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
            if (!data.has_custom_key) {
                showEncryptKeyBanner();
            }
        })
        .catch(() => {});
});

function showEncryptKeyBanner() {
    const content = document.querySelector('.content-body');
    if (!content) return;
    const banner = document.createElement('div');
    banner.id = 'encrypt-key-banner';
    banner.style.cssText = `
        background: #fef3c7; border: 1px solid #fde68a;
        border-radius: 8px; padding: 12px 16px; margin-bottom: 16px;
        display: flex; align-items: center; justify-content: space-between;
        gap: 12px; font-size: 14px;
    `;
    banner.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;flex:1">
            <span style="font-size:20px">🔐</span>
            <span>
                系统使用<strong>自动生成的加密密钥</strong>，
                建议设置自定义环境变量 <code>CONFIG_ENCRYPT_KEY</code> 以增强安全性。
                <a href="/config" style="color:var(--primary);text-decoration:underline">前往配置页</a>
            </span>
        </div>
        <button onclick="dismissEncryptKeyBanner()" style="
            background:none;border:none;cursor:pointer;font-size:18px;padding:4px;
            color:#92400e;flex-shrink:0;
        " title="我知道了，不再提示">✕</button>
    `;
    content.insertBefore(banner, content.firstChild);
}

function dismissEncryptKeyBanner() {
    localStorage.setItem('encrypt_key_dismissed', '1');
    const banner = document.getElementById('encrypt-key-banner');
    if (banner) banner.remove();
}

// ── CSRF Token 注入 ───────────────────────────
// 页面加载时从 API 获取 CSRF token，在所有写请求中注入
document.addEventListener('DOMContentLoaded', async function() {
    try {
        const resp = await fetch('/api/auth/csrf-token', { credentials: 'same-origin' });
        if (resp.ok) {
            const data = await resp.json();
            window.__csrf_token = data.csrf_token;
        }
    } catch(e) {
        // 未登录时静默失败，登录后会通过 htmx:configRequest 自动注入
    }
});

// 所有 htmx 写请求自动注入 CSRF token
document.body.addEventListener('htmx:configRequest', function(evt) {
    if (window.__csrf_token &&
        evt.detail.verb !== 'GET' &&
        evt.detail.verb !== 'get') {
        evt.detail.headers['X-CSRF-Token'] = window.__csrf_token;
    }
});

// 登录成功后刷新 CSRF token
document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (evt.detail.pathInfo && evt.detail.pathInfo.requestPath === '/api/auth/login') {
        if (evt.detail.successful) {
            fetch('/api/auth/csrf-token', { credentials: 'same-origin' })
                .then(r => r.json())
                .then(data => { window.__csrf_token = data.csrf_token; })
                .catch(() => {});
        }
    }
});
