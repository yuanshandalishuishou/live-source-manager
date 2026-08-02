/* ══════════════════════════════════════════════════════════════
   EPG 源管理 — 增删改查 / 抓取 / 生成
   ══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  var csrf = window.__csrf_token || '';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  async function api(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' }, opts.headers || {});
    var r = await fetch(url, opts);
    if (!r.ok) {
      var e = await r.json().catch(function () { return { detail: r.statusText }; });
      throw new Error(e.detail || ('HTTP ' + r.status));
    }
    return r.json();
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
      setStatus('加载失败：' + e.message, true);
    }
  }
  function setStatus(msg, err) {
    var el = document.getElementById('epgStatus');
    if (!el) return;
    el.innerHTML = (err ? '⚠️ ' : '') + esc(msg);
  }

  function render(sources) {
    var tb = document.getElementById('epgSrcBody');
    tb.innerHTML = '';
    if (!sources.length) {
      tb.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--epg-text-dim);padding:30px">暂无 EPG 源，点击「＋ 添加源」添加。</td></tr>';
      return;
    }
    sources.forEach(function (s) {
      var tr = document.createElement('tr');
      var last = s.last_status === 'ok'
        ? '<span class="epg-badge ok">成功 ' + (s.last_channel_count || 0) + ' 频道</span>'
        : s.last_status === 'error'
          ? '<span class="epg-badge err" title="' + esc(s.last_error || '') + '">失败</span>'
          : '<span class="epg-badge off">未抓取</span>';
      tr.innerHTML =
        '<td><b>' + esc(s.name) + '</b></td>' +
        '<td class="src-url" title="' + esc(s.url) + '">' + esc(s.url) + '</td>' +
        '<td><span class="epg-badge ' + (s.enabled ? 'on' : 'off') + '">' + (s.enabled ? '启用' : '禁用') + '</span></td>' +
        '<td>' + esc(s.priority) + '</td>' +
        '<td>' + esc(refreshLabel(s)) + '</td>' +
        '<td>' + last + '<br><span style="color:var(--epg-text-dim);font-size:11px">' + fmtTs(s.last_fetch_at) + '</span></td>' +
        '<td><div class="epg-row-actions" data-id="' + s.id + '">' +
          '<button class="epg-mini" data-act="refresh">抓取</button>' +
          '<button class="epg-mini" data-act="edit">编辑</button>' +
          '<button class="epg-mini danger" data-act="delete">删除</button>' +
        '</div></td>';
      tb.appendChild(tr);
    });
  }

  // ── 弹层 ────────────────────────────────────
  function openModal(src) {
    src = src || {};
    document.getElementById('f_id').value = src.id || '';
    document.getElementById('f_name').value = src.name || '';
    document.getElementById('f_url').value = src.url || '';
    document.getElementById('f_enabled').value = src.enabled ? '1' : '0';
    document.getElementById('f_priority').value = src.priority != null ? src.priority : 100;
    document.getElementById('f_refresh_mode').value = src.refresh_mode || '';
    document.getElementById('f_refresh_when').value =
      src.refresh_mode === 'interval' ? (src.refresh_minutes || '') : (src.refresh_at || '');
    document.getElementById('epgModalTitle').textContent = src.id ? '编辑 EPG 源' : '添加 EPG 源';
    document.getElementById('epgModal').classList.add('show');
  }
  function closeModal() { document.getElementById('epgModal').classList.remove('show'); }

  async function save() {
    var id = document.getElementById('f_id').value;
    var when = document.getElementById('f_refresh_when').value.trim();
    var mode = document.getElementById('f_refresh_mode').value;
    var payload = {
      name: document.getElementById('f_name').value.trim(),
      url: document.getElementById('f_url').value.trim(),
      enabled: document.getElementById('f_enabled').value === '1',
      priority: parseInt(document.getElementById('f_priority').value, 10) || 100,
      refresh_mode: mode,
      refresh_at: '',
      refresh_minutes: 0,
    };
    if (when) {
      if (/^\d{1,2}:\d{2}$/.test(when)) payload.refresh_at = when;
      else if (/^\d+$/.test(when)) payload.refresh_minutes = parseInt(when, 10);
    }
    try {
      if (id) await api('/api/epg/sources/' + id, { method: 'PUT', body: JSON.stringify(payload) });
      else await api('/api/epg/sources', { method: 'POST', body: JSON.stringify(payload) });
      closeModal();
      load();
    } catch (e) {
      alert('保存失败：' + e.message);
    }
  }

  async function doRefresh(id) {
    try {
      await api('/api/epg/sources/' + id + '/refresh', { method: 'POST' });
      setStatus('已加入抓取队列，稍后刷新查看结果');
      setTimeout(load, 4000);
    } catch (e) { alert('抓取失败：' + e.message); }
  }
  async function doRefreshAll() {
    if (!confirm('确认触发全量抓取？将下载所有启用源并重新对齐频道。')) return;
    try {
      await api('/api/epg/refresh-all', { method: 'POST' });
      setStatus('全量抓取已触发，请稍候');
      setTimeout(load, 6000);
    } catch (e) { alert('触发失败：' + e.message); }
  }
  async function doGenerate() {
    try {
      setStatus('正在生成 epg.xml.gz…');
      var r = await api('/api/epg/generate', { method: 'POST' });
      if (r.ok) setStatus('已生成：' + (r.channels || 0) + ' 频道 / ' + (r.programmes || 0) + ' 节目');
      else setStatus('生成失败：' + (r.message || '未知'), true);
    } catch (e) { alert('生成失败：' + e.message); }
  }
  async function doCopyUrl() {
    try {
      var r = await api('/api/epg/url');
      await navigator.clipboard.writeText(r.url);
      setStatus('已复制：' + r.url);
    } catch (e) { alert('复制失败：' + e.message); }
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('epgAddBtn').addEventListener('click', function () { openModal(); });
    document.getElementById('epgModalCancel').addEventListener('click', closeModal);
    document.getElementById('epgModalSave').addEventListener('click', save);
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
        });
      } else if (act === 'delete') {
        if (confirm('确认删除该 EPG 源及其全部节目数据？')) {
          api('/api/epg/sources/' + id, { method: 'DELETE' }).then(load).catch(function (e) { alert('删除失败：' + e.message); });
        }
      }
    });
    load();
  });
})();
