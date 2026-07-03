/* ================================================================
 * audit-components.js — 审计日志共享组件
 * 被 audit.html 和 logs.html 共同引用
 * ================================================================ */

// ── 审计日志操作类型中文标签映射 ────────────────
var actionLabels = {
    'login': '登录', 'logout': '退出', 'config_update': '配置更新',
    'config_section_update': '配置段更新', 'config_reload': '配置重载',
    'source_add': '添加源', 'source_update': '更新源', 'source_delete': '删除源',
    'user_create': '创建用户', 'user_update': '更新用户', 'user_delete': '删除用户',
    'user_enable': '启用用户', 'user_disable': '禁用用户', 'user_password_reset': '重置密码',
    'test_trigger': '测试触发', 'encrypt_key_update': '更新加密密钥',
    'password_change': '修改密码',
};

// ── 审计日志操作类型颜色映射（预留，可选使用） ──
var actionColors = {
    'login': 'passed', 'logout': 'disabled', 'config_update': 'testing',
    'config_section_update': 'testing', 'config_reload': 'testing',
    'source_add': 'passed', 'source_update': 'testing', 'source_delete': 'failed',
    'user_create': 'passed', 'user_update': 'testing', 'user_delete': 'failed',
    'user_enable': 'passed', 'user_disable': 'failed', 'user_password_reset': 'testing',
    'test_trigger': 'testing', 'encrypt_key_update': 'testing',
    'password_change': 'testing',
};

// ── 格式化时间戳 ────────────────────────────────
function formatTimestamp(ts) {
    if (!ts) return '-';
    return ts;
}

// ── 渲染审计日志表格 ────────────────────────────
function renderAuditLogs(data) {
    var container = document.getElementById('audit-list');
    var totalEl = document.getElementById('audit-total');
    var paginationEl = document.getElementById('audit-pagination');

    if (totalEl) totalEl.textContent = data.total ? '共 ' + data.total + ' 条' : '';

    if (!data.logs || data.logs.length === 0) {
        if (container) {
            container.innerHTML =
                '<div class="empty-state" style="text-align:center;padding:40px;color:var(--text-light)">暂无审计日志</div>';
        }
        if (paginationEl) paginationEl.innerHTML = '';
        return;
    }

    var html = '<table><thead><tr>' +
        '<th>时间</th><th>用户</th><th>操作类型</th><th>操作对象</th><th>详情</th><th>IP</th>' +
        '</tr></thead><tbody>';

    data.logs.forEach(function(l) {
        var actionLabel = actionLabels[l.action] || l.action;
        var detail = l.detail || '';
        if (detail.length > 80) detail = detail.substring(0, 80) + '…';
        html += '<tr>' +
            '<td style="font-size:12px;white-space:nowrap">' + formatTimestamp(l.created_at) + '</td>' +
            '<td>' + escapeHtml(l.username) + '</td>' +
            '<td><span class="status-badge status-audit-' + l.action + '">' + actionLabel + '</span></td>' +
            '<td>' + escapeHtml(l.target || '-') + '</td>' +
            '<td style="font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(detail) + '">' + escapeHtml(detail) + '</td>' +
            '<td style="font-size:12px;color:var(--text-light)">' + escapeHtml(l.ip_address || '-') + '</td>' +
            '</tr>';
    });
    html += '</tbody></table>';
    if (container) container.innerHTML = html;

    // 分页
    if (paginationEl) {
        var totalPages = Math.ceil(data.total / pageSize);
        paginationEl.innerHTML = '';
        if (totalPages > 1) {
            var phtml = '';
            if (currentPage > 1) {
                phtml += '<button class="btn btn-sm btn-outline" onclick="loadAuditLogs(' + (currentPage - 1) + ')">上一页</button>';
            }
            for (var i = 1; i <= totalPages && i <= 10; i++) {
                phtml += '<button class="btn btn-sm ' + (i === currentPage ? 'btn-primary' : 'btn-outline') + '" onclick="loadAuditLogs(' + i + ')">' + i + '</button>';
            }
            if (currentPage < totalPages) {
                phtml += '<button class="btn btn-sm btn-outline" onclick="loadAuditLogs(' + (currentPage + 1) + ')">下一页</button>';
            }
            paginationEl.innerHTML = phtml;
        }
    }
}

// ── escapeHtml 辅助函数 ─────────────────────────
function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
