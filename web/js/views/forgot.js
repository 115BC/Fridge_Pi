/* 忘记密码：校园邮箱验证码重置 */
(function (w) {
  'use strict';
  const { post, toast } = w.Core;

  function render(app) {
    app.innerHTML = `<div class="login-wrap"><div class="login-card">
      <div class="login-logo">🔑</div><h1>重置密码</h1>
      <p class="muted">通过校园邮箱验证码重置登录密码</p>
      <form id="resetForm">
        <label>校园邮箱</label><input id="fEmail" required placeholder="学号@mail.bnu.edu.cn">
        <label>验证码</label>
        <div style="display:flex;gap:8px">
          <input id="fCode" required placeholder="6位验证码" style="flex:1">
          <button type="button" class="btn btn-primary btn-sm" id="fSend">发送验证码</button>
        </div>
        <label>新密码</label><input id="fPw" type="password" required placeholder="${w.Core.PASSWORD_HINT}">
        <button class="btn btn-primary btn-block" type="submit">重置密码</button>
        <button type="button" class="btn btn-ghost btn-block" id="fBack" style="margin-top:8px">返回登录</button>
      </form>
    </div></div>`;
    const $ = id => document.getElementById(id);
    $('fBack').onclick = () => w.App.goLogin();
    let cd = 0;
    $('fSend').onclick = async () => {
      const email = $('fEmail').value.trim();
      if (!email) return toast('请先填校园邮箱');
      if (cd > 0) return;
      try {
        await post('/api/v1/email/send-code', { email, purpose: 'reset' }, false);
        toast('验证码已发送'); cd = 60;
        const btn = $('fSend'); const t = setInterval(() => {
          btn.textContent = cd + '秒后重发'; btn.disabled = true;
          if (--cd <= 0) { clearInterval(t); btn.textContent = '发送验证码'; btn.disabled = false; }
        }, 1000);
      } catch (e) { toast(e.message); }
    };
    $('resetForm').onsubmit = async e => {
      e.preventDefault();
      try {
        if (!w.Core.validPassword($('fPw').value)) throw new Error(w.Core.PASSWORD_HINT);
        await post('/api/v1/web/reset-password', {
          email: $('fEmail').value.trim(), code: $('fCode').value.trim(),
          new_password: $('fPw').value
        }, false);
        toast('密码已重置，请登录'); w.App.goLogin();
      } catch (err) { toast(err.message); }
    };
  }
  w.ForgotView = { render };
})(window);
