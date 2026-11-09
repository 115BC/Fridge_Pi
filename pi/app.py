import asyncio
import hashlib
import hmac
import json
import socket
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
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
OFFLINE_FILE = BASE_DIR / "data" / "offline_items.jsonl"
VERSION = "1.5"

lock = ElectromagneticLock(CFG["lock"]["gpio_pin"], mock=CFG["lock"]["mock"],
                           feedback_pin=CFG["lock"].get("feedback_gpio"),
                           pulse_seconds=CFG["lock"].get("pulse_seconds", 1.0),
                           relock_estimate=CFG["lock"].get("auto_relock_seconds", 60))
camera = Camera(mock=CFG["camera"]["mock"])
classifier = LocalClassifier()
printer = LabelPrinter(CFG["printer"]["name"], label_mm=CFG["printer"].get("label_mm", [40, 30]))
button = None
last_unlock: dict = {"code": "", "at": 0.0}   # 供触控屏取出页确认开锁已执行
door_watch: dict = {"unlocked_at": 0.0, "cycle_done": False,
                    "saw_open": False}   # 反馈线监控：一次真实开合门


def mark_unlocked(code: str):
    last_unlock.update({"code": code, "at": time.time()})
    door_watch.update(unlocked_at=time.time(), cycle_done=False, saw_open=False)


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


async def do_unlock(code: str, ts: int, token: str) -> dict:
    verify_token(code, ts, token)
    was_locked = lock.is_locked
    lock.unlock()
    mark_unlocked(code)
    log_event("unlock", code, "first" if was_locked else "again")
    return {"status": "unlocked", "code": code, "feedback": lock.has_feedback}


# ---------- 断网判定与离线登记 ----------
# 轮询成功即在线；超过 NET_STALE 秒没有成功才判离线（云托管冷启动会有几秒超时）
NET = {"online": True, "last_ok": time.time(), "last_err": ""}
NET_STALE = 10.0


def net_online() -> bool:
    return NET["online"] and time.time() - NET["last_ok"] <= NET_STALE


def load_offline_items() -> list[dict]:
    if not OFFLINE_FILE.exists():
        return []
    out = []
    for line in OFFLINE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def save_offline_items(items: list[dict]):
    OFFLINE_FILE.parent.mkdir(exist_ok=True)
    tmp = OFFLINE_FILE.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items),
                   encoding="utf-8")
    tmp.replace(OFFLINE_FILE)


def offline_pending() -> list[dict]:
    # 旧队列没有 confirmed 字段（当时登记即入库），按已完成处理
    return [i for i in load_offline_items()
            if not i.get("synced") and i.get("confirmed", True)]


def new_offline_code() -> str:
    used = {i.get("code") for i in load_offline_items()}
    while True:
        code = uuid.uuid4().hex[:8].upper()
        if code not in used:
            return code


# 与 server/batch.py 同一公式：16 天一轮，1-8 / 11-18 两组
BATCH_EPOCH = date(2026, 1, 1)
OFFLINE_STORAGE_DAYS = 6      # 断网时按默认存放天数


def batch_no(on: date) -> int:
    d = (on - BATCH_EPOCH).days % 16
    return d + 1 if d < 8 else d + 3


def keep_until(stored: date, days: int = OFFLINE_STORAGE_DAYS) -> str:
    return (stored + timedelta(days=days)).isoformat()


# 刚打印、还没扫码/输码入库的标签：5 分钟内有效
AWAITING: dict[str, dict] = {}
AWAIT_TTL = 300


def remember_awaiting(code: str, **info):
    AWAITING[code] = {"at": time.time(), **info}
    now = time.time()
    for k in [k for k, v in AWAITING.items() if now - v["at"] > AWAIT_TTL]:
        AWAITING.pop(k, None)


def awaiting_info(code: str) -> dict | None:
    info = AWAITING.get(code)
    if not info:
        return None
    if time.time() - info["at"] > AWAIT_TTL:
        AWAITING.pop(code, None)
        return None
    return info


