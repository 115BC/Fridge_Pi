# 部署文档（当前采用方案 B，方案 A 保留可切换）

两种方案**代码完全相同**，只改配置，随时互切。

## 方案 B（现行）：小程序后端 + 树莓派端 同机部署在冰箱旁的树莓派

```
[学生/宿管手机微信] ──HTTPS──> [公网域名] ──frp/端口映射──> [树莓派 Nginx :443]
                                                      ├─ proxy_pass → server  :8001
                                                      └─ (本机调用) pi API    :8000（不对公网开放）
[树莓派 pi 服务] ── localhost 轮询 ──> [树莓派 server 服务]
```

### 1. 系统准备（64 位 Raspberry Pi OS）
```bash
sudo apt update && sudo apt install -y python3-gpiozero rpicam-apps cups nginx
pip install -r pi/requirements.txt -r server/requirements.txt
mkdir -p ~/fridge_pi && # 上传 pi/ server/ 两目录
python3 server/../pi/scripts/download_model.py   # 在 pi/ 目录下运行，下载分类模型
```

### 2. 配置（只有这 4 处与方案 A 不同）
| 文件 | 键 | 方案 B 值 | 方案 A 值 |
|------|----|----------|----------|
| `pi/config.yaml` | `backend.base_url` | `http://127.0.0.1:8001` | 公网后端地址 |
| `server/config.yaml` | `print.pi_local_api` | `http://127.0.0.1:8000` | 留空（或改走下发队列） |
| `server/config.yaml` | `security.pi_secret` | = `pi/config.yaml` 的 `security.secret` | 同左 |
| 数据库/照片位置 | 都在派上 | `server/data/fridge.db` | 在云服务器上 |

其余密钥（`jwt_secret` 等）两方案都要改成随机值。

### 3. 两个 systemd 服务
```bash
sudo cp pi/fridge-pi.service server/fridge-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fridge-server fridge-pi
systemctl status fridge-server fridge-pi
```

### 4. 让手机微信能访问（微信硬性要求：HTTPS + 已备案域名）
学校机房有公网 IP：路由器映射 443 → 派，DNS 指向学校域名，certbot 签证书。
没有公网 IP（最常见）：用 **frp**——租一台有公网的轻量云主机跑 frps，派上跑 frpc：

```ini
# /etc/frp/frpc.toml （树莓派上）
serverAddr = "公网服务器IP"
serverPort = 7000
[[proxies]]
name = "fridge-https"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8443          # Nginx 监听口
remotePort = 8443         # 或公网 443
```

```nginx
# /etc/nginx/sites-available/fridge （树莓派上）
server {
    listen 8443 ssl;
    server_name fridge.your-school-domain.cn;
    ssl_certificate     /etc/letsencrypt/live/.../fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/.../privkey.pem;
    client_max_body_size 20m;            # 盘点照片上传
    location / { proxy_pass http://127.0.0.1:8001; proxy_set_header Host $host; }
    # 注意：pi 的 :8000 不要对外代理，只允许本机调用
}
```

最后在微信公众平台「开发设置 → request 合法域名」填 `https://fridge.your-school-domain.cn`，
并把 `miniprogram/config.js` 的 `BASE_URL` 改成该域名。

### 5. 触控屏 kiosk 自启
```bash
mkdir -p ~/.config/wayfire/autostart   # 或 /etc/xdg/autostart（取决于桌面环境）
cat > ~/.config/labwc/autostart 2>/dev/null <<'EOF'
chromium-browser --kiosk --noerrdialogs http://localhost:8000/kiosk &
EOF
```

## 方案 A（保留）：server 上云，派只跑 pi

1. `server/` 部署到云主机/微信云托管（同样需 HTTPS 备案域名，云托管可免备案）
2. `pi/config.yaml`：`backend.base_url = https://你的云域名`
3. `server/config.yaml`：`print.pi_local_api` 留空；打印改由 pi 轮询领取（需给 pending 队列加 print 指令类型，或登记后学生在小程序点"打印标签"触发本机打印）
4. 派与服务器间已有 `X-Pi-Secret` + HMAC 令牌保护，切换零代码改动

## 安全清单（两方案通用）
- [ ] `jwt_secret`、`pi_secret`/`security.secret` 改为随机值且两端一致
- [ ] pi 的 :8000 只监听/只允许本机访问（电磁锁控制绝不能暴露公网）
- [ ] `bootstrap.admin_openids` 填超管 openid 后**清掉测试账号**
- [ ] 定期备份 `server/data/fridge.db`（含全部台账与留痕）
- [ ] 服务器时间同步（NTP），三色判定与令牌过期都依赖时钟
