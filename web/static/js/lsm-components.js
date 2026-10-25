/* ================================================================
 * lsm-components.js — Live Source Manager 前端组件库（唯一实现）
 *
 * 架构决策（UI 优化增量设计 §2.1）：
 *   全站交互组件的唯一实现落在本文件；app.js 只保留应用胶水
 *   （htmx 事件/CSRF/加密密钥横幅/全局别名），页面脚本一律经
 *   window.LSM.* 与 window.IS_ADMIN 交互。
 *
 * 约定：
 *   - 本文件为纯定义，顶层不做任何 DOM 访问（可安全置于 <head>）；
 *   - 样式类名与 app.css「组件库 v3」章节一一对应；
 *   - 所有用户输入展示必须经 escapeHtml，禁止裸拼 innerHTML。
 * ================================================================ */

window.LSM = (function() {
    'use strict';

    /* ── 转义工具（全站唯一实现，收敛 13 处） ─────────── */

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

    /* ── SVG 图标（统一线性图标，替代 emoji，跨平台一致） ──
       仅内部受信任的固定 path，不接收外部输入；样式见 app.css .icon。
       注意：与 web/templates/_icons.html 的服务端宏保持同名同步。 */
    var ICONS = {
        dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
        sources: '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.2 19.1 19.1"/>',
        config: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82.33l.06.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
        rules: '<path d="M12 2H2v10l9.29 9.29a1 1 0 0 0 1.41 0l8.59-8.59a1 1 0 0 0 0-1.41L12 2z"/><circle cx="7" cy="7" r="1.2"/>',
        test: '<path d="M9 3h6M10 3v6L4.5 18a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3"/><path d="M7 15h10"/>',
        epg: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="m17 2-5 5-5-5"/>',
        globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
        logs: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8"/>',
        users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
        logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
        menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
        copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
        success: '<circle cx="12" cy="12" r="10"/><path d="m8.5 12.5 2.5 2.5 5-5"/>',
        error: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
        warning: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
        info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
        search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>',
        refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/>',
        play: '<polygon points="6 3 20 12 6 21 6 3"/>',
        pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
        stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
        plus: '<path d="M12 5v14M5 12h14"/>',
        edit: '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
        trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
        clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
        zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
        wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
        chart: '<path d="M3 3v18h18"/><path d="M7 15v3M12 10v8M17 5v13"/>',
        send: '<path d="m22 2-7 20-4-9-9-4 20-7z"/>',
        code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
        sparkles: '<path d="M12 3l1.9 5.8L20 10l-6.1 1.2L12 17l-1.9-5.8L4 10l6.1-1.2L12 3z"/><path d="M19 15l.9 2.6L22.5 18.5l-2.6.9L19 22l-.9-2.6-2.6-.9 2.6-.9L19 15z"/>',
        eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
        lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
        download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>'
    };

    // 渲染内联 SVG 图标；未知名称返回空串（调用方应使用上方受信任名称）
    function icon(name) {
        return '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
            (ICONS[name] || '') + '</svg>';
    }

    /* ── Toast 通知（唯一 Toast） ────────────────────── */

    var TOAST_ICONS = {
        success: 'success', error: 'error', warning: 'warning', info: 'info'
    };

    function showToast(message, type, duration) {
        type = TOAST_ICONS[type] ? type : 'info';
        duration = typeof duration === 'number' ? duration : 3000;

        var container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container';
            container.setAttribute('role', 'status');
            container.setAttribute('aria-live', 'polite');
            document.body.appendChild(container);
        }

        var toast = document.createElement('div');
        toast.className = 'toast toast-' + type;

        var iconEl = document.createElement('span');
        iconEl.className = 'toast-icon';
        iconEl.innerHTML = icon(TOAST_ICONS[type]);

        var text = document.createElement('span');
        text.className = 'toast-msg';
        text.textContent = message == null ? '' : String(message);

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'toast-close';
        closeBtn.setAttribute('aria-label', '关闭提示');
        closeBtn.textContent = '×';

        toast.appendChild(iconEl);
        toast.appendChild(text);
        toast.appendChild(closeBtn);

        var removed = false;
        function remove() {
            if (removed) return;
            removed = true;
            toast.classList.add('toast-leaving');
            setTimeout(function() {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            }, 200);
        }
        closeBtn.addEventListener('click', remove);
        if (duration > 0) setTimeout(remove, duration);

        container.appendChild(toast);
    }

    /* ── Modal 对话框（唯一 Modal） ───────────────────── */

    // 已打开模态栈（后进先出）；closeTopModal 供页面脚本在异步成功后关闭顶层模态
    var modalStack = [];

    // options: { title, content(HTML 字符串或 DOM 节点), buttons, width, onClose }
    //   buttons: [{ text, action, type: primary/danger/outline, onClick(close), closeOnClick }]
    // 返回 { overlay, modal, close }
    function showModal(options) {
        options = options || {};
        var previouslyFocused = document.activeElement;

        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';

        var modal = document.createElement('div');
        modal.className = 'modal-box';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        if (options.title) modal.setAttribute('aria-label', options.title);
        if (options.width) modal.style.width = options.width;

        // 头部
        var header = document.createElement('div');
        header.className = 'modal-header';
        var titleEl = document.createElement('h3');
        titleEl.className = 'modal-title';
        titleEl.textContent = options.title || '提示';
        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'modal-close';
        closeBtn.setAttribute('aria-label', '关闭');
        closeBtn.innerHTML = '&times;';
        header.appendChild(titleEl);
        header.appendChild(closeBtn);

        // 主体
        var body = document.createElement('div');
        body.className = 'modal-body';
        if (typeof options.content === 'string') {
            body.innerHTML = options.content;
        } else if (options.content) {
            body.appendChild(options.content);
        }

        modal.appendChild(header);
        modal.appendChild(body);

        // 页脚按钮
        if (options.buttons && options.buttons.length) {
            var footer = document.createElement('div');
            footer.className = 'modal-footer';
            options.buttons.forEach(function(b) {
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'btn ' + (b.type === 'primary' ? 'btn-primary'
                    : b.type === 'danger' ? 'btn-danger' : 'btn-outline');
                btn.textContent = b.text || '';
                if (b.action) btn.dataset.action = b.action;
                btn.addEventListener('click', function() {
                    var result = true;
                    if (typeof b.onClick === 'function') result = b.onClick(close);
                    if (result === false) return;          // 回调返回 false 阻止关闭
                    if (b.closeOnClick !== false) close();
                });
                footer.appendChild(btn);
            });
            modal.appendChild(footer);
        }

        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        var closed = false;
        var handle = { overlay: overlay, modal: modal, close: close };
        modalStack.push(handle);

        function close() {
            if (closed) return;
            closed = true;
            var idx = modalStack.indexOf(handle);
            if (idx >= 0) modalStack.splice(idx, 1);
            document.removeEventListener('keydown', onKeydown);
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            // 关闭时还原焦点
            if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
                previouslyFocused.focus();
            }
            if (typeof options.onClose === 'function') options.onClose();
        }
        function onKeydown(e) {
            if (e.key === 'Escape') { e.preventDefault(); close(); }
        }
        document.addEventListener('keydown', onKeydown);
        closeBtn.addEventListener('click', close);
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) close();
        });

        // 打开时聚焦模态内首个可聚焦元素
        var focusable = modal.querySelector(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])'
        );
        if (focusable) {
            focusable.focus();
        } else {
            modal.tabIndex = -1;
            modal.focus();
        }

        return handle;
    }

    // 关闭栈顶模态（页面脚本在异步操作成功后调用；无打开模态时静默无操作）
    function closeTopModal() {
        var handle = modalStack[modalStack.length - 1];
        if (handle) handle.close();
    }

    /* ── 确认框（替代全部原生 confirm） ───────────────── */

    function confirm(message, title) {
        return new Promise(function(resolve) {
            var settled = false;
            function done(value) {
                if (!settled) { settled = true; resolve(value); }
            }
            showModal({
                title: title || '确认操作',
                content: '<p class="modal-text">' + escapeHtml(message) + '</p>',
                buttons: [
                    { text: '取消', action: 'cancel', type: 'outline',
                      onClick: function() { done(false); } },
                    { text: '确认', action: 'confirm', type: 'danger',
                      onClick: function() { done(true); } }
                ],
                onClose: function() { done(false); }
            });
        });
    }

    /* ── 单行输入框（替代原生 prompt） ────────────────── */

    function prompt(options) {
        options = options || {};
        return new Promise(function(resolve) {
            var settled = false;
            function done(value) {
                if (!settled) { settled = true; resolve(value); }
            }
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'form-input';
            input.value = options.value || '';
            if (options.placeholder) input.placeholder = options.placeholder;

            var field = document.createElement('div');
            field.className = 'prompt-field';
            if (options.label) {
                var label = document.createElement('label');
                label.className = 'prompt-label';
                label.textContent = options.label;
                field.appendChild(label);
            }
            field.appendChild(input);

            var handle = showModal({
                title: options.title || '请输入',
                content: field,
                width: options.width,
                buttons: [
                    { text: '取消', action: 'cancel', type: 'outline',
                      onClick: function() { done(null); } },
                    { text: options.confirmText || '确定', action: 'confirm', type: 'primary',
                      onClick: function() { done(input.value); } }
                ],
                onClose: function() { done(null); }
            });
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    done(input.value);
                    handle.close();
                }
            });
            setTimeout(function() { input.focus(); input.select(); }, 0);
        });
    }

    /* ── 单选组输入框（替代带选项的 prompt） ───────────── */

    function promptChoice(options) {
        options = options || {};
        return new Promise(function(resolve) {
            var settled = false;
            function done(value) {
                if (!settled) { settled = true; resolve(value); }
            }
            var group = document.createElement('div');
            group.className = 'prompt-choice';
            var choices = options.choices || [];
            choices.forEach(function(ch, idx) {
                var item = document.createElement('label');
                item.className = 'prompt-choice-item';
                var radio = document.createElement('input');
                radio.type = 'radio';
                radio.name = 'lsm-prompt-choice';
                radio.value = ch.value;
                radio.checked = (options.value != null && options.value === ch.value)
                    || (options.value == null && idx === 0);
                var text = document.createElement('span');
                text.className = 'prompt-choice-text';
                text.textContent = ch.label || ch.value;
                item.appendChild(radio);
                item.appendChild(text);
                if (ch.description) {
                    var desc = document.createElement('div');
                    desc.className = 'prompt-choice-desc';
                    desc.textContent = ch.description;
                    item.appendChild(desc);
                }
                group.appendChild(item);
            });

            showModal({
                title: options.title || '请选择',
                content: group,
                width: options.width,
                buttons: [
                    { text: '取消', action: 'cancel', type: 'outline',
                      onClick: function() { done(null); } },
                    { text: options.confirmText || '确定', action: 'confirm', type: 'primary',
                      onClick: function() {
                          var checked = group.querySelector('input[type="radio"]:checked');
                          done(checked ? checked.value : null);
                      } }
                ],
                onClose: function() { done(null); }
            });
        });
    }

    /* ── 密码输入框（显隐切换 + 强度条 + 二次确认） ────── */

    function promptPassword(options) {
        options = options || {};
        var minLength = typeof options.minLength === 'number' ? options.minLength : 6;
        var requireConfirm = options.requireConfirm !== false;

        return new Promise(function(resolve) {
            var settled = false;
            function done(value) {
                if (!settled) { settled = true; resolve(value); }
            }

            var wrap = document.createElement('div');
            wrap.className = 'pwd-field';

            if (options.label) {
                var label = document.createElement('label');
                label.className = 'prompt-label';
                label.textContent = options.label;
                wrap.appendChild(label);
            }

            var inputWrap = document.createElement('div');
            inputWrap.className = 'pwd-input-wrap';
            var input = document.createElement('input');
            input.type = 'password';
            input.className = 'form-input';
            input.autocomplete = 'new-password';
            var toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'btn btn-sm btn-outline pwd-toggle';
            toggleBtn.textContent = '显示';
            toggleBtn.addEventListener('click', function() {
                var show = input.type === 'password';
                input.type = show ? 'text' : 'password';
                toggleBtn.textContent = show ? '隐藏' : '显示';
            });
            inputWrap.appendChild(input);
            inputWrap.appendChild(toggleBtn);
            wrap.appendChild(inputWrap);

            // 强度条：长度 + 大小写 + 数字 + 符号四类占几类
            var strengthBar = document.createElement('div');
            strengthBar.className = 'pwd-strength';
            var strengthFill = document.createElement('div');
            strengthFill.className = 'pwd-strength-fill';
            var strengthText = document.createElement('span');
            strengthText.className = 'pwd-strength-text';
            strengthBar.appendChild(strengthFill);
            strengthBar.appendChild(strengthText);
            wrap.appendChild(strengthBar);

            function scorePassword(value) {
                var kinds = 0;
                if (value.length >= 8) kinds++;
                if (/[a-z]/.test(value) && /[A-Z]/.test(value)) kinds++;
                if (/\d/.test(value)) kinds++;
                if (/[^A-Za-z0-9]/.test(value)) kinds++;
                return kinds; // 0..4
            }
            function refreshStrength() {
                var value = input.value;
                if (!value) {
                    strengthFill.className = 'pwd-strength-fill';
                    strengthFill.style.width = '0';
                    strengthText.textContent = '';
                    return;
                }
                var score = scorePassword(value);
                var level = score <= 1 ? 'weak' : (score <= 3 ? 'mid' : 'strong');
                var label = score <= 1 ? '弱' : (score <= 3 ? '中' : '强');
                strengthFill.className = 'pwd-strength-fill pwd-strength-' + level;
                strengthFill.style.width = Math.max(20, score * 25) + '%';
                strengthText.textContent = '强度：' + label;
            }
            input.addEventListener('input', refreshStrength);

            var confirmInput = null;
            if (requireConfirm) {
                confirmInput = document.createElement('input');
                confirmInput.type = 'password';
                confirmInput.className = 'form-input';
                confirmInput.autocomplete = 'new-password';
                confirmInput.placeholder = '再次输入新密码';
                var confirmWrap = document.createElement('div');
                confirmWrap.className = 'pwd-input-wrap';
                confirmWrap.style.marginTop = '8px';
                confirmWrap.appendChild(confirmInput);
                wrap.appendChild(confirmWrap);
            }

            var errorEl = document.createElement('div');
            errorEl.className = 'form-error';
            errorEl.style.display = 'none';
            wrap.appendChild(errorEl);

            var handle = showModal({
                title: options.title || '设置密码',
                content: wrap,
                width: options.width,
                buttons: [
                    { text: '取消', action: 'cancel', type: 'outline',
                      onClick: function() { done(null); } },
                    { text: options.confirmText || '确定', action: 'confirm', type: 'primary',
                      onClick: function() {
                          var value = input.value;
                          if (value.length < minLength) {
                              errorEl.textContent = '密码长度至少 ' + minLength + ' 位';
                              errorEl.style.display = 'block';
                              return false;   // 阻止关闭
                          }
                          if (requireConfirm && confirmInput && confirmInput.value !== value) {
                              errorEl.textContent = '两次输入的密码不一致';
                              errorEl.style.display = 'block';
                              return false;
                          }
                          done(value);
                      } }
                ],
                onClose: function() { done(null); }
            });
            setTimeout(function() { input.focus(); }, 0);
        });
    }

    /* ── 空状态 ──────────────────────────────────────── */

    function showEmptyState(container, options) {
        options = options || {};
        if (!container) return;
        container.innerHTML = '';
        var box = document.createElement('div');
        box.className = 'empty-state';
        if (options.icon) {
            var iconBox = document.createElement('div');
            iconBox.className = 'empty-state-icon';
            // 已知图标名渲染 SVG；其余按纯文本兜底（向后兼容）
            if (ICONS[options.icon]) iconBox.innerHTML = icon(options.icon);
            else iconBox.textContent = options.icon;
            box.appendChild(iconBox);
        }
        var text = document.createElement('div');
        text.className = 'empty-state-text';
        text.textContent = options.text || '暂无数据';
        box.appendChild(text);
        if (options.actionText && typeof options.onAction === 'function') {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-primary';
            btn.textContent = options.actionText;
            btn.addEventListener('click', options.onAction);
            box.appendChild(btn);
        }
        container.appendChild(box);
    }

    /* ── 分页（唯一分页实现） ─────────────────────────── */

    // state: { page, totalPages, total, onChange(page) }
    function renderPagination(container, state) {
        state = state || {};
        if (!container) return;
        var page = state.page || 1;
        var totalPages = state.totalPages || 1;
        container.innerHTML = '';

        if (state.total != null) {
            var info = document.createElement('span');
            info.className = 'pagination-info';
            info.textContent = '共 ' + state.total + ' 条';
            container.appendChild(info);
        }

        var pages = document.createElement('div');
        pages.className = 'pagination-pages';

        function addBtn(label, target, opts) {
            opts = opts || {};
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'pagination-btn' + (opts.active ? ' active' : '');
            btn.textContent = label;
            if (opts.disabled) {
                btn.disabled = true;
            } else {
                btn.addEventListener('click', function() {
                    if (typeof state.onChange === 'function') state.onChange(target);
                });
            }
            pages.appendChild(btn);
        }
        function addEllipsis() {
            var ell = document.createElement('span');
            ell.className = 'pagination-ellipsis';
            ell.textContent = '…';
            pages.appendChild(ell);
        }

        addBtn('上一页', page - 1, { disabled: page <= 1 });

        var from = Math.max(1, page - 2);
        var to = Math.min(totalPages, page + 2);
        if (from > 1) {
            addBtn('1', 1);
            if (from > 2) addEllipsis();
        }
        for (var p = from; p <= to; p++) {
            addBtn(String(p), p, { active: p === page });
        }
        if (to < totalPages) {
            if (to < totalPages - 1) addEllipsis();
            addBtn(String(totalPages), totalPages);
        }

        addBtn('下一页', page + 1, { disabled: page >= totalPages });

        container.appendChild(pages);
    }

    /* ── Tabs（容器内 .tab-btn[data-tab] + .tab-content） ── */

    function initTabs(container) {
        if (!container) return;
        var tabs = container.querySelectorAll('.tab-btn');
        var panels = container.querySelectorAll('.tab-content');
        tabs.forEach(function(tab) {
            tab.addEventListener('click', function() {
                tabs.forEach(function(t) {
                    t.classList.remove('active');
                    t.setAttribute('aria-selected', 'false');
                });
                panels.forEach(function(p) { p.classList.remove('active'); });
                tab.classList.add('active');
                tab.setAttribute('aria-selected', 'true');
                var target = document.getElementById(tab.dataset.tab);
                if (target) target.classList.add('active');
            });
        });
    }

    /* ── Toggle Switch ───────────────────────────────── */

    function createToggle(checked, onChange) {
        var label = document.createElement('label');
        label.className = 'toggle';
        var input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = !!checked;
        input.addEventListener('change', function() {
            if (typeof onChange === 'function') onChange(input.checked);
        });
        var slider = document.createElement('span');
        slider.className = 'toggle-slider';
        label.appendChild(input);
        label.appendChild(slider);
        return label;
    }

    /* ── 按钮 Loading 态 ─────────────────────────────── */

    function setBtnLoading(btn, loading, text) {
        if (!btn) return;
        if (loading) {
            if (!btn.dataset.lsmOriginalHtml) {
                btn.dataset.lsmOriginalHtml = btn.innerHTML;
            }
            btn.classList.add('btn-loading');
            btn.disabled = true;
            if (text) btn.textContent = text;
        } else {
            btn.classList.remove('btn-loading');
            btn.disabled = false;
            if (btn.dataset.lsmOriginalHtml) {
                btn.innerHTML = btn.dataset.lsmOriginalHtml;
                delete btn.dataset.lsmOriginalHtml;
            }
        }
    }

    /* ── 时间格式化（秒级时间戳 → zh-CN） ─────────────── */

    function formatDate(ts) {
        if (!ts) return '-';
        var d = new Date(ts * 1000);
        if (isNaN(d.getTime())) return '-';
        return d.toLocaleString('zh-CN', { hour12: false });
    }

    /* ── CSRF / 角色 ─────────────────────────────────── */

    // 调用时读取 token（修 F13：禁止 IIFE 加载时捕获，登录后会拿到旧值）
    async function ensureCsrfToken(force) {
        if (!force && window.__csrf_token) return window.__csrf_token;
        try {
            const resp = await fetch('/api/auth/csrf-token', { credentials: 'same-origin' });
            if (resp.ok) {
                const data = await resp.json();
                window.__csrf_token = data.csrf_token;
            }
        } catch (e) {
            // 未登录时静默失败，登录后会由 app.js 胶水刷新
        }
        return window.__csrf_token || '';
    }

    function isAdmin() {
        return window.IS_ADMIN === true;
    }

    /* ── 暴露公共 API ─────────────────────────────────── */

    return {
        showToast:       showToast,
        showModal:       showModal,
        closeTopModal:   closeTopModal,
        confirm:         confirm,
        prompt:          prompt,
        promptChoice:    promptChoice,
        promptPassword:  promptPassword,
        showEmptyState:  showEmptyState,
        renderPagination: renderPagination,
        initTabs:        initTabs,
        createToggle:    createToggle,
        setBtnLoading:   setBtnLoading,
        escapeHtml:      escapeHtml,
        escapeJs:        escapeJs,
        icon:            icon,
        formatDate:      formatDate,
        ensureCsrfToken: ensureCsrfToken,
        isAdmin:         isAdmin
    };
})();
