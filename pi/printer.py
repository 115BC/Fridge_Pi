"""标签生成与打印：40x30mm 不干胶标签（二维码 + 编码/品名/到期日）。

无打印机（mock）时仍生成 PNG 到 data/labels/，便于校对版式；
有打印机时通过 CUPS `lp` 命令输出（树莓派 sudo apt install cups 后添加打印机）。
"""
import shutil
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LABEL_DIR = BASE_DIR / "data" / "labels"
# 300dpi 下 40x30mm ≈ 472x354 点
W, H = 472, 354


class LabelPrinter:
    def __init__(self, printer_name: str = ""):
        self.printer_name = printer_name
        self.mock = (not printer_name) or shutil.which("lp") is None
        if self.mock:
            print("[printer] 未配置/未找到 CUPS lp，标签仅生成 PNG 模拟")

    def print_label(self, code: str, name: str, expire_at: str) -> Path:
        import qrcode
        from PIL import Image, ImageDraw, ImageFont

        qr = qrcode.make(code)
        qr = qr.resize((240, 240))

        label = Image.new("RGB", (W, H), "white")
        label.paste(qr, (12, (H - 240) // 2))
        d = ImageDraw.Draw(label)
        try:
            f_big = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 40)
            f_small = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 28)
        except OSError:
            f_big = f_small = ImageFont.load_default()
        x = 270
        d.text((x, 40), code, fill="black", font=f_big)
        d.text((x, 120), (name or "")[:8], fill="black", font=f_small)
        d.text((x, 190), f"到期 {expire_at[:10]}", fill="black", font=f_small)
        d.rectangle([0, 0, W - 1, H - 1], outline="black")

        LABEL_DIR.mkdir(parents=True, exist_ok=True)
        out = LABEL_DIR / f"{code}_{int(time.time())}.png"
        label.save(out)

        if not self.mock:
            subprocess.run(["lp", "-d", self.printer_name, "-o", "media=Custom.40x30mm", str(out)],
                           check=True, capture_output=True)
            print(f"[printer] 已打印标签 {code}")
        return out