async def sync_offline_items(client: httpx.AsyncClient, base: str, headers: dict):
    """断网期间本地登记的物品 -> 联网后补录进后端待认领区（按标签编码幂等）。"""
    items = load_offline_items()
    # 只补录已扫码确认（真正放进冰箱）的；刚打印还没确认的不能进台账
    pend = [i for i in items if not i.get("synced") and i.get("confirmed", True)]
    if not pend:
        return
    payload = {"items": [{k: i.get(k) for k in
                          ("code", "name", "category", "keep_until", "expire_at",
                           "quantity", "fridge_id", "stored_at", "batch_no")}
                         for i in pend]}
    r = await client.post(f"{base}/api/v1/pi/offline-items", json=payload, headers=headers)
    if r.status_code != 200:
        print(f"[offline] 同步失败 HTTP {r.status_code}: {r.text[:120]}")
        return
    d = r.json()
    done = set(d.get("created", [])) | set(d.get("skipped", []))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    items = load_offline_items()      # 重读：等待网络期间可能又登记了新的离线物品
    for i in items:
        if i.get("code") in done and not i.get("synced"):
            i["synced"] = True
            i["synced_at"] = now
    save_offline_items(items)
    log_event("offline_sync", ",".join(sorted(done)),
              f"created={len(d.get('created', []))} skipped={len(d.get('skipped', []))}")


BOOT_ID = uuid.uuid4().hex   # 进程级标识：服务重启即变，后端据此记一次 pi_online


def local_net_info() -> dict:
    """心跳附带的局域网信息。主 IP 用 UDP connect 选路（不发包），网卡列表走 ip -j addr。"""
    ip = ""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()
    if not ip or ip.startswith("127."):
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = ""
    nics = []
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=3)
        for iface in json.loads(out.stdout or "[]"):
            addrs = [a["local"] for a in iface.get("addr_info", [])
                     if a.get("family") == "inet" and not a["local"].startswith("127.")]
            if addrs:
                nics.append({"name": iface.get("ifname", ""), "addrs": addrs})
    except Exception:
        nics = [{"name": "primary", "addrs": [ip]}] if ip else []
    try:
        uptime = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        uptime = int(time.monotonic())
    return {"ip": ip, "hostname": socket.gethostname(), "nics": nics,
            "uptime_s": uptime, "boot_id": BOOT_ID}


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
                NET.update(online=True, last_ok=time.time(), last_err="")
                if offline_pending():
                    await sync_offline_items(client, base, headers)
                now = time.time()
                if now - last_hb >= hb_every:
                    last_hb = now
                    await client.post(f"{base}/api/v1/pi/heartbeat", headers=headers, json={
                        "fridge_id": fridge_id, "is_locked": lock.is_locked,
                        "mock": lock.mock, "version": VERSION, **local_net_info()})
            except Exception as e:
                NET.update(online=False, last_err=str(e) or type(e).__name__)
                print(f"[poll] 后端不可达: {NET['last_err']}")
            await asyncio.sleep(interval)


class UnlockRequest(BaseModel):
    code: str
    ts: int
    token: str


async def button_press():
    """实体按键按下：脉冲开锁（新锁关门自动回锁，状态以蓝白反馈线为准）。"""
    was_locked = lock.is_locked
    lock.unlock()
    mark_unlocked("button")
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


async def watch_door():
    """反馈线监控：开锁脉冲后必须先看到锁舌弹开（未锁），再看到压回（上锁），才算一次真实开合门。"""
    while True:
        try:
            if lock.has_feedback and door_watch["unlocked_at"] and not door_watch["cycle_done"]:
                if not lock.is_locked:
                    if not door_watch["saw_open"]:
                        door_watch["saw_open"] = True
                        log_event("door_open", last_unlock.get("code", ""), "检测到锁舌弹开")
                elif door_watch["saw_open"] and time.time() - door_watch["unlocked_at"] > 2:
                    door_watch["cycle_done"] = True
                    log_event("door_cycle", last_unlock.get("code", ""), "检测到门开过又关回")
        except Exception as e:
            print(f"[door] 监控异常: {e}")
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if CFG["backend"]["base_url"]:
        task = asyncio.create_task(poll_backend())
        print(f"[poll] 已启用后端轮询 -> {CFG['backend']['base_url']}")
    else:
        print("[poll] 未配置 backend.base_url，仅提供本地 API")
    door_task = asyncio.create_task(watch_door())
    lock.lock()
    setup_button()
    yield
    if task:
        task.cancel()
    door_task.cancel()
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
    lock.lock()
    log_event("manual_lock", "")
    return {"status": "locked"}


