/* 核心层：请求封装 / 存储 / 常量 / DOM 工具 */
(function (w) {
  'use strict';
  const C = w.WEB_CONFIG;

  const store = {
    get token() { return localStorage.getItem('fridge_token') || ''; },
    set token(v) { v ? localStorage.setItem('fridge_token', v) : localStorage.removeItem('fridge_token'); },
    get role() { return localStorage.getItem('fridge_role') || ''; },
    set role(v) { v ? localStorage.setItem('fridge_role', v) : localStorage.removeItem('fridge_role'); },
    get user() { try { return JSON.parse(localStorage.getItem('fridge_user') || 'null'); } catch (_) { return null; } },
    set user(v) { v ? localStorage.setItem('fridge_user', JSON.stringify(v)) : localStorage.removeItem('fridge_user'); },
    get remember() { return localStorage.getItem('fridge_remember') === '1'; },
    set remember(v) { localStorage.setItem('fridge_remember', v ? '1' : '0'); },
    get savedAccount() { return localStorage.getItem('fridge_account') || ''; },
    set savedAccount(v) { v ? localStorage.setItem('fridge_account', v) : localStorage.removeItem('fridge_account'); },
    clear() { ['fridge_token', 'fridge_role', 'fridge_user'].forEach(k => localStorage.removeItem(k)); }
  };

  let toastTimer = null;
  function toast(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  async function request(path, method = 'GET', data = null, auth = true) {
    const headers = { 'Content-Type': 'application/json' };
    if (auth && store.token) headers['Authorization'] = 'Bearer ' + store.token;
    let res;
    try {
      res = await fetch(C.API_BASE + path, {
        method, headers, body: data === null ? undefined : JSON.stringify(data)
      });
    } catch (e) {
      throw new Error('网络异常：' + e.message);
    }
    let body;
    try { body = await res.json(); } catch (_) { body = {}; }
    if (res.status === 401 && auth) {
      store.clear(); w.App && w.App.goLogin && w.App.goLogin();
      throw new Error('登录已过期，请重新登录');
    }
    if (!res.ok) throw new Error(body.detail || ('请求失败 ' + res.status));
    return body;
  }
  const get = (p, a) => request(p, 'GET', null, a !== false);
  const post = (p, d) => request(p, 'POST', d === undefined ? {} : d);
  const put = (p, d) => request(p, 'PUT', d);
  const del = (p) => request(p, 'DELETE');

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtDur(s) {
    s = Number(s) || 0;
    if (s < 60) return s + '秒';
    if (s < 3600) return Math.floor(s / 60) + '分钟';
    if (s < 86400) return Math.floor(s / 3600) + '小时';
    return Math.floor(s / 86400) + '天';
  }
  function fmtAgo(sec) {
    if (sec == null) return '-';
    return sec < 60 ? sec + '秒前' : Math.floor(sec / 60) + '分钟前';
  }
  function copy(text) {
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast('已复制'), () => toast('复制失败'));
    else toast('当前浏览器不支持复制');
  }

  // 确认弹层（替代 wx.showModal）
  function modal(opts) {
    return new Promise(resolve => {
      const mask = document.createElement('div');
      mask.className = 'modal-mask';
      mask.innerHTML = `<div class="modal">
        <div class="modal-title">${esc(opts.title || '提示')}</div>
        <div class="modal-body">${esc(opts.content || '')}</div>
        ${opts.input ? `<input class="modal-input" id="modalInput" value="${esc(opts.inputValue || '')}" placeholder="${esc(opts.placeholder || '')}">` : ''}
        <div class="modal-actions">
          ${opts.showCancel === false ? '' : '<button class="btn btn-ghost" data-act="cancel">取消</button>'}
          <button class="btn btn-primary" data-act="ok">${esc(opts.confirmText || '确定')}</button>
        </div></div>`;
      document.body.appendChild(mask);
      const close = val => { mask.remove(); resolve(val); };
      mask.addEventListener('click', e => {
        const act = e.target.dataset.act;
        if (act === 'ok') close(opts.input ? mask.querySelector('#modalInput').value : true);
        else if (act === 'cancel' || e.target === mask) close(null);
      });
      if (opts.input) setTimeout(() => mask.querySelector('#modalInput').focus(), 30);
    });
  }

  const COLORS = { red: '超期', yellow: '临期', green: '正常' };
  const STATUS = { active: '在库', pending_claim: '待认领', taken_out: '已取出', removed: '已清理', cancelled: '已作废', awaiting_scan: '待扫码' };
  const ROLES = { student: '学生', manager: '宿管', admin: '超管' };

  w.Core = { store, request, get, post, put, del, toast, esc, fmtDur, fmtAgo, copy, modal,
             COLORS, STATUS, ROLES };
})(window);
