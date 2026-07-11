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
                html += '<td class="actions">';
                if (document.querySelector('.user-role')?.textContent === '管理员') {
                    html += '<button class="btn btn-sm btn-outline" onclick="showSourceCategories(\'' + sid + '\')">🏷️ 分类</button> ';
                    html += '<a href="/sources/' + sid + '/edit" class="btn btn-sm btn-outline">编辑</a> ';
                    html += '<button class="btn btn-sm btn-outline" onclick="deleteSource(\'' + sid + '\',\'' + escapeJs(name) + '\')">删除</button>';
                }
                html += '</td>';
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
                html += '<button class="btn btn-sm btn-outline" onclick="showEditUserForm(' + u.id + ',\'' + escapeJs(u.username) + '\',\'' + escapeJs(u.display_name || '') + '\',\'' + u.role + '\')">编辑</button> ';
                html += '<button class="btn btn-sm btn-outline" onclick="toggleUser(' + u.id + ',\'' + escapeJs(u.username) + '\',' + (u.is_active ? 'true' : 'false') + ')">' + (u.is_active ? '禁用' : '启用') + '</button> ';
                html += '<button class="btn btn-sm btn-outline" onclick="resetUserPassword(' + u.id + ',\'' + escapeJs(u.username) + '\')">🔑 重置密码</button> ';
                html += '<button class="btn btn-sm btn-outline" onclick="deleteUser(' + u.id + ',\'' + escapeJs(u.username) + '\')">删除</button>';
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
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// 用于把数据安全地嵌入单引号 JS 字符串字面量，如 onclick="f('"+escapeJs(x)+"')"
// 同时做 HTML 转义，确保外层双引号 HTML 属性不被破坏
function escapeJs(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, "\\'")
        .replace(/\\/g, '\\\\')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r');
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
    await ensureCsrfToken();
});

// 异步获取 CSRF token，避免时序问题（token 未加载时用户就触发了写操作）
async function ensureCsrfToken() {
    if (window.__csrf_token) return window.__csrf_token;
    try {
        const resp = await fetch('/api/auth/csrf-token', { credentials: 'same-origin' });
        if (resp.ok) {
            const data = await resp.json();
            window.__csrf_token = data.csrf_token;
        }
    } catch(e) {
        // 未登录时静默失败，登录后会通过 htmx:configRequest 自动注入
    }
    return window.__csrf_token || '';
}

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

// ── 源分类弹窗 ──────────────────────────
function showSourceCategories(sourceId) {
    var modal = document.getElementById('source-cat-modal');
    if (!modal) return;
    modal.style.display = 'flex';
    var body = document.getElementById('source-cat-body');
    body.innerHTML = '<div class="loading">加载中...</div>';
    modal.dataset.sourceId = sourceId;
    // 同时获取源信息取得 channel_name
    fetch('/api/sources/' + encodeURIComponent(sourceId))
    .then(function(r) { return r.json(); })
    .then(function(src) {
        modal.dataset.channelName = src.name || '';
    })
    .catch(function() {
        modal.dataset.channelName = '';
    });

    fetch('/api/sources/' + encodeURIComponent(sourceId) + '/categories')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var modal = document.getElementById('source-cat-modal');
            var chName = modal ? (modal.dataset.channelName || '') : '';
            var html = '<div style="margin-bottom:12px">频道: <strong id="source-cat-channel-name">' + escapeHtml(chName) + '</strong></div>';
            html += '<table><thead><tr><th>维度</th><th>当前分类</th><th>修正</th></tr></thead><tbody>';
            var dims = data.dimensions || [];
            var cats = data.categories || {};
            dims.forEach(function(dim) {
                var val = cats[dim.dim_key] || '-';
                html += '<tr>';
                html += '<td><strong>' + escapeHtml(dim.dim_name) + '</strong></td>';
                html += '<td><span class="badge badge-green">' + escapeHtml(val) + '</span></td>';
                html += '<td>';
                html += '<input type="text" class="form-input form-input-sm" style="width:160px" ';
                html += 'id="cat-input-' + dim.dim_key + '" ';
                html += 'value="' + escapeHtml(val === '-' ? '' : val) + '" ';
                html += 'placeholder="手动输入分类值">';
                html += '</td></tr>';
            });
            html += '</tbody></table>';
            html += '<div id="source-cat-result"></div>';
            body.innerHTML = html;
        })
        .catch(function(e) {
            body.innerHTML = '<div class="alert alert-error">加载失败: ' + e.message + '</div>';
        });
}

