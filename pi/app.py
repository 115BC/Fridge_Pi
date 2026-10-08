import asyncio
import hashlib
import hmac
import json
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from lock import ElectromagneticLock
from camera import Camera
from classify import LocalClassifier, CATEGORIES
from printer import LabelPrinter

BASE_DIR = Path(__file__).resolve().parent
CFG = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
EVENTS_FILE = BASE_DIR / "data" / "events.jsonl"
ITEMS_FILE = BASE_DIR / "data" / "items.jsonl"
VERSION = "1.3"

lock = ElectromagneticLock(CFG["lock"]["gpio_pin"], mock=CFG["lock"]["mock"])
camera = Camera(mock=CFG["camera"]["mock"])
classifier = LocalClassifier()
printer = LabelPrinter(CFG["printer"]["name"])
relock_task: asyncio.Task | None = None
button = None
last_unlock: dict = {"code": "", "at": 0.0}   # 供触控屏取出页确认开锁已执行


def log_event(event: str, code: str, detail: str = ""):
    EVENTS_FILE.parent.mkdir(exist_ok=True)
    entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event,
             "code": code, "detail": detail}
    with EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[event] {entry}")


def sign(code: str, ts: int) -> str:
    secret = CFG["security"]["secret"].encode()
    return hmac.new(secret, f"{code}|{ts}".encode(), hashlib.sha256).hexdigest()


def verify_token(code: str, ts: int, token: str) -> None:
    max_age = CFG["security"]["max_token_age_seconds"]
    if abs(time.time() - ts) > max_age:
        raise HTTPException(400, "令牌已过期，请重新在小程序登记")
    if not hmac.compare_digest(sign(code, ts), token):
        raise HTTPException(400, "令牌校验失败")


async def schedule_relock(code: str):
    global relock_task
    if relock_task:
        relock_task.cancel()
    seconds = CFG["lock"]["auto_relock_seconds"]

    async def _relock():
        await asyncio.sleep(seconds)
        lock.lock()
        log_event("auto_relock", code)

    relock_task = asyncio.create_task(_relock())


class UnlockRequest(BaseModel):
    code: str
    ts: int
    token: str


async def do_unlock(code: str, ts: int, token: str) -> dict:
    verify_token(code, ts, token)
    was_locked = lock.is_locked
    lock.unlock()
    await schedule_relock(code)
    last_unlock.update({"code": code, "at": time.time()})
    log_event("unlock", code, "first" if was_locked else "again")
    return {"status": "unlocked", "code": code, "auto_relock_seconds": CFG["lock"]["auto_relock_seconds"]}


async def poll_backend():
    """树莓派只主动向外请求（内网无需开放端口），从小程序后端拉取待执行的解锁指令并上报心跳。"""
    base = CFG["backend"]["base_url"].rstrip("/")
    fridge_id = CFG["backend"]["fridge_id"]
    interval = CFG["backend"]["poll_interval_seconds"]
    hb_every = CFG["backend"].get("heartbeat_seconds", 10)
    headers = {"X-Pi-Secret": CFG["security"]["secret"]}
    last_hb = 0.0
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            try:
                r = await client.get(f"{base}/api/v1/pi/pending-unlocks",
                                     params={"fridge_id": fridge_id}, headers=headers)
                for cmd in r.json().get("commands", []):
                    try:
                        await do_unlock(cmd["code"], cmd["ts"], cmd["token"])
                        ack = {"code": cmd["code"], "status": "unlocked"}
                    except HTTPException as e:
                        ack = {"code": cmd["code"], "status": "rejected", "reason": e.detail}
                    await client.post(f"{base}/api/v1/pi/unlock-result", json=ack, headers=headers)
                    log_event("backend_ack", ack["code"], ack["status"])
                now = time.time()
                if now - last_hb >= hb_every:
                    last_hb = now
                    await client.post(f"{base}/api/v1/pi/heartbeat", headers=headers, json={
                        "fridge_id": fridge_id, "is_locked": lock.is_locked,
                        "mock": lock.mock, "version": VERSION})
            except Exception as e:
                print(f"[poll] 后端不可达: {e}")
            await asyncio.sleep(interval)