def _qr_variants(cv2, img):
    """按代价从低到高产出解码候选图；命中即停，所以顺序有意义。
    标签基本居中，故只放大中心区域（整图 2x 在派 3B 上要 1.7s，收益却和中心裁剪重叠）。"""
    yield img
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    yield g
    h, w = g.shape[:2]
    crop = g[h // 8:h * 7 // 8, w // 8:w * 7 // 8]
    yield cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, otsu = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield otsu
    blur = cv2.GaussianBlur(g, (0, 0), 3)
    yield cv2.addWeighted(g, 1.8, blur, -0.8, 0)   # 未自动对焦时靠锐化救回边缘


def decode_qr_codes(image_path: Path) -> list[str]:
    """识别照片中的标签二维码（物品编码）。

    定焦摄像头贴近就糊，单靠 OpenCV 检测器成功率很低：先用 zxing-cpp 跑一组
    预处理变体（灰度/中心放大/二值化/锐化），再退回 OpenCV 检测器兜底。
    """
    try:
        import cv2
        img = cv2.imread(str(image_path))
        if img is None:
            return []
        codes: list[str] = []
        try:
            import zxingcpp
            for var in _qr_variants(cv2, img):
                for r in zxingcpp.read_barcodes(var):
                    if r.text:
                        codes.append(r.text.strip())
                if codes:
                    break
        except ImportError:
            pass
        if not codes:
            det = cv2.QRCodeDetector()
            for var in (img, cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)):
                ok, decoded, _, _ = det.detectAndDecodeMulti(var)
                if ok:
                    codes = [c.strip() for c in decoded if c]
                if codes:
                    break
        # 标签码是 8 位字母数字；画面里若混进别的二维码，优先取像编码的那个
        codes = [c.upper() for c in codes if c]
        codes.sort(key=lambda c: 0 if len(c) == 8 and c.isalnum() else 1)
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
preview_active = False       # USB 摄像头走 OpenCV 流时用
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


async def _usb_preview_reader():
    """USB 摄像头：cv2 读帧（阻塞部分丢线程池），编码 JPEG 后广播。"""
    loop = asyncio.get_running_loop()
    try:
        while True:
            frame = await loop.run_in_executor(None, camera.read_jpeg)
            if frame is None:
                await asyncio.sleep(0.05)
                continue
            for q in list(preview_subscribers):
                if q.qsize() < 2:
                    q.put_nowait(frame)
    finally:
        preview_subscribers.clear()


async def stop_preview():
    global preview_proc, preview_task, preview_active
    if preview_task:
        preview_task.cancel()
        try:
            await preview_task
        except asyncio.CancelledError:
            pass
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
    if preview_active:
        preview_active = False
        await asyncio.get_running_loop().run_in_executor(None, camera.close_stream)
    preview_subscribers.clear()


@app.post("/api/preview/start")
async def preview_start():
    """启动实时预览：USB 走 OpenCV MJPEG，CSI 走 rpicam-vid。"""
    global preview_proc, preview_task, preview_active
    if camera.mock:
        raise HTTPException(400, "无真实摄像头，不支持实时预览")
    if camera.usb_index is not None:
        if not preview_active:
            loop = asyncio.get_running_loop()
            if not await loop.run_in_executor(None, camera.open_stream):
                raise HTTPException(500, "USB 摄像头打开失败")
            preview_active = True
            preview_task = asyncio.create_task(_usb_preview_reader())
        return {"status": "live", "stream": "/api/preview/stream"}
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
    if not preview_active and (preview_proc is None or preview_proc.poll() is not None):
        raise HTTPException(404, "预览未启动")
    q: asyncio.Queue = asyncio.Queue(maxsize=3)
    preview_subscribers.add(q)

    async def gen():
        try:
            while preview_active or (preview_proc is not None and preview_proc.poll() is None):
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=5)
                except asyncio.TimeoutError:
                    continue
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
    name: str = ""
    owner_name: str = ""
    room: str = ""
    batch_no: str = ""
    keep_until: str = ""


