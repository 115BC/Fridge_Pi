/* 扫码：优先 BarcodeDetector（需 HTTPS/localhost + 摄像头授权），不支持则弹框手动输码 */
(function (w) {
  'use strict';
  const { toast, modal } = w.Core;

  async function pickCode(cb) {
    if (w.BarcodeDetector) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        return openCameraModal(stream, cb);
      } catch (e) { /* 授权失败或没有摄像头 → 手动输入 */ }
    }
    manual(cb);
  }

  async function manual(cb) {
    const v = await modal({ title: '输入编码', content: '无法使用摄像头时，手动输入标签码或屏幕登录码',
                            input: true, placeholder: '如 ABCD1234 或 fridge-login:xxxx' });
    if (v && v.trim()) cb(v.trim());
  }

  function openCameraModal(stream, cb) {
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML = `<div class="modal">
      <div class="modal-title">扫二维码 / 条形码</div>
      <video id="scanVideo" style="width:100%;max-height:340px;background:#000" autoplay playsinline></video>
      <div class="modal-body" id="scanHint">将标签码对准摄像头…</div>
      <div class="modal-actions"><button class="btn btn-ghost" data-act="manual">手动输入</button>
      <button class="btn btn-ghost" data-act="cancel">取消</button></div></div>`;
    document.body.appendChild(mask);
    const video = mask.querySelector('#scanVideo');
    video.srcObject = stream;
    let stopped = false;
    const stop = () => { if (stopped) return; stopped = true;
      try { stream.getTracks().forEach(t => t.stop()); } catch (_) {}
      clearInterval(timer); mask.remove(); };
    const detector = new w.BarcodeDetector({ formats: ['qr_code', 'code_128', 'ean_13'] });
    const timer = setInterval(async () => {
      if (stopped || video.readyState < 2) return;
      try {
        const codes = await detector.detect(video);
        if (codes && codes.length) { const raw = codes[0].rawValue; stop(); cb(raw); }
      } catch (_) {}
    }, 350);
    mask.addEventListener('click', e => {
      const act = e.target.dataset.act;
      if (act === 'cancel') stop();
      else if (act === 'manual') { stop(); manual(cb); }
    });
  }

  w.Scanner = { pickCode };
})(window);
