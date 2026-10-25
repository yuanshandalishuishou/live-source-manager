/* ================================================================
 * audit-components.js — 审计日志共享组件（logs.html 审计 tab）
 *
 * T03 变更：
 *   - 徽标由未定义的 status-audit-* 改 .badge + actionColors 映射
 *     （G-4：passed→green / failed→red / testing→yellow / 其他→gray）
 *   - escapeHtml 收敛至 LSM.escapeHtml（§5.4 台账），删本地实现
 *   - 分页统一 LSM.renderPagination（U-20），删手写上下页两套
 *   - 空态统一 LSM.showEmptyState
 * 依赖（由 logs.js 以全局契约提供）：currentPage / pageSize / loadAuditLogs
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

// ── 审计日志操作类型颜色映射（语义色 → 徽标色） ──
var actionColors = {
    'login': 'passed', 'logout': 'disabled', 'config_update': 'testing',
    'config_section_update': 'testing', 'config_reload': 'testing',
    'source_add': 'passed', 'source_update': 'testing', 'source_delete': 'failed',
    'user_create': 'passed', 'user_update': 'testing', 'user_delete': 'failed',
    'user_enable': 'passed', 'user_disable': 'failed', 'user_password_reset': 'testing',
    'test_trigger': 'testing', 'encrypt_key_update': 'testing',
    'password_change': 'testing',
};

// 语义色名 → .badge-* 徽标变体
var ACTION_BADGE_VARIANTS = {
    'passed': 'green', 'failed': 'red', 'testing': 'yellow', 'disabled': 'gray'
};

// ── 操作类型 → 徽标 class（G-4） ────────────────
function actionBadgeClass(action) {
    var semantic = actionColors[action] || 'disabled';
    return 'badge badge-' + (ACTION_BADGE_VARIANTS[semantic] || 'gray');
}

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
            LSM.showEmptyState(container, { icon: 'search', text: '暂无审计日志' });
        }
        if (paginationEl) paginationEl.innerHTML = '';
        return;
    }

    var html = '<table class="data-table"><thead><tr>' +
        '<th>时间</th><th>用户</th><th>操作类型</th><th>操作对象</th><th>详情</th><th>IP</th>' +
        '</tr></thead><tbody>';

    data.logs.forEach(function(l) {
        var actionLabel = actionLabels[l.action] || l.action;
        var detail = l.detail || '';
        if (detail.length > 80) detail = detail.substring(0, 80) + '…';
        html += '<tr>' +
            '<td class="text-sm">' + LSM.escapeHtml(formatTimestamp(l.created_at)) + '</td>' +
            '<td>' + LSM.escapeHtml(l.username) + '</td>' +
            '<td><span class="' + actionBadgeClass(l.action) + '">' + LSM.escapeHtml(actionLabel) + '</span></td>' +
            '<td>' + LSM.escapeHtml(l.target || '-') + '</td>' +
            '<td class="text-sm detail-cell" title="' + LSM.escapeHtml(detail) + '">' + LSM.escapeHtml(detail) + '</td>' +
            '<td class="text-sm text-muted">' + LSM.escapeHtml(l.ip_address || '-') + '</td>' +
            '</tr>';
    });
    html += '</tbody></table>';
    if (container) container.innerHTML = html;

    // 分页：统一 LSM.renderPagination（U-20）
    if (paginationEl) {
        var totalPages = Math.max(1, Math.ceil((data.total || 0) / pageSize));
        LSM.renderPagination(paginationEl, {
            page: currentPage || 1,
            totalPages: totalPages,
            total: data.total || 0,
            onChange: function(page) {
                if (typeof window.loadAuditLogs === 'function') window.loadAuditLogs(page);
            }
        });
    }
}