async def button_press():
    """实体按键按下：与登记开锁走同一套流程（留痕 + 超时自动落锁）。"""
    was_locked = lock.is_locked
    lock.unlock()
    await schedule_relock("button")
    log_event("button_unlock", "button", "first" if was_locked else "again")


def setup_button():
    global button
    pin = CFG["lock"].get("button_gpio")
    if pin in (None, ""):
        print("[button] 未配置 lock.button_gpio，按键不启用")
        return
    try:
        from gpiozero import Button
        loop = asyncio.get_running_loop()

        def _pressed():
            asyncio.run_coroutine_threadsafe(button_press(), loop)

        button = Button(pin, pull_up=True, bounce_time=0.05)
        button.when_pressed = _pressed
        print(f"[button] 实体按键已监听 GPIO{pin}（按下=开锁）")
    except Exception as e:
        print(f"[button] gpiozero 不可用，按键停用: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if CFG["backend"]["base_url"]:
        task = asyncio.create_task(poll_backend())
        print(f"[poll] 已启用后端轮询 -> {CFG['backend']['base_url']}")
    else:
        print("[poll] 未配置 backend.base_url，仅提供本地 API")
    lock.lock()
    setup_button()
    yield
    if task:
        task.cancel()
    await stop_preview()


app = FastAPI(title="fridge-pi 锁控服务", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
def status():
    return {"is_locked": lock.is_locked, "mock": lock.mock, "button": button is not None}


@app.post("/api/unlock")
async def unlock(req: UnlockRequest):
    return await do_unlock(req.code, req.ts, req.token)


@app.post("/api/lock")
async def manual_lock():
    global relock_task
    if relock_task:
        relock_task.cancel()
    lock.lock()
    log_event("manual_lock", "")
    return {"status": "locked"}


def decode_qr_codes(image_path: Path) -> list[str]:
    """识别照片中的标签二维码（物品编码）；原图失手时放大 2x 再扫一遍。"""
    try:
        import cv2
        img = cv2.imread(str(image_path))
        det = cv2.QRCodeDetector()
        ok, decoded, _, _ = det.detectAndDecodeMulti(img)
        codes = [c for c in decoded if c] if ok else []
        if not codes and img is not None:
            big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            ok2, decoded2, _, _ = det.detectAndDecodeMulti(big)
            codes = [c for c in decoded2 if c] if ok2 else []
        return codes
    except Exception as e:
        print(f"[qr] 解码失败: {e}")
        return []


@app.post("/api/capture")
async def capture_and_recognize():
    """拍照 -> 标签二维码解码 + 本地分类建议，返回给触控屏确认页。"""
    photo = camera.capture()
    return await recognize(photo, "capture")


async def recognize(photo: Path, event: str):
    result = {
        "photo": photo.name,
        "qr_codes": decode_qr_codes(photo),
        "guess": classifier.classify(photo),
        "categories": CATEGORIES,
    }
    log_event(event, ",".join(result["qr_codes"]),
              f"guess={result['guess']['category']} conf={result['guess']['confidence']}")
    return result


PREVIEW_PORT = 8002
preview_proc = None
preview_task: asyncio.Task | None = None
preview_subscribers: set[asyncio.Queue] = set()


async def _preview_reader():
    """从 rpicam-vid stdout 切分 JPEG 帧（FFD8..FFD9），广播给各网页客户端。"""
    loop = asyncio.get_running_loop()
    buf = b""
    while True:
        chunk = await loop.run_in_executor(None, preview_proc.stdout.read, 65536)
        if not chunk:
            break
        buf += chunk
        while True:
            end = buf.find(b"\xff\xd9")
            if end == -1:
                break
            start = buf.find(b"\xff\xd8")
            if start == -1 or start > end:
                buf = buf[end + 2:]
                continue
            frame = buf[start:end + 2]
            buf = buf[end + 2:]
            for q in list(preview_subscribers):
                if q.qsize() < 2:      # 慢客户端丢旧帧，保证低延迟
                    q.put_nowait(frame)
    preview_subscribers.clear()


async def stop_preview():
    global preview_proc, preview_task
    if preview_task:
        preview_task.cancel()
        preview_task = None
    if preview_proc and preview_proc.poll() is None:
        preview_proc.terminate()
        for _ in range(30):
            if preview_proc.poll() is not None:
                break
            await asyncio.sleep(0.1)
        else:
            preview_proc.kill()
    preview_proc = None
    preview_subscribers.clear()


@app.post("/api/preview/start")
async def preview_start():
    """启动 rpicam-vid MJPEG 实时流（stdout 取流，供触控屏取景）。"""
    global preview_proc, preview_task
    if camera.mock or not camera.vid_bin:
        raise HTTPException(400, "无真实摄像头，不支持实时预览")
    if preview_proc is None or preview_proc.poll() is not None:
        preview_proc = subprocess.Popen(
            [camera.vid_bin, "-t", "0", "-n", "--codec", "mjpeg",
             "--width", "1920", "--height", "1080", "--framerate", "12",
             "--quality", "88", "-o", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        await asyncio.sleep(1.0)
        if preview_proc.poll() is not None:
            preview_proc = None
            raise HTTPException(500, "实时流启动失败（摄像头被占用？）")
        preview_task = asyncio.create_task(_preview_reader())
    return {"status": "live", "stream": "/api/preview/stream"}


@app.post("/api/preview/stop")
async def preview_stop():
    await stop_preview()
    return {"status": "stopped"}


@app.get("/api/preview/stream")
async def preview_stream():
    """同源 HTTP MJPEG 流，避免网页 canvas 跨域污染。"""
    if preview_proc is None or preview_proc.poll() is not None:
        raise HTTPException(404, "预览未启动")
    q: asyncio.Queue = asyncio.Queue(maxsize=3)
    preview_subscribers.add(q)

    async def gen():
        try:
            while preview_proc and preview_proc.poll() is None:
                frame = await q.get()
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        finally:
            preview_subscribers.discard(q)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/capture_frame")
async def capture_frame(file: UploadFile):
    """接收触控屏从实时视频抓取的当前帧 -> 解码 + 分类。"""
    from camera import CAPTURE_DIR
    if camera.mock:
        raise HTTPException(400, "无真实摄像头")
    data = await file.read()
    if len(data) < 1000 or len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "帧数据无效")
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    photo = CAPTURE_DIR / f"{int(time.time() * 1000)}.jpg"
    photo.write_bytes(data)
    return await recognize(photo, "capture_frame")


@app.get("/photos/{name}")
async def get_photo(name: str):
    from camera import CAPTURE_DIR
    path = (CAPTURE_DIR / name).resolve()
    if not str(path).startswith(str(CAPTURE_DIR.resolve())) or not path.exists():
        raise HTTPException(404, "照片不存在")
    from fastapi.responses import FileResponse
    return FileResponse(path)


class PrintReq(BaseModel):
    code: str
    name: str
    expire_at: str


@app.post("/api/print")
async def print_label(req: PrintReq):
    """一物一签：方案B下由后端在登记成功后直连调用；无打印机时生成 PNG。"""
    out = printer.print_label(req.code, req.name, req.expire_at)
    log_event("print_label", req.code, out.name)
    return {"status": "printed" if not printer.mock else "rendered_only", "file": out.name}


class ItemRecord(BaseModel):
    code: str          # 标签编码（可为空，后续人工补贴）
    name: str          # 物品名（人工确认）
    category: str      # 人工确认后的分类
    guess_category: str = ""  # 机器建议，留档用于后续微调
    photo: str = ""


@app.post("/api/items")
async def save_item(rec: ItemRecord):
    """触控屏确认后保存入库记录。"""
    ITEMS_FILE.parent.mkdir(exist_ok=True)
    entry = rec.model_dump() | {"at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with ITEMS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    corrected = rec.category != rec.guess_category and rec.guess_category != ""
    log_event("item_saved", rec.code,
              f"{rec.name}/{rec.category}" + ("/已修正" if corrected else ""))
    return {"status": "saved", "corrected": corrected}


@app.get("/kiosk")
async def kiosk_page():
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "static" / "kiosk.html")


class UnlockCodeReq(BaseModel):
    code: str


@app.post("/api/unlock_code")
async def unlock_code(req: UnlockCodeReq):
    """触控屏凭标签编码开锁（存入/取出通用）：交给后端校验后走轮询链路执行。"""
    base = CFG["backend"]["base_url"].rstrip("/")
    if not base:
        raise HTTPException(503, "未配置后端，无法校验标签")
    code = req.code.strip()
    if not code:
        raise HTTPException(400, "请输入标签编码")
    async with httpx.AsyncClient(timeout=6) as client:
        r = await client.post(f"{base}/api/v1/kiosk/unlock", json={"code": code},
                              headers={"X-Pi-Secret": CFG["security"]["secret"]})
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or f"HTTP {r.status_code}"
        except Exception:
            detail = f"HTTP {r.status_code}"
        raise HTTPException(r.status_code if r.status_code in (400, 404) else 502, detail)
    d = r.json()
    log_event("kiosk_unlock", code, f"requested name={d.get('name')}")
    return {"status": "sent", "code": code, "name": d.get("name", "")}


def _backend_url(path: str = "") -> str:
    base = CFG["backend"]["base_url"].rstrip("/")
    if not base:
        raise HTTPException(503, "未配置后端")
    return base + path


@app.post("/api/login_start")
async def login_start():
    """屏幕出登录码：向后端领 ticket，返回图片 URL（浏览器 img 直连后端，无跨域问题）。"""
    base = _backend_url()
    async with httpx.AsyncClient(timeout=6) as client:
        r = await client.post(f"{base}/api/v1/kiosk/login/start", json={},
                              headers={"X-Pi-Secret": CFG["security"]["secret"]})
    if r.status_code != 200:
        raise HTTPException(502, "登录服务不可用")
    d = r.json()
    d["qr_url"] = f"{base}/api/v1/kiosk/login/qr?ticket={d['ticket']}"
    return d


@app.get("/api/login_status")
async def login_status(ticket: str):
    base = _backend_url()
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"{base}/api/v1/kiosk/login/status", params={"ticket": ticket})
    return r.json() if r.status_code == 200 else {"status": "expired"}


@app.get("/api/my_items")
async def my_items(ticket: str):
    base = _backend_url()
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"{base}/api/v1/kiosk/my-items", params={"login_ticket": ticket})
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.json().get("detail", "获取失败"))
    return r.json()


