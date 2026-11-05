# ESP32 锁控板固件（BLE 外设）

把继电器、门锁、实体按钮从树莓派上分出来：ESP32 做 BLE 外设，派做中心。
派 ↔ 云托管的链路完全不变，只是"谁去拉继电器脚"从 GPIO17 换成了 ESP32 的 GPIO26。

单条命令开锁延迟 <200ms（已连接时）。锁控板掉线时派返回 503「锁控板离线」，
kiosk 会提示改用 8 位编码手动输入；板子断连期间实体按钮仍可在本地开锁（应急逃生），
事件用 `btn_seq` 计数在重连后补报。

## 接线（共地、共电源，锁的电流回路不经过本板）

| 接到 | ESP32 | 说明 |
|---|---|---|
| 继电器模块 IN | **GPIO26** | 高电平吸合，空闲输出低 = 待机断电（fail-secure） |
| 门锁干接点（蓝/白任一） | **GPIO32** | 软件内部上拉；短路=已上锁=低电平。另一根接 GND，**严禁接电源** |
| 实体开锁按钮 | **GPIO33** | 一端 GPIO33、一端 GND，内部上拉，无需外部电阻 |
| 继电器 VCC / COM | 独立 5V **+** | |
| 继电器 GND / 锁黑线 / ESP32 GND | 独立 5V **−** | **共地不可省** |
| 锁红线 | 继电器 **NO** | 锁回路：电源+ → COM → NO → 锁 → 电源−，全程不上 ESP32 板 |
| ESP32 供电 | `5V` 脚（或烧录时用 USB） | **别接 `3V3`**，那是 LDO 输出；USB 与外部 5V 不要同时喂，会倒灌 |

电源建议 **5V/2A 以上**，并在电源输出端子和 ESP32 的 5V/GND 针脚上各并一颗
**470~1000µF** 电解电容——锁开锁瞬间是大电流，板载 AMS1117 需要 5V 轨保持在 ~4.4V 以上，
否则就是"发指令 → 板子欠压重启 → BLE 掉线"。串口打印 `brownout detector was triggered`
即为此症；固件会把复位原因（`esp_reset_reason()`，9=欠压）带在状态里，派上
`GET /api/lock_board_status` 直接看 `reset_reason_text`。

**避开这些脚**：GPIO0/2/5/15 是 strapping 脚（GPIO15 启动必须为高，被继电器拉低芯片起不来）；
GPIO34/35/36/39 只能输入且**没有内部上拉**，接干接点会乱飘。

## 密钥与配置

```bash
cp src/secrets.example.h src/secrets.h
vi src/secrets.h      # LOCK_SECRET 填派上 pi/config.yaml 的 security.secret（真实值不入库）
```
`LOCK_DEVICE` / `BLE_NAME` 要和派上 `pi/config.yaml` 的 `lock.ble.device_id` /
`lock.ble.advertise_prefix` 对应。

## 编译与烧录

```bash
pip install platformio            # 或 pipx install platformio
pio run                           # 编译
pio run -t upload --upload-port COM5     # 烧录（Windows 看设备管理器里的 COM 号）
pio device monitor --port COM5 --baud 115200
```

CH340 板子多半**不能自动进下载模式**：esptool 报 `Wrong boot mode detected (0x13)` 时，
按住 **BOOT** 不放 → 点一下 **EN** → 松开 BOOT，再执行烧录命令。

## 协议

GATT 服务 `6f1a1001-8d3b-4f0a-9a2e-5b7c9d0e1f01`

| 特征 | UUID 尾号 | 属性 | 内容 |
|---|---|---|---|
| STATE | `...1002` | 读 + 通知 | `S,<locked>,<open_seq>,<btn_seq>,<reset_reason>,<fw>` |
| CMD | `...1003` | 写 | `U,<nonce>,<sig>`，`sig = HMAC_SHA256(secret, "<device>|U|<nonce>")` 前 16 位 |

- 状态变化、按钮按下、每 10 秒心跳各通知一次；派超过 30 秒收不到通知就主动断开重连
- `open_seq` 是锁舌弹开次数，派用它判定"真的开过门"（BLE 通知有延迟，只靠电平会漏掉弹开瞬间）
- 板子不连 WiFi、没有时钟，所以防重放靠 **nonce 去重（保留最近 16 条）+ 命令最小间隔 1.5s**，不用时间戳
- 签名不对、nonce 重复、间隔过短 → 静默丢弃（不回报，避免给攻击者反馈）

## 派侧启用

```yaml
# pi/config.yaml
lock:
  mode: ble
  ble:
    advertise_prefix: "FRIDGE-LOCK"
    device_id: "lock-01"
```

```bash
pip install bleak          # 已在 pi/requirements.txt
sudo apt install -y bluez
# 派 3B 开机常把蓝牙 RF-Kill 软阻塞，需要放行（本仓库 scripts/bt-unblock.service）
sudo cp scripts/bt-unblock.sh /usr/local/sbin/ && sudo chmod 755 /usr/local/sbin/bt-unblock.sh
sudo cp scripts/bt-unblock.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now bt-unblock
sudo systemctl restart fridge-pi
curl -s http://127.0.0.1:8000/api/lock_board_status
```

`lock.mode` 改回 `gpio` 就完全恢复旧的"单派直控"行为，两条路线并存、随时可切。
