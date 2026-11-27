/* 注册页：姓名/寝室/校园邮箱(学号)/验证码/密码 */
(function (w) {
  'use strict';
  const { post, toast, esc } = w.Core;

  function render(app) {
    app.innerHTML = `<div class="login-wrap"><div class="login-card">
      <div class="login-logo">❄</div><h1>注册账号</h1>
      <p class="muted">校园邮箱验证码注册 · 学号即登录名</p>
      <form id="regForm">
        <label>姓名</label><input id="rName" required placeholder="真实姓名">
        <label>寝室号</label><input id="rRoom" required placeholder="如 2-201">
        <label>校园邮箱</label><input id="rEmail" required placeholder="学号@mail.bnu.edu.cn">
        <label>邮箱验证码</label>
        <div style="display:flex;gap:8px">
          <input id="rCode" required placeholder="6位验证码" style="flex:1">
          <button type="button" class="btn btn-primary btn-sm" id="rSend">发送验证码</button>
        </div>
        <label>登录密码</label><input id="rPw" type="password" required placeholder="${w.Core.PASSWORD_HINT}">
        <button class="btn btn-primary btn-block" type="submit">注 册</button>
        <button type="button" class="btn btn-ghost btn-block" id="rBack" style="margin-top:8px">返回登录</button>
      </form>
      <div class="login-tip">仅支持 @mail.bnu.edu.cn 校园邮箱，前缀必须为纯数字学号</div>
    </div></div>`;

    const $ = id => document.getElementById(id);
    $('rBack').onclick = () => w.App.goLogin();

    let cooldown = 0;
    $('rSend').onclick = async () => {
      const email = $('rEmail').value.trim();
      if (!email) return toast('请先填校园邮箱');
      if (cooldown > 0) return;
      try {
        await post('/api/v1/email/send-code', { email, purpose: 'register' }, false);
        toast('验证码已发送，请查收邮件');
        cooldown = 60;
        const btn = $('rSend');
        const timer = setInterval(() => {
          btn.textContent = cooldown + '秒后重发'; btn.disabled = true;
          if (--cooldown <= 0) { clearInterval(timer); btn.textContent = '发送验证码'; btn.disabled = false; }
        }, 1000);
      } catch (e) { toast(e.message); }
    };

    $('regForm').onsubmit = async e => {
      e.preventDefault();
      const btn = e.target.querySelector('[type=submit]');
      btn.disabled = true; btn.textContent = '注册中…';
      try {
        if (!w.Core.validPassword($('rPw').value)) throw new Error(w.Core.PASSWORD_HINT);
        const r = await post('/api/v1/web/register', {
          name: $('rName').value.trim(), room: $('rRoom').value.trim(),
          email: $('rEmail').value.trim(), code: $('rCode').value.trim(),
          password: $('rPw').value
        }, false);
        w.Core.store.token = r.token; w.Core.store.role = r.role; w.Core.store.user = r.user;
        toast('注册成功');
        const pending = sessionStorage.getItem('fridge_kiosk_ticket');
        if (pending) { location.href = 'kiosk-login.html?ticket=' + encodeURIComponent(pending); return; }
        w.App.goHome();
      } catch (err) { toast(err.message); }
      finally { btn.disabled = false; btn.textContent = '注 册'; }
    };
  }

  w.RegisterView = { render };
})(window);
