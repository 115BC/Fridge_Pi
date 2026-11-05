"""一次性 BLE 探测：扫描->连接->读 STATE->订阅通知->发签名开锁命令->再读 STATE。"""
import asyncio
import hashlib
import hmac
import os
import sys

import yaml
from bleak import BleakClient, BleakScanner

sys.path.insert(0, os.path.expanduser("~/fridge_pi/pi"))
from lock_ble import CHR_CMD, CHR_STATE, CMD_OP_UNLOCK  # noqa: E402

CFG = yaml.safe_load(open(os.path.expanduser("~/fridge_pi/pi/config.yaml"), encoding="utf-8"))
SECRET = CFG["security"]["secret"].encode()
DEVICE = CFG["lock"].get("ble", {}).get("device_id", "lock-01")
PREFIX = CFG["lock"].get("ble", {}).get("advertise_prefix", "FRIDGE-LOCK")


async def main():
    print("扫描中…")
    target = None
    for d in await BleakScanner.discover(timeout=12):
        if d.name and d.name.startswith(PREFIX):
            target = d
            print("发现 %s (%s)" % (d.name, d.address))
            break
    if not target:
        print("没扫到以 %s 开头的设备" % PREFIX)
        return

    seen = []

    async with BleakClient(target.address, timeout=20) as client:
        await client.start_notify(CHR_STATE, lambda _s, data: seen.append(bytes(data).decode()))
        print("初始 STATE: %s" % (await client.read_gatt_char(CHR_STATE)).decode())

        nonce = os.urandom(8)
        sig = hmac.new(SECRET, f"{DEVICE}|U|{nonce.hex()}".encode(), hashlib.sha256).digest()[:8]
        await client.write_gatt_char(CHR_CMD, CMD_OP_UNLOCK + nonce + sig, response=True)
        print("已写入开锁命令（签名 %s…）" % sig.hex()[:8])

        # 再发一条同样 nonce 的，应被板子当重放丢掉（现象上无区别，仅确认不炸）
        await client.write_gatt_char(CHR_CMD, CMD_OP_UNLOCK + nonce + sig, response=True)
        print("重放同一条命令已发送（板子应静默丢弃）")

        await asyncio.sleep(3)
        print("通知收到 %d 条: %s" % (len(seen), seen[-3:]))
        print("结束 STATE: %s" % (await client.read_gatt_char(CHR_STATE)).decode())


asyncio.run(main())
