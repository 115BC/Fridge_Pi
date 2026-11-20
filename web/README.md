# 独立网站前端（web/）

内网用的独立网站，功能对齐微信小程序（学生 / 宿管 / 超管三角色），**小程序与 Pi 端完全不动**。原生 HTML + CSS + JS，零构建，静态托管即可。

## 架构
```
浏览器 → Nginx 静态托管（本目录）
          │ fetch（后端 CORS allow_origins=*）
          ↓ FastAPI 后端（云托管域名或自有部署）
          └── users/items/... 与小程序共用，JWT 同格式
```
网站登录用**账号+密码**（内网自用，不依赖微信登录/备案/小程序发布）：后端按 `web_username` 查 users 表、`verify_password` 校验后签发与小程序同格式的 JWT（payload 仍是 openid/role），因此网站拿到 token 能直接调用所有现有业务 API。和小程序的姓名、物品、开锁、改期等操作实时互通。

## 与小程序的能力差异
- **扫码**：优先用浏览器 `BarcodeDetector` + 摄像头（需 HTTPS 或 localhost），不支持则弹框手动输入编码
- **提醒触达**：网页没有微信订阅消息能力，通知以站内「通知历史」展示；微信推送继续由小程序承担
- **CSV 导出**：`GET /api/v1/export/items.csv` → Blob 下载（替代 `wx.shareFileMessage`）
- **拍照盘点**：读本地图片转 base64 → `POST /api/v1/stocktake_b64`（小程序因 callContainer 不支持 multipart 才走 base64，网站也可直接用 multipart `/stocktake`）

## 配置
`config.js` 的 `API_BASE` 指向后端地址：
- 本地开发：`http://127.0.0.1:8001`
- 部署后：改成你的云服务器反代地址或云托管公网域名

## 账号开通
网站账号与微信账号一一对应（同一条 users 记录）。首次需要开通：
1. 超管在网站「学生管理 / 账号管理」里点「开通网站账号」，填登录名+初始密码（勾选强制改密）
2. 或本地/云端跑一次性脚本 `python server/scripts/init_web_accounts.py` 批量生成（初始密码只打印一次）
3. 用户首次登录会被强制改密

后端接口：`POST /api/v1/web/login`、`POST /api/v1/web/change-password`、`POST /api/v1/admin/users/{id}/web-account`。

## 本地运行
```bash
# 后端 8001（仓库 .venv，NO_PROXY='*' 前缀）
cd server && ../.venv/Scripts/python.exe main.py
# 静态站点 8088
cd web && ../.venv/Scripts/python.exe -m http.server 8088 --bind 127.0.0.1
# 浏览器打开 http://127.0.0.1:8088/
```

## 部署到云服务器（待部署）
1. 把 `web/` 整个目录放到 Nginx 静态根（如 `/var/www/fridge-web`）
2. 修改 `config.js` 的 `API_BASE` 为后端公网地址
3. Nginx 配 `location / { try_files $uri $uri/ /index.html; }`；如果 API 走同域反代，加 `location /api/ { proxy_pass <后端>; }` 并把 `API_BASE` 设为同源
4. 建议 HTTPS（摄像头扫码 `BarcodeDetector` 要求安全上下文；localhost 例外）
5. 后端新增的 3 个登录端点需手动发布才生效：云托管控制台「部署新版本 → 从代码仓库拉取 master」重新构建
