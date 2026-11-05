"""BLE 锁控板通道：树莓派做中心，连 ESP32 的 GATT 服务，收状态通知、发开锁命令。

接口对齐 ElectromagneticLock（is_locked / has_feedback / unlock / lock），
额外提供 open_seq（锁舌弹开次数），供 watch_door 做"真的开过门"判定。

协议：
  STATE(读+通知) 文本  S,<locked>,<open_seq>,<btn_seq>,<reset_reason>,<fw>
  CMD  (写)     17 字节 0x55 | nonce[8] | sig[8]
               sig = HMAC_SHA256(secret, "<device>|U|<nonce_hex>") 的前 8 字节
板子不连 WiFi 没有时钟，所以防重放靠 nonce 去重 + 命令最小间隔，不用时间戳。
命令定长 17 字节，正好落在 BLE 默认 MTU(23) 的单包里，不用处理分片。
"""
import asyncio
import hashlib
import hmac
import os
import time

CHR_STATE = "6f1a1002-8d3b-4f0a-9a2e-5b7c9d0e1f01"
CHR_CMD = "6f1a1003-8d3b-4f0a-9a2e-5b7c9d0e1f01"
CMD_OP_UNLOCK = b"\x55"

# esp_reset_reason() 枚举，只把常见的几个翻成人话
RESET_REASONS = {1: "上电", 2: "外部复位", 3: "软件重启", 4: "panic",
                 5: "中断看门狗", 6: "任务看门狗", 7: "看门狗", 8: "深睡", 9: "欠压(brownout)"}


class BleLock:
    def __init__(self, cfg: dict, secret: str, on_button=None):
        self.prefix = cfg.get("advertise_prefix", "FRIDGE-LOCK")
        self.device_id = cfg.get("device_id", "lock-01")
        self.scan_timeout = cfg.get("scan_timeout", 8)
        self.secret = secret.encode()
        self.on_button = on_button
        self.mock = False
        self.has_feedback = True          # 反馈触点由板子读，派这边只认状态
        self._locked = True
        self._open_seq = 0
        self._btn_seq = 0
        self._reset_reason = 0
        self.fw = ""
        self.connected = False
        self.last_rx = 0.0
        self.last_err = ""
        self.reconnects = 0
        self._addr = ""
        self._cmd: asyncio.Queue = asyncio.Queue()

    # ---------- 与 ElectromagneticLock 对齐的接口 ----------
    @property
    def is_locked(self) -> bool:
        return self._locked

    @property
    def open_seq(self) -> int:
        return self._open_seq

    def unlock(self):
        if not self.connected:
            raise RuntimeError("锁控板离线（BLE 未连接）")
        nonce = os.urandom(8)
        sig = hmac.new(self.secret, f"{self.device_id}|U|{nonce.hex()}".encode(),
                       hashlib.sha256).digest()[:8]
        self._cmd.put_nowait(CMD_OP_UNLOCK + nonce + sig)

    def lock(self):
        """关门压回锁舌自动上锁，板子没有主动落锁动作；保留接口兼容。"""

    def status(self) -> dict:
        return {"mode": "ble", "connected": self.connected, "address": self._addr,
                "fw": self.fw, "open_seq": self._open_seq, "btn_seq": self._btn_seq,
                "reset_reason": self._reset_reason,
                "reset_reason_text": RESET_REASONS.get(self._reset_reason, str(self._reset_reason)),
                "last_rx_age": round(time.time() - self.last_rx, 1) if self.last_rx else None,
                "reconnects": self.reconnects, "last_err": self.last_err}

    # ---------- 内部 ----------
    def _apply(self, raw) -> None:
        text = bytes(raw).decode("utf-8", "replace").strip()
        if not text.startswith("S,"):
            return
        parts = text.split(",")
        if len(parts) < 6:
            return
        try:
            locked = parts[1] == "1"
            open_seq = int(parts[2])
            btn_seq = int(parts[3])
            reason = int(parts[4])
        except ValueError:
            return
        self._locked = locked
        self._open_seq = open_seq
        self._reset_reason = reason
        self.fw = parts[5]
        self.last_rx = time.time()
        new_btn = btn_seq - self._btn_seq
        self._btn_seq = btn_seq
        if new_btn > 0 and self.on_button:
            asyncio.create_task(self._fire_button(new_btn))

    async def _fire_button(self, count: int):
        try:
            await self.on_button()
        except Exception as e:
            print(f"[ble] 按钮回调异常: {e}")

    async def _find(self):
        from bleak import BleakScanner
        for d in await BleakScanner.discover(timeout=self.scan_timeout):
            if d.name and d.name.startswith(self.prefix):
                self._addr = d.address
                return d.address
        return ""

    async def run(self):
        from bleak import BleakClient
        while True:
            try:
                if not self._addr:
                    self._addr = await self._find()
                    if not self._addr:
                        self.last_err = f"没扫到以 {self.prefix} 开头的设备"
                        await asyncio.sleep(3)
                        continue
                def _gone(_client):
                    self.connected = False
                async with BleakClient(self._addr, timeout=15,
                                       disconnected_callback=_gone) as client:
                    self.connected = True
                    self.last_err = ""
                    self.reconnects += 1
                    print(f"[ble] 锁控板已连接 {self._addr}")
                    await client.start_notify(CHR_STATE, lambda _s, data: self._apply(data))
                    self._apply(await client.read_gatt_char(CHR_STATE))
                    while client.is_connected:
                        try:
                            payload = await asyncio.wait_for(self._cmd.get(), timeout=1.0)
                            await client.write_gatt_char(CHR_CMD, payload, response=True)
                        except asyncio.TimeoutError:
                            pass
                        if self.last_rx and time.time() - self.last_rx > 30:
                            self.last_err = "30 秒没收到状态通知，主动重连"
                            break
            except Exception as e:
                self.last_err = f"{type(e).__name__}: {e}"
                print(f"[ble] 锁控板链路异常: {self.last_err}")
                self._addr = ""
            finally:
                self.connected = False
            await asyncio.sleep(2)
