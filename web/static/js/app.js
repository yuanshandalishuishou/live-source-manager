/* ── Live Source Manager — 应用胶水 ─────────────────
 * 组件实现已统一迁至 lsm-components.js（window.LSM，唯一实现）。
 * 本文件只保留：全局别名、htmx 事件胶水、加密密钥横幅。
 * 已删除（死代码/迁出）：formatSourceList/formatUserList/loadSourcesPage/
 * deleteSource/showSourceCategories/saveSourceCategories/hideSourceCatModal、
 * 裸 showToast/escapeHtml/escapeJs 实现体、LSM IIFE、console.log。
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

    // ── htm 写操作结果提示 ────────────────────────────
    // 成功提示仅对声明 data-toast 属性的触发元素补报，避免与 swap 目标自行渲染双报
    function showHtmxAlert(evt) {
        var el = evt.detail.elt;
        if (!el) return;
        var status = evt.detail.xhr ? evt.detail.xhr.status : 0;
        if (!(el.matches('form') || el.matches('[hx-post]') || el.matches('[hx-put]') || el.matches('[hx-delete]'))) {
            return;
        }
        if (status >= 200 && status < 300) {
            if (el.dataset && el.dataset.toast) {
                window.LSM.showToast(el.dataset.toast, 'success');
            }
        } else if (status >= 400) {
            try {
                var data = JSON.parse(evt.detail.xhr.responseText);
                window.LSM.showToast(data.detail || '操作失败', 'error');
            } catch (e) {
                window.LSM.showToast('操作失败 (' + status + ')', 'error');
            }
        }
    }

    // ── htmx 全局胶水 ─────────────────────────────────
    // 本文件在 <head> 同步执行时 document.body 尚未解析，
    // 全部 body 级监听必须在 DOM 就绪后注册。
    function initHtmxGlue() {
        // 请求标记 + 写请求 CSRF 自动注入
        document.body.addEventListener('htmx:configRequest', function(evt) {
            evt.detail.headers['HX-Request'] = 'true';
            var verb = (evt.detail.verb || '').toUpperCase();
            if (window.__csrf_token && verb !== 'GET') {
                evt.detail.headers['X-CSRF-Token'] = window.__csrf_token;
            }
        });

        // 401 未认证 → 登录页
        document.body.addEventListener('htmx:responseError', function(evt) {
            if (evt.detail.xhr && evt.detail.xhr.status === 401) {
                window.location.href = '/login';
            }
        });

        // swap 后统一提示入口
        document.body.addEventListener('htmx:afterSwap', function(evt) {
            showHtmxAlert(evt);
        });

        // 登录成功后强制刷新 CSRF token（旧 token 属于匿名态，已失效）
        document.body.addEventListener('htmx:afterRequest', function(evt) {
            if (evt.detail.pathInfo && evt.detail.pathInfo.requestPath === '/api/auth/login'
                && evt.detail.successful) {
                window.LSM.ensureCsrfToken(true);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initHtmxGlue);
    } else {
        initHtmxGlue();
    }

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
