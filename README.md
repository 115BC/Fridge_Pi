# 宿舍冰箱智能管理系统（fridge_pi）

> 创客教育 × 校园总务合作项目：用真实可用的系统替代宿舍冰箱的纸质登记。
> 学生微信登录管理自己的物品，冰箱触控屏拍照登记存取，宿管拍照盘点、依规清理留痕。

三端架构（2026-10 起后端部署在微信云托管，每栋楼的冰箱只跑设备端）：

```
┌── 微信云托管（正式后端）──┐      ┌──── 树莓派 5（每栋楼一台，WiFi）────┐
│ server/ FastAPI + 内置MySQL │ ←HTTPS轮询(X-Pi-Secret)← pi/ 锁控+摄像头+触控屏 :8000
│  ↑ wx.cloud.callContainer   │      │  （只出不进，不开放任何入站端口）    │
└──────────┬──────────────────┘      └────────────────────────────────────┘
           └──────────── 微信小程序（学生 / 宿管 / 超管）────────────┘
```

## 核心业务流程

| 环节 | 方式 |
|---|---|
| 登录 | 微信登录（code2session），首次绑定**姓名+寝室号+楼宇（必选）**；未登录不可用任何功能 |
| 楼宇 | 楼=冰箱=一台派（`buildings` 表映射 fridge_id）；学生只属一楼，注册后只能自助改姓名/寝室，**改楼仅超管**；宿管可切换查看任意楼 |
| 存入 | 只能在冰箱触控屏：屏幕出登录码 → 小程序扫码确认身份 → 拍照识别分类 → 确认登记 → 打印 40×30mm 标签 + 电锁脉冲开锁，物品归属登录者（学生自助登记自动落到本楼冰箱） |
| 取出 | 触控屏扫标签/输 8 位编码；屏幕"我的物品"；学生手机出示取物码给屏幕摄像头；小程序一键开锁。**确认页按真实开合门判定**：检测到锁舌弹开又压回才自动归档"已取出"，120 秒未关回默认"继续存放" |
| 提醒 | 三色规则（红=超期/黄=临期/绿=正常），按楼一键批量推订阅消息 + 每日自动批次；超期 ≥3 天自动进入宿管「超期待处理」清单；学生页有"通知历史"兜底（不依赖订阅额度） |
| 盘点 | 宿管拍冰箱内部照 → 批量识别标签 → 命中/台账未见/无登记 三类；照片存 MySQL（发布不丢，保留 N 天后自动清理） |
| 治理 | 待认领区（宿管登记物品/清理物品）→ 学生扫码认领（一人一主）；清理留痕；在库额度（学生默认 5 件，宿管按人调）；到期日仅宿管/超管可改 |
| 台账导出 | 宿管页一键导出 CSV（含楼宇/主人/到期/状态/体积估算），转发到微信后用 Excel 打开 |
| 容量 | 按楼（冰箱）独立容量，类别先验×数量估算体积，宿管逐件修正 |

## 功能清单

**`pi/` 树莓派端（FastAPI :8000，WiFi 接入，只主动外连）**
- 电控锁：**瞬间通电开锁型**——继电器 0.6s 脉冲开锁（待机断电），蓝/白干接点**状态反馈**（GPIO26 上拉，短路=已上锁），关门压回锁舌自动重新上锁；实体应急按键（GPIO27）
- 门状态服务：`/api/door_status`（是否有反馈线/当前锁状态/一次真实开合门事件），触控屏据此做取出闭环与"门未关"全局红色告警横幅
- CSI 摄像头拍照 + rpicam-vid MJPEG 实时预览；QR 解码原图+2x 两遍扫描
- 本地离线分类：ONNX Runtime + MobileNetV3，仅作建议，人工确认修正
- 标签生成打印：Pillow 渲染 40×30mm（QR+编码+名称+到期日），CUPS 直打或落 PNG（打印机待到货）
- 触控屏 kiosk（labwc + Chromium，`Cache-Control: no-store` 防缓存旧页）：**开机即主页（存入/取出）**，无独立待机页；90s 无操作回主页+登出+关摄像头；屏幕中文输入用常用词快捷点选（虚拟键盘不走拼音输入法）
- 后端轮询：HMAC 一次性令牌开锁指令 + 心跳上报锁状态

**`server/` 小程序后端（FastAPI，微信云托管；本地/派上无 MYSQL_ADDRESS 时自动用 SQLite）**
- 三角色：学生 / 宿管（物品域：台账/提醒/盘点/容量/开锁/改学生姓名寝室）/ 超管（+账号/角色/改楼/楼宇管理/系统调试/超管联系方式）
- 楼宇管理：`buildings(name, fridge_id)`、学生 building 字段、按楼过滤台账/提醒/容量/CSV
- 物品状态机：active | pending_claim | taken_out | removed；escalated 超期未处理标记
- 订阅消息推送（模板字段 thing1/time2/number3/thing5；43101=无额度，学生端点击处自动续额度）
- 照片落库（photos 表 LONGBLOB，`/photos/{name}` 从库读）
- 数据运维：行级导出/导入迁移通道（`export-rows`/`import-rows`）+ 每日冷备通道（`backup-rows`，配 `scripts/cloud_backup.sh` 在派上 cron，SD 卡留 14 份）
- 回归脚本 `scripts/test_planb.py`：**58 项端到端断言**（含楼宇/照片落库/超期升级/CSV/备份通道）

