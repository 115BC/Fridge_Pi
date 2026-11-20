/* 登录页：账号+密码，首次登录强制改密 */
(function (w) {
  'use strict';
  const { store, post, toast, modal, esc } = w.Core;

  function render(app) {
    app.innerHTML = `<div class="login-wrap">
      <div class="login-card">
        <div class="login-logo">❄</div>
        <h1>${esc(w.WEB_CONFIG.APP_TITLE)}</h1>
        <p class="muted">内网网站 · 账号密码登录（与微信小程序同一账号）</p>
        <form id="loginForm">
          <label>网站账号</label>
          <input id="username" autocomplete="username" placeholder="如 张三-301" value="${esc(store.savedAccount)}" required>
          <label>密码</label>
          <input id="password" type="password" autocomplete="current-password" placeholder="请输入密码" required>
          <label class="checkbox"><input type="checkbox" id="remember" ${store.remember ? 'checked' : ''}> 记住账号</label>
          <button class="btn btn-primary btn-block" type="submit">登 录</button>
        </form>
        <div class="login-tip">账号由宿管/超管开通。忘记密码请联系超管重置。</div>
      </div>
    </div>`;
    document.getElementById('loginForm').onsubmit = async e => {
      e.preventDefault();
      const username = document.getElementById('username').value.trim();
      const password = document.getElementById('password').value;
      const remember = document.getElementById('remember').checked;
      const btn = e.target.querySelector('button[type=submit]');
      btn.disabled = true; btn.textContent = '登录中…';
      try {
        const r = await post('/api/v1/web/login', { username, password }, false);
        store.token = r.token; store.role = r.role; store.user = r.user;
        store.remember = remember; store.savedAccount = remember ? username : '';
        if (r.must_change_password) await forceChangePassword(password);
        w.App.goHome();
      } catch (err) {
        toast(err.message);
      } finally {
        btn.disabled = false; btn.textContent = '登 录';
      }
    };
  }

  async function forceChangePassword(oldPassword) {
    while (true) {
      const np = await modal({ title: '首次登录须修改密码', content: '请输入新密码（至少 6 位）',
                               input: true, placeholder: '新密码', showCancel: false, confirmText: '确认修改' });
      if (!np || np.length < 6) { toast('新密码至少 6 位'); continue; }
      try {
        await post('/api/v1/web/change-password', { old_password: oldPassword, new_password: np });
        toast('密码已修改'); return;
      } catch (err) {
        toast(err.message); return;
      }
    }
  }

  w.LoginView = { render };
})(window);
