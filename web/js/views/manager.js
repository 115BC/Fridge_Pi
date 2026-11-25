/* 管理页：宿管 4 tab + 超管 3 tab（对照小程序 manager.wxml） */
(function (w) {
  'use strict';
  const { store, get, post, del, toast, modal, esc, fmtDur, fmtAgo, copy,
          COLORS, STATUS, ROLES } = w.Core;
  const isAdmin = () => store.role === 'admin';
  const S = {
    section: 'items', building: '', q: '', view: 'active', sort: 'expire_at',
    filter: '', items: [], counts: {}, escCount: 0, cap: null, fridge: null,
    storageDays: 6, users: [], buildings: [], contact: '', logs: [],
    queue: [], calls: [], nodes: [], nodeEvents: [], debug: null,
    unlockTip: '', stock: null
  };

  function render(app) {
    app.innerHTML = w.App.header('manager') + `<div class="wrap" id="mgrBody">
      <div class="card"><div class="muted">加载中…</div></div></div>`;
    w.App.bindHeader();
    Promise.allSettled([get('/api/v1/buildings', false),
                        get('/api/v1/settings/storage-days')]).then(([b, d]) => {
      if (b.status === 'fulfilled') { S.buildings = b.value.buildings || []; S.contact = b.value.admin_contact || ''; }
      if (d.status === 'fulfilled') S.storageDays = d.value.days || 6;
      draw(app);
    });
  }

  function draw(app) {
    const box = app.querySelector('#mgrBody') || document.getElementById('mgrBody');
    const tabs = [['items','物品管理'],['students','学生管理'],['logs','开锁记录'],['queue','排队与呼叫']];
    if (isAdmin()) tabs.push(...[['users','账号管理'],['devices','设备监控'],['debug','系统调试']]);
    box.innerHTML = `
      <div class="tabs">${tabs.map(([k, n]) =>
        `<button class="tab ${S.section === k ? 'on' : ''}" data-s="${k}">${n}</button>`).join('')}</div>
      ${S.unlockTip ? `<div class="card" style="color:${S.unlockTip.startsWith('冰箱已') ? 'var(--success)' : 'var(--danger)'}">${esc(S.unlockTip)}</div>` : ''}
      <div class="toolbar card" style="margin-bottom:14px">
        <span class="muted">楼宇</span>
        <select id="mBuilding"><option value="">全部楼宇</option>
          ${S.buildings.map(b => `<option value="${esc(b.name)}" ${S.building === b.name ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}
        </select>
        <button class="btn btn-ghost btn-sm" id="mRefresh">刷新</button>
      </div>
      <div id="mgrSection"></div>`;
    box.querySelectorAll('.tab').forEach(t => t.onclick = () => { S.section = t.dataset.s; draw(app); });
    box.querySelector('#mBuilding').onchange = e => { S.building = e.target.value; draw(app); };
    box.querySelector('#mRefresh').onclick = () => loadSection();
    loadSection();
  }

  function currentFridge() {
    const b = S.buildings.find(x => x.name === S.building);
    return b ? b.fridge_id : 'fridge-01';
  }

  async function loadSection() {
    const box = document.getElementById('mgrSection');
    if (!box) return;
    if (S.section === 'items') return loadItems(box);
    if (S.section === 'students') return loadStudents(box);
    if (S.section === 'logs') return loadLogs(box);
    if (S.section === 'queue') return loadQueue(box);
    if (S.section === 'users') return loadUsers(box);
    if (S.section === 'devices') return loadNodes(box);
    if (S.section === 'debug') return loadDebug(box);
  }

  // ---------- 物品管理 ----------
  async function loadItems(box) {
    const parts = [`status=${S.view}`, `sort=${S.sort}`];
    if (S.filter === 'escalated') parts.push('escalated=1');
    else if (S.filter) parts.push('color=' + S.filter);
    if (S.q) parts.push('q=' + encodeURIComponent(S.q));
    if (S.building) parts.push('building=' + encodeURIComponent(S.building));
    let d;
    try { d = await get('/api/v1/items?' + parts.join('&')); } catch (e) { box.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
    S.items = d.items || []; S.counts = d.counts || {}; S.escCount = d.escalated_count || 0;
    const [cap, fr] = await Promise.allSettled([
      get('/api/v1/capacity?fridge_id=' + encodeURIComponent(currentFridge())),
      get('/api/v1/fridge/state?fridge_id=' + encodeURIComponent(currentFridge()))
    ]);
    S.cap = cap.status === 'fulfilled' ? cap.value : null;
    S.fridge = fr.status === 'fulfilled' ? fr.value : null;

    const qs = S.building ? `?building=${encodeURIComponent(S.building)}` : '';
    box.innerHTML = `
      <div class="card">
        <div class="counts">
          <span>超期 <b class="badge badge-red">${S.counts.red || 0}</b></span>
          <span>临期 <b class="badge badge-yellow">${S.counts.yellow || 0}</b></span>
          <span>正常 <b class="badge badge-green">${S.counts.green || 0}</b></span>
          <span>超期未处理 <b>${S.escCount}</b></span>
        </div>
        <div class="muted" style="margin-top:8px">${S.fridge && S.fridge.locked ? '冰箱已满（已上锁）' : '冰箱正常'} ·
          排队 ${S.fridge && S.fridge.queue_count || 0} 人 · 未处理呼叫 ${S.fridge && S.fridge.open_calls || 0} ·
          容量 ${S.cap ? (S.cap.used_ml / 1000).toFixed(1) + '/' + (S.cap.capacity_ml / 1000).toFixed(0) + 'L (' + S.cap.util_pct + '%)' : '-'}</div>
      </div>
      <div class="card">
        <div class="hd"><div class="card-title" style="margin:0">台账与操作</div>
          <div class="btn-row">
            <button class="btn btn-primary btn-sm" id="btnScanLogin">扫屏幕登录码</button>
            <button class="btn btn-success btn-sm" id="btnScanUnlock">扫标签开锁</button>
            <button class="btn btn-ghost btn-sm" id="btnStock">拍照盘点</button>
            <button class="btn btn-ghost btn-sm" id="btnRemind">一键提醒</button>
            <button class="btn btn-ghost btn-sm" id="btnCsv">导出 CSV</button>
            ${isAdmin() ? '<button class="btn btn-ghost btn-sm" id="btnDays">存放天数</button>' : ''}
          </div></div>
        <div class="toolbar">
          <button class="tab ${S.view === 'active' ? 'on' : ''}" data-v="active">在库</button>
          <button class="tab ${S.view === 'pending_claim' ? 'on' : ''}" data-v="pending_claim">待认领</button>
          <button class="tab ${S.view === 'taken_out' ? 'on' : ''}" data-v="taken_out">已取出/归档</button>
          <input id="mQ" placeholder="搜索物品/编码/姓名" value="${esc(S.q)}">
          <select id="mFilter">
            <option value="">全部颜色</option>
            <option value="red" ${S.filter === 'red' ? 'selected' : ''}>超期</option>
            <option value="yellow" ${S.filter === 'yellow' ? 'selected' : ''}>临期</option>
            <option value="green" ${S.filter === 'green' ? 'selected' : ''}>正常</option>
            <option value="escalated" ${S.filter === 'escalated' ? 'selected' : ''}>超期未处理</option>
          </select>
          <select id="mSort">
            <option value="expire_at" ${S.sort === 'expire_at' ? 'selected' : ''}>按到期</option>
            <option value="created_at" ${S.sort === 'created_at' ? 'selected' : ''}>按登记</option>
            <option value="name" ${S.sort === 'name' ? 'selected' : ''}>按名称</option>
          </select>
        </div>
        ${S.items.length ? `<div class="table-wrap"><table><thead><tr><th>物品/编码</th><th>主人</th><th>状态</th><th>存放至/编号</th><th>体积</th><th>操作</th></tr></thead><tbody>
          ${S.items.map(it => `<tr>
            <td class="t1" data-label="物品/编码"><b>${esc(it.name)}</b><div class="muted">${esc(it.code)} · ${esc(it.category || '')}</div></td>
            <td data-label="主人">${esc(it.owner_name || '')} <div class="muted">${esc(it.owner_room || '')}</div></td>
            <td data-label="状态"><span class="badge badge-${it.color || 'green'}">${esc(it.color_label || STATUS[it.status] || it.status)}</span>
                ${it.escalated ? '<span class="badge badge-red">超期未处理</span>' : ''}</td>
            <td data-label="存放至/编号">${esc(it.expire_at || '-')}<div class="muted">编号 ${esc(it.batch_no || '-')} · ${esc(it.stored_at || '')}</div></td>
            <td data-label="体积">${esc(it.volume_ml ? (it.volume_ml/1000).toFixed(1)+'L实测' : (it.vol_est_ml/1000).toFixed(1)+'L估')}</td>
            <td data-label="操作"><div class="row-actions">
              ${it.status === 'active' ? `<button class="btn btn-primary btn-sm" data-act="unlock" data-code="${esc(it.code)}">开锁</button>` : ''}
              <button class="btn btn-ghost btn-sm" data-act="rename" data-id="${it.id}" data-name="${esc(it.name)}">改名</button>
              <button class="btn btn-ghost btn-sm" data-act="volume" data-id="${it.id}" data-vol="${esc(it.volume_ml || '')}">体积</button>
              ${isAdmin() ? `<button class="btn btn-ghost btn-sm" data-act="keep" data-id="${it.id}" data-keep="${esc(it.expire_at || '')}">存放至</button>` : ''}
              ${it.status === 'active' ? `<button class="btn btn-ghost btn-sm" data-act="pending" data-id="${it.id}" data-name="${esc(it.name)}">待认领</button>` : ''}
              ${it.status === 'pending_claim' ? `<button class="btn btn-ghost btn-sm" data-act="restore" data-id="${it.id}">恢复</button>` : ''}
              ${it.status === 'active' || it.status === 'pending_claim' ? `<button class="btn btn-ghost btn-sm" data-act="removed" data-id="${it.id}" data-name="${esc(it.name)}">依规清理</button>
              <button class="btn btn-success btn-sm" data-act="taken" data-id="${it.id}" data-name="${esc(it.name)}">已取出</button>
              <button class="btn btn-ghost btn-sm" data-act="notify" data-id="${it.id}" data-name="${esc(it.name)}">提醒本人</button>` : ''}
            </div></td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">该视图暂无物品</div>'}
      </div>
      ${S.stock ? `<div class="card"><div class="card-title">盘点结果</div>
        <div class="muted">照片中二维码 ${S.stock.total_in_photo_qr} 个 · 找到 ${S.stock.found} · 缺失 ${S.stock.missing} · 未知 ${(S.stock.unknown_codes||[]).length}</div>
        <div class="muted">缺失编码：${esc((S.stock.missing_codes || S.stock.unknown_codes || []).join(', '))}</div>
        ${S.stock.photo_url ? `<a href="${esc(S.stock.photo_url)}" target="_blank">查看盘点照片</a>` : ''}
        <button class="btn btn-ghost btn-sm" id="btnStockClose">关闭</button></div>` : ''}
      ${qs ? '' : ''}`;
    bindItems(box);
  }

  function bindItems(box) {
    box.querySelectorAll('[data-v]').forEach(b => b.onclick = () => { S.view = b.dataset.v; loadItems(box); });
    const q = box.querySelector('#mQ');
    if (q) q.onchange = () => { S.q = q.value.trim(); loadItems(box); };
    const f = box.querySelector('#mFilter');
    if (f) f.onchange = () => { S.filter = f.value; loadItems(box); };
    const s = box.querySelector('#mSort');
    if (s) s.onchange = () => { S.sort = s.value; loadItems(box); };
    const on = (id, fn) => { const el = box.querySelector('#' + id); if (el) el.onclick = fn; };
    on('btnScanLogin', () => w.Scanner.pickCode(code => {
      const m = (code || '').match(/fridge-login:([0-9a-zA-Z]+)/);
      if (!m) return toast('这不是屏幕登录码');
      post('/api/v1/kiosk/login/confirm', { ticket: m[1] }).then(() => toast('已登录屏幕，可在屏幕上登记'))
        .catch(e => toast('登录失败：' + e.message));
    }));
    on('btnScanUnlock', () => w.Scanner.pickCode(code => unlock(code.trim())));
    on('btnStock', () => stocktake(box));
    on('btnStockClose', () => { S.stock = null; loadItems(box); });
    on('btnRemind', async () => {
      if (!await modal({ title: '一键提醒', content: '向当前列表中的临期/超期物品主人推送微信提醒？' })) return;
      try {
        const d = await post('/api/v1/reminders', { item_ids: S.items.map(i => i.id), building: S.building });
        toast(`应发 ${d.total}，成功 ${d.sent}`);
      } catch (e) { toast('提醒失败：' + e.message); }
    });
    on('btnCsv', () => exportCsv());
    on('btnDays', async () => {
      const v = await modal({ title: '默认存放天数', content: `当前 ${S.storageDays} 天（1~60）`,
                              input: true, inputValue: String(S.storageDays) });
      if (v === null) return;
      const n = parseInt(v);
      if (!(n >= 1 && n <= 60)) return toast('请输入 1~60');
      try { await post('/api/v1/settings/storage-days', { days: n }); S.storageDays = n; toast('已更新'); }
      catch (e) { toast('失败：' + e.message); }
    });
    box.querySelectorAll('[data-act]').forEach(btn => btn.onclick = async () => {
      const a = btn.dataset.act, id = btn.dataset.id;
      if (a === 'unlock') return unlock(btn.dataset.code);
      if (a === 'rename') {
        const v = await modal({ title: '修改物品名', content: '物品名', input: true, inputValue: btn.dataset.name });
        if (!v || !v.trim()) return;
        return action(id, 'rename', { value: v.trim() }, box);
      }
      if (a === 'volume') {
        const v = await modal({ title: '修正体积(ml)', content: '0=恢复估算', input: true,
                                inputValue: btn.dataset.vol || '0' });
        if (v === null) return;
        try { await post(`/api/v1/items/${id}/volume`, { volume_ml: Number(v) || 0 }); toast('已修正'); loadItems(box); }
        catch (e) { toast('失败：' + e.message); }
      }
      if (a === 'keep') {
        const v = await modal({ title: '修改存放至', content: '格式 YYYY-MM-DD', input: true,
                                inputValue: btn.dataset.keep });
        if (!v) return;
        try { await post(`/api/v1/items/${id}/keep-until`, { keep_until: v.trim() }); toast('已修改'); loadItems(box); }
        catch (e) { toast('失败：' + e.message); }
      }
      const labels = { pending: ['移入待认领区', `「${btn.dataset.name}」移入待认领区？`],
                       removed: ['依规清理', `确认清理「${btn.dataset.name}」？建议先拍照公示。`],
                       taken: ['标记已取出', `确认「${btn.dataset.name}」已由本人取走？`] };
      if (labels[a] && !await modal({ title: labels[a][0], content: labels[a][1] })) return;
      if (a === 'notify') {
        try { const d = await post(`/api/v1/items/${id}/notify-cleaned`, {}); await modal({ title: '提醒结果', content: d.msg || '已发送', showCancel: false }); }
        catch (e) { toast('失败：' + e.message); }
        return;
      }
      if (a === 'restore') return action(id, 'restore', {}, box);
      if (['pending','removed','taken'].includes(a)) return action(id, a, {}, box);
    });
  }

  async function action(id, act, extra, box) {
    try { await post(`/api/v1/items/${id}/action`, Object.assign({ action: act }, extra));
          toast('已处理'); loadItems(box); }
    catch (e) { toast('失败：' + e.message); }
  }

  async function unlock(code) {
    if (!code) return;
    try {
      const d = await post(`/api/v1/items/${encodeURIComponent(code)}/unlock`, {});
      S.unlockTip = '正在通知冰箱开锁…'; draw(document.getElementById('app'));
      pollUnlock(d.item_id, 0);
    } catch (e) { toast('开锁失败：' + e.message); }
  }
  async function pollUnlock(id, tries) {
    if (tries > 20) { S.unlockTip = '开锁超时，请检查冰箱端服务'; draw(document.getElementById('app')); return; }
    try {
      const d = await get(`/api/v1/items/${id}/unlock`);
      if (d.status === 'unlocked') S.unlockTip = '冰箱已开锁';
      else if (d.status === 'rejected') S.unlockTip = '开锁被拒绝：' + (d.result_reason || '');
      else if (d.status === 'expired') S.unlockTip = '开锁指令已过期，请重新发起';
      else { setTimeout(() => pollUnlock(id, tries + 1), 1500); return; }
      draw(document.getElementById('app'));
      if (d.status === 'unlocked') setTimeout(() => { S.unlockTip = ''; draw(document.getElementById('app')); }, 8000);
    } catch (_) { setTimeout(() => pollUnlock(id, tries + 1), 1500); }
  }

  async function stocktake(box) {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = 'image/*';
    input.onchange = async () => {
      if (!input.files[0]) return;
      try {
        const b64 = await new Promise((res, rej) => {
          const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.onerror = rej;
          r.readAsDataURL(input.files[0]);
        });
        toast('识别中…');
        S.stock = await post('/api/v1/stocktake_b64', { image_b64: b64, fridge_id: currentFridge() });
        loadItems(box);
      } catch (e) { toast('盘点失败：' + e.message); }
    };
    input.click();
  }

  async function exportCsv() {
    const parts = ['status=all'];
    if (S.building) parts.push('building=' + encodeURIComponent(S.building));
    try {
      const res = await fetch(w.WEB_CONFIG.API_BASE + '/api/v1/export/items.csv?' + parts.join('&'),
                              { headers: { Authorization: 'Bearer ' + store.token } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `冰箱台账_${S.building || '全部'}_${Date.now()}.csv`;
      a.click(); URL.revokeObjectURL(a.href);
      toast('已导出');
    } catch (e) { toast('导出失败：' + e.message); }
  }

  // ---------- 学生管理 / 楼宇 ----------
  async function loadStudents(box) {
    try {
      const [us, bd] = await Promise.all([
        get('/api/v1/users' + (S.building ? '?building=' + encodeURIComponent(S.building) : '')),
        get('/api/v1/buildings', false)
      ]);
      S.users = us.users || []; S.buildings = bd.buildings || []; S.contact = bd.admin_contact || '';
    } catch (e) { box.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
    const isA = isAdmin();
    box.innerHTML = `
      ${isA ? `<div class="card"><div class="card-title">楼宇管理</div>
        <div class="muted">超管联系方式：${esc(S.contact || '未设置')}</div>
        <div class="toolbar"><input id="newBuilding" placeholder="楼宇名"><input id="newFridge" placeholder="冰箱编号如 fridge-01">
          <button class="btn btn-primary btn-sm" id="btnAddBuilding">新增楼宇</button>
          <button class="btn btn-ghost btn-sm" id="btnContact">设联系方式</button></div>
        <div class="muted">${S.buildings.map(b => `${esc(b.name)}→${esc(b.fridge_id)} <button class="btn btn-danger btn-sm" data-delb="${esc(b.name)}">删</button>`).join(' ')}</div></div>` : ''}
      <div class="card"><div class="card-title">学生列表（${S.users.length}）</div>
        <div class="toolbar"><input id="uQ" placeholder="搜索姓名/寝室" value="${esc(S.q)}"></div>
        <div class="table-wrap"><table><thead><tr><th>姓名/寝室</th><th>楼宇</th><th>角色</th><th>额度</th><th>网站账号/学号</th><th>校园邮箱</th><th>操作</th></tr></thead><tbody>
        ${S.users.map(u => `<tr><td class="t1" data-label="姓名/寝室"><b>${esc(u.name)}</b><div class="muted">${esc(u.room)} · ${esc(u.openid)}</div></td>
          <td data-label="楼宇">${esc(u.building || '未分楼')}</td><td data-label="角色">${ROLES[u.role] || u.role}</td><td data-label="额度">${esc(u.quota || 5)}</td>
          <td data-label="网站账号/学号">${esc(u.web_username || '-')}<div class="muted">${esc(u.student_id || '')}</div></td>
          <td data-label="校园邮箱">${esc(u.email || '未绑定')}</td><td data-label="操作"><div class="row-actions">
            <button class="btn btn-ghost btn-sm" data-act="profile" data-id="${u.id}" data-name="${esc(u.name)}" data-room="${esc(u.room)}">改资料</button>
            ${isA ? `<button class="btn btn-ghost btn-sm" data-act="building" data-id="${u.id}" data-building="${esc(u.building)}">改楼</button>
            <button class="btn btn-ghost btn-sm" data-act="role" data-id="${u.id}" data-role="${u.role}">改角色</button>` : ''}
            <button class="btn btn-ghost btn-sm" data-act="quota" data-id="${u.id}" data-quota="${esc(u.quota || 5)}">额度</button>
            ${isA ? `<button class="btn btn-primary btn-sm" data-act="web" data-id="${u.id}" data-name="${esc(u.name)}">${u.web_username ? '重置网站密码' : '开通网站账号'}</button>
            <button class="btn btn-ghost btn-sm" data-act="editall" data-id="${u.id}">改全部</button>
            <button class="btn btn-ghost btn-sm" data-act="mailreset" data-id="${u.id}" data-name="${esc(u.name)}">邮件重置密码</button>` : ''}
          </div></td></tr>`).join('')}
        </tbody></table></div></div>`;
    bindStudents(box);
  }
  function editAllUser(id, box) {
    const u = S.users.find(x => x.id == id);
    if (!u) return toast('用户不存在');
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML = `<div class="modal" style="max-width:560px">
      <div class="modal-title">超管编辑：${esc(u.name)}（密码不可改，请用"邮件重置密码"）</div>
      <div class="grid g2" style="gap:6px">
        ${[['name','姓名',u.name],['room','寝室号',u.room],['building','楼宇',u.building],
           ['role','角色(student/manager/admin)',u.role],['quota','额度',u.quota],
           ['email','校园邮箱',u.email],['student_id','学号',u.student_id],
           ['web_username','网站登录名',u.web_username],['phone','电话',u.phone]]
          .map(([k,label,v]) => `<div><label>${esc(label)}</label><input data-f="${k}" value="${esc(v || '')}"></div>`).join('')}
      </div>
      <div class="modal-actions" style="margin-top:14px">
        <button class="btn btn-ghost" data-act="cancel">取消</button>
        <button class="btn btn-primary" data-act="save">保存</button>
      </div></div>`;
    document.body.appendChild(mask);
    mask.onclick = async e => {
      const act = e.target.dataset.act;
      if (act === 'cancel' || e.target === mask) return mask.remove();
      if (act !== 'save') return;
      const data = {};
      mask.querySelectorAll('[data-f]').forEach(inp => {
        const v = inp.value.trim();
        if (v !== '') data[inp.dataset.f] = inp.dataset.f === 'quota' ? Number(v) : v;
      });
      if (!data.name || !data.room) return toast('姓名和寝室号不能为空');
      try {
        await post(`/api/v1/admin/users/${id}/edit`, data);
        mask.remove(); toast('已保存'); loadStudents(box);
      } catch (err) { toast('失败：' + err.message); }
    };
  }

  function bindStudents(box) {
    const on = (id, fn) => { const el = box.querySelector('#' + id); if (el) el.onclick = fn; };
    const uq = box.querySelector('#uQ');
    if (uq) uq.onchange = () => { S.q = uq.value.trim(); loadStudents(box); };
    on('btnAddBuilding', async () => {
      const name = box.querySelector('#newBuilding').value.trim();
      const fridge = box.querySelector('#newFridge').value.trim();
      if (!name || !fridge) return toast('请填楼宇名和冰箱编号');
      try { await post('/api/v1/admin/buildings', { name, fridge_id: fridge }); toast('已新增'); loadStudents(box); }
      catch (e) { toast('失败：' + e.message); }
    });
    on('btnContact', async () => {
      const v = await modal({ title: '超管联系方式', content: '留空则清空', input: true, inputValue: S.contact });
      if (v === null) return;
      try { await post('/api/v1/admin/contact', { value: v.trim() }); toast('已设置'); loadStudents(box); }
      catch (e) { toast('失败：' + e.message); }
    });
    box.querySelectorAll('[data-delb]').forEach(b => b.onclick = async () => {
      if (!await modal({ title: '删除楼宇', content: `确定删除「${b.dataset.delb}」？楼内有学生时会被拒绝。` })) return;
      try { await del('/api/v1/admin/buildings/' + encodeURIComponent(b.dataset.delb)); toast('已删除'); loadStudents(box); }
      catch (e) { toast('失败：' + e.message); }
    });
    box.querySelectorAll('[data-act]').forEach(btn => btn.onclick = async () => {
      const a = btn.dataset.act, id = btn.dataset.id;
      try {
        if (a === 'profile') {
          const name = await modal({ title: '改姓名', content: '姓名', input: true, inputValue: btn.dataset.name });
          const room = await modal({ title: '改寝室', content: '寝室号', input: true, inputValue: btn.dataset.room });
          if (name === null || room === null || !name.trim() || !room.trim()) return;
          await post(`/api/v1/users/${id}/profile`, { name: name.trim(), room: room.trim() });
        } else if (a === 'building') {
          const v = await modal({ title: '改楼宇', content: '楼宇名', input: true, inputValue: btn.dataset.building });
          if (!v) return;
          await post(`/api/v1/users/${id}/building`, { building: v.trim() });
        } else if (a === 'role') {
          const v = await modal({ title: '改角色', content: 'student / manager / admin', input: true, inputValue: btn.dataset.role });
          if (!v) return;
          await post(`/api/v1/users/${id}/role`, { role: v.trim() });
        } else if (a === 'quota') {
          const v = await modal({ title: '在库额度(1~50)', content: '额度', input: true, inputValue: btn.dataset.quota });
          if (!v) return;
          await post(`/api/v1/users/${id}/quota`, { quota: Number(v) });
        } else if (a === 'web') {
          const uname = await modal({ title: '网站账号', content: '登录名（唯一）', input: true,
                                      inputValue: btn.dataset.name + '-' + (S.users.find(x => x.id == id) || {}).room });
          if (!uname) return;
          const pw = await modal({ title: '初始密码', content: w.Core.PASSWORD_HINT, input: true });
          if (!pw || !w.Core.validPassword(pw)) return toast(w.Core.PASSWORD_HINT);
          await post(`/api/v1/admin/users/${id}/web-account`, { web_username: uname.trim(), password: pw, must_change_password: true });
        } else if (a === 'editall') {
          return editAllUser(id, box);
        } else if (a === 'mailreset') {
          if (!await modal({ title: '邮件重置密码', content: `系统将生成随机密码发送到「${btn.dataset.name}」的校园邮箱，你看不到新密码。确定继续？` })) return;
          const r = await post(`/api/v1/admin/users/${id}/reset-password`, {});
          await modal({ title: '已发送', content: r.detail || '新密码已发送到学生邮箱', showCancel: false });
          return;
        }
        toast('已保存'); loadStudents(box);
      } catch (e) { toast('失败：' + e.message); }
    });
  }

  // ---------- 开锁记录 ----------
  async function loadLogs(box) {
    try { S.logs = (await get('/api/v1/unlock_logs?limit=100')).logs || []; }
    catch (e) { box.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
    box.innerHTML = `<div class="card"><div class="card-title">开锁记录（最近 100）</div>
      ${S.logs.length ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>物品/编码</th><th>发起人</th><th>结果</th></tr></thead><tbody>
      ${S.logs.map(l => `<tr><td data-label="时间">${esc(l.created_at || '')}</td><td class="t1" data-label="物品/编码">${esc(l.item_name || '')} ${esc(l.code || '')}</td>
        <td data-label="发起人">${esc(l.actor_name || l.actor || '')}</td><td data-label="结果">${esc(l.status || l.result || '')}</td></tr>`).join('')}
      </tbody></table></div>` : '<div class="empty">暂无开锁记录</div>'}</div>`;
  }

  // ---------- 排队与呼叫 ----------
  async function loadQueue(box) {
    const qs = S.building ? '?building=' + encodeURIComponent(S.building) : '';
    try {
      const [fr, q, calls] = await Promise.all([
        get('/api/v1/fridge/state?fridge_id=' + encodeURIComponent(currentFridge())),
        get('/api/v1/queue' + qs), get('/api/v1/calls' + qs)
      ]);
      S.fridge = fr; S.queue = q.queue || []; S.calls = calls.calls || [];
    } catch (e) { box.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
    box.innerHTML = `
      <div class="card"><div class="hd"><div class="card-title" style="margin:0">冰箱上锁状态</div>
        <button class="btn ${S.fridge.locked ? 'btn-success' : 'btn-primary'} btn-sm" id="btnLock">${S.fridge.locked ? '解除上锁' : '设为已满'}</button></div>
        <div class="muted">${S.fridge.locked ? '已上锁：学生暂停存入' : '正常'} · 排队 ${S.fridge.queue_count || 0} 人 · 未处理呼叫 ${S.fridge.open_calls || 0}</div>
        ${S.building ? '' : '<div class="muted">上锁/通知需先选具体楼宇</div>'}
      </div>
      <div class="card"><div class="hd"><div class="card-title" style="margin:0">排队（${S.queue.length}）</div>
        <button class="btn btn-ghost btn-sm" id="btnNotifyQueue">通知排队学生</button></div>
        ${S.queue.length ? `<div class="table-wrap"><table><thead><tr><th>位次</th><th>学生</th><th>时间</th><th>状态</th></tr></thead><tbody>
          ${S.queue.map(q => `<tr><td data-label="位次">${esc(q.position)}</td><td class="t1" data-label="学生">${esc(q.name || '')} ${esc(q.room || '')}</td>
            <td data-label="时间">${esc(q.created_at || '')}</td><td data-label="状态">${esc(q.status || '')}</td></tr>`).join('')}</tbody></table></div>`
          : '<div class="empty">暂无排队</div>'}</div>
      <div class="card"><div class="card-title">匿名呼叫（${S.calls.length}）</div>
        ${S.calls.length ? S.calls.map(c => `<div class="kv"><div class="li-name">${esc(c.kind_label || c.kind)}
          ${c.status === 'open' ? '<span class="badge badge-yellow">待处理</span>' : '<span class="badge badge-green">已处理</span>'}</div>
          <div class="muted">${esc(c.created_at || '')} ${esc(c.note || '')}</div>
          ${c.status === 'open' ? `<button class="btn btn-success btn-sm" data-done="${c.id}">已处理</button>` : ''}</div>`).join('')
          : '<div class="empty">暂无呼叫</div>'}</div>`;
    const on = (id, fn) => { const el = box.querySelector('#' + id); if (el) el.onclick = fn; };
    on('btnLock', async () => {
      if (!S.building) return toast('请先选择具体楼宇');
      const locked = !S.fridge.locked;
      if (!await modal({ title: locked ? '设为冰箱已满' : '解除上锁',
        content: locked ? '学生将不能存入（只能排队登记），取出不受影响。' : '学生可继续存入，并自动提醒排队学生。' })) return;
      try { const d = await post('/api/v1/fridge/lock', { locked, fridge_id: currentFridge() });
            toast(locked ? '已上锁' : `已解除，提醒 ${d.notified || 0} 人`); loadQueue(box); }
      catch (e) { toast('失败：' + e.message); }
    });
    on('btnNotifyQueue', async () => {
      if (!S.building) return toast('请先选择具体楼宇');
      if (!await modal({ title: '通知排队学生', content: '推送微信提醒（1 小时内不重复）？' })) return;
      try { const d = await post('/api/v1/queue/notify', { fridge_id: currentFridge() }); toast(`已通知 ${d.sent} 人`); }
      catch (e) { toast('失败：' + e.message); }
    });
    box.querySelectorAll('[data-done]').forEach(b => b.onclick = async () => {
      try { await post(`/api/v1/calls/${b.dataset.done}/done`, {}); toast('已处理'); loadQueue(box); }
      catch (e) { toast('失败：' + e.message); }
    });
  }

  // ---------- 账号管理（超管）：与学生管理共用列表端点，展示网站账号/角色/额度 ----------
  function loadUsers(box) {
    return loadStudents(box);
  }

  // ---------- 设备监控（超管） ----------
  async function loadNodes(box) {
    try {
      const d = await get('/api/v1/admin/nodes');
      S.nodes = d.nodes || []; S.nodeEvents = d.node_events || [];
    } catch (e) { box.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
    box.innerHTML = `
      <div class="card"><div class="card-title">冰箱节点 · 局域网地址与供电</div>
        ${S.nodes.length ? S.nodes.map(n => {
          const p = n.power || {}, flags = n.power_flags || [];
          const now = flags.filter(f => f.indexOf('当前') === 0);
          const past = flags.filter(f => f.indexOf('当前') !== 0);
          return `<div class="kv">
            <div class="li-name">${esc(n.fridge_id)} <span class="badge ${n.online ? 'badge-green' : 'badge-red'}">${n.online ? '在线' : '离线'}</span>
              ${n.mock ? '<span class="badge badge-yellow">模拟</span>' : ''}
              ${now.length ? `<span class="badge badge-red">${esc(now.join('·'))}</span>` : ''}</div>
            <div class="li-name">IP ${esc(n.ip || '未上报')} <span class="copy" data-copy="${esc(n.ip || '')}">复制</span></div>
            <div class="muted">主机 ${esc(n.hostname || '-')} · 已运行 ${fmtDur(n.uptime_s)} · 心跳 ${fmtAgo(n.seconds_ago)}</div>
            <div class="muted">供电 ${p.v != null ? Number(p.v).toFixed(2) + 'V · ' + (p.a_est != null ? Number(p.a_est).toFixed(2) + 'A(估)' : '-') + ' · ' + (p.w != null ? Number(p.w).toFixed(1) + 'W' : '-') : '未上报电压电流（仅 Pi 5 支持）'}${past.length ? ' · ' + esc(past.join('·')) : ''}</div>
            <div class="muted">版本 ${esc(n.version || '-')} · 最后心跳 ${esc(n.last_seen)}</div></div>`;
        }).join('') : '<div class="empty">暂无心跳</div>'}</div>
      <div class="card"><div class="card-title">上线 / IP 变更 / 供电事件（最近 30）</div>
        ${S.nodeEvents.length ? S.nodeEvents.map(e => `<div class="kv"><div class="li-name">${esc(e.type === 'pi_online' ? '上线' : e.type === 'pi_ip_change' ? 'IP 变更' : e.type === 'pi_undervolt' ? '欠压' : '供电恢复')} <span class="muted">${esc(e.actor)}</span></div>
          <div class="muted">${esc(e.created_at)} · ${esc(e.detail)}</div></div>`).join('') : '<div class="empty">暂无</div>'}</div>`;
    box.querySelectorAll('[data-copy]').forEach(el => el.onclick = () => copy(el.dataset.copy));
  }

  // ---------- 系统调试（超管） ----------
  async function loadDebug(box) {
    try {
      const [pi, ev] = await Promise.all([get('/api/v1/debug/pi'), get('/api/v1/debug/events?limit=50')]);
      S.debug = { pi, ev };
    } catch (e) { box.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
    const { pi, ev } = S.debug;
    box.innerHTML = `
      <div class="card"><div class="card-title">冰箱节点状态</div>
        ${(pi.pi_nodes || []).map(n => `<div class="kv"><div class="li-name">${esc(n.fridge_id)}
          <span class="badge ${n.is_locked ? 'badge-green' : 'badge-red'}">${n.is_locked ? '已落锁' : '开锁中'}</span></div>
          <div class="muted">版本 ${esc(n.version)} · ${esc(n.last_seen)}</div></div>`).join('') || '<div class="empty">暂无</div>'}</div>
      <div class="card"><div class="card-title">解锁指令（最近 50）</div>
        ${(pi.unlock_commands || []).map(c => `<div class="kv"><div class="li-name">${esc(c.code)} · ${esc(c.status)}</div>
          <div class="muted">${esc(c.created_at)} ${esc(c.actor || '')}</div></div>`).join('') || '<div class="empty">暂无</div>'}</div>
      <div class="card"><div class="card-title">事件流水（最近 50）</div>
        ${(ev.events || []).map(e => `<div class="kv"><div class="li-name">${esc(e.type)} <span class="muted">${esc(e.actor)}</span></div>
          <div class="muted">${esc(e.created_at)} · ${esc(e.detail)}</div></div>`).join('') || '<div class="empty">暂无</div>'}</div>`;
  }

  w.ManagerView = { render };
})(window);
