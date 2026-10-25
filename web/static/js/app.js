/* ── Live Source Manager — 应用胶水 ─────────────────
 * 组件实现已统一迁至 lsm-components.js（window.LSM，唯一实现）。
 * 本文件只保留：全局别名、原生登出、加密密钥横幅。
 * 已删除（死代码/迁出）：formatSourceList/formatUserList/loadSourcesPage/
 * deleteSource/showSourceCategories/saveSourceCategories/hideSourceCatModal、
 * 裸 showToast/escapeHtml/escapeJs 实现体、LSM IIFE、console.log、
 * 表单/登出的事件胶水（已改原生 fetch，见 pages/auth.js 与本文件 doLogout）。
 * ─────────────────────────────────────────────────── */
(function() {
    'use strict';

    // ── 全局别名（存量裸调用点零改动兼容） ─────────────
    // 注意: 不别名 confirm/alert/prompt —— LSM.confirm 返回 Promise，
    // 裸 if(!confirm(...)) 守卫会恒真放行破坏性操作；页面脚本一律显式 LSM.*
    window.showToast = window.LSM.showToast;
    window.escapeHtml = window.LSM.escapeHtml;
    window.escapeJs = window.LSM.escapeJs;
    window.ensureCsrfToken = window.LSM.ensureCsrfToken;
    window.formatDate = window.LSM.formatDate;
    window.isAdmin = window.LSM.isAdmin;

    // ── 原生登出（base.html 退出登录按钮 onclick 调用） ──
    // 无论成功或失败（含会话已失效的 401/403 或网络异常），都回到登录页。
    window.doLogout = async function() {
        var csrfToken = '';
        try {
            csrfToken = await window.LSM.ensureCsrfToken();
        } catch (e) {
            csrfToken = window.__csrf_token || '';
        }
        try {
            await fetch('/api/auth/logout', {
                method: 'POST',
                headers: { 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin'
            });
        } catch (e) {
            // 网络异常同样回登录页，不阻断用户登出
        }
        window.location.href = '/login';
    };

    // ── 加密密钥提示横幅（样式类化，见 app.css） ───────
    document.addEventListener('DOMContentLoaded', function() {
        if (localStorage.getItem('encrypt_key_dismissed')) return;
        fetch('/api/auth/encrypt-key-status', { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.has_custom_key) {
                    showEncryptKeyBanner();
                }
            })
            .catch(function() {});
    });

    function showEncryptKeyBanner() {
        var content = document.querySelector('.content-body');
        if (!content) return;
        var banner = document.createElement('div');
        banner.id = 'encrypt-key-banner';
        banner.className = 'encrypt-banner';
        banner.innerHTML =
            '<div class="encrypt-banner-text">' +
                '<span class="encrypt-banner-icon" aria-hidden="true">' + LSM.icon('lock') + '</span>' +
                '<span>系统使用<strong>自动生成的加密密钥</strong>，建议设置自定义环境变量 ' +
                '<code>CONFIG_ENCRYPT_KEY</code> 以增强安全性。' +
                '<a href="/config">前往配置页</a></span>' +
            '</div>' +
            '<button type="button" class="encrypt-banner-close" aria-label="不再提示" title="我知道了，不再提示">✕</button>';
        banner.querySelector('.encrypt-banner-close')
            .addEventListener('click', dismissEncryptKeyBanner);
        content.insertBefore(banner, content.firstChild);
    }

    function dismissEncryptKeyBanner() {
        localStorage.setItem('encrypt_key_dismissed', '1');
        var banner = document.getElementById('encrypt-key-banner');
        if (banner) banner.remove();
    }

    // 页面加载即确保 CSRF token 就绪（base.html 渲染时已内联注入，此处兜底刷新）
    document.addEventListener('DOMContentLoaded', function() {
        window.LSM.ensureCsrfToken();
    });
})();
