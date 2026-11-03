# 树莓派端（锁控服务）说明

树莓派（派 3B / 派 5 均实机跑过，Debian 13）运行 `app.py`，负责四件事：

1. **电控锁控制**：校验一次性令牌后发脉冲开锁（待机断电），蓝/白干接点反馈锁舌状态，关门压回锁舌自动重新上锁；`/api/door_status` 提供一次真实开合门事件
2. **摄像头**：rpicam-vid MJPEG 实时预览 + 抓帧解码标签二维码（取出/贴标确认时扫码；不做拍照分类）
3. **触控屏**：`/kiosk` 扫码登录 → 录入名称/分类 → 打印标签 → 贴标扫码/输 8 位编码确认 → 开锁；取出、满柜排队、匿名呼叫宿管
4. **后端轮询**：主动向小程序后端拉取解锁指令（冰箱在内网，无需开放入站端口）

## 摄像头与触控屏

- 摄像头走 **CSI + libcamera**（`rpicam-still` 静拍 / `rpicam-vid` 预览，代码里自动探测，没有则 mock）：
  - 现装官方 Camera Module v2（OV5647，**定焦，最近合焦约 25~30cm**，贴太近必糊——是硬件限制不是故障，指导用户保持 25~40cm）
  - 换 IMX708 类自动对焦模组（对焦 15cm~∞、1200 万像素）：`/boot/firmware/config.txt` 里把 overlay 改成 `dtoverlay=imx708` 后重启，`rpicam-hello --list-cameras` 能列出模式才算识别成功；静帧分辨率建议用 16:9（4608×2592 被裁成 4:3 会浪费像素）
  - USB(UVC) 摄像头走 V4L2，`rpicam-*` 取不到图，需要另写 `cv2.VideoCapture(0, CAP_V4L2)` 取帧
- 扫码解码：**zxing-cpp 优先**，依次跑原图 / 灰度 / 中心区 2x / Otsu 二值化 / 锐化五个变体，OpenCV `QRCodeDetector` 兜底（Debian 13 已无 `libzbar0`，OpenCV 5 也不带微信检测器）。派 3B 实测：命中约 0.23s，画面里完全没码跑完全部变体约 2.0s；合成退化图 32 例 20→25
- 触控屏：官方 Touch Display 2（DSI2）或 HDMI 屏。**没有触摸屏时用鼠标 + HDMI 屏也能跑**，此时不必装 wvkbd 屏幕键盘
- kiosk 自启写 `~/.config/labwc/autostart`（labwc 是 Wayland 合成器，Chromium 和 VNC 都挂在它上面，不能整个去掉）：

```bash
chromium --ozone-platform=wayland --enable-wayland-ime --wayland-text-input-version=3 \
  --user-data-dir=$HOME/kiosk-profile --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --disable-features=Translate,TranslateUI \
  --password-store=basic --kiosk http://127.0.0.1:8000/kiosk &
```

两个必踩坑：① 不加 `--password-store=basic` 会被 gnome-keyring「新建钥匙环密码」弹窗挡住页面；
② 命令行禁用翻译不够，还要写企业策略 `/etc/chromium/policies/managed/kiosk.json`
（`TranslateEnabled:false` + `VideoCaptureAllowedUrls:["http://127.0.0.1:8000"]` 预授权摄像头，免弹权限框）。
自动登录由 `/etc/lightdm/lightdm.conf` 的 `autologin-user` + `autologin-session=rpd-labwc` 负责。

## 存入流程（两步）

1. 屏幕登录（小程序「扫屏幕登录码」）→ 选名称/分类 → 点「确认存入（打印标签）」
2. 后端建 `awaiting_scan` 台账并打印标签；屏幕显示 8 位编码、全字符键盘与摄像头
3. 标签贴到物品上 → 对准摄像头扫码或点键盘输入编码 → 「确认入库并开锁」
4. 后端转 active（学生）或 pending_claim（宿管/未认领）并下发开锁指令；**5 分钟内未确认自动作废**

标签版式 60×40mm @203dpi：左栏 8 位编码在上、二维码在下；右栏**到期日的"号"（大号，不带月份）**、物品名称、姓名-寝室、存放至。
大字用号数是让宿管按号清理（存放至 2026-11-08 → 印 8）；拿不到存放至时退回批次编号。
批次本身仍 16 天一轮：`(日期-2026-01-01).days % 16`，前 8 天为 1~8，后 8 天为 11~18（后端按 stored_at 重算，不信任派传来的值）。