function hideSourceCatModal() {
    var modal = document.getElementById('source-cat-modal');
    if (modal) modal.style.display = 'none';
}

// ── 保存维度修正（保存到 channel_name_mapping） ──
function saveSourceCategories() {
    var modal = document.getElementById('source-cat-modal');
    if (!modal) return;
    var resultEl = document.getElementById('source-cat-result');
    if (!resultEl) return;

    var csrfToken = window.__csrf_token || '';
    var inputs = document.querySelectorAll('#source-cat-body input[id^="cat-input-"]');
    
    // 收集各维度值
    var categories = {};
    var hasChanges = false;
    inputs.forEach(function(input) {
        var dimKey = input.id.replace('cat-input-', '');
        var dimValue = input.value.trim();
        if (dimValue) {
            categories[dimKey] = dimValue;
            hasChanges = true;
        }
    });

    if (!hasChanges) {
        showToast('没有需要保存的修改', 'info');
        return;
    }

    // 从 modal 中获取 channel_name
    var channelNameEl = document.getElementById('source-cat-channel-name');
    var channelName = channelNameEl ? channelNameEl.textContent.trim() : '';
    if (!channelName) {
        showToast('无法获取频道名称', 'error');
        return;
    }

    resultEl.innerHTML = '<div style="color:var(--text-light)">⏳ 保存中...</div>';

    fetch('/api/channel-mapping/' + encodeURIComponent(channelName), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify(categories)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.message) {
            resultEl.innerHTML = '<div class="alert alert-success">✅ ' + escapeHtml(data.message) + '</div>';
            showToast('分类已保存', 'success');
        } else {
            resultEl.innerHTML = '<div class="alert alert-warning">保存返回异常</div>';
        }
    })
    .catch(function(e) {
        resultEl.innerHTML = '<div class="alert alert-error">保存失败: ' + e.message + '</div>';
    });
}
/* ================================================================
 * LSM Web UI — 通用组件库（v2 统一配置管理页增强）
 * 目标: 提供统一的 Modal, Toast, Tabs, Toggle 组件
 * 使用 IIFE 模式，所有公共 API 暴露在 LSM 命名空间下
 * ================================================================ */

