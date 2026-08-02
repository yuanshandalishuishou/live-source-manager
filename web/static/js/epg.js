/* ══════════════════════════════════════════════════════════════
   EPG 节目单网格 — 交互逻辑（premium：磁性 hover / 当前时刻竖线 / 主题切换）
   ══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var csrf = window.__csrf_token || '';
  var state = { hours: 12, keyword: '', data: null, timer: null };

  // ── 主题 ────────────────────────────────────
  function resolveSystem() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches
      ? 'light'
      : 'dark';
  }
  function applyTheme(t) {
    if (t === 'system') t = resolveSystem();
    document.documentElement.setAttribute('data-epg-theme', t);
  }
  function initTheme() {
    var saved = localStorage.getItem('epg-theme') || 'system';
    applyTheme(saved);
    var btn = document.getElementById('epgThemeBtn');
    if (btn) {
      btn.textContent = '🌗 ' + ({ system: '跟随系统', light: '浅色', dark: '深色' }[saved] || '跟随系统');
      btn.addEventListener('click', function () {
        var order = ['system', 'light', 'dark'];
        var cur = localStorage.getItem('epg-theme') || 'system';
        var next = order[(order.indexOf(cur) + 1) % order.length];
        localStorage.setItem('epg-theme', next);
        applyTheme(next);
        btn.textContent = '🌗 ' + ({ system: '跟随系统', light: '浅色', dark: '深色' }[next]);
      });
    }
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
        if ((localStorage.getItem('epg-theme') || 'system') === 'system') applyTheme('system');
      });
    }
  }

  function hourWidth() {
    var v = getComputedStyle(document.documentElement).getPropertyValue('--epg-hour-w');
    return parseFloat(v) || 132;
  }

  function fmtTime(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return '';
    var h = d.getHours(), m = d.getMinutes();
    return (h < 10 ? '0' + h : h) + ':' + (m < 10 ? '0' + m : m);
  }

  // ── 数据获取 ────────────────────────────────
  async function loadGrid() {
    showLoading(true);
    try {
      var url = '/api/epg/grid?hours=' + state.hours + '&keyword=' + encodeURIComponent(state.keyword);
      var resp = await fetch(url, { headers: { 'X-CSRF-Token': csrf } });
      var data = await resp.json();
      state.data = data;
      render(data);
      updateStatus(data);
    } catch (e) {
      showEmpty('加载失败：' + e.message);
    } finally {
      showLoading(false);
    }
  }

  function showLoading(on) {
    var el = document.getElementById('epgLoading');
    if (el) el.style.display = on ? 'block' : 'none';
  }
  function showEmpty(msg) {
    var el = document.getElementById('epgEmpty');
    if (el) { el.textContent = msg; el.style.display = 'block'; }
  }

  // ── 渲染 ────────────────────────────────────
  function render(data) {
    var inner = document.getElementById('epgInner');
    if (!inner) return;
    inner.innerHTML = '';
    var channels = data.channels || [];
    if (!channels.length) {
      showEmpty('暂无节目单数据。请前往「🛰️ EPG 源」添加并抓取 EPG 源。');
      return;
    }
    var hw = hourWidth();
    var winStart = new Date(data.start).getTime();
    var winEnd = new Date(data.end).getTime();
    var span = winEnd - winStart;
    var trackW = state.hours * hw;

    // 时间轴表头
    var tl = document.createElement('div');
    tl.className = 'epg-timeline';
    var corner = document.createElement('div');
    corner.className = 'epg-corner';
    corner.textContent = '频道 / 时间';
    tl.appendChild(corner);
    var d = new Date(winStart);
    d.setMinutes(0, 0, 0);
    for (var i = 0; i <= state.hours; i++) {
      var tick = document.createElement('div');
      tick.className = 'epg-tick';
      tick.style.width = hw + 'px';
      tick.textContent = fmtTime(d.toISOString());
      tl.appendChild(tick);
      d = new Date(d.getTime() + 3600 * 1000);
    }
    inner.appendChild(tl);

    var nowMs = new Date(data.now).getTime();

    channels.forEach(function (ch) {
      var row = document.createElement('div');
      row.className = 'epg-row';

      // 频道列
      var cell = document.createElement('div');
      cell.className = 'epg-ch';
      if (ch.icon) {
        var img = document.createElement('img');
        img.className = 'epg-logo';
        img.src = ch.icon;
        img.alt = '';
        img.onerror = function () { this.style.display = 'none'; };
        cell.appendChild(img);
      } else {
        var fb = document.createElement('div');
        fb.className = 'epg-logo-fallback';
        fb.textContent = (ch.name || '?').slice(0, 1);
        cell.appendChild(fb);
      }
      var meta = document.createElement('div');
      var nm = document.createElement('div');
      nm.className = 'epg-ch-name';
      nm.textContent = ch.name;
      nm.title = ch.name;
      meta.appendChild(nm);
      var sub = document.createElement('div');
      sub.className = 'epg-ch-sub';
      sub.textContent = ch.source_name || (ch.matched ? '已对齐' : '未对齐');
      sub.title = sub.textContent;
      meta.appendChild(sub);
      cell.appendChild(meta);
      row.appendChild(cell);

      // 轨道
      var track = document.createElement('div');
      track.className = 'epg-track';
      track.style.width = trackW + 'px';

      var nowProg = null;
      (ch.programmes || []).forEach(function (p) {
        var s = new Date(p.start).getTime();
        var e = new Date(p.stop).getTime();
        if (isNaN(s) || isNaN(e)) return;
        var left = ((s - winStart) / span) * trackW;
        var width = ((e - s) / span) * trackW;
        if (width < 4) width = 4;
        if (left + width < 0 || left > trackW) return;
        var block = document.createElement('div');
        block.className = 'epg-prog';
        if (s <= nowMs && nowMs < e) {
          block.classList.add('is-now');
          nowProg = p;
          var badge = document.createElement('span');
          badge.className = 'p-now-badge';
          badge.textContent = 'LIVE';
          block.appendChild(badge);
        }
        block.style.left = Math.max(0, left) + 'px';
        block.style.width = Math.min(trackW - Math.max(0, left), width) + 'px';
        var t = document.createElement('div');
        t.className = 'p-title';
        t.textContent = p.title;
        var tm = document.createElement('div');
        tm.className = 'p-time';
        tm.textContent = fmtTime(p.start) + ' – ' + fmtTime(p.stop) + (p.category ? ' · ' + p.category : '');
        block.appendChild(t);
        block.appendChild(tm);
        block.addEventListener('mouseenter', function (ev) { showTip(ev, ch, p); });
        block.addEventListener('mousemove', moveTip);
        block.addEventListener('mouseleave', hideTip);
        track.appendChild(block);
      });
      row.appendChild(track);
      inner.appendChild(row);
    });

    // 当前时刻竖线
    var line = document.createElement('div');
    line.className = 'epg-now-line';
    line.id = 'epgNowLine';
    line.style.left = (state.hours * hw) * ((nowMs - winStart) / span) + 'px';
    inner.appendChild(line);
  }

  // ── 详情弹层 ────────────────────────────────
  var tip = null;
  function ensureTip() {
    if (!tip) { tip = document.createElement('div'); tip.className = 'epg-tip'; document.body.appendChild(tip); }
    return tip;
  }
  function showTip(ev, ch, p) {
    var t = ensureTip();
    t.innerHTML =
      '<h4>' + escapeHtml(p.title || '未知节目') + '</h4>' +
      '<div class="muted">' + escapeHtml(ch.name) + '</div>' +
      '<div>' + fmtTime(p.start) + ' – ' + fmtTime(p.stop) + '</div>' +
      (p.category ? '<div class="muted">类型：' + escapeHtml(p.category) + '</div>' : '') +
      (p.desc ? '<div style="margin-top:6px">' + escapeHtml(p.desc) + '</div>' : '');
    t.classList.add('show');
    moveTip(ev);
  }
  function moveTip(ev) {
    if (!tip) return;
    var x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + 290 > window.innerWidth) x = ev.clientX - 290;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }
  function hideTip() { if (tip) tip.classList.remove('show'); }
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function updateStatus(data) {
    var el = document.getElementById('epgStatus');
    if (el) {
      el.innerHTML =
        '<span class="epg-dot"></span> ' +
        '频道 ' + (data.total || 0) + ' · 显示 ' + ((data.channels || []).length) + ' · 时区偏移 ' +
        (data.tz_offset_minutes || 0) + ' 分钟';
    }
  }

  // ── 事件绑定 ────────────────────────────────
  function bind() {
    var search = document.getElementById('epgSearch');
    if (search) {
      var t;
      search.addEventListener('input', function () {
        clearTimeout(t);
        t = setTimeout(function () { state.keyword = search.value.trim(); loadGrid(); }, 350);
      });
    }
    var hours = document.getElementById('epgHours');
    if (hours) hours.addEventListener('change', function () { state.hours = parseInt(hours.value, 10) || 12; loadGrid(); });
    var refresh = document.getElementById('epgRefresh');
    if (refresh) refresh.addEventListener('click', loadGrid);
  }

  // 当前时刻线每分钟推进
  function tickNow() {
    var line = document.getElementById('epgNowLine');
    if (line && state.data) {
      var hw = hourWidth();
      var winStart = new Date(state.data.start).getTime();
      var winEnd = new Date(state.data.end).getTime();
      var nowMs = Date.now();
      line.style.left = (state.hours * hw) * ((nowMs - winStart) / (winEnd - winStart)) + 'px';
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    bind();
    loadGrid();
    state.timer = setInterval(tickNow, 60000);
  });
})();
