"""摄像头：优先 USB(UVC) 走 OpenCV V4L2，否则官方 Camera Module 走 rpicam CLI，都没有则模拟。"""
import glob
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

CAPTURE_DIR = Path(__file__).resolve().parent / "data" / "captures"
USB_SIZE = (1280, 720)      # MJPG；YUYV 在 USB2 上帧率太低
USB_FPS = 15


class Camera:
    """USB 摄像头：cv2.VideoCapture(CAP_V4L2) + MJPG，实时流与抓帧共用同一个句柄。
    CSI 摄像头：rpicam-still / rpicam-vid CLI。无摄像头环境自动模拟。"""

    def __init__(self, mock: bool = False):
        self.bin = shutil.which("rpicam-still") or shutil.which("libcamera-still")
        self.vid_bin = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
        self.usb_index = None if mock else self._find_usb()
        self._cap = None
        self._lock = threading.Lock()
        self.mock = mock or (self.usb_index is None and self.bin is None)
        if self.usb_index is not None:
            print(f"[camera] USB(V4L2) 摄像头 /dev/video{self.usb_index}")
        elif self.mock:
            print("[camera] 未找到 USB/rpicam 摄像头，使用模拟图片")

    @staticmethod
    def _find_usb():
        """只认挂在 USB 总线上的 video 节点，避免打开 bcm2835 ISP/codec 节点；grab 一次确认能出帧。"""
        try:
            import cv2
        except ImportError:
            return None
        for node in sorted(glob.glob("/sys/class/video4linux/video*"),
                           key=lambda p: int(re.search(r"video(\d+)$", p).group(1))):
            if "/usb" not in os.path.realpath(os.path.join(node, "device")):
                continue
            idx = int(re.search(r"video(\d+)$", node).group(1))
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            try:
                if cap.isOpened() and cap.grab():
                    return idx
            finally:
                cap.release()
        return None

    def _open_locked(self):
        import cv2
        cap = cv2.VideoCapture(self.usb_index, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, USB_SIZE[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, USB_SIZE[1])
        cap.set(cv2.CAP_PROP_FPS, USB_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap
        return True

    def open_stream(self) -> bool:
        with self._lock:
            if self._cap is not None and self._cap.isOpened():
                return True
            return self._open_locked()

    def close_stream(self):
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    def read_jpeg(self, quality: int = 85):
        """读一帧并编码成 JPEG；流每隔 1/fps 秒调用一次。"""
        import cv2
        with self._lock:
            if self._cap is None or not self._cap.isOpened():
                return None
            ok, frame = self._cap.read()
            if not ok or frame is None:
                return None
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes() if ok else None

    def _capture_usb(self) -> Path:
        out = CAPTURE_DIR / f"{int(time.time() * 1000)}.jpg"
        fresh = not (self._cap is not None and self._cap.isOpened())
        if fresh and not self.open_stream():
            raise RuntimeError("USB 摄像头打开失败")
        data = None
        for _ in range(4 if fresh else 1):   # 新开设备先丢几帧，等自动曝光/对焦稳定
            data = self.read_jpeg(92) or data
        if data is None:
            raise RuntimeError("USB 摄像头取帧失败")
        out.write_bytes(data)
        return out

    def capture(self) -> Path:
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        if self.usb_index is not None:
            return self._capture_usb()
        out = CAPTURE_DIR / f"{int(time.time() * 1000)}.jpg"
        if not self.mock:
            try:
                subprocess.run(
                    [self.bin, "-o", str(out), "--nopreview", "-t", "100",
                     "--width", "1280", "--height", "960", "--autofocus-mode", "continuous"],
                    check=True, capture_output=True)
                return out
            except subprocess.CalledProcessError:
                raise           # CSI 相机不可用时明确报错，不再落模拟图
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 480), (235, 235, 235))
        ImageDraw.Draw(img).text((230, 230), "MOCK CAMERA", (90, 90, 90))
        img.save(out)
        return out
