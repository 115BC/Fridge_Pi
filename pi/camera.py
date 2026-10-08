import shutil
import subprocess
import time
from pathlib import Path

CAPTURE_DIR = Path(__file__).resolve().parent / "data" / "captures"


class Camera:
    """官方 Camera Module，通过 rpicam-still（新系统）或 libcamera-still（旧系统）CLI 拍照；
    无摄像头环境自动模拟。"""

    def __init__(self, mock: bool = False):
        self.bin = shutil.which("rpicam-still") or shutil.which("libcamera-still")
        self.vid_bin = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
        self.mock = mock or self.bin is None
        if self.mock:
            print("[camera] 未找到 rpicam-still/libcamera-still，使用模拟图片")

    def capture(self) -> Path:
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        out = CAPTURE_DIR / f"{int(time.time() * 1000)}.jpg"
        if not self.mock:
            subprocess.run(
                [self.bin, "-o", str(out), "--nopreview", "-t", "100",
                 "--width", "1280", "--height", "960", "--autofocus-mode", "continuous"],
                check=True, capture_output=True)
            return out
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 480), (235, 235, 235))
        ImageDraw.Draw(img).text((230, 230), "MOCK CAMERA", (90, 90, 90))
        img.save(out)
        return out
