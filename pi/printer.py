"""标签生成与打印：40x30mm 不干胶标签（二维码 + 编码/品名/到期日）。

打印机：佳博 GP-3120TUC（203dpi，TSPL 指令集）。优先直写 /dev/usb/lp0
（服务用户需在 lp 组：sudo usermod -aG lp chen）；
其次 CUPS lp（配置 printer.name 时）；都没有则 mock 仅落 PNG。
"""
import shutil
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LABEL_DIR = BASE_DIR / "data" / "labels"
TSPL_DEV = "/dev/usb/lp0"
DOTS_PER_MM = 8          # 203dpi ≈ 8 点/毫米
DEFAULT_MM = (40, 30)


class LabelPrinter:
    def __init__(self, printer_name: str = "", label_mm=None):
        self.printer_name = printer_name
        w_mm, h_mm = label_mm or DEFAULT_MM
        self.w_mm, self.h_mm = int(w_mm), int(h_mm)
        self.W = self.w_mm * DOTS_PER_MM
        self.H = self.h_mm * DOTS_PER_MM
        self.dev = TSPL_DEV if Path(TSPL_DEV).exists() else ""
        self.cups = bool(printer_name) and shutil.which("lp") is not None
        self.mock = not (self.dev or self.cups)
        if self.mock:
            print("[printer] 未找到 /dev/usb/lp0 或 CUPS，标签仅生成 PNG 模拟")

    def print_label(self, code: str, name: str, expire_at: str) -> Path:
        label = self._render(code, name, expire_at)
        LABEL_DIR.mkdir(exist_ok=True)
        out = LABEL_DIR / f"{code}_{int(time.time())}.png"
        label.save(out)
        if self.dev:
            self._send_tspl(label)
            print(f"[printer] TSPL 已打印标签 {code}")
        elif self.cups:
            subprocess.run(["lp", "-d", self.printer_name, "-o", "media=Custom.40x30mm", str(out)],
                           check=True, capture_output=True)
            print(f"[printer] CUPS 已打印标签 {code}")
        return out

    def _fit_font(self, text: str, avail_px: int, size: int):
        """从指定字号往下缩，直到文字实际宽度放得下；缩到 12 仍放不下才截断。"""
        from PIL import ImageFont
        while size > 12:
            f = self._font(size)
            if f.getlength(text) <= avail_px:
                return text, f
            size -= 1
        f = self._font(size)
        while len(text) > 2 and f.getlength(text) > avail_px:
            text = text[:-1]
        return text, f

    @staticmethod
    def _font(size: int):
        from PIL import ImageFont
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", size)
        except OSError:
            return ImageFont.load_default()

    def _render(self, code: str, name: str, expire_at: str):
        import qrcode
        from PIL import Image, ImageDraw

        W, H = self.W, self.H
        qr_side = int(H * 0.70)
        mx = int(W * 0.02)
        qr = qrcode.make(code).resize((qr_side, qr_side))
        label = Image.new("RGB", (W, H), "white")
        label.paste(qr, (mx, (H - qr_side) // 2))
        d = ImageDraw.Draw(label)
        x = mx + qr_side + int(W * 0.03)
        avail = W - x - 26          # 右侧留打印盲区
        t1, f1 = self._fit_font(code, avail, int(H * 0.12))
        t2, f2 = self._fit_font((name or "").strip() or "未命名", avail, int(H * 0.10))
        t3, f3 = self._fit_font(f"到期 {expire_at[:10]}", avail, int(H * 0.09))
        d.text((x, int(H * 0.12)), t1, fill="black", font=f1)
        d.text((x, int(H * 0.42)), t2, fill="black", font=f2)
        d.text((x, int(H * 0.66)), t3, fill="black", font=f3)
        d.rectangle([0, 0, W - 1, H - 1], outline="black")
        return label

    def _send_tspl(self, img):
        """PIL 图 → TSPL BITMAP 指令。位=1 表示不打印（白），0 打印（黑）。"""
        g = img.convert("L")
        px = g.load()
        W, H = self.W, self.H
        wbytes = W // 8
        buf = bytearray()
        for y in range(H):
            for xb in range(wbytes):
                byte = 0
                for b in range(8):
                    if px[xb * 8 + b, y] > 128:
                        byte |= 0x80 >> b
                buf.append(byte)
        cmd = (f"SIZE {self.w_mm} mm,{self.h_mm} mm\r\nGAP 2 mm,0\r\nDIRECTION 0\r\nCLS\r\n".encode()
               + f"BITMAP 0,0,{wbytes},{H},0,".encode()
               + bytes(buf) + b"\r\nPRINT 1,1\r\n")
        with open(self.dev, "wb") as f:
            f.write(cmd)
