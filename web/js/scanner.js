/* 扫码：安卓用 BarcodeDetector；iPhone Safari 用 jsQR 逐帧解二维码；无摄像头则手动输码。
   摄像头要求安全上下文（HTTPS 或 localhost），HTTP 公网访问会直接落到手动输入。 */
(function (w) {
  'use strict';
  const { toast, modal } = w.Core;

  async function pickCode(cb) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast('当前不是安全连接（需 HTTPS），无法调用摄像头，请手动输入');
      return manual(cb);
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    } catch (e) {
      toast('摄像头不可用或未授权，请手动输入');
      return manual(cb);
    }
    const decode = await combinedFn();
    if (decode) {
      return openCameraModal(stream, cb, decode);
    }
    stream.getTracks().forEach(t => t.stop());
    toast('此浏览器不支持扫码识别，请手动输入');
    return manual(cb);
  }

  async function combinedFn() {
    const barcode = await detectorFn();
    const jsqr = w.jsQR ? jsqrFn() : null;
    if (!barcode && !jsqr) return null;
    return async video => {
      if (barcode) {
        try {
          const raw = await barcode(video);
          if (raw) return raw;
        } catch (_) {}
      }
      if (jsqr) {
        try {
          const raw = await jsqr(video);
          if (raw) return raw;
        } catch (_) {}
      }
      return null;
    };
  }

  async function detectorFn() {
    if (!w.BarcodeDetector) return null;
    try {
      const d = new w.BarcodeDetector({ formats: ['qr_code', 'code_128', 'ean_13'] });
      return async video => {
        const codes = await d.detect(video);
        return codes && codes.length ? codes[0].rawValue : null;
      };
    } catch (_) {
      return null;
    }
  }

  function jsqrFn() {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    return async video => {
      if (!video.videoWidth) return null;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const code = w.jsQR(img.data, img.width, img.height);
      return code && code.data ? code.data : null;
    };
  }

  async function manual(cb) {
    const v = await modal({ title: '输入编码', content: '无法使用摄像头时，手动输入标签码或屏幕登录码',
                            input: true, placeholder: '如 ABCD1234 或 fridge-login:xxxx' });
    if (v && v.trim()) cb(v.trim());
  }

  function openCameraModal(stream, cb, decode) {
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
    const timer = setInterval(async () => {
      if (stopped || video.readyState < 2) return;
      try {
        const raw = await decode(video);
        if (raw) { stop(); cb(raw); }
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