## 接线（断电操作）

现装是 **5V 瞬间通电锁 + 独立供电**（12V 断电上锁型已弃用）：

```
独立 5V 电源(任意 5V/1A+ 旧充电头) + ──► 继电器 COM
独立 5V 电源 −  ──► 锁黑线，并且这一极必须与树莓派 GND 共地（不可省）
锁红线 ──► 继电器 NO
树莓派 GPIO17(物理11脚) ──► 继电器 IN      继电器 VCC ──► 派 5V(物理2/4)  GND ──► 派 GND(物理6)
门锁蓝/白干接点 ──► 一根 GPIO26(物理37)、一根派 GND     （无电触点，切勿接电源）
```

- 红黑=控制：**待机必须断电**，只在 `pulse_seconds`（默认 1s）脉冲期间通电弹开锁扣
- 蓝白=状态反馈：短路=已上锁；软件内部上拉。没接反馈线时把 `lock.feedback_gpio` 注释掉，否则触点悬空会把状态恒判为"未锁"
- 关门压回锁舌即自动重新上锁，所以**没有"60 秒自动落锁"这回事**（`auto_relock_seconds` 仅在无反馈线时作状态估算）
- 锁和标签机都不要挂在派的 5V 轨上：派 3B/5 都会因此欠压（实测派 5 用 PD 充电宝 5V/3A 档开锁瞬间黑屏；派 3B 接锁后 dmesg 反复报 Undervoltage）
- 排查"LED 闪但不咔哒"：输入侧 LED 由信号驱动，**线圈没通电也会闪**。先量继电器 VCC 是不是真的 5V（物理 2/4，不是物理 1 的 3.3V）；再看脉冲期间 `vcgencmd get_throttled` 有无新增欠压——完全没有电流痕迹就说明线圈根本没吸合

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
| `lock.pulse_seconds` / `lock.feedback_gpio` | 开锁脉冲秒数(默认 1，锁说明书 0.5~35) / 状态反馈 GPIO(26，未接线就删掉这行)；`auto_relock_seconds` 仅无反馈线时估算用 |
| `lock.mock` | Windows/无 GPIO 环境调试设 `true` |
| `security.secret` | 与后端共享的 HMAC 密钥，**必须修改** |
| `backend.base_url` | 小程序后端地址；留空=只开本地 API |
| `backend.fridge_id` | 本冰箱编号，如 `fridge-01` |

## 在树莓派上部署

派上 `~/fridge_pi` 不是 git 仓库，代码用 SSH/SFTP 直传（`pi/` 整个目录，排除 `data/`、`models/`、`__pycache__`）。

```bash
# 1) 系统包：GPIO 走 apt 的 gpiozero，中文靠 wqy（浏览器渲染 + 标签字体同一个）
sudo apt install -y python3-gpiozero python3-venv fonts-wqy-microhei fonts-wqy-zenhei rpicam-apps
# 标签机直写 /dev/usb/lp0 需要 lp 组（加完必须重启服务才生效）
sudo usermod -aG lp chen

# 2) venv 复用系统包，只装缺的；派上从 PyPI 官方源会卡死，换清华源
python3 -m venv --system-site-packages ~/fridge_pi/.venv
~/fridge_pi/.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r pi/requirements.txt
#   派 3B 内存 905MB，onnxruntime 可以不装（models/ 空目录时分类自动停用）

# 3) 服务（ExecStart 要指向 venv python 和 chen 的家目录）
sudo cp pi/fridge-pi.service /etc/systemd/system/fridge-pi.service
sudo systemctl daemon-reload && sudo systemctl enable --now fridge-pi
journalctl -u fridge-pi -f          # 看日志

# 4) 云端 MySQL 每日冷备到 SD 卡（留 14 份）
chmod +x ~/fridge_pi/scripts/cloud_backup.sh
(crontab -l 2>/dev/null | grep -v cloud_backup.sh; echo "17 4 * * * \$HOME/fridge_pi/scripts/cloud_backup.sh") | crontab -
```

`config.yaml` 里 `security.secret` 必须与云托管环境变量 `PI_SECRET` 一致（真实密钥只写在派上和云上，仓库里是占位）。
开机自启、崩溃自动拉起；开锁/落锁/扫码/打印等事件留痕在 `data/events.jsonl`。
验收顺序：`/healthz` → `/api/status` → 云端 `pending-unlocks`/`heartbeat` 返回 200 → `/api/print` 出签 → 开锁脉冲看 `door_status` 的 `saw_open`。

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