**`miniprogram/` 微信小程序（体验版，走 `wx.cloud.callContainer` 免域名备案）**
- 登录页：两步式（微信登录 → 姓名+寝室+楼宇下拉绑定）
- 学生页：我的宿舍（改姓名/寝室、"楼错了联系超管"）、我的物品（取物码/取出开锁/改名/已取出）、扫屏幕登录码、扫码认领、通知历史
- 宿管页：楼宇切换筛选（记住上次）、物品管理（盘点/一键提醒/超期待处理/CSV 导出/开锁/改期/体积/待认领流转）、学生管理（跨楼查看、代改姓名寝室）
- 超管追加：账号管理（角色/额度/改学生楼宇）、楼宇管理（加删楼/超管联系方式）、系统调试（心跳/开锁指令/事件流水/提醒记录）

## 硬件清单

| 部件 | 型号/说明 | 接法 |
|---|---|---|
| 主机 | 树莓派 5 8GB，Debian 13，WiFi | **必须 5V/5A 级供电**（实测 5.1V/5A 头；PD 充电宝 5V/3A 档带锁必欠压黑屏） |
| 摄像头 | 官方 Camera Module v2（定焦，合焦 ~25cm） | CSI 翻盖座，银色触点朝外 |
| 触摸屏 | 官方 Touch Display 2 | DSI2（与 CSI 不冲突） |
| 电控锁 | **5V 瞬间通电型**：脉冲开锁+锁舌回弹上锁+干接点状态反馈 | 红/黑=控制（继电器 NO 串 5V，**待机断电**）；蓝/白=反馈（一根 GPIO26/物理37、一根 GND/物理39，切勿接电源） |
| 继电器 | 1 路高电平触发模块 | IN→BCM17(物理11)，VCC→5V，GND→GND |
| 应急按键 | 自复位按钮 | BCM27(物理13) ↔ GND（内部上拉） |
| 标签机 | 佳博 GP-3120T 类 USB 热敏标签机（待到货）+ 40×30mm 热敏不干胶 | USB 接派，CUPS |
| 散热 | JST 4 线风扇（PWM 无效，恒速） | 可换官方 Active Cooler |

## 快速开始

```bash
# 1. 下载分类模型（可选，缺失自动停用建议分类）
python pi/scripts/download_model.py

# 2. 后端（本地开发=SQLite；云托管注入 MYSQL_ADDRESS 自动切 MySQL）
cd server && pip install -r requirements.txt && vi config.yaml   # 或云环境变量覆盖
uvicorn main:app --host 0.0.0.0 --port 8001

# 3. 树莓派端（非派环境自动 mock 摄像头/锁）
cd pi && pip install -r requirements.txt
python app.py            # :8000，浏览器打开 http://<ip>:8000/kiosk

# 4. 小程序：微信开发者工具导入 miniprogram/（云托管 callContainer 通道需 wx.cloud.init）

# 5. 端到端回归（需 2/3 同机运行，58 项）
python server/scripts/test_planb.py
```

## 配置要点

| 键 | 说明 |
|---|---|
| `wechat.appid/secret` | 小程序凭据；留空=开发模式（code 直接当 openid，仅限无网络调试） |
| `wechat.dev_login` | `true` 时接受 `dev:` 前缀假 code（回归脚本用），**生产必须 false** |
| `security.pi_secret` | 派与云共享密钥（X-Pi-Secret + HMAC 开锁令牌签名），两边一致 |
| `security.jwt_secret` | 登录令牌签名，部署时换强随机 |
| `bootstrap.admin/manager_openids` | 引导提权：列入的 openid 登录自动升超管/宿管，配完即清空 |
| `rules.warn_days / overdue_escalate_days` | 临期分色阈值 / 超期进宿管待办的天数（默认 3） |
| `lock.feedback_gpio / pulse_seconds` | 锁状态反馈 GPIO / 开锁脉冲秒数（0.5~35s 规格内，实配 0.6） |
| 云托管环境变量 | `MYSQL_*`（内置库自动启用）、`JWT_SECRET`、`PI_SECRET`、`WX_*`、`PRINT_PI_LOCAL=""`、`ALLOW_DB_TRANSFER=0`（迁移完关闭） |

## 部署与数据运维

- 后端镜像：`server/Dockerfile`，云托管 GitHub 流水线发布（**必须选「从代码仓库拉取」**，选旧镜像不会构建新代码）；`/healthz` 的 `build` 字段核对线上版本
- 云托管默认域名不能配合法域名 → 小程序走 `wx.cloud.callContainer`（免备案），该通道不支持 multipart → 盘点照片走 base64 接口
- 数据迁移/冷备：`export-rows → import-rows`（一次性）、派上 cron 每日 `backup-rows` 落 SD 卡（`scripts/cloud_backup.sh`）
- 上生产必做：`dev_login:false`、强随机双 secret、清空 bootstrap 名单、AppSecret 泄露过就重置、派 :8000 不暴露公网

## 相关文档

- `方案.md` — 立项方案 v0.3（部分描述已被实现迭代取代，以本 README 为准）
- `server/DEPLOY.md` — 后端部署细节（含云端迁移史）
- `pi/README.md` — 树莓派端说明
