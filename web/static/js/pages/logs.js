/* ================================================================
 * pages/logs.js — 日志与审计页面脚本
 * 应用日志：level=ALL 直传（不欺诈映射），后端 400/422 时降级为
 *   不带 level 拉取 + 前端按级别集合过滤；行级 LEVEL 着色；
 * 下载：单请求 /api/logs?level=ALL&tail=99999 → blob；
 * 自动刷新：document.hidden 暂停；
 * 审计：徽标映射走 audit-components.js（actionColors），
 *   分页统一 LSM.renderPagination。
 * ================================================================ */
(function() {
    'use strict';

    var autoRefreshTimer = null;
    var currentPage = 1;
    var pageSize = 50;

    // 审计组件（audit-components.js）依赖的分页钩子
    window.loadAuditLogs = loadAuditLogs;
    window.currentPage = 1;
    window.pageSize = pageSize;

    /* ── Tab 切换（class 驱动，保留 aria-selected 实践） ── */
    function switchTab(name) {
        document.querySelectorAll('.logs-page .tabs .tab-btn').forEach(function(el) {
            el.classList.toggle('active', el.dataset.tab === name);
            el.setAttribute('aria-selected', el.dataset.tab === name ? 'true' : 'false');
        });
        document.querySelectorAll('.logs-page .tab-content').forEach(function(el) {
            el.classList.toggle('active', el.id === 'tab-' + name);
        });

        if (name === 'audit-logs') {
            loadActionTypes();
            loadAuditLogs(1);
            stopAutoRefresh();
        } else {
            toggleAutoRefresh();
        }
    }

    /* ── 应用日志 ───────────────────────────────── */
    // 级别处理（T01 修复，Q7 决策）：level=ALL 直传不欺诈；
    // 后端原生支持（system.py：level.upper() != 'ALL' 时才过滤）；
    // 若后端版本不支持（400/422），降级为不带 level 拉取 + 前端按级别集合过滤。
    function fetchLogLines(level, tail) {
        return fetch('/api/logs?level=' + encodeURIComponent(level) + '&tail=' + tail, {
            credentials: 'same-origin'
        })
            .then(function(r) {
                if (r.status === 400 || r.status === 422) {
                    return fetch('/api/logs?tail=' + tail, { credentials: 'same-origin' })
                        .then(function(r2) { return r2.json(); })
                        .then(function(data) {
                            var lines = data.logs || [];
                            if (level === 'ALL') return lines;
                            return lines.filter(function(line) {
                                return line.toUpperCase().indexOf(level.toUpperCase()) !== -1;
                            });
                        });
                }
                return r.json().then(function(data) { return data.logs || []; });
            });
    }

    // 日志行格式：[2026-10-25 12:00:00] [INFO] [name] message
    // 按行首第二个方括号的 LEVEL 字段着色，解析失败不着色
    var LOG_LEVEL_RE = /^\[[^\]]*\]\s+\[(\w+)\]/;

    function logLineClass(line) {
        var m = LOG_LEVEL_RE.exec(line || '');
        if (!m) return 'log-line';
        var level = m[1].toUpperCase();
        if (level === 'ERROR' || level === 'CRITICAL') return 'log-line log-error';
        if (level === 'WARNING' || level === 'WARN') return 'log-line log-warn';
        if (level === 'DEBUG') return 'log-line log-debug';
        return 'log-line';
    }

    function refreshLogs() {
        var level = document.getElementById('log-level').value;
        var tail = document.getElementById('log-tail').value;
        fetchLogLines(level, tail)
            .then(function(logLines) {
                var html = logLines.map(function(line) {
                    return '<div class="' + logLineClass(line) + '">' + LSM.escapeHtml(line) + '</div>';
                }).join('');
                document.getElementById('logs-content').innerHTML = html;
                var countEl = document.getElementById('log-count');
                if (countEl) countEl.textContent = '共 ' + logLines.length + ' 行';
            })
            .catch(function(e) {
                document.getElementById('logs-content').innerHTML = '<div class="alert alert-error">加载失败: ' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误') + '</div>';
            });
    }

    /* ── 自动刷新（页面隐藏时暂停） ──────────────── */
    function isAppTabActive() {
        return !document.getElementById('tab-audit-logs').classList.contains('active');
    }

    function stopAutoRefresh() {
        if (autoRefreshTimer) {
            clearInterval(autoRefreshTimer);
            autoRefreshTimer = null;
        }
    }

    function toggleAutoRefresh() {
        stopAutoRefresh();
        if (document.getElementById('auto-refresh').checked && isAppTabActive() && !document.hidden) {
            autoRefreshTimer = setInterval(refreshLogs, 5000);
        }
    }

    /* ── 下载（单请求 → blob，删原双请求绕行） ───── */
    function downloadLogs() {
        fetch('/api/logs?level=ALL&tail=99999', { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(logData) {
                var blob = new Blob([(logData.logs || []).join('\n')], { type: 'text/plain;charset=utf-8' });
                var a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = 'app.log';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(a.href);
            })
            .catch(function(e) {
                LSM.showToast((e && e.message) ? e.message : '下载失败', 'error');
            });
    }

    /* ── 审计日志 ───────────────────────────────── */
    function loadActionTypes() {
        fetch('/api/audit/actions', { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(actions) {
                var sel = document.getElementById('audit-action-filter');
                if (!sel) return;
                while (sel.options.length > 1) sel.remove(1);
                (actions || []).forEach(function(a) {
                    var opt = document.createElement('option');
                    opt.value = a;
                    opt.textContent = actionLabels[a] || a;
                    sel.appendChild(opt);
                });
            })
            .catch(function() { /* 操作类型下拉加载失败时保留"全部" */ });
    }

    function loadAuditLogs(page) {
        page = page || 1;
        currentPage = page;
        window.currentPage = page;
        var action = document.getElementById('audit-action-filter').value;
        var url = '/api/audit?page=' + page + '&size=' + pageSize;
        if (action) url += '&action=' + encodeURIComponent(action);

        fetch(url, { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(data) { renderAuditLogs(data); })
            .catch(function(e) {
                document.getElementById('audit-list').innerHTML = '<div class="alert alert-error">加载失败: ' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误') + '</div>';
            });
    }

    /* ── 初始化 ─────────────────────────────────── */
    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('.logs-page .tabs .tab-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var tabName = btn.dataset.tab;
                if (tabName) switchTab(tabName);
            });
        });

        // 应用日志工具栏
        var levelSel = document.getElementById('log-level');
        if (levelSel) levelSel.addEventListener('change', refreshLogs);
        var tailInput = document.getElementById('log-tail');
        if (tailInput) tailInput.addEventListener('change', refreshLogs);
        var refreshBtn = document.getElementById('btn-refresh-logs');
        if (refreshBtn) refreshBtn.addEventListener('click', refreshLogs);
        var downloadBtn = document.getElementById('btn-download-logs');
        if (downloadBtn) downloadBtn.addEventListener('click', downloadLogs);

        // 审计工具栏
        var auditFilter = document.getElementById('audit-action-filter');
        if (auditFilter) auditFilter.addEventListener('change', function() { loadAuditLogs(1); });
        var auditRefreshBtn = document.getElementById('btn-refresh-audit');
        if (auditRefreshBtn) auditRefreshBtn.addEventListener('click', function() { loadAuditLogs(currentPage); });

        toggleAutoRefresh();
        refreshLogs();

        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                stopAutoRefresh();
            } else if (isAppTabActive()) {
                toggleAutoRefresh();
                refreshLogs();
            }
        });
    });
})();
