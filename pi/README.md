# 树莓派端（锁控服务）说明

树莓派 5 (8GB) 运行 `app.py`，负责四件事：

1. **电磁锁控制**：校验一次性令牌后开锁，超时自动落锁（先登记、后存放）
2. **拍照识别**：官方 Camera Module（libcamera CLI）拍照 → 二维码解码标签 + MobileNetV3 本地分类（纯离线）
3. **触控屏确认页**：`/kiosk` 显示识别建议，学生确认或修正分类后保存入库记录
4. **后端轮询**：主动向小程序后端拉取解锁指令（冰箱在内网，无需开放入站端口）

## 摄像头与触控屏

- 摄像头：官方 Camera Module 3，CSI 排线接 Pi 5 的 CAMERA 接口，装好后运行
  `libcamera-hello` 确认出图；`sudo apt install rpicam-apps`（新版系统自带）
- 触控屏：**官方 Raspberry Pi Touch Display 2**（7 寸，DSI2 排线直连，USB-C 可反向给 Pi 5 供电）
  替代：微雪 7 寸 DSI LCD (H) / 5 寸 HDMI+USB 款。屏幕装在冰箱**箱体外部**，加亚克力面板防潮防结露
- 触控屏开机全屏打开确认页（写入 `/etc/xdg/autostart/kiosk.desktop` 或 labwc autostart）：

```bash
chromium-browser --kiosk --noerrdialogs --disable-session-crashed-bubble http://localhost:8000/kiosk
```

## 物品分类模型（一次性准备）

```bash
python scripts/download_model.py    # 下载 MobileNetV3 ONNX (~21MB) + 类别表到 models/
```

无模型时分类自动停用（建议一律为"其他"），不影响锁控/登记功能。
分类只作**建议**，最终分类以触控屏上的人工确认为准；修正记录留在
`data/items.jsonl`（guess_category vs category），日后可用于微调专用模型。

## 接线（断电操作）

```
树莓派 GPIO17(物理11脚) ──► MOSFET/继电器模块 IN
树莓派 GND(物理 6 脚)  ──► 模块 GND ──► 12V 电源负极   （三方共地，必须！）
12V 电源(≥1A) 正极 ──► 电磁锁 +端，串 1N4007 续流二极管(环向+极)
电磁锁 -端 ──► 模块 继电器/DRAIN 端 ──► 12V 负极
```

- 电磁锁选**断电上锁型 (fail-secure)**：GPIO 高电平=通电开锁，掉电自动落锁，安全
- 不要用树莓派 5V 给锁供电（Pi 5 对外供电有限，锁瞬时电流大，会重启）
- 若用自保持锁（脉冲开锁/落锁），改 `lock.py` 为短脉冲输出即可

## 实体开锁按键（可选）

轻触按键一端接 **GPIO27（物理 13 脚）**，另一端接 **GND（物理 6/9/14/20/25 任一）**，
无需外部电阻（软件启用内部上拉）。按下 = 开锁，与扫码开锁同一流程：
留痕 `button_unlock` + `auto_relock_seconds` 超时自动落锁。
在 `config.yaml` 删除 `lock.button_gpio` 行即可停用。
注意：按键是无条件开锁，请装在只有宿管可及的位置（或仅作箱内应急逃生按钮）。

## 配置

编辑 `config.yaml`：

| 键 | 说明 |
|----|------|
| `lock.gpio_pin` | BCM 引脚号，默认 17 |
| `lock.auto_relock_seconds` | 开锁后自动落锁秒数，默认 60 |
| `lock.mock` | Windows/无 GPIO 环境调试设 `true` |
| `security.secret` | 与后端共享的 HMAC 密钥，**必须修改** |
| `backend.base_url` | 小程序后端地址；留空=只开本地 API |
| `backend.fridge_id` | 本冰箱编号，如 `fridge-01` |

## 在树莓派上部署

```bash
sudo apt install -y python3-gpiozero python3-yaml python3-httpx python3-fastapi
# 或: pip install -r requirements.txt
mkdir -p ~/fridge_pi && # 上传本目录
sudo cp fridge-pi.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fridge-pi
journalctl -u fridge-pi -f          # 看日志
```

开机自启、崩溃自动拉起；解锁/落锁/超时事件记录在 `data/events.jsonl`。

## 本地 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 存活检查 |
| GET | `/api/status` | 当前锁状态 |
| POST | `/api/unlock` | `{code, ts, token}` 校验令牌并开锁 |
| POST | `/api/lock` | 手动落锁（宿管） |
| POST | `/api/capture` | 拍照 → 二维码解码 + 分类建议 |
| POST | `/api/items` | 触控屏确认后保存入库记录 |
| GET | `/photos/{name}` | 查看拍摄照片 |
| GET | `/kiosk` | 触控屏确认页（浏览器全屏打开） |

令牌算法：`token = HMAC_SHA256(secret, "{code}|{ts}")`，`ts` 为 Unix 秒，
超过 `max_token_age_seconds`（默认 120s）拒绝。后端登记成功后即时下发。

### 手工联调（后端没就绪前先这样演示）

```bash
python make_token.py <secret> R2-0417    # 生成一条 curl 命令，直接执行
curl http://<派IP>:8000/api/status       # is_locked 变化即为成功
```

### 与小程序后端的对接协议（server/ 已实现）

所有请求带头 `X-Pi-Secret: <security.secret>`，该值必须与 `server/config.yaml` 的 `security.pi_secret` 一致。

```
GET  {base}/api/v1/pi/pending-unlocks?fridge_id=fridge-01
     -> {"commands":[{"code","ts","token"}]}       # 拉取后置为 dispatched，不重复下发
POST {base}/api/v1/pi/unlock-result {"code","status","reason?"}   # 回执 unlocked/rejected
POST {base}/api/v1/pi/heartbeat {"fridge_id","is_locked","mock","version"}  # 每 heartbeat_seconds 上报
```

解锁令牌：`token = HMAC_SHA256(pi_secret, "{code}|{ts}")`，由后端在登记时签发，Pi 用同一密钥校验。

## 在 Windows 上开发调试

`config.yaml` 里 `mock: true`，然后 `python app.py`，
用 `make_token.py` 生成命令模拟小程序后端调用即可，无需硬件。
