/* 学生页：我的宿舍 / 我的物品 / 通知 / 排队 / 扫码登录确认 / 扫码认领 */
(function (w) {
  'use strict';
  const { store, get, post, put, toast, modal, esc, COLORS, STATUS } = w.Core;
  let state = { items: [], notices: [], queue: null, locked: false, contact: '' };

  async function load() {
    const [mine, notices, queue, buildings] = await Promise.allSettled([
      get('/api/v1/items/mine'), get('/api/v1/notifications/mine'),
      get('/api/v1/queue/mine'), get('/api/v1/buildings', false)
    ]);
    if (mine.status === 'fulfilled') state.items = mine.value.items || [];
    if (notices.status === 'fulfilled') state.notices = notices.value.notifications || [];
    if (queue.status === 'fulfilled') {
      state.queue = queue.value.queue; state.locked = !!queue.value.locked;
    }
    if (buildings.status === 'fulfilled') state.contact = buildings.value.admin_contact || '';
    renderBody();
  }

  function render(app) {
    app.innerHTML = w.App.header('student') + `<div class="wrap" id="studentBody">
      <div class="card"><div class="muted">加载中…</div></div></div>`;
    w.App.bindHeader();
    load();
  }

  function renderBody() {
    const box = document.getElementById('studentBody');
    if (!box) return;
    const u = store.user || {};
    const items = state.items;
    box.innerHTML = `
      <div class="grid g2">
        <div class="card">
          <div class="hd"><div class="card-title" style="margin:0">我的宿舍</div>
            <button class="btn btn-ghost btn-sm" id="btnEditProfile">修改</button></div>
          <div class="li-name">${esc(u.name || '-')} · ${esc(u.room || '-')}</div>
          <div class="muted">楼宇：${esc(u.building || '待补登记')} · 在库额度：${esc(u.quota || 5)} 件</div>
          ${state.contact ? `<div class="muted" style="margin-top:8px">超管联系方式：${esc(state.contact)}
            <span class="copy" id="copyContact">复制</span></div>` : ''}
          <div class="muted" style="margin-top:6px">${u.email ? `校园邮箱 ${esc(u.email)} · 学号 ${esc(u.student_id || '-')}`
            : '<button class="btn btn-ghost btn-sm" id="btnBindEmail">绑定校园邮箱</button>'}</div>
          <div class="muted" style="margin-top:6px">楼宇或姓名有误？联系超管修改。</div>
        </div>
        <div class="card">
          <div class="card-title">冰箱状态</div>
          <div class="li-name">${state.locked ? '<span class="badge badge-red">冰箱已满（已上锁）</span>' : '<span class="badge badge-green">可存入</span>'}</div>
          ${state.queue ? `<div class="muted" style="margin-top:8px">排队中 · 第 ${esc(state.queue.position || '-')} 位
            <button class="btn btn-ghost btn-sm" id="btnCancelQueue">取消排队</button></div>`
            : '<div class="muted" style="margin-top:8px">你当前没有排队</div>'}
          <div class="btn-row">
            <button class="btn btn-primary btn-sm" id="btnScanLogin">扫屏幕登录码</button>
            <button class="btn btn-success btn-sm" id="btnScanClaim">扫标签认领</button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="hd"><div class="card-title" style="margin:0">我的物品（${items.length}）</div>
          <button class="btn btn-ghost btn-sm" id="btnRefresh">刷新</button></div>
        ${items.length ? `<table><thead><tr><th>物品</th><th>状态</th><th>存放至</th><th>体积</th><th>操作</th></tr></thead><tbody>
          ${items.map(it => `<tr>
            <td><b>${esc(it.name)}</b><div class="muted">${esc(it.code)} · ${esc(it.category || '')} · 编号${esc(it.batch_no || '-')}</div></td>
            <td><span class="badge badge-${it.color || 'green'}">${esc(it.color_label || COLORS[it.color] || STATUS[it.status] || it.status)}</span></td>
            <td>${esc(it.expire_at || '-')}</td>
            <td>${esc(it.vol_l || (it.vol_est_ml / 1000).toFixed(1))}L</td>
            <td><div class="row-actions">
              ${it.status === 'active' ? `<button class="btn btn-primary btn-sm" data-act="qr" data-id="${it.id}">取物码</button>` : ''}
              <button class="btn btn-ghost btn-sm" data-act="rename" data-id="${it.id}" data-name="${esc(it.name)}">改名</button>
              ${it.status === 'active' ? `<button class="btn btn-success btn-sm" data-act="taken" data-id="${it.id}" data-name="${esc(it.name)}">已取出</button>` : ''}
            </div></td></tr>`).join('')}
        </tbody></table>` : '<div class="empty">暂无物品。存入请在冰箱触控屏登记。</div>'}
      </div>

      <div class="card">
        <div class="card-title">通知历史</div>
        ${state.notices.length ? state.notices.map(n => `<div class="kv">
          <div class="li-name">${esc(n.title || n.kind || '提醒')} <span class="badge badge-gray">${esc(n.status_label || n.status)}</span></div>
          <div class="muted">${esc(n.created_at || '')} ${esc(n.detail || '')}</div></div>`).join('')
          : '<div class="empty">暂无通知</div>'}
      </div>`;

    bind();
  }

  function bind() {
    const $ = id => document.getElementById(id);
    $('btnEditProfile') && ($('btnEditProfile').onclick = editProfile);
    $('btnBindEmail') && ($('btnBindEmail').onclick = bindEmail);
    $('copyContact') && ($('copyContact').onclick = () => w.Core.copy(state.contact));
    $('btnRefresh') && ($('btnRefresh').onclick = load);
    $('btnCancelQueue') && ($('btnCancelQueue').onclick = async () => {
      if (await modal({ title: '取消排队', content: '确定不再排队？清出空间后就不会提醒你了。' })) {
        try { await post('/api/v1/queue/mine/cancel', {}); toast('已取消排队'); load(); }
        catch (e) { toast('取消失败：' + e.message); }
      }
    });
    $('btnScanLogin') && ($('btnScanLogin').onclick = () => w.Scanner.pickCode(code => {
      const raw = (code || '').trim();
      const m = raw.match(/^(?:fridge-login:?\s*)?([0-9a-fA-F]{32})$/) || raw.match(/[?&]ticket=([0-9a-fA-F]{32})/);
      if (!m) return toast('这不是屏幕登录码：' + String(code || '').slice(0, 60));
      post('/api/v1/kiosk/login/confirm', { ticket: m[1] })
        .then(() => { toast('已登录冰箱屏幕，请在屏幕上继续'); load(); })
        .catch(e => toast('登录失败：' + e.message));
    }));
    $('btnScanClaim') && ($('btnScanClaim').onclick = () => w.Scanner.pickCode(code => {
      const c = (code || '').trim(); if (!c) return;
      post(`/api/v1/items/${encodeURIComponent(c)}/claim`, {})
        .then(d => { toast('已认领：' + d.name); load(); })
        .catch(e => toast('认领失败：' + e.message));
    }));
    document.querySelectorAll('#studentBody [data-act]').forEach(btn => {
      btn.onclick = async () => {
        const { act, id, name } = btn.dataset;
        if (act === 'qr') return showQr(id);
        if (act === 'rename') {
          const v = await modal({ title: '修改物品名', content: '物品名', input: true, inputValue: name });
          if (!v || !v.trim()) return;
          try { await post(`/api/v1/items/${id}/action`, { action: 'rename', value: v.trim() }); toast('已改名'); load(); }
          catch (e) { toast('失败：' + e.message); }
        }
        if (act === 'taken') {
          if (!await modal({ title: '取出登记', content: `确认「${name}」已取出？台账将归档这条记录。` })) return;
          try { await post(`/api/v1/items/${id}/action`, { action: 'taken' }); toast('已登记取出'); load(); }
          catch (e) { toast('失败：' + e.message); }
        }
      };
    });
  }

  async function editProfile() {
    const u = store.user || {};
    const name = await modal({ title: '修改姓名', content: '姓名', input: true, inputValue: u.name });
    if (name === null) return;
    const room = await modal({ title: '修改寝室号', content: '如 2-417', input: true, inputValue: u.room });
    if (room === null) return;
    if (!name.trim() || !room.trim()) return toast('姓名和寝室号不能为空');
    try {
      const d = await put('/api/v1/me', { name: name.trim(), room: room.trim() });
      store.user = d.user; toast('资料已更新'); load();
    } catch (e) { toast('保存失败：' + e.message); }
  }

  async function bindEmail() {
    const email = await w.Core.modal({ title: '绑定校园邮箱', content: '学号@mail.bnu.edu.cn（前缀须为纯数字学号）',
                                       input: true, placeholder: '202411081071@mail.bnu.edu.cn' });
    if (!email || !email.trim()) return;
    try {
      await w.Core.post('/api/v1/email/send-code',
                        { email: email.trim(), purpose: 'bind' }, false);
      const code = await w.Core.modal({ title: '邮箱验证码', content: '验证码已发送，请输入 6 位验证码',
                                        input: true, placeholder: '6位验证码' });
      if (!code || !code.trim()) return;
      const r = await w.Core.post('/api/v1/web/bind-email',
                                  { email: email.trim(), code: code.trim() });
      const me = await w.Core.get('/api/v1/me');
      store.user = me.user;
      toast('绑定成功，学号 ' + r.student_id);
      renderBody();
    } catch (e) { toast('绑定失败：' + e.message); }
  }

  async function showQr(id) {
    get(`/api/v1/items/${id}/qr`).then(d => {
      const mask = document.createElement('div');
      mask.className = 'modal-mask';
      mask.innerHTML = `<div class="modal">
        <div class="modal-title">取物码</div>
        <div class="modal-body">${esc(d.code)} · ${esc(d.name)}<br>把二维码对准冰箱触控屏摄像头开锁</div>
        <img class="qr-img" src="data:image/png;base64,${d.png_base64}" alt="取物码">
        <div class="modal-actions"><button class="btn btn-primary" data-act="close">关闭</button></div></div>`;
      document.body.appendChild(mask);
      mask.onclick = e => { if (e.target === mask || e.target.dataset.act === 'close') mask.remove(); };
    }).catch(e => toast('生成失败：' + e.message));
  }

  w.StudentView = { render, load };
})(window);