@app.post("/api/print")
async def print_label(req: PrintReq):
    """一物一签：方案B下由后端在登记成功后直连调用；无打印机时生成 PNG。"""
    out = printer.print_label(req.code, req.name, owner_name=req.owner_name,
                              room=req.room, batch_no=req.batch_no,
                              keep_until=req.keep_until)
    log_event("print_label", req.code, out.name)
    return {"status": "printed" if (printer.dev or printer.cups) else "rendered_only", "file": out.name}


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
    return FileResponse(BASE_DIR / "static" / "kiosk.html",
                        headers={"Cache-Control": "no-store"})   # 防 chromium 用旧缓存页


class UnlockCodeReq(BaseModel):
    code: str


@app.get("/api/net_status")
def net_status():
    """触控屏用：后端是否可达 + 断网期间本地待同步的登记条数。"""
    return {"online": net_online(), "last_ok": NET["last_ok"], "error": NET["last_err"],
            "pending": len(offline_pending())}


class OfflineItemReq(BaseModel):
    name: str
    category: str = "其他"
    quantity: int = 1


@app.post("/api/offline_item")
async def offline_item(req: OfflineItemReq):
    """断网存入第 1 步：本机生成编码 + 打印标签（不开锁），
    贴好标签扫二维码/输 8 位编码后 /api/confirm_store 才开锁并记为已确认。"""
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "请填写物品名称")
    code = new_offline_code()
    today = date.today()
    keep = keep_until(today)
    bno = batch_no(today)
    try:
        out = printer.print_label(code, name, batch_no=str(bno), keep_until=keep)
        print_status = "printed" if not printer.mock else "rendered_only"
        log_event("print_label", code, out.name)
    except Exception as e:
        print_status = "failed"
        log_event("print_fail", code, str(e))
    items = load_offline_items()
    items.append({"code": code, "name": name, "category": req.category,
                  "keep_until": keep, "quantity": max(1, req.quantity),
                  "fridge_id": CFG["backend"]["fridge_id"],
                  "stored_at": today.isoformat(), "batch_no": bno,
                  "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "synced": False, "confirmed": False})
    save_offline_items(items)
    remember_awaiting(code, name=name, batch_no=bno, keep_until=keep, offline=True)
    log_event("offline_item", code, f"{name}/{req.category} 断网登记待扫码")
    return {"status": "awaiting_scan", "offline": True, "code": code,
            "batch_no": bno, "keep_until": keep, "print": print_status,
            "queued": len([i for i in items if not i.get("synced")])}


@app.post("/api/unlock_code")
async def unlock_code(req: UnlockCodeReq):
    """触控屏凭标签编码开锁（存入/取出通用）：交给后端校验后走轮询链路执行。"""
    base = CFG["backend"]["base_url"].rstrip("/")
    if not base:
        raise HTTPException(503, "未配置后端，无法校验标签")
    code = req.code.strip()
    if not code:
        raise HTTPException(400, "请输入标签编码")
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(f"{base}/api/v1/kiosk/unlock", json={"code": code},
                                  headers={"X-Pi-Secret": CFG["security"]["secret"]})
    except httpx.HTTPError:
        raise HTTPException(503, "当前离线：取物开锁需要联网，可长按实体应急按键开门")
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
    quantity: int = 1
    login_ticket: str = ""


@app.post("/api/register_item")
async def register_item(req: RegisterItemReq):
    """触控屏登记第 1 步：后端建待入库台账 + 打印标签（不开锁）。
    贴好标签扫码/输码后 /api/confirm_store 才真正入库并开锁。"""
    base = CFG["backend"]["base_url"].rstrip("/")
    if not base:
        raise HTTPException(503, "未配置后端，无法登记")
    payload = req.model_dump() | {"fridge_id": CFG["backend"]["fridge_id"]}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(f"{base}/api/v1/kiosk/items", json=payload,
                                  headers={"X-Pi-Secret": CFG["security"]["secret"]})
    except httpx.HTTPError:
        raise HTTPException(503, "网络中断，登记未完成：请重试，或返回主页改走离线存入")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or f"HTTP {r.status_code}"
        except Exception:
            detail = f"HTTP {r.status_code}"
        raise HTTPException(r.status_code if r.status_code < 500 else 502, detail)
    d = r.json()
    d.setdefault("name", req.name)
    if d.get("print") not in ("queued", "printed") and d.get("code"):
        # 后端在云端够不到派本机 /api/print -> 派自己把标签打出来
        try:
            out = printer.print_label(d["code"], req.name,
                                      owner_name=d.get("owner_name", ""),
                                      room=d.get("room", ""),
                                      batch_no=str(d.get("batch_no") or ""),
                                      keep_until=d.get("keep_until", ""))
            log_event("print_label", d["code"], out.name)
            d["print"] = "printed" if (printer.dev or printer.cups) else "rendered_only"
        except Exception as e:
            log_event("print_fail", d.get("code", ""), str(e))
            d["print"] = "failed"
    if d.get("code"):
        remember_awaiting(d["code"], name=req.name, batch_no=d.get("batch_no"),
                          keep_until=d.get("keep_until", ""), offline=False)
    return d


class ConfirmStoreReq(BaseModel):
    code: str


@app.post("/api/confirm_store")
async def confirm_store(req: ConfirmStoreReq):
    """登记第 2 步：扫标签二维码 / 输 8 位编码 -> 正式入库并开锁。"""
    code = req.code.strip().upper()
    if not code:
        raise HTTPException(400, "请扫描或输入标签上的 8 位编码")
    # 断网登记：不依赖后端，本机直接开锁，联网后补录进待认领区
    off = next((i for i in load_offline_items()
                if i.get("code") == code and not i.get("synced")
                and not i.get("confirmed", True)), None)
    if off:
        was_locked = lock.is_locked
        lock.unlock()
        mark_unlocked(code)
        log_event("offline_unlock", code, "first" if was_locked else "again")
        items = load_offline_items()
        for i in items:
            if i.get("code") == code and not i.get("synced"):
                i["confirmed"] = True
                i["confirmed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_offline_items(items)
        AWAITING.pop(code, None)
        log_event("offline_confirm", code, f"{off.get('name', '')} 扫码确认入库")
        return {"ok": True, "offline": True, "code": code,
                "name": off.get("name", ""), "status": "pending_claim",
                "batch_no": off.get("batch_no"), "keep_until": off.get("keep_until", "")}
    base = CFG["backend"]["base_url"].rstrip("/")
    if not base:
        raise HTTPException(503, "未配置后端，无法确认入库")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(f"{base}/api/v1/kiosk/items/{code}/confirm-store",
                                  json={}, headers={"X-Pi-Secret": CFG["security"]["secret"]})
    except httpx.HTTPError:
        raise HTTPException(503, "网络中断：确认入库需要联网，请重试")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or f"HTTP {r.status_code}"
        except Exception:
            detail = f"HTTP {r.status_code}"
        raise HTTPException(r.status_code if r.status_code < 500 else 502, detail)
    d = r.json()
    AWAITING.pop(code, None)
    log_event("confirm_store", code, f"{d.get('name', '')} 扫码入库")
    return d


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
    d = r.json()
    return d


# ---------- 满柜上锁 / 排队 / 呼叫宿管（转发后端；断网只读回退） ----------
def _pi_headers() -> dict:
    return {"X-Pi-Secret": CFG["security"]["secret"]}


@app.get("/api/fridge_state")
async def fridge_state():
    """触控屏：冰箱是否上锁、默认存放天数、当天批次编号。"""
    base = CFG["backend"]["base_url"].rstrip("/")
    fallback = {"fridge_id": CFG["backend"]["fridge_id"], "locked": False,
                "storage_days": OFFLINE_STORAGE_DAYS, "batch_no": batch_no(date.today()),
                "today": date.today().isoformat(), "offline": True}
    if not base:
        return fallback
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base}/api/v1/kiosk/state",
                                 params={"fridge_id": CFG["backend"]["fridge_id"]},
                                 headers=_pi_headers())
        if r.status_code != 200:
            return fallback
        d = r.json()
        d["offline"] = False
        return d
    except httpx.HTTPError:
        return fallback


class QueueJoinReq(BaseModel):
    login_ticket: str = ""
    name: str
    category: str = "其他"
    quantity: int = 1


@app.post("/api/queue_join")
async def queue_join(req: QueueJoinReq):
    """满柜排队：只登记名字/类别，不打印标签、不建台账、不开锁。"""
    base = _backend_url()
    payload = req.model_dump() | {"fridge_id": CFG["backend"]["fridge_id"]}
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(f"{base}/api/v1/kiosk/queue", json=payload,
                                  headers=_pi_headers())
    except httpx.HTTPError:
        raise HTTPException(503, "排队登记需要联网，请稍后再试")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or f"HTTP {r.status_code}"
        except Exception:
            detail = f"HTTP {r.status_code}"
        raise HTTPException(r.status_code if r.status_code < 500 else 502, detail)
    d = r.json()
    log_event("queue_join", "", f"{req.name} 第{d.get('position')}位")
    return d


@app.get("/api/my_queue")
async def my_queue(login_ticket: str):
    base = CFG["backend"]["base_url"].rstrip("/")
    if not base or not login_ticket:
        return {"queue": None, "locked": False, "offline": True}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base}/api/v1/kiosk/my-queue",
                                 params={"login_ticket": login_ticket,
                                         "fridge_id": CFG["backend"]["fridge_id"]})
        if r.status_code != 200:
            return {"queue": None, "locked": False, "offline": True}
        return r.json()
    except httpx.HTTPError:
        return {"queue": None, "locked": False, "offline": True}


