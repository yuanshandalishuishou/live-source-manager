/* ══════════════════════════════════════════════════════════════
   EPG 源管理 — 增删改查 / 抓取 / 生成
   T05：原生弹窗收敛 LSM.showToast/confirm；编辑弹层由静态
   .epg-modal 迁移至 LSM.showModal（动态构建，label for/id
   关联）；CSRF 调用时读取（F13）；转义收敛 LSM.escapeHtml；
   徽标/按钮/表格令牌化并入主组件体系。
   ══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── API 封装（CSRF 调用时读取，F13：禁 IIFE 加载时捕获） ── */
  async function api(url, opts) {
    opts = opts || {};
    var csrf = await LSM.ensureCsrfToken();
    opts.headers = Object.assign(
      { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' },
      opts.headers || {}
    );
    var r = await fetch(url, opts);
    if (!r.ok) {
      var e = await r.json().catch(function () { return { detail: r.statusText }; });
      throw new Error(e.detail || ('HTTP ' + r.status));
    }
    return r.json();
  }

  /* ── 错误文案规约（U-21）：禁 '' + e ─────────────────────── */
  function errText(e) {
    return (e && e.message) ? e.message : '网络错误';
  }

  function fmtTs(s) {
    if (!s) return '—';
    var d = new Date(s);
    if (isNaN(d)) return s;
    var p = function (n) { return n < 10 ? '0' + n : n; };
    return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }
  function refreshLabel(s) {
    var m = s.refresh_mode || '';
    if (m === 'daily') return '每天 ' + (s.refresh_at || '—');
    if (m === 'interval') return '每 ' + (s.refresh_minutes || '—') + ' 分';
    return '跟随全局';
  }

  async function load() {
    try {
      var data = await api('/api/epg/sources');
      render(data.sources || []);
      setStatus('');
    } catch (e) {
      setStatus('加载失败：' + errText(e), true);
    }
  }
  function setStatus(msg, err) {
    var el = document.getElementById('epgStatus');
    if (!el) return;
    el.textContent = (err ? '⚠️ ' : '') + msg;
  }

  /* ── 徽标（.badge 语义色家族，见 app.css） ────────────────── */
  function enabledBadge(on) {
    return '<span class="badge ' + (on ? 'badge-green' : 'badge-gray') + '">' +
      (on ? '启用' : '禁用') + '</span>';
  }
  function lastBadge(s) {
    if (s.last_status === 'ok') {
      return '<span class="badge badge-green">成功 ' + (s.last_channel_count || 0) + ' 频道</span>';
    }
    if (s.last_status === 'error') {
      return '<span class="badge badge-red" title="' + LSM.escapeHtml(s.last_error || '') + '">失败</span>';
    }
    return '<span class="badge badge-gray">未抓取</span>';
  }

  function render(sources) {
    var tb = document.getElementById('epgSrcBody');
    if (!tb) return;
    tb.innerHTML = '';
    if (!sources.length) {
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.colSpan = 7;
      td.className = 'data-table-empty';
      td.textContent = '暂无 EPG 源，点击「＋ 添加源」添加。';
      tr.appendChild(td);
      tb.appendChild(tr);
      return;
    }
    sources.forEach(function (s) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td><b>' + LSM.escapeHtml(s.name) + '</b></td>' +
        '<td class="src-url" title="' + LSM.escapeHtml(s.url) + '">' + LSM.escapeHtml(s.url) + '</td>' +
        '<td>' + enabledBadge(s.enabled) + '</td>' +
        '<td>' + LSM.escapeHtml(s.priority) + '</td>' +
        '<td>' + LSM.escapeHtml(refreshLabel(s)) + '</td>' +
        '<td>' + lastBadge(s) + '<br><span class="last-fetch">' + LSM.escapeHtml(fmtTs(s.last_fetch_at)) + '</span></td>' +
        '<td><div class="epg-row-actions" data-id="' + s.id + '">' +
          '<button type="button" class="btn btn-sm btn-outline" data-act="refresh">抓取</button>' +
          '<button type="button" class="btn btn-sm btn-outline" data-act="edit">编辑</button>' +
          '<button type="button" class="btn btn-sm btn-danger" data-act="delete">删除</button>' +
        '</div></td>';
      tb.appendChild(tr);
    });
  }

  /* ── 编辑弹层（T05：静态 .epg-modal-mask → LSM.showModal） ── */
  function labeledField(labelText, control) {
    var group = document.createElement('div');
    group.className = 'form-group';
    var label = document.createElement('label');
    label.textContent = labelText;
    label.setAttribute('for', control.id);
    group.appendChild(label);
    group.appendChild(control);
    return group;
  }

  function buildForm(src) {
    var form = document.createElement('form');
    form.className = 'form';
    form.noValidate = true;
    // 防御：部分浏览器在无提交按钮时仍可能触发隐式提交
    form.addEventListener('submit', function (ev) { ev.preventDefault(); });

    var idInput = document.createElement('input');
    idInput.type = 'hidden';
    idInput.id = 'epg-f-id';
    idInput.value = src.id || '';

    var nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.id = 'epg-f-name';
    nameInput.className = 'form-input';
    nameInput.placeholder = '例如：51zmt';
    nameInput.value = src.name || '';

    var urlInput = document.createElement('input');
    urlInput.type = 'text';
    urlInput.id = 'epg-f-url';
    urlInput.className = 'form-input';
    urlInput.placeholder = 'https://epg.51zmt.top:8000/e.xml.gz';
    urlInput.value = src.url || '';

    var enabledSelect = document.createElement('select');
    enabledSelect.id = 'epg-f-enabled';
    enabledSelect.className = 'form-select';
    [['1', '启用'], ['0', '禁用']].forEach(function (opt) {
      var o = document.createElement('option');
      o.value = opt[0];
      o.textContent = opt[1];
      enabledSelect.appendChild(o);
    });
    enabledSelect.value = src.enabled ? '1' : '0';

    var priorityInput = document.createElement('input');
    priorityInput.type = 'number';
    priorityInput.id = 'epg-f-priority';
    priorityInput.className = 'form-input';
    priorityInput.min = '0';
    priorityInput.value = src.priority != null ? src.priority : 100;

    var modeSelect = document.createElement('select');
    modeSelect.id = 'epg-f-refresh-mode';
    modeSelect.className = 'form-select';
    [['', '跟随全局'], ['daily', '每天定点'], ['interval', '按间隔分钟']].forEach(function (opt) {
      var o = document.createElement('option');
      o.value = opt[0];
      o.textContent = opt[1];
      modeSelect.appendChild(o);
    });
    modeSelect.value = src.refresh_mode || '';

    var whenInput = document.createElement('input');
    whenInput.type = 'text';
    whenInput.id = 'epg-f-refresh-when';
    whenInput.className = 'form-input';
    whenInput.placeholder = '03:30 或 360';
    whenInput.value = src.refresh_mode === 'interval'
      ? (src.refresh_minutes || '')
      : (src.refresh_at || '');

    var row1 = document.createElement('div');
    row1.className = 'form-row';
    row1.appendChild(labeledField('启用', enabledSelect));
    row1.appendChild(labeledField('优先级（越小越优先）', priorityInput));

    var row2 = document.createElement('div');
    row2.className = 'form-row';
    row2.appendChild(labeledField('更新模式（留空=跟随全局）', modeSelect));
    row2.appendChild(labeledField('定时(HH:MM) / 间隔(分钟)', whenInput));

    form.appendChild(idInput);
    form.appendChild(labeledField('名称', nameInput));
    form.appendChild(labeledField('XMLTV 地址（url）', urlInput));
    form.appendChild(row1);
    form.appendChild(row2);
    return form;
  }

  function openModal(src) {
    src = src || {};
    LSM.showModal({
      title: src.id ? '编辑 EPG 源' : '添加 EPG 源',
      content: buildForm(src),
      width: '520px',
      buttons: [
        { text: '取消', action: 'cancel', type: 'outline' },
        {
          text: '保存', action: 'confirm', type: 'primary',
          onClick: function () { saveSource(); return false; }  // 保存成功后由 saveSource 关闭
        }
      ]
    });
  }

  async function saveSource() {
    var id = document.getElementById('epg-f-id').value;
    var name = document.getElementById('epg-f-name').value.trim();
    var url = document.getElementById('epg-f-url').value.trim();
    if (!name || !url) {
      LSM.showToast('请填写名称和 XMLTV 地址', 'warning');
      return;
    }
    var when = document.getElementById('epg-f-refresh-when').value.trim();
    var payload = {
      name: name,
      url: url,
      enabled: document.getElementById('epg-f-enabled').value === '1',
      priority: parseInt(document.getElementById('epg-f-priority').value, 10) || 100,
      refresh_mode: document.getElementById('epg-f-refresh-mode').value,
      refresh_at: '',
      refresh_minutes: 0
    };
    if (when) {
      if (/^\d{1,2}:\d{2}$/.test(when)) payload.refresh_at = when;
      else if (/^\d+$/.test(when)) payload.refresh_minutes = parseInt(when, 10);
    }
    try {
      if (id) await api('/api/epg/sources/' + id, { method: 'PUT', body: JSON.stringify(payload) });
      else await api('/api/epg/sources', { method: 'POST', body: JSON.stringify(payload) });
      LSM.closeTopModal();
      LSM.showToast(id ? 'EPG 源已更新' : 'EPG 源已添加', 'success');
      load();
    } catch (e) {
      LSM.showToast('保存失败：' + errText(e), 'error');
    }
  }

  async function doRefresh(id) {
    try {
      await api('/api/epg/sources/' + id + '/refresh', { method: 'POST' });
      setStatus('已加入抓取队列，稍后刷新查看结果');
      setTimeout(load, 4000);
    } catch (e) {
      LSM.showToast('抓取失败：' + errText(e), 'error');
    }
  }
  async function doRefreshAll() {
    var ok = await LSM.confirm('确认触发全量抓取？将下载所有启用源并重新对齐频道。', '全量抓取');
    if (!ok) return;
    try {
      await api('/api/epg/refresh-all', { method: 'POST' });
      setStatus('全量抓取已触发，请稍候');
      setTimeout(load, 6000);
    } catch (e) {
      LSM.showToast('触发失败：' + errText(e), 'error');
    }
  }
  async function doGenerate() {
    try {
      setStatus('正在生成 epg.xml.gz…');
      var r = await api('/api/epg/generate', { method: 'POST' });
      if (r.ok) setStatus('已生成：' + (r.channels || 0) + ' 频道 / ' + (r.programmes || 0) + ' 节目');
      else setStatus('生成失败：' + (r.message || '未知'), true);
    } catch (e) {
      LSM.showToast('生成失败：' + errText(e), 'error');
    }
  }
  async function doCopyUrl() {
    try {
      var r = await api('/api/epg/url');
      await navigator.clipboard.writeText(r.url);
      setStatus('已复制：' + r.url);
    } catch (e) {
      LSM.showToast('复制失败：' + errText(e), 'error');
    }
  }
  async function deleteSource(id) {
    var ok = await LSM.confirm('确认删除该 EPG 源及其全部节目数据？', '确认删除');
    if (!ok) return;
    try {
      await api('/api/epg/sources/' + id, { method: 'DELETE' });
      LSM.showToast('EPG 源已删除', 'success');
      load();
    } catch (e) {
      LSM.showToast('删除失败：' + errText(e), 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('epgAddBtn').addEventListener('click', function () { openModal(); });
    document.getElementById('epgRefreshAll').addEventListener('click', doRefreshAll);
    document.getElementById('epgGenerate').addEventListener('click', doGenerate);
    document.getElementById('epgCopyUrl').addEventListener('click', doCopyUrl);
    document.getElementById('epgSrcBody').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-act]');
      if (!btn) return;
      var id = parseInt(btn.parentElement.getAttribute('data-id'), 10);
      var act = btn.getAttribute('data-act');
      if (act === 'refresh') doRefresh(id);
      else if (act === 'edit') {
        api('/api/epg/sources?enabled_only=false').then(function (d) {
          var src = (d.sources || []).find(function (x) { return x.id === id; });
          if (src) openModal(src);
        }).catch(function (e2) {
          LSM.showToast('加载源详情失败：' + errText(e2), 'error');
        });
      } else if (act === 'delete') {
        deleteSource(id);
      }
    });
    load();
  });
})();
