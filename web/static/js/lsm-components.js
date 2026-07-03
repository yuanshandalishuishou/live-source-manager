/* ================================================================
 * lsm-components.js — Live Source Manager 前端组件库
 * 包含: Toast, Modal, Tabs, Toggle, Confirm 等通用组件
 * ================================================================
 * 注意: 本文件为 LSM 全局命名空间的另一入口。
 * 组件已在 app.js 中以 LSM 命名空间提供，本文件作为兼容性占位。
 * ================================================================ */

// 如果 LSM 尚未定义（例如 app.js 未加载），则在此定义
if (typeof LSM === 'undefined') {
    var LSM = (function() {
        'use strict';

        // ── Toast 通知 ───────────────────────────────
        function showToast(message, type, duration) {
            type = type || 'info';
            duration = duration || 3000;
            var container = document.getElementById('toast-container');
            if (!container) {
                container = document.createElement('div');
                container.id = 'toast-container';
                container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:400px';
                document.body.appendChild(container);
            }
            var toast = document.createElement('div');
            var colors = {
                success: { bg: '#dcfce7', color: '#166534', icon: '✅' },
                error:   { bg: '#fee2e2', color: '#991b1b', icon: '❌' },
                info:    { bg: '#dbeafe', color: '#1e40af', icon: 'ℹ️' },
                warning: { bg: '#fef3c7', color: '#92400e', icon: '⚠️' },
            };
            var c = colors[type] || colors.info;
            toast.style.cssText = 'padding:12px 16px;border-radius:6px;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,.15);display:flex;align-items:center;gap:8px;background:' + c.bg + ';color:' + c.color + ';border:1px solid ' + (type === 'success' ? '#bbf7d0' : type === 'error' ? '#fecaca' : type === 'warning' ? '#fde68a' : '#bfdbfe');
            toast.innerHTML = '<span>' + c.icon + '</span><span>' + escapeHtml(message) + '</span>';
            container.appendChild(toast);
            setTimeout(function() {
                toast.style.opacity = '0';
                toast.style.transition = 'opacity .3s';
                setTimeout(function() { toast.remove(); }, 300);
            }, duration);
        }

        // ── 确认对话框 ──────────────────────────────
        function confirm(message, title) {
            return new Promise(function(resolve) {
                if (window.confirm(title ? title + '\n' + message : message)) {
                    resolve(true);
                } else {
                    resolve(false);
                }
            });
        }

        // ── Toggle Switch ────────────────────────────
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

        // ── escapeHtml ──────────────────────────────
        function escapeHtml(str) {
            if (!str) return '';
            var div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        return {
            showToast:   showToast,
            confirm:     confirm,
            createToggle: createToggle,
            escapeHtml:  escapeHtml,
        };
    })();
}
