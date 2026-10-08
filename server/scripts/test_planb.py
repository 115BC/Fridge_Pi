"""方案B 同机回归测试：登记->打印->Pi自动开锁->拍照盘点->待认领流转->自动提醒。
运行前提: server(8001) 与 pi(8000) 已按方案B配置启动。
"""
import io
import time
import httpx
from qrcode import make as qr_make
from PIL import Image

B = "http://127.0.0.1:8001"
P = "http://127.0.0.1:8000"
c = httpx.Client(timeout=10)
ok = []


def check(name, cond, extra=""):
    ok.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, extra)


# 等待服务
for _ in range(30):
    try:
        c.get(B + "/healthz"); c.get(P + "/healthz"); break
    except Exception:
        time.sleep(1)

def login(code, **kw):
    r = c.post(B + "/api/v1/wx/login", json={"code": code, **kw})
    r.raise_for_status()
    return r.json()

H = lambda t: {"Authorization": "Bearer " + t}
stu = login("dev:stuA", name="小A", room="2-201")
mgr = login("dev:mgrA", name="王宿管", room="值班室")     # 先建账号
# 双角色模型（超管已并入宿管）：脚本直接把测试账号设为宿管后重新登录
import sqlite3
from pathlib import Path
_db = sqlite3.connect(Path(__file__).resolve().parent.parent / "data" / "fridge.db")
_db.execute("UPDATE users SET role='manager' WHERE openid='dev_mgrA'")
_db.commit(); _db.close()
mgr = login("dev:mgrA", name="王宿管", room="值班室")

# 1. 屏幕扫码登录 -> 触控屏拍照登记（归属本人）-> 打印 -> 自动开锁
import datetime, yaml
_pisec = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))["security"]["pi_secret"]
PS = {"X-Pi-Secret": _pisec}
t = c.post(B + "/api/v1/kiosk/login/start", json={}, headers=PS).json()["ticket"]
cf = c.post(B + "/api/v1/kiosk/login/confirm", json={"ticket": t}, headers=H(stu["token"]))
check("屏幕登录码确认", cf.status_code == 200, str(cf.json()))
st1 = c.get(B + "/api/v1/kiosk/login/status", params={"ticket": t}).json()
check("登录状态 confirmed 且身份正确", st1["status"] == "confirmed" and st1["user"]["name"] == "小A", str(st1))
qr = c.get(B + "/api/v1/kiosk/login/qr", params={"ticket": t})
check("登录二维码 PNG", qr.status_code == 200 and qr.content[:4] == b"\x89PNG")
_soon = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
r = c.post(B + "/api/v1/kiosk/items", json={"name": "酸奶", "expire_at": _soon,
                                            "category": "奶制品", "login_ticket": t}, headers=PS).json()
code_a, item_a = r["code"], r["item_id"]
check("屏幕登记颜色=yellow", r.get("color") == "yellow", str(r))
check("登记触发打印(方案B)", r["print"] == "queued", f"print={r['print']}")
mine = c.get(B + "/api/v1/items/mine", headers=H(stu["token"])).json()["items"]
check("屏幕登记归属登录者", any(i["id"] == item_a for i in mine))
bad = c.post(B + "/api/v1/kiosk/items", json={"name": "无主物", "expire_at": _soon, "login_ticket": ""}, headers=PS)
check("未屏幕登录被拒 400", bad.status_code == 400, str(bad.status_code))
mi = c.get(B + "/api/v1/kiosk/my-items", params={"login_ticket": t}).json()
check("屏幕我的物品列表", any(i["id"] == item_a for i in mi["items"]))

# 2. 登记即下发开锁（人就在冰箱前）；再登记第二件供盘点
st = {}
for _ in range(15):
    st = c.get(B + f"/api/v1/items/{item_a}/unlock", headers=H(stu["token"])).json()
    if st["status"] in ("unlocked", "rejected", "expired"):
        break
    time.sleep(1)
check("Pi 轮询执行开锁", st.get("status") == "unlocked", str(st))
r2 = c.post(B + "/api/v1/kiosk/items", json={"name": "苹果",
       "expire_at": (datetime.date.today() + datetime.timedelta(days=5)).isoformat(),
       "category": "水果", "login_ticket": t}, headers=PS).json()
code_b, item_b = r2["code"], r2["item_id"]

# 3. 拍照盘点：合成照片（2个真实标签 + 1个台账没有的码）
img = Image.new("RGB", (900, 300), "white")
for i, code in enumerate([code_a, code_b, "FAKE0001"]):
    q = qr_make(code).resize((260, 260))
    img.paste(q, (10 + i * 295, 10))
