# 小程序后端说明

FastAPI + SQLite，为微信小程序提供登录鉴权、台账、三色提醒、账号管理、调试信息，
并与树莓派对接（下发解锁指令、接收回执与心跳）。数据库、事件均在本地，隐私不出校。

## 角色与权限

| 角色 | 权限 |
|------|------|
| 学生 student | 登记物品、查看**自己**的存放（三色排序）、登记后查看开锁状态 |
| 宿管 manager | 查看**全部**存放信息、按颜色筛选/搜索、**手动触发**到期提醒推送 |
| 超级管理员 admin | 宿管全部权限 + 账号角色管理 + 调试信息（Pi 心跳/解锁指令/事件流水/提醒记录） |

首次登录默认为学生。产生第一个超管：把该用户 openid 填进 `config.yaml` 的
`bootstrap.admin_openids`（开发模式下 openid 形如 `dev_<code>`），或先用已有超管在「账号」页设置。

## 三色规则

以到期日与今天比较（阈值 `rules.warn_days`，默认 2 天）：

- **红 超期**：到期日 < 今天
- **黄 临期**：今天 ≤ 到期日 ≤ 今天+warn_days
- **绿 正常**：其余

所有列表**默认按 红→黄→绿、到期日升序**排序，最紧急的排最前。

## 配置（config.yaml）

| 键 | 说明 |
|----|------|
| `wechat.appid` / `secret` | 小程序凭证；**留空=开发模式**（前端把 code 当 openid，推送只记录不真发） |
| `wechat.remind_template_id` | 到期提醒订阅消息模板 ID |
| `security.jwt_secret` | 登录令牌签名，务必修改 |
| `security.pi_secret` | **必须与树莓派 `pi/config.yaml` 的 `security.secret` 一致** |
| `rules.warn_days` | 临期阈值天数 |
| `bootstrap.admin_openids` | 登录即设为超管的 openid 列表 |

## 运行

```bash
pip install -r requirements.txt
python main.py            # 默认 http://0.0.0.0:8001
```

上线：需 HTTPS + 已备案域名（微信 request 合法域名要求）。可用 nginx 反代 + 证书，
或部署到微信云托管/学校机房公网映射。生产建议把 SQLite 换为 PostgreSQL（改 `db.py` 即可）。

## 主要接口

| 方法 | 路径 | 角色 | 说明 |
|------|------|------|------|
| POST | `/api/v1/wx/login` | 公开 | code 换登录态，返回 token+role |
| GET | `/api/v1/me` | 登录 | 当前用户 |
| POST | `/api/v1/items` | 登录 | 登记物品，生成编码+解锁指令 |
| GET | `/api/v1/items/mine` | 登录 | 我的存放（三色排序） |
| GET | `/api/v1/items/{id}/unlock` | 登录 | 查询该物品开锁状态 |
| GET | `/api/v1/items` | 宿管+ | 全部存放，支持 `color`/`q`/`status` |
| POST | `/api/v1/reminders` | 宿管+ | 手动提醒：`{item_ids:[]}` 或 `{colors:["red","yellow"]}` |
| GET | `/api/v1/users` | 超管 | 账号列表 |
| POST | `/api/v1/users/{id}/role` | 超管 | 改角色 |
| GET | `/api/v1/debug/pi` | 超管 | Pi 节点+解锁指令 |
| GET | `/api/v1/debug/events` | 超管 | 事件流水+提醒记录 |
| GET | `/api/v1/pi/pending-unlocks` | Pi | 拉取待执行解锁指令（带 `X-Pi-Secret`） |
| POST | `/api/v1/pi/unlock-result` | Pi | 解锁回执 |
| POST | `/api/v1/pi/heartbeat` | Pi | 心跳上报 |

## 订阅消息说明

微信「一次性订阅消息」需用户在小程序内**每次授权一次**才能推一条。当前实现：
学生登记时调用 `wx.requestSubscribeMessage` 申请一次授权（`miniprogram/config.js` 填模板 ID 后生效），
宿管点提醒即消耗该授权推送。若学校主体可申请**长期订阅消息**模板，则无需每次授权，更适合本场景。
开发模式下不真发，只在提醒记录里写明「应向谁推送什么」，便于联调。
