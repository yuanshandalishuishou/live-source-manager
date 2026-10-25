/* ================================================================
 * pages/auth.js — 登录 / 强制改密 页面脚本
 * 被 login.html 与 force_pwd.html 共用；只经 LSM.* 交互。
 * 功能：密码显隐切换、CapsLock 提示、新密码强度条、登录响应
 *       处理（强制改密跳转/失败照实）、改密提交校验与 loading。
 * ================================================================ */
(function() {
    'use strict';

    /* ── 密码显隐切换 ───────────────────────────── */
    function wirePwdToggle(inputId, btnId) {
        var input = document.getElementById(inputId);
        var btn = document.getElementById(btnId);
        if (!input || !btn) return;
        btn.addEventListener('click', function() {
            var show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.textContent = show ? '隐藏' : '显示';
            btn.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
            input.focus();
        });
    }

    /* ── CapsLock 大写锁定提示 ──────────────────── */
    function wireCapsLock(inputId, hintId) {
        var input = document.getElementById(inputId);
        var hint = document.getElementById(hintId);
        if (!input || !hint) return;
        function check(e) {
            if (typeof e.getModifierState !== 'function') return;
            hint.style.display = e.getModifierState('CapsLock') ? 'block' : 'none';
        }
        input.addEventListener('keydown', check);
        input.addEventListener('keyup', check);
        input.addEventListener('blur', function() { hint.style.display = 'none'; });
    }

    /* ── 密码强度（长度+大小写+数字+符号四类占几类，0..4） ── */
    function scorePassword(value) {
        var kinds = 0;
        if (value.length >= 8) kinds++;
        if (/[a-z]/.test(value) && /[A-Z]/.test(value)) kinds++;
        if (/\d/.test(value)) kinds++;
        if (/[^A-Za-z0-9]/.test(value)) kinds++;
        return kinds;
    }

    function wirePwdStrength(inputId, barId, fillId, textId) {
        var input = document.getElementById(inputId);
        var bar = document.getElementById(barId);
        var fill = document.getElementById(fillId);
        var text = document.getElementById(textId);
        if (!input || !bar || !fill || !text) return;
        input.addEventListener('input', function() {
            var value = input.value;
            if (!value) {
                bar.style.display = 'none';
                return;
            }
            bar.style.display = 'flex';
            var score = scorePassword(value);
            var level = score <= 1 ? 'weak' : (score <= 3 ? 'mid' : 'strong');
            var label = score <= 1 ? '弱' : (score <= 3 ? '中' : '强');
            fill.className = 'pwd-strength-fill pwd-strength-' + level;
            fill.style.width = Math.max(20, score * 25) + '%';
            text.textContent = '强度：' + label;
        });
    }

    /* ── 登录页 ─────────────────────────────────── */
    function initLogin() {
        var form = document.querySelector('.login-form');
        if (!form) return;

        wirePwdToggle('password', 'password-toggle');
        wireCapsLock('password', 'password-capslock');

        // 失败照实展示后端 detail（决策②，保持既有行为）
        form.addEventListener('htmx:responseError', function(evt) {
            var errDiv = document.getElementById('login-error');
            if (!errDiv) return;
            errDiv.style.display = 'block';
            try {
                var data = JSON.parse(evt.detail.xhr.responseText);
                errDiv.textContent = data.detail || '登录失败';
            } catch (e) {
                errDiv.textContent = '登录失败';
            }
        });

        // 成功：首次登录/默认密码强制改密 → 跳改密页，否则进控制台（S2 修复保持）
        form.addEventListener('htmx:afterRequest', function(evt) {
            if (!evt.detail.successful) return;
            var raw = {};
            try {
                raw = JSON.parse(evt.detail.xhr.responseText || '{}');
            } catch (e) {
                raw = {};
            }
            if (raw && raw.force_change_password) {
                window.location.href = '/force-password-change';
                return;
            }
            window.location.href = '/';
        });
    }

    /* ── 强制改密页 ─────────────────────────────── */
    function initForcePwd() {
        var form = document.getElementById('force-pwd-form');
        if (!form) return;

        wirePwdToggle('current-password', 'current-password-toggle');
        wirePwdToggle('new-password', 'new-password-toggle');
        wirePwdToggle('confirm-password', 'confirm-password-toggle');
        wireCapsLock('current-password', 'current-password-capslock');
        wireCapsLock('new-password', 'new-password-capslock');
        wireCapsLock('confirm-password', 'confirm-password-capslock');
        wirePwdStrength('new-password', 'pwd-strength', 'pwd-strength-fill', 'pwd-strength-text');

        form.addEventListener('submit', async function(evt) {
            evt.preventDefault();
            var errDiv = document.getElementById('pwd-error');
            errDiv.style.display = 'none';
            var oldPw = document.getElementById('current-password').value;
            var newPw = document.getElementById('new-password').value;
            var confirmPw = document.getElementById('confirm-password').value;
            if (newPw !== confirmPw) {
                errDiv.textContent = '两次输入的新密码不一致';
                errDiv.style.display = 'block';
                return;
            }
            if (newPw === oldPw) {
                errDiv.textContent = '新密码不能与当前密码相同';
                errDiv.style.display = 'block';
                return;
            }
            var btn = document.getElementById('pwd-btn');
            LSM.setBtnLoading(btn, true, '提交中...');
            try {
                // CSRF 调用时读取（禁加载时捕获，F13）
                var csrfToken = await LSM.ensureCsrfToken();
                var resp = await fetch('/api/auth/password', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ old_password: oldPw, new_password: newPw })
                });
                if (resp.ok) {
                    window.location.href = '/';
                    return;
                }
                var data = {};
                try { data = await resp.json(); } catch (e) { /* 响应体非 JSON 时用状态码兜底 */ }
                errDiv.textContent = data.detail || '密码修改失败（HTTP ' + resp.status + '）';
                errDiv.style.display = 'block';
            } catch (e) {
                errDiv.textContent = '网络错误，请重试';
                errDiv.style.display = 'block';
            } finally {
                LSM.setBtnLoading(btn, false);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        initLogin();
        initForcePwd();
    });
})();