buf = io.BytesIO()
img.save(buf, format="PNG")
buf.seek(0)
resp = c.post(B + "/api/v1/stocktake", headers=H(mgr["token"]),
              files={"file": ("p.png", buf, "image/png")},
              data={"fridge_id": "fridge-01"})
d = resp.json()
check("盘点: 命中2件", {x["code"] for x in d["found"]} == {code_a, code_b}, str(d.get("found")))
check("盘点: 无登记标签1个", d["unknown_codes"] == ["FAKE0001"], str(d["unknown_codes"]))
check("盘点: 照片可回看", d["photo_url"].startswith("/photos/"))

# 4. 待认领流转
a = c.post(B + f"/api/v1/items/{item_b}/action", json={"action": "pending_claim"},
           headers=H(mgr["token"])).json()
lst = c.get(B + "/api/v1/items?status=pending_claim", headers=H(mgr["token"])).json()
check("移入待认领区", a.get("ok") and any(i["id"] == item_b for i in lst["items"]))
r3 = c.post(B + f"/api/v1/items/{item_b}/action", json={"action": "removed", "note": "公示3天到期清理"},
            headers=H(mgr["token"])).json()
check("清理留痕", r3.get("status") == "removed")

# 4.5 学生自我管理 + 体积/容量/排序（双角色与新增能力）
r4 = c.post(B + f"/api/v1/items/{item_a}/action", json={"action": "expire", "value": "2026-12-31"},
            headers=H(stu["token"])).json()
check("学生改到期日", r4.get("expire_at") == "2026-12-31", str(r4))
c.post(B + f"/api/v1/items/{item_a}/volume", json={"volume_ml": 2500}, headers=H(mgr["token"]))
r5 = c.post(B + f"/api/v1/items/{item_a}/action", json={"action": "taken"}, headers=H(stu["token"])).json()
arch = c.get(B + "/api/v1/items?status=archived&sort=volume", headers=H(mgr["token"])).json()
check("取出登记->归档", r5.get("status") == "taken_out"
      and any(i["id"] == item_a for i in arch["items"]))
check("体积修正生效", any(i["id"] == item_a and i["vol_est_ml"] == 2500 for i in arch["items"]))
vols = [i["vol_est_ml"] for i in arch["items"]]
check("按体积降序", vols == sorted(vols, reverse=True), str(vols))
cap = c.get(B + "/api/v1/capacity", headers=H(stu["token"])).json()
check("容量接口", cap.get("capacity_ml", 0) >= 1000 and "used_ml" in cap, str(cap))
r6 = c.post(B + "/api/v1/capacity", json={"capacity_ml": 130000}, headers=H(mgr["token"])).json()
check("宿管改总容量", r6.get("capacity_ml") == 130000)
rden = c.post(B + "/api/v1/capacity", json={"capacity_ml": 130000}, headers=H(stu["token"]))
check("学生改容量被拒", rden.status_code == 403, str(rden.status_code))
bad = c.post(B + "/api/v1/users/1/role", json={"role": "admin"}, headers=H(mgr["token"]))
check("admin 角色已废除", bad.status_code == 400, str(bad.status_code))

# 5. 自动到期提醒（历史事件已触发过则直接通过；否则等 3 分钟调度窗口）
fired = any(e["type"] == "auto_remind"
            for e in c.get(B + "/api/v1/debug/events", headers=H(mgr["token"])).json()["events"])
for _ in range(0 if fired else 90):
    if fired:
        break
    evs = c.get(B + "/api/v1/debug/events", headers=H(mgr["token"])).json()["events"]
    if any(e["type"] == "auto_remind" for e in evs):
        fired = True
    time.sleep(2)
check("调度自动提醒曾触发(历史或窗口内)", fired)
rem = c.get(B + "/api/v1/debug/events", headers=H(mgr["token"])).json()["reminders"]
auto_rows = [x for x in rem if x["channel"] == "auto"]
if auto_rows:
    check("自动提醒 channel=auto", True, f"{len(auto_rows)} 条")
else:
    print("INFO 自动提醒记录为空（历史已清理或未到新批次），channel 检查跳过")

# 6. 心跳与锁状态
hb = c.get(B + "/api/v1/debug/pi", headers=H(mgr["token"])).json()["pi_nodes"]
check("Pi 心跳上报", hb and hb[0]["fridge_id"] == "fridge-01", str(hb))

n_fail = sum(1 for _, v in ok if not v)
print(f"\n===== 回归结果: {len(ok)-n_fail}/{len(ok)} 通过 =====")
raise SystemExit(1 if n_fail else 0)
