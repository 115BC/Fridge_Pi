# 树莓派端（锁控服务）说明

树莓派 5 (8GB) 运行 `app.py`，负责四件事：

1. **电控锁控制**：校验一次性令牌后发脉冲开锁（待机断电），蓝/白干接点反馈锁舌状态，关门压回锁舌自动重新上锁；`/api/door_status` 提供一次真实开合门事件
2. **摄像头**：rpicam-vid MJPEG 实时预览 + 抓帧解码标签二维码（取出/贴标确认时扫码；不做拍照分类）
3. **触控屏**：`/kiosk` 扫码登录 → 录入名称/分类 → 打印标签 → 贴标扫码/输 8 位编码确认 → 开锁；取出、满柜排队、匿名呼叫宿管
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

## 存入流程（两步）

1. 屏幕登录（小程序「扫屏幕登录码」）→ 选名称/分类 → 点「确认存入（打印标签）」
2. 后端建 `awaiting_scan` 台账并打印标签；屏幕显示 8 位编码、全字符键盘与摄像头
3. 标签贴到物品上 → 对准摄像头扫码或点键盘输入编码 → 「确认入库并开锁」
4. 后端转 active（学生）或 pending_claim（宿管/未认领）并下发开锁指令；**5 分钟内未确认自动作废**

标签版式 60×40mm @203dpi：左栏 8 位编码在上、二维码在下；右栏批次编号（大号）、物品名称、姓名-寝室、存放至。
批次 16 天一轮：`(日期-2026-01-01).days % 16`，前 8 天为 1~8，后 8 天为 11~18。

## 接线（断电操作）

```
树莓派 GPIO17(物理11脚) ──► MOSFET/继电器模块 IN
树莓派 GND(物理 6 脚)  ──► 模块 GND ──► 12V 电源负极   （三方共地，必须！）
12V 电源(≥1A) 正极 ──► 电磁锁 +端，串 1N4007 续流二极管(环向+极)
电磁锁 -端 ──► 模块 继电器/DRAIN 端 ──► 12V 负极
```

- 现装锁为**瞬间通电开锁型**（2026-10 换装）：红黑=1s 脉冲控制（切勿常通电），蓝白=无电干接点反馈（短路=已上锁，一根接 GPIO26 一根接 GND，严禁接电源）；关门压回锁舌即上锁
- 不要用树莓派 5V 给锁供电（Pi 5 对外供电有限，锁瞬时电流大，会重启）
- 派 5 必须用 5V/5A 级电源（锁挂同一 5V 轨；PD 充电宝 5V/3A 档实测开锁欠压黑屏）

## 实体开锁按键（可选）

轻触按键一端接 **GPIO27（物理 13 脚）**，另一端接 **GND（物理 6/9/14/20/25 任一）**，
无需外部电阻（软件启用内部上拉）。按下 = 开锁，与扫码开锁同一流程：
留痕 `button_unlock`；`auto_relock_seconds` 仅在未接反馈线时作状态估算。
在 `config.yaml` 删除 `lock.button_gpio` 行即可停用。
注意：按键是无条件开锁，请装在只有宿管可及的位置（或仅作箱内应急逃生按钮）。

## 配置

编辑 `config.yaml`：

| 键 | 说明 |
|----|------|
| `lock.gpio_pin` | BCM 引脚号，默认 17 |
| `lock.pulse_seconds` / `lock.feedback_gpio` | 开锁脉冲秒数(0.6) / 状态反馈 GPIO(26)；`auto_relock_seconds` 仅无反馈线时估算用 |
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
| POST | `/api/lock` | 手动切断开锁脉冲（宿管） |
| GET | `/api/door_status` | 锁状态/反馈线/一次开合门事件（触控屏确认页与门未关横幅用） |
| GET | `/api/net_status` | 后端是否可达 + 本地待同步的离线登记条数（触控屏离线横幅用） |
| POST | `/api/register_item` | 存入第 1 步：后端建待入库台账 + 打印标签 |
| POST | `/api/confirm_store` | 存入第 2 步：扫/输标签编码 → 正式入库并开锁（离线登记则本机开锁） |
| POST | `/api/offline_item` | 断网存入第 1 步：本机生成编码+打印标签，记录写入 `data/offline_items.jsonl` |
| GET | `/api/fridge_state` | 代理后端：冰箱是否上锁/默认存放天数/今日批次（断网回退默认值） |
| POST/GET/POST | `/api/queue_join` `/api/my_queue` `/api/queue_cancel` | 满柜排队（不打印/不建台账） |
| POST | `/api/call` | 匿名呼叫宿管（unlock/full/supply，60 秒内同类去重） |
| POST | `/api/capture` / `/api/capture_frame` | 静拍 / 抓视频帧 → 解码标签二维码 |
| GET | `/photos/{name}` | 查看抓拍照片 |
| GET | `/kiosk` | 触控屏（浏览器全屏打开） |

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
POST {base}/api/v1/pi/offline-items {"items":[{"code","name","category","keep_until","stored_at","batch_no","quantity","fridge_id"}]}
     -> {"created":[...],"skipped":[...],"invalid":[...]}   # 断网登记补录进待认领区，按标签编码幂等
```

解锁令牌：`token = HMAC_SHA256(pi_secret, "{code}|{ts}")`，由后端在登记时签发，Pi 用同一密钥校验。

## 断网离线登记

轮询连续失败超过 10 秒判定离线（`/api/net_status`）。离线时触控屏首页显示黄色横幅，
存入免扫码登录：本机生成 8 位编码 → 打印标签 → **贴标扫码/输码确认后本机开锁**，
记录写入 `data/offline_items.jsonl`（`synced:false`，确认后 `confirmed:true`）。
恢复联网后随轮询自动调用 `/api/v1/pi/offline-items` 补录（仅补 `confirmed` 条目，
保留批次/存放至），后端建为**待认领**物品，学生可在小程序扫码认领。
离线期间取物开锁仍需联网，可用箱体实体应急按键。

## 在 Windows 上开发调试

`config.yaml` 里 `mock: true`，然后 `python app.py`，
用 `make_token.py` 生成命令模拟小程序后端调用即可，无需硬件。