class RegisterItemReq(BaseModel):
    name: str
    category: str = "其他"
    expire_at: str
    quantity: int = 1
    login_ticket: str = ""


@app.post("/api/register_item")
async def register_item(req: RegisterItemReq):
    """触控屏拍照登记：转发后端建台账+打印+开锁（共享密钥只留在派本地，不进浏览器）。"""
    base = CFG["backend"]["base_url"].rstrip("/")
    if not base:
        raise HTTPException(503, "未配置后端，无法登记")
    payload = req.model_dump() | {"fridge_id": CFG["backend"]["fridge_id"]}
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.post(f"{base}/api/v1/kiosk/items", json=payload,
                              headers={"X-Pi-Secret": CFG["security"]["secret"]})
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or f"HTTP {r.status_code}"
        except Exception:
            detail = f"HTTP {r.status_code}"
        raise HTTPException(r.status_code if r.status_code < 500 else 502, detail)
    return r.json()


class TakeoutConfirmReq(BaseModel):
    code: str
    taken: bool


@app.post("/api/takeout_confirm")
async def takeout_confirm(req: TakeoutConfirmReq):
    """取出确认页：已取出(归档) / 继续存放(台账不动)。"""
    base = _backend_url()
    async with httpx.AsyncClient(timeout=6) as client:
        r = await client.post(f"{base}/api/v1/kiosk/takeout-done",
                              json={"code": req.code, "taken": req.taken},
                              headers={"X-Pi-Secret": CFG["security"]["secret"]})
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or f"HTTP {r.status_code}"
        except Exception:
            detail = f"HTTP {r.status_code}"
        raise HTTPException(r.status_code if r.status_code < 500 else 502, detail)
    return r.json()


@app.get("/api/last_unlock")
def read_last_unlock():
    return last_unlock


if __name__ == "__main__":
    uvicorn.run(app, host=CFG["server"]["host"], port=CFG["server"]["port"])