class QueueCancelReq(BaseModel):
    login_ticket: str = ""


@app.post("/api/queue_cancel")
async def queue_cancel(req: QueueCancelReq):
    base = _backend_url()
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(f"{base}/api/v1/kiosk/queue/cancel", json=req.model_dump(),
                                  headers=_pi_headers())
    except httpx.HTTPError:
        raise HTTPException(503, "取消排队需要联网，请稍后再试")
    if r.status_code != 200:
        raise HTTPException(502, "取消失败，请稍后再试")
    return r.json()


class CallReq(BaseModel):
    kind: str            # unlock | full | supply
    note: str = ""


@app.post("/api/call")
async def call_manager(req: CallReq):
    """匿名呼叫宿管：只上报冰箱与种类，不带任何身份信息。"""
    if req.kind not in ("unlock", "full", "supply"):
        raise HTTPException(400, "未知呼叫类型")
    base = _backend_url()
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(f"{base}/api/v1/kiosk/call",
                                  json={"fridge_id": CFG["backend"]["fridge_id"],
                                        "kind": req.kind, "note": req.note},
                                  headers=_pi_headers())
    except httpx.HTTPError:
        raise HTTPException(503, "呼叫宿管需要联网，请稍后再试")
    if r.status_code != 200:
        raise HTTPException(502, "呼叫失败，请稍后再试")
    log_event("call_manager", "", req.kind)
    return r.json()


@app.get("/api/last_unlock")
def read_last_unlock():
    return last_unlock


@app.get("/api/door_status")
def read_door_status():
    """触控屏用：是否有反馈线、当前是否上锁、最近一次开锁时刻、是否已完成一次开合门。"""
    return {"has_feedback": lock.has_feedback, "is_locked": lock.is_locked,
            "unlocked_at": door_watch["unlocked_at"], "cycle_done": door_watch["cycle_done"],
            "saw_open": door_watch["saw_open"]}


if __name__ == "__main__":
    uvicorn.run(app, host=CFG["server"]["host"], port=CFG["server"]["port"])
