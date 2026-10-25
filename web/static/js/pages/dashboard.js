/* ================================================================
 * pages/dashboard.js — 仪表盘页面脚本
 * 统计卡 / 频道 Top10 进度条 / 系统状态 / 快速操作；
 * 30s 轮询 + document.hidden 可见性暂停；版本号无硬编码回退。
 * ================================================================ */
(function() {
    'use strict';

    var POLL_INTERVAL = 30000;
    var pollTimer = null;

    /* ── 统计卡片 ───────────────────────────────── */
    function loadStats() {
        fetch('/api/dashboard/stats')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var rateText = data.rate || '0%';
                var rateCls = 'card stat-rate ' + rateVariantClass(rateText);
                var html = '';
                html += '<div class="card stat-total"><div class="stat-value">' + (data.total_sources || 0) + '</div><div class="stat-label">直播源总数</div></div>';
                html += '<div class="card stat-valid"><div class="stat-value">' + (data.valid || 0) + '</div><div class="stat-label">有效源</div></div>';
                html += '<div class="card stat-invalid"><div class="stat-value">' + (data.invalid || 0) + '</div><div class="stat-label">无效源</div></div>';
                html += '<div class="' + rateCls + '"><div class="stat-value">' + LSM.escapeHtml(rateText) + '</div><div class="stat-label">有效率</div></div>';
                document.getElementById('stats-cards').innerHTML = html;
            })
            .catch(function() {
                document.getElementById('stats-cards').innerHTML =
                    '<div class="card loading">加载失败</div>';
            });
    }

    // 有效率语义色：≥60% success / 30–60% warning / <30% error
    function rateVariantClass(rateText) {
        var m = /([\d.]+)/.exec(rateText || '');
        var v = m ? parseFloat(m[1]) : 0;
        if (v >= 60) return 'stat-rate-success';
        if (v >= 30) return 'stat-rate-warning';
        return 'stat-rate-error';
    }

    /* ── 频道分组统计（Top10 进度条） ────────────── */
    function loadChannelStats() {
        fetch('/api/dashboard/channel-stats')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var channels = (data.channels || []).slice();
                channels.sort(function(a, b) { return (b.count || 0) - (a.count || 0); });
                channels = channels.slice(0, 10);
                if (channels.length === 0) {
                    LSM.showEmptyState(document.getElementById('channel-stats'), {
                        icon: '📊', text: '暂无频道数据'
                    });
                    return;
                }
                var max = channels[0].count || 1;
                var html = '';
                for (var i = 0; i < channels.length; i++) {
                    var ch = channels[i];
                    var pct = Math.max(2, Math.round((ch.count || 0) / max * 100));
                    html += '<div class="stat-row">' +
                        '<span>' + LSM.escapeHtml(ch.name) + '</span>' +
                        '<div class="progress-bar channel-bar"><div class="progress-fill" style="width:' + pct + '%"></div></div>' +
                        '<span class="channel-count">' + (ch.count || 0) + '</span>' +
                        '</div>';
                }
                document.getElementById('channel-stats').innerHTML = html;
            })
            .catch(function() {
                document.getElementById('channel-stats').innerHTML =
                    '<div class="loading">加载失败</div>';
            });
    }

    /* ── 系统运行状态 ───────────────────────────── */
    function loadSystemStatus() {
        fetch('/api/dashboard/status')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var sys = data.system || {};
                var test = data.test || {};
                var html = '<div class="status-grid">';
                html += '<div class="status-item"><span class="label">内存使用</span><span>' + LSM.escapeHtml(sys.memory_usage || 'N/A') + '</span></div>';
                html += '<div class="status-item"><span class="label">CPU</span><span>' + LSM.escapeHtml(sys.cpu || 'N/A') + '</span></div>';
                html += '<div class="status-item"><span class="label">FFmpeg/FFprobe</span><span class="' + (sys.ffprobe_available ? 'status-passed' : 'status-failed') + '">' + (sys.ffprobe_available ? '可用' : '不可用') + '</span></div>';
                html += '<div class="status-item"><span class="label">服务进程</span><span>' + (sys.process_running ? '运行中' : '已停止') + '</span></div>';
                // 版本号无硬编码回退（U-19）：无值显示「—」
                html += '<div class="status-item"><span class="label">版本</span><span>' + LSM.escapeHtml(data.version || '—') + '</span></div>';
                html += '</div>';
                if (test.last_run) {
                    var testStatus = test.status === 'running' ? '运行中' : (test.status === 'completed' ? '已完成' : '空闲');
                    html += '<div class="test-section">';
                    html += '<div class="form-section-title">🧪 最近测试</div>';
                    html += '<div class="status-grid">';
                    html += '<div class="status-item"><span class="label">状态</span><span>' + testStatus + '</span></div>';
                    var passed = test.passed || 0;
                    var failed = test.failed || 0;
                    var total = test.total || 0;
                    var pct = total > 0 ? ((passed / total * 100).toFixed(1) + '%') : '-';
                    html += '<div class="status-item"><span class="label">通过 / 失败</span><span>' + passed + ' / ' + failed + ' (' + pct + ')</span></div>';
                    html += '<div class="status-item"><span class="label">最后运行</span><span>' + LSM.escapeHtml(test.last_run) + '</span></div>';
                    html += '</div></div>';
                }
                document.getElementById('system-status').innerHTML = html;
            })
            .catch(function() {
                document.getElementById('system-status').innerHTML =
                    '<div class="loading">加载失败</div>';
            });
    }

    /* ── 轮询（页面隐藏时暂停，恢复时立即刷新） ──── */
    function loadAll() {
        loadStats();
        loadChannelStats();
        loadSystemStatus();
    }

    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(loadAll, POLL_INTERVAL);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            stopPolling();
        } else {
            loadAll();
            startPolling();
        }
    });

    document.addEventListener('DOMContentLoaded', function() {
        loadAll();
        startPolling();
    });
})();
