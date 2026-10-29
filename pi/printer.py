"""标签生成与打印：60x40mm 不干胶标签。

版式：左半上方 8 位物品编号、下方二维码；右半自上而下 批次编号（大号）、
物品名称、姓名-寝室、存放至日期。
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
DEFAULT_MM = (60, 40)


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

    def print_label(self, code: str, name: str, owner_name: str = "", room: str = "",
                    batch_no: str = "", keep_until: str = "") -> Path:
        label = self._render(code, name, owner_name, room, batch_no, keep_until)
        LABEL_DIR.mkdir(parents=True, exist_ok=True)
        out = LABEL_DIR / f"{code}_{int(time.time())}.png"
        label.save(out)
        if self.dev:
            self._send_tspl(label)
            print(f"[printer] TSPL 已打印标签 {code}")
        elif self.cups:
            subprocess.run(["lp", "-d", self.printer_name,
                            "-o", f"media=Custom.{self.w_mm}x{self.h_mm}mm", str(out)],
                           check=True, capture_output=True)
            print(f"[printer] CUPS 已打印标签 {code}")
        return out

    def _fit_font(self, text: str, avail_px: int, size: int):
        """从指定字号往下缩，直到放得下；缩到 12 仍放不下才截断。"""
        while size > 12:
            f = self._font(size)
            if f.getlength(text) <= avail_px:
                return text, f, size
            size -= 1
        f = self._font(size)
        while len(text) > 2 and f.getlength(text) > avail_px:
            text = text[:-1]
        return text, f, size

    FONT_CANDIDATES = (
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    )

    @staticmethod
    def _font(size: int):
        from PIL import ImageFont
        for path in LabelPrinter.FONT_CANDIDATES:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _render(self, code: str, name: str, owner_name: str = "", room: str = "",
                batch_no: str = "", keep_until: str = ""):
        import qrcode
        from PIL import Image, ImageDraw

        W, H = self.W, self.H
        label = Image.new("RGB", (W, H), "white")
        d = ImageDraw.Draw(label)

        qr_side = int(H * 0.625)
        code_f = self._font(int(H * 0.085))
        gap = 10
        code_h = int(H * 0.085 * 1.25)
        top = max(6, (H - (code_h + gap + qr_side)) // 2)
        lw = qr_side + 20                       # 左栏宽
        d.text((10 + (lw - 10 - d.textlength(code, font=code_f)) / 2, top),
               code, fill="black", font=code_f)
        qr = qrcode.make(code).resize((qr_side, qr_side), Image.NEAREST)
        label.paste(qr, (10, top + code_h + gap))

        left_edge = 10 + lw + 4
        d.line([(left_edge, 10), (left_edge, H - 10)], fill="black", width=2)
        x = left_edge + 12
        avail = W - x - 10

        owner = (owner_name or "未认领").strip()
        owner_line = f"{owner}-{room.strip()}" if (room or "").strip() else owner
        lines = [(str(batch_no or ""), int(H * 0.30)),
                 ((name or "").strip() or "未命名", int(H * 0.135)),
                 (owner_line, int(H * 0.105)),
                 (f"存放至 {keep_until[:10]}" if keep_until else "", int(H * 0.085))]
        sized = []
        for text, size in lines:
            if not text:
                continue
            t, f, s = self._fit_font(text, avail, size)
            sized.append((t, f, s))
        heights = [int(s * 1.30) for _, _, s in sized]
        y = max(6, (H - sum(heights)) // 2)
        for (text, f, _), hh in zip(sized, heights):
            d.text((x, y), text, fill="black", font=f)
            y += hh
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
