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
# 203dpi 原生：40x30mm = 320x240 点
W, H = 320, 240


class LabelPrinter:
    def __init__(self, printer_name: str = ""):
        self.printer_name = printer_name
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

    def _render(self, code: str, name: str, expire_at: str):
        import qrcode
        from PIL import Image, ImageDraw, ImageFont

        qr = qrcode.make(code).resize((180, 180))
        label = Image.new("RGB", (W, H), "white")
        label.paste(qr, (12, (H - 180) // 2))
        d = ImageDraw.Draw(label)
        try:
            f_big = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 28)
            f_small = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 20)
        except OSError:
            f_big = f_small = ImageFont.load_default()
        x = 204
        d.text((x, 34), code, fill="black", font=f_big)
        d.text((x, 92), (name or "")[:8], fill="black", font=f_small)
        d.text((x, 140), f"到期 {expire_at[:10]}", fill="black", font=f_small)
        d.rectangle([0, 0, W - 1, H - 1], outline="black")
        return label

    def _send_tspl(self, img):
        """PIL 图 → TSPL BITMAP 指令。位=1 表示不打印（白），0 打印（黑）。"""
        g = img.convert("L")
        px = g.load()
        wbytes = W // 8
        buf = bytearray()
        for y in range(H):
            for xb in range(wbytes):
                byte = 0
                for b in range(8):
                    if px[xb * 8 + b, y] > 128:
                        byte |= 0x80 >> b
                buf.append(byte)
        cmd = (b"SIZE 40 mm,30 mm\r\nGAP 2 mm,0\r\nDIRECTION 0\r\nCLS\r\n"
               + f"BITMAP 0,0,{wbytes},{H},0,".encode()
               + bytes(buf) + b"\r\nPRINT 1,1\r\n")
        with open(self.dev, "wb") as f:
            f.write(cmd)