window.LSM = (function() {
    'use strict';

    // ── Toast 通知系统 ───────────────────────────────
    // 与 app.js 中的 showToast() 共存，使用 LSM.showToast() 调用
    function showToast(message, type, duration) {
        type = type || 'info';
        duration = duration || 3000;
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:400px';
            document.body.appendChild(container);
        }
        const toast = document.createElement('div');
        toast.style.cssText = 'padding:12px 16px;border-radius:6px;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,.15);animation:slideIn .2s ease;display:flex;align-items:center;gap:8px;word-break:break-word';
        const colors = {
            success: { bg: '#dcfce7', color: '#166534', border: '#bbf7d0', icon: '✅' },
            error:   { bg: '#fee2e2', color: '#991b1b', border: '#fecaca', icon: '❌' },
            info:    { bg: '#dbeafe', color: '#1e40af', border: '#bfdbfe', icon: 'ℹ️' },
            warning: { bg: '#fef3c7', color: '#92400e', border: '#fde68a', icon: '⚠️' },
        };
        const c = colors[type] || colors.info;
        toast.style.background = c.bg;
        toast.style.color = c.color;
        toast.style.border = '1px solid ' + c.border;
        toast.innerHTML = '<span>' + c.icon + '</span><span>' + escapeHtml(message) + '</span>';
        container.appendChild(toast);
        setTimeout(function() {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity .3s';
            setTimeout(function() { toast.remove(); }, 300);
        }, duration);
    }

    // ── Modal 对话框 ────────────────────────────────
    // options: { title, content (HTML string), width, buttons, onClose }
    //   buttons: [{ text, action, type ('primary'|'danger'|'outline'), onClick(close), closeOnClick }]
    function showModal(options) {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.5);animation:fadeIn .15s ease';

        const modal = document.createElement('div');
        modal.className = 'modal-box';
        modal.style.cssText = 'background:var(--card-bg);border-radius:var(--radius);padding:24px;width:' + (options.width || '480px') + ';max-width:90vw;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.3)';

        var btnsHtml = '';
        if (options.buttons) {
            btnsHtml = options.buttons.map(function(b) {
                var cls = b.type === 'primary' ? 'btn btn-primary'
                    : b.type === 'danger'  ? 'btn btn-danger'
                    :                        'btn btn-outline';
                return '<button class="' + cls + '" data-action="' + (b.action || '') + '">' + escapeHtml(b.text) + '</button>';
            }).join(' ');
        }

        modal.innerHTML = ''
            + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">'
            +   '<h3 style="font-size:18px;font-weight:600">' + escapeHtml(options.title || '提示') + '</h3>'
            +   '<button class="modal-close" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text-light);line-height:1">&times;</button>'
            + '</div>'
            + '<div class="modal-body">' + (options.content || '') + '</div>'
            + (btnsHtml
                ? '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">' + btnsHtml + '</div>'
                : '');

        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        // 关闭处理
        var close = function() {
            overlay.remove();
            if (options.onClose) options.onClose();
        };
        overlay.querySelector('.modal-close').onclick = close;
        overlay.onclick = function(e) {
            if (e.target === overlay) close();
        };

        // 按钮点击处理
        if (options.buttons) {
            modal.querySelectorAll('[data-action]').forEach(function(btn) {
                btn.onclick = function() {
                    var action = btn.dataset.action;
                    var handler = null;
                    for (var i = 0; i < options.buttons.length; i++) {
                        if (options.buttons[i].action === action) {
                            handler = options.buttons[i];
                            break;
                        }
                    }
                    if (handler && handler.onClick) {
                        var result = handler.onClick(close);
                        if (result === false) return;    // 阻止关闭
                    }
                    if (!handler || handler.closeOnClick !== false) {
                        close();
                    }
                };
            });
        }

        return { overlay: overlay, modal: modal, close: close };
    }

    // ── Tabs 组件 ──────────────────────────────────
    // container 内需包含 .tab-btn（data-tab 指向 panel id）和 .tab-content 面板
    function initTabs(container) {
        var tabs = container.querySelectorAll('.tab-btn');
        var panels = container.querySelectorAll('.tab-content');
        tabs.forEach(function(tab) {
            tab.onclick = function() {
                tabs.forEach(function(t) { t.classList.remove('active'); });
                panels.forEach(function(p) { p.classList.remove('active'); });
                tab.classList.add('active');
                var target = document.getElementById(tab.dataset.tab);
                if (target) target.classList.add('active');
            };
        });
    }

    // ── Toggle Switch（开关控件） ──────────────────
    // 返回 <label class="toggle"> 元素，内含 checkbox 输入
    function createToggle(checked, onChange) {
        var label = document.createElement('label');
        label.className = 'toggle';
        var input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = !!checked;
        input.onchange = function() {
            if (onChange) onChange(input.checked);
        };
        var slider = document.createElement('span');
        slider.className = 'toggle-slider';
        label.appendChild(input);
        label.appendChild(slider);
        return label;
    }

    // ── 确认对话框（Promise 封装） ─────────────────
    function confirm(message, title) {
        return new Promise(function(resolve) {
            showModal({
                title: title || '确认操作',
                content: '<p style="font-size:14px;color:var(--text)">' + escapeHtml(message) + '</p>',
                buttons: [
                    { text: '取消', action: 'cancel', type: 'outline', onClick: function() { resolve(false); } },
                    { text: '确认', action: 'confirm', type: 'danger', onClick: function() { resolve(true); } },
                ]
            });
        });
    }

    // ── 工具函数 ──────────────────────────────────
    function escapeHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatDate(ts) {
        if (!ts) return '-';
        var d = new Date(ts * 1000);
        return d.toLocaleString('zh-CN', { hour12: false });
    }

    // 暴露公共 API
    return {
        showToast:   showToast,
        showModal:   showModal,
        initTabs:    initTabs,
        createToggle: createToggle,
        confirm:     confirm,
        escapeHtml:  escapeHtml,
        escapeJs:    escapeJs,
        formatDate:  formatDate
    };
})();
