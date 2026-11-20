/* 应用入口：路由守卫 / 视图切换 / 登录 */
(function (w) {
  'use strict';
  const { store, get, post, toast, modal, esc, ROLES } = w.Core;
  const app = document.getElementById('app');
  let currentView = null;

  function render(html) { app.innerHTML = html; }
  function goLogin() {
    if (currentView === 'login') return;
    currentView = 'login';
    w.LoginView.render(app);
  }
  function goHome() {
    const role = store.role;
    if (role === 'student') goStudent();
    else if (role === 'manager' || role === 'admin') goManager();
    else goLogin();
  }
  function goStudent() { currentView = 'student'; w.StudentView.render(app); }
  function goManager() { currentView = 'manager'; w.ManagerView.render(app); }

  function header(active) {
    const user = store.user || {};
    return `<header class="topbar">
      <div class="brand">${esc(w.WEB_CONFIG.APP_TITLE)}</div>
      <nav class="nav">
        <button class="nav-item ${active === 'student' ? 'on' : ''}" id="navStudent" ${store.role === 'student' ? '' : 'hidden'}>学生</button>
        <button class="nav-item ${active === 'manager' ? 'on' : ''}" id="navManager" ${store.role === 'manager' || store.role === 'admin' ? '' : 'hidden'}>管理</button>
      </nav>
      <div class="who">${esc(user.name || '')} <span class="badge badge-blue">${ROLES[store.role] || ''}</span>
        <button class="btn btn-ghost btn-sm" id="btnLogout">退出</button></div>
    </header>`;
  }
  function bindHeader() {
    const s = document.getElementById('navStudent');
    const m = document.getElementById('navManager');
    if (s) s.onclick = goStudent;
    if (m) m.onclick = goManager;
    const out = document.getElementById('btnLogout');
    if (out) out.onclick = async () => {
      if (await modal({ title: '退出登录', content: '确定退出当前账号？' })) {
        store.clear(); goLogin();
      }
    };
  }

  async function boot() {
    if (!store.token) return goLogin();
    try {
      const me = await get('/api/v1/me');
      store.user = me.user; store.role = me.role;
      if (me.user && me.user.must_change_password) {
        const ok = await modal({ title: '首次登录', content: '请先修改初始密码', showCancel: false });
        goHome();
        // 由视图层弹改密（学生/管理页统一在顶部提示）
      }
      goHome();
    } catch (e) {
      toast(e.message); goLogin();
    }
  }

  w.App = { render, goLogin, goHome, goStudent, goManager, header, bindHeader };
  boot();
})(window);
