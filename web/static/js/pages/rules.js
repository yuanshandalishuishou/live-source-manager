/* ================================================================
 * pages/rules.js — 分类规则管理页面脚本
 * 三 tab 规则 / toggle 同步渲染（U-06）/ 排序 data-id / 测试分类；
 * 写请求统一 LSM.ensureCsrfToken；破坏性操作 LSM.confirm；
 * 关键词徽标 .badge-list 容器；规则弹窗 .is-open + Esc/焦点管理。
 * ================================================================ */
(function() {
    'use strict';

    /* ── 静态弹窗：类驱动显隐 + Esc + 焦点管理 ────── */
    var modalReturnFocus = null;

    function openRuleModal() {
        var modal = document.getElementById('rule-modal');
        modalReturnFocus = document.activeElement;
        modal.classList.add('is-open');
        var first = modal.querySelector('input:not([disabled]), textarea, select');
        if (first) first.focus();
    }

    function closeRuleModal() {
        var modal = document.getElementById('rule-modal');
        modal.classList.remove('is-open');
        if (modalReturnFocus && typeof modalReturnFocus.focus === 'function') {
            modalReturnFocus.focus();
        }
        modalReturnFocus = null;
        document.getElementById('rule-form-result').innerHTML = '';
    }

    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        if (document.getElementById('rule-modal').classList.contains('is-open')) {
            closeRuleModal();
        }
    });

    document.querySelectorAll('#rule-modal [data-modal-close]').forEach(function(el) {
        el.addEventListener('click', closeRuleModal);
    });

    /* ── 工具 ───────────────────────────────────── */
    function reloadRuleList(ruleType) {
        if (ruleType === 'content') {
            loadRules('content', 'rules-category-list');
        } else {
            loadRules('media_type', 'rules-channel-type-list');
        }
    }

    /* ── 加载规则列表 ───────────────────────────── */
    function loadRules(ruleType, targetId) {
        var el = document.getElementById(targetId);
        if (!el) return;
        el.innerHTML = '<div class="loading">加载中...</div>';

        fetch('/api/rules?rule_type=' + ruleType, { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.rules || data.rules.length === 0) {
                    el.innerHTML = '<div class="empty-state">暂无规则，点击上方"新增规则"添加</div>';
                    return;
                }
                var html = '<table class="data-table"><thead><tr>'
                    + '<th class="w-50">序号</th>'
                    + '<th>名称</th>'
                    + '<th>关键词</th>'
                    + '<th class="w-80">优先级</th>'
                    + '<th class="w-80">状态</th>'
                    + '<th class="w-140">操作</th>'
                    + '</tr></thead><tbody>';

                data.rules.forEach(function(rule, ruleIdx) {
                    var keywords = [];
                    try {
                        keywords = JSON.parse(rule.keywords || '[]');
                    } catch (e) {
                        keywords = [];
                    }
                    // 关键词徽标：.badge-list 容器（gap 替代内联 margin）
                    var kwHtml = '<div class="badge-list">' + keywords.map(function(kw) {
                        return '<span class="badge badge-blue">' + LSM.escapeHtml(kw) + '</span>';
                    }).join('') + '</div>';

                    // 行内写按钮仅对管理员渲染（查看者只读；后端另有 403 兜底）
                    var actionBtns = '';
                    if (window.IS_ADMIN) {
                        actionBtns += '<button class="btn btn-sm btn-outline" data-action="edit-rule" data-rule-id="' + rule.id + '" data-rule-type="' + LSM.escapeHtml(ruleType) + '">编辑</button> ';
                        actionBtns += '<button class="btn btn-sm btn-outline" data-action="delete-rule" data-rule-id="' + rule.id + '" data-rule-name="' + LSM.escapeHtml(rule.name) + '" data-rule-type="' + LSM.escapeHtml(ruleType) + '">删除</button> ';
                        actionBtns += '<button class="btn btn-sm btn-outline" data-action="move-rule" data-rule-id="' + rule.id + '" data-direction="up" data-rule-type="' + LSM.escapeHtml(ruleType) + '"' + (ruleIdx === 0 ? ' disabled' : '') + '>↑</button>';
                        actionBtns += '<button class="btn btn-sm btn-outline" data-action="move-rule" data-rule-id="' + rule.id + '" data-direction="down" data-rule-type="' + LSM.escapeHtml(ruleType) + '"' + (ruleIdx === data.rules.length - 1 ? ' disabled' : '') + '>↓</button>';
                    }

                    html += '<tr data-rule-row="' + rule.id + '">';
                    html += '<td>' + (rule.sort_order || 0) + '</td>';
                    html += '<td><strong>' + LSM.escapeHtml(rule.name) + '</strong></td>';
                    html += '<td>' + kwHtml + '</td>';
                    html += '<td><span class="badge badge-yellow font-normal">' + rule.priority + '</span></td>';
                    html += '<td>';
                    html += '<div id="toggle-' + ruleType + '-' + rule.id + '" data-rule-id="' + rule.id + '" data-rule-type="' + ruleType + '" data-active="' + rule.is_active + '"></div>';
                    html += '</td>';
                    html += '<td class="actions">' + actionBtns + '</td>';
                    html += '</tr>';
                });

                html += '</tbody></table>';
                el.innerHTML = html;

                // DOM 已就绪，同步初始化 toggle（U-06 修复：无 setTimeout，
                // 初始文本用 data-active 直接同步渲染）
                el.querySelectorAll('[data-rule-id]').forEach(function(container) {
                    // is_active 来自 SQLite 整数 1/0，经 JSON 序列化后为数字，
                    // 拼入 data-active 后 dataset.active 是字符串 "1"/"0"，
                    // 必须按数值判定，而非 'true' 字符串比较（修复"全部显示已禁用"）
                    var rawActive = container.dataset.active;
                    var isActive = rawActive === '1' || rawActive === 'true' || rawActive === 1 || rawActive === true;
                    container.innerHTML = '';
                    if (window.IS_ADMIN) {
                        var toggle = LSM.createToggle(isActive, function(checked) {
                            toggleRuleActive(parseInt(container.dataset.ruleId, 10), checked, container.dataset.ruleType);
                        });
                        container.appendChild(toggle);
                        var label = document.createElement('span');
                        label.className = 'toggle-label';
                        label.style.marginLeft = '4px';
                        label.style.fontSize = '12px';
                        label.textContent = isActive ? '已启用' : '已禁用';
                        container.appendChild(label);
                    } else {
                        // 查看者只读：状态徽标替代可交互 toggle
                        container.innerHTML = '<span class="badge ' + (isActive ? 'badge-green' : 'badge-gray') + '">' + (isActive ? '已启用' : '已禁用') + '</span>';
                    }
                });
            })
            .catch(function(e) {
                el.innerHTML = '<div class="alert alert-error">加载失败: ' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误') + '</div>';
            });
    }

    /* ── 行内按钮事件委托（替代内联 onclick） ────── */
    function bindListActions() {
        ['rules-category-list', 'rules-channel-type-list', 'rules-exclusions-list'].forEach(function(listId) {
            var el = document.getElementById(listId);
            if (!el) return;
            el.addEventListener('click', function(e) {
                var btn = e.target.closest('button[data-action]');
                if (!btn) return;
                var action = btn.dataset.action;
                var ruleType = btn.dataset.ruleType;
                if (action === 'edit-rule') {
                    showEditRuleModal(parseInt(btn.dataset.ruleId, 10), ruleType);
                } else if (action === 'delete-rule') {
                    deleteRule(parseInt(btn.dataset.ruleId, 10), btn.dataset.ruleName, ruleType);
                } else if (action === 'move-rule') {
                    moveRule(parseInt(btn.dataset.ruleId, 10), btn.dataset.direction, ruleType);
                } else if (action === 'delete-exclusion') {
                    deleteExclusion(parseInt(btn.dataset.exclusionId, 10));
                }
            });
        });
    }

    /* ── 切换规则活跃状态 ───────────────────────── */
    function toggleRuleActive(ruleId, isActive, ruleType) {
        LSM.ensureCsrfToken().then(function(csrfToken) {
            return fetch('/api/rules/' + ruleId, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin',
                body: JSON.stringify({ is_active: isActive ? 1 : 0 })
            });
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                LSM.showToast(data.message || '状态已更新', 'success');
                reloadRuleList(ruleType);
            })
            .catch(function(e) {
                LSM.showToast('更新失败: ' + ((e && e.message) ? e.message : '网络错误'), 'error');
            });
    }

    /* ── 新增/编辑规则弹窗 ──────────────────────── */
    function showAddRuleModal() {
        var activeTab = document.querySelector('#rules-tabs .tab-btn.active');
        var ruleType = 'content';
        if (activeTab) {
            var tabId = activeTab.dataset.tab;
            if (tabId === 'tab-channel-type') ruleType = 'media_type';
        }

        document.getElementById('rule-modal-title').textContent = '新增规则';
        document.getElementById('rule-id').value = '';
        document.getElementById('rule-rule-type').value = ruleType;
        document.getElementById('rule-name').value = '';
        document.getElementById('rule-keywords').value = '';
        document.getElementById('rule-priority').value = '100';
        document.getElementById('rule-sort-order').value = '0';
        document.getElementById('rule-form-result').innerHTML = '';
        openRuleModal();
    }

    function showEditRuleModal(ruleId, ruleType) {
        fetch('/api/rules?rule_type=' + ruleType, { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var rule = null;
                if (data.rules) {
                    for (var i = 0; i < data.rules.length; i++) {
                        if (data.rules[i].id === ruleId) {
                            rule = data.rules[i];
                            break;
                        }
                    }
                }
                if (!rule) {
                    LSM.showToast('未找到规则', 'error');
                    return;
                }

                document.getElementById('rule-modal-title').textContent = '编辑规则';
                document.getElementById('rule-id').value = rule.id;
                document.getElementById('rule-rule-type').value = rule.rule_type;
                document.getElementById('rule-name').value = rule.name;

                var keywords = [];
                try {
                    keywords = JSON.parse(rule.keywords || '[]');
                } catch (e) { /* 关键词非 JSON 时按空处理 */ }
                document.getElementById('rule-keywords').value = keywords.join('\n');

                document.getElementById('rule-priority').value = rule.priority;
                document.getElementById('rule-sort-order').value = rule.sort_order || 0;
                document.getElementById('rule-form-result').innerHTML = '';
                openRuleModal();
            })
            .catch(function(e) {
                LSM.showToast('加载规则失败: ' + ((e && e.message) ? e.message : '网络错误'), 'error');
            });
    }

    /* ── 保存规则（新增或编辑） ─────────────────── */
    function saveRule() {
        var ruleId = document.getElementById('rule-id').value;
        var ruleType = document.getElementById('rule-rule-type').value;
        var name = document.getElementById('rule-name').value.trim();
        var keywordsRaw = document.getElementById('rule-keywords').value;
        var priority = parseInt(document.getElementById('rule-priority').value) || 100;
        var sortOrder = parseInt(document.getElementById('rule-sort-order').value) || 0;
        var resultEl = document.getElementById('rule-form-result');

        if (!name) {
            resultEl.innerHTML = '<div class="alert alert-error">请输入规则名称</div>';
            return;
        }

        var keywords = keywordsRaw.split('\n')
            .map(function(k) { return k.trim(); })
            .filter(function(k) { return k.length > 0; });

        var isNew = !ruleId;
        var url = isNew ? '/api/rules' : '/api/rules/' + ruleId;
        var method = isNew ? 'POST' : 'PUT';
        var body = {
            rule_type: ruleType,
            name: name,
            keywords: keywords,
            priority: priority,
            sort_order: sortOrder
        };

        resultEl.innerHTML = '<div class="text-muted text-sm">保存中...</div>';

        LSM.ensureCsrfToken().then(function(csrfToken) {
            return fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin',
                body: JSON.stringify(body)
            });
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.id !== undefined || data.message) {
                    LSM.showToast(data.message || '保存成功', 'success');
                    closeRuleModal();
                    reloadRuleList(ruleType);
                } else {
                    resultEl.innerHTML = '<div class="alert alert-error">' + LSM.escapeHtml(data.detail || '保存失败') + '</div>';
                }
            })
            .catch(function(e) {
                resultEl.innerHTML = '<div class="alert alert-error">' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误') + '</div>';
            });
    }

    /* ── 删除规则 ───────────────────────────────── */
    function deleteRule(ruleId, ruleName, ruleType) {
        LSM.confirm('确定删除规则 "' + ruleName + '" 吗？').then(function(confirmed) {
            if (!confirmed) return;
            LSM.ensureCsrfToken().then(function(csrfToken) {
                return fetch('/api/rules/' + ruleId, {
                    method: 'DELETE',
                    headers: { 'X-CSRF-Token': csrfToken },
                    credentials: 'same-origin'
                });
            })
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    LSM.showToast(data.message || '规则已删除', 'success');
                    reloadRuleList(ruleType);
                })
                .catch(function(e) {
                    LSM.showToast('删除失败: ' + ((e && e.message) ? e.message : '网络错误'), 'error');
                });
        });
    }

    /* ── 移动排序（data-rule-id 读取，无正则） ──── */
    function moveRule(ruleId, direction, ruleType) {
        var targetEl = ruleType === 'content' ? document.getElementById('rules-category-list') : document.getElementById('rules-channel-type-list');
        var table = targetEl.querySelector('table');
        if (!table) return;

        var rows = table.querySelectorAll('tbody tr');
        var orders = [];
        var currentIndex = -1;

        rows.forEach(function(row, idx) {
            var ruleIdEl = row.querySelector('[data-rule-id]');
            var rid = ruleIdEl ? parseInt(ruleIdEl.dataset.ruleId, 10) : 0;
            var sortCell = row.querySelector('td:first-child');
            var sortVal = parseInt(sortCell ? sortCell.textContent.trim() : '0');
            if (rid === ruleId) currentIndex = idx;
            orders.push({ id: rid, sort_order: sortVal });
        });

        if (currentIndex < 0) {
            LSM.showToast('无法确定当前行位置', 'error');
            return;
        }

        var newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1;
        if (newIndex < 0 || newIndex >= orders.length) {
            LSM.showToast('已在边界', 'info');
            return;
        }

        // 交换 sort_order 后整体重排（从 0 递增）
        var tmp = orders[currentIndex].sort_order;
        orders[currentIndex].sort_order = orders[newIndex].sort_order;
        orders[newIndex].sort_order = tmp;
        orders.forEach(function(o, idx) {
            o.sort_order = idx;
        });

        LSM.ensureCsrfToken().then(function(csrfToken) {
            return fetch('/api/rules/batch-order', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin',
                body: JSON.stringify({ orders: orders })
            });
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                LSM.showToast(data.message || '排序已更新', 'success');
                reloadRuleList(ruleType);
            })
            .catch(function(e) {
                LSM.showToast('排序失败: ' + ((e && e.message) ? e.message : '网络错误'), 'error');
            });
    }

    /* ── 测试分类（结果卡片保留，徽标类统一） ────── */
    function testClassification() {
        var input = document.getElementById('test-channel-input');
        var channelName = input.value.trim();
        if (!channelName) {
            LSM.showToast('请输入频道名称', 'warning');
            return;
        }

        var resultEl = document.getElementById('test-result');
        resultEl.classList.remove('hidden');
        resultEl.innerHTML = '<div class="text-muted">测试中...</div>';

        LSM.ensureCsrfToken().then(function(csrfToken) {
            return fetch('/api/rules/test-classification', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin',
                body: JSON.stringify({ channel_name: channelName })
            });
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var cats = data.categories || {};
                var matches = data.matches_by_dim || {};
                var dimNames = {
                    content: '内容类型', region: '地区', language: '语言',
                    quality: '画质', media_type: '媒体类型', genre: '类型'
                };
                var html = '<div class="card test-result-card">';
                html += '<div class="card-header"><h3>' + LSM.icon('chart') + ' 分类测试结果</h3></div>';
                html += '<div class="card-body">';
                html += '<p><strong>频道名称：</strong>' + LSM.escapeHtml(channelName) + '</p>';

                html += '<p class="mt-20"><strong>分类结果：</strong></p>';
                html += '<table class="data-table text-sm"><thead><tr><th>维度</th><th>分类值</th></tr></thead><tbody>';
                Object.keys(cats).forEach(function(dim) {
                    var label = dimNames[dim] || dim;
                    html += '<tr><td>' + LSM.escapeHtml(label) + '</td>';
                    html += '<td><span class="badge badge-green">' + LSM.escapeHtml(cats[dim] || '未分类') + '</span></td></tr>';
                });
                html += '</tbody></table>';

                var dimKeys = Object.keys(matches);
                if (dimKeys.length > 0) {
                    html += '<p class="mt-20"><strong>匹配规则：</strong></p>';
                    html += '<table class="data-table text-sm"><thead><tr><th>维度</th><th>关键词</th><th>规则名称</th><th>优先级</th></tr></thead><tbody>';
                    dimKeys.forEach(function(dim) {
                        var label = dimNames[dim] || dim;
                        (matches[dim] || []).forEach(function(m) {
                            html += '<tr><td>' + LSM.escapeHtml(label) + '</td>';
                            html += '<td><span class="badge badge-blue">' + LSM.escapeHtml(m.keyword) + '</span></td>';
                            html += '<td>' + LSM.escapeHtml(m.rule_name) + '</td>';
                            html += '<td>' + m.priority + '</td></tr>';
                        });
                    });
                    html += '</tbody></table>';
                } else {
                    html += '<p class="mt-20">未匹配到任何规则</p>';
                }

                html += '</div></div>';
                resultEl.innerHTML = html;
            })
            .catch(function(e) {
                resultEl.innerHTML = '<div class="alert alert-error">测试失败: ' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误') + '</div>';
            });
    }

    /* ── 从 YAML 重新导入 / 恢复默认 ────────────── */
    function postConfirmAction(confirmMsg, confirmTitle, url, successMsg, onSuccess) {
        LSM.confirm(confirmMsg, confirmTitle).then(function(confirmed) {
            if (!confirmed) return;
            LSM.ensureCsrfToken().then(function(csrfToken) {
                return fetch(url, {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': csrfToken },
                    credentials: 'same-origin'
                });
            })
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    LSM.showToast(data.message || successMsg, 'success');
                    if (typeof onSuccess === 'function') onSuccess();
                })
                .catch(function(e) {
                    LSM.showToast('操作失败: ' + ((e && e.message) ? e.message : '网络错误'), 'error');
                });
        });
    }

    function reloadAllRuleLists() {
        reloadRuleList('content');
        reloadRuleList('media_type');
    }

    /* ── 排除映射 ───────────────────────────────── */
    function loadExclusions() {
        var el = document.getElementById('rules-exclusions-list');
        if (!el) return;
        el.innerHTML = '<div class="loading">加载中...</div>';

        fetch('/api/rules/exclusions', { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.exclusions || data.exclusions.length === 0) {
                    el.innerHTML = '<div class="empty-state">暂无排除映射</div>';
                    return;
                }
                var html = '<table class="data-table"><thead><tr>'
                    + '<th class="w-50">ID</th>'
                    + '<th>省份关键词</th>'
                    + '<th>排除关键词</th>'
                    + '<th>备注</th>'
                    + '<th class="w-80">操作</th>'
                    + '</tr></thead><tbody>';

                data.exclusions.forEach(function(excl) {
                    html += '<tr>';
                    html += '<td>' + excl.id + '</td>';
                    html += '<td><span class="badge badge-blue">' + LSM.escapeHtml(excl.province_keyword) + '</span></td>';
                    html += '<td><span class="badge badge-yellow">' + LSM.escapeHtml(excl.excluded_keyword) + '</span></td>';
                    html += '<td>' + (excl.note ? LSM.escapeHtml(excl.note) : '-') + '</td>';
                    html += '<td>' + (window.IS_ADMIN ?
                        '<button class="btn btn-sm btn-outline" data-action="delete-exclusion" data-exclusion-id="' + excl.id + '">删除</button>' : '') + '</td>';
                    html += '</tr>';
                });

                html += '</tbody></table>';
                el.innerHTML = html;
            })
            .catch(function(e) {
                el.innerHTML = '<div class="alert alert-error">加载失败: ' +
                    LSM.escapeHtml((e && e.message) ? e.message : '网络错误') + '</div>';
            });
    }

    function addExclusion() {
        var province = document.getElementById('excl-province').value.trim();
        var keyword = document.getElementById('excl-keyword').value.trim();
        var note = document.getElementById('excl-note').value.trim();

        if (!province || !keyword) {
            LSM.showToast('请填写省份关键词和排除关键词', 'warning');
            return;
        }

        LSM.ensureCsrfToken().then(function(csrfToken) {
            return fetch('/api/rules/exclusions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                credentials: 'same-origin',
                body: JSON.stringify({
                    province_keyword: province,
                    excluded_keyword: keyword,
                    note: note
                })
            });
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.id) {
                    LSM.showToast(data.message || '排除映射已添加', 'success');
                    document.getElementById('excl-province').value = '';
                    document.getElementById('excl-keyword').value = '';
                    document.getElementById('excl-note').value = '';
                    loadExclusions();
                } else {
                    LSM.showToast(data.detail || '添加失败', 'error');
                }
            })
            .catch(function(e) {
                LSM.showToast('添加失败: ' + ((e && e.message) ? e.message : '网络错误'), 'error');
            });
    }

    function deleteExclusion(exclusionId) {
        LSM.confirm('确定删除此排除映射吗？').then(function(confirmed) {
            if (!confirmed) return;
            LSM.ensureCsrfToken().then(function(csrfToken) {
                return fetch('/api/rules/exclusions/' + exclusionId, {
                    method: 'DELETE',
                    headers: { 'X-CSRF-Token': csrfToken },
                    credentials: 'same-origin'
                });
            })
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    LSM.showToast(data.message || '排除映射已删除', 'success');
                    loadExclusions();
                })
                .catch(function(e) {
                    LSM.showToast('删除失败: ' + ((e && e.message) ? e.message : '网络错误'), 'error');
                });
        });
    }

    /* ── 初始化 ─────────────────────────────────── */
    document.addEventListener('DOMContentLoaded', function() {
        // Tab 切换
        document.querySelectorAll('#rules-tabs .tab-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var tabKey = btn.dataset.tab;
                document.querySelectorAll('#rules-tabs .tab-btn').forEach(function(b) {
                    b.classList.remove('active');
                    b.setAttribute('aria-selected', 'false');
                });
                document.querySelectorAll('#rules-tabs .tab-content').forEach(function(p) {
                    p.classList.remove('active');
                });
                btn.classList.add('active');
                btn.setAttribute('aria-selected', 'true');
                var panel = document.getElementById(tabKey);
                if (panel) panel.classList.add('active');
            });
        });

        // 工具栏（管理员按钮由 Jinja 门控渲染）
        var btnAddRule = document.getElementById('btn-add-rule');
        if (btnAddRule) btnAddRule.addEventListener('click', showAddRuleModal);
        var btnReimport = document.getElementById('btn-reimport-rules');
        if (btnReimport) btnReimport.addEventListener('click', function() {
            postConfirmAction(
                '确定从 YAML 重新导入分类规则吗？\n此操作将清空现有规则并重新导入。',
                '确认重新导入', '/api/rules/reimport', '导入完成', reloadAllRuleLists);
        });
        var btnResetRules = document.getElementById('btn-reset-rules');
        if (btnResetRules) btnResetRules.addEventListener('click', function() {
            postConfirmAction(
                '确定将分类规则恢复为系统默认吗？\n此操作将清空你自定义的所有规则与排除映射，并从 channel_rules.yml 重新导入出厂默认。',
                '确认恢复默认', '/api/rules/reset-defaults', '已恢复默认规则', reloadAllRuleLists);
        });
        var btnResetDict = document.getElementById('btn-reset-dict');
        if (btnResetDict) btnResetDict.addEventListener('click', function() {
            postConfirmAction(
                '确定将分类字典恢复为系统默认吗？\n此操作将清空你自定义的所有分类字典选项，并重新种子出厂默认值。',
                '确认恢复默认', '/api/category-dictionary/reset-defaults', '已恢复默认字典');
        });
        var btnTest = document.getElementById('btn-test-classification');
        if (btnTest) btnTest.addEventListener('click', testClassification);
        var btnAddExclusion = document.getElementById('btn-add-exclusion');
        if (btnAddExclusion) btnAddExclusion.addEventListener('click', addExclusion);

        // 规则表单提交
        document.getElementById('rule-form').addEventListener('submit', function(evt) {
            evt.preventDefault();
            saveRule();
        });

        bindListActions();
        loadRules('content', 'rules-category-list');
        loadRules('media_type', 'rules-channel-type-list');
        loadExclusions();
    });
})();
