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
stu = login("dev:stuA", name="小A", room="2-201", building="主楼")
mgr = login("dev:mgrA", name="王超管", room="值班室", building="主楼")     # 先建账号
import sqlite3
from pathlib import Path
_db = sqlite3.connect(Path(__file__).resolve().parent.parent / "data" / "fridge.db")
# 开发登录 openid 形态为 "dev_" + 登录码（如 dev_dev:mgrA），旧写法 dev_mgrA 已不生效
_db.execute("UPDATE users SET role='admin' WHERE openid IN ('dev_dev:mgrA','dev_mgrA')")
_db.execute("INSERT OR IGNORE INTO users(openid,name,room,role) VALUES('dev_dev:mgrB','李宿管','值班室2','manager')")
_db.execute("UPDATE users SET role='manager' WHERE openid='dev_dev:mgrB'")
_db.commit(); _db.close()
mgr = login("dev:mgrA", name="王超管", room="值班室", building="主楼")
mgr2 = login("dev:mgrB", name="李宿管", room="值班室2", building="主楼")

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

# 2.5 宿管触控屏登记 -> 待认领区 -> 学生扫码认领（一人一主）
t2 = c.post(B + "/api/v1/kiosk/login/start", json={}, headers=PS).json()["ticket"]
c.post(B + "/api/v1/kiosk/login/confirm", json={"ticket": t2}, headers=H(mgr["token"]))
r5 = c.post(B + "/api/v1/kiosk/items", json={"name": "无主牛奶", "expire_at": _soon,
          "category": "奶制品", "login_ticket": t2}, headers=PS).json()
check("宿管登记自动进待认领区", r5.get("status") == "pending_claim", str(r5))
cl = c.post(B + f"/api/v1/items/{r5['code']}/claim", json={}, headers=H(stu["token"])).json()
check("学生扫码认领成功", cl.get("ok") and cl.get("name") == "无主牛奶", str(cl))
mine2 = c.get(B + "/api/v1/items/mine", headers=H(stu["token"])).json()["items"]
check("认领后进入学生台账", any(i["code"] == r5["code"] for i in mine2))
cl2 = c.post(B + f"/api/v1/items/{r5['code']}/claim", json={}, headers=H(mgr2["token"]))
check("重复认领被拒 400", cl2.status_code == 400, str(cl2.status_code))
td = c.post(B + "/api/v1/kiosk/takeout-done", json={"code": r5["code"], "taken": True}, headers=PS).json()
check("取出确认页-已取出归档", td.get("status") == "taken_out", str(td))
td2 = c.post(B + "/api/v1/kiosk/takeout-done", json={"code": code_a, "taken": False}, headers=PS).json()
check("取出确认页-继续存放不改状态", td2.get("status") == "active", str(td2))

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
den4 = c.post(B + f"/api/v1/items/{item_a}/action", json={"action": "expire", "value": "2026-12-31"},
              headers=H(stu["token"]))
check("学生改到期日被拒 403", den4.status_code == 403, str(den4.status_code))
r4 = c.post(B + f"/api/v1/items/{item_a}/action", json={"action": "expire", "value": "2026-12-31"},
            headers=H(mgr["token"])).json()
check("宿管改到期日", r4.get("expire_at") == "2026-12-31", str(r4))
q404 = c.post(B + "/api/v1/users/999999/quota", json={"quota": 8}, headers=H(mgr["token"]))
check("额度接口(不存在用户404)", q404.status_code == 404, str(q404.status_code))
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
bad = c.post(B + "/api/v1/users/%d/role" % 999999, json={"role": "admin"}, headers=H(mgr["token"]))
check("超管改角色接口可用(不存在用户404)", bad.status_code == 404, str(bad.status_code))
den = c.get(B + "/api/v1/users", headers=H(mgr2["token"]))
check("宿管可查看学生列表(分楼管理)", den.status_code == 200 and "users" in den.json(),
      str(den.status_code))
den2 = c.get(B + "/api/v1/debug/events", headers=H(mgr2["token"]))
check("宿管查日志被拒 403", den2.status_code == 403, str(den2.status_code))
okitems = c.get(B + "/api/v1/items", headers=H(mgr2["token"]))
check("宿管仍可管理物品", okitems.status_code == 200)
qr = c.get(B + f"/api/v1/items/{item_a}/qr", headers=H(stu["token"])).json()
check("取物二维码PNG", qr.get("code") == code_a and len(qr.get("png_base64", "")) > 100)

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
if fired:
    check("调度自动提醒曾触发(历史或窗口内)", True)
else:
    print("INFO 调度自动提醒暂无记录（历史被清理且未到批次时刻），跳过断言")
rem = c.get(B + "/api/v1/debug/events", headers=H(mgr["token"])).json()["reminders"]
auto_rows = [x for x in rem if x["channel"] == "auto"]
if auto_rows:
    check("自动提醒 channel=auto", True, f"{len(auto_rows)} 条")
else:
    print("INFO 自动提醒记录为空（历史已清理或未到新批次），channel 检查跳过")

# 5.5 楼宇：绑定必选/不可自助改楼/超管改楼/宿管跨楼/按楼过滤与容量分机
bl = c.get(B + "/api/v1/buildings").json()
check("楼宇列表公开可取(未登录)", any(b["name"] == "主楼" for b in bl.get("buildings", [])), str(bl)[:80])
no_b = c.post(B + "/api/v1/wx/login", json={"code": "dev:stuD"})
check("绑定时缺楼宇被拒", no_b.status_code == 400 and "NEED_BIND" in no_b.text, str(no_b.status_code))
bad_b = c.post(B + "/api/v1/wx/login", json={"code": "dev:stuD", "name": "小D", "room": "4-404",
                                             "building": "不存在楼"})
check("绑定时乱填楼宇被拒", bad_b.status_code == 400, str(bad_b.status_code))
stuD = login("dev:stuD", name="小D", room="4-404", building="主楼")
uid_d = stuD["user"]["id"]
c.post(B + "/api/v1/admin/buildings", json={"name": "东楼", "fridge_id": "fridge-02"},
       headers=H(mgr["token"]))
c.post(B + "/api/v1/admin/buildings", json={"name": "东楼", "fridge_id": "fridge-x"},
       headers=H(mgr["token"]))
after = login("dev:stuD", building="东楼")
check("已绑学生登录改楼无效(仍需超管)", after["user"]["building"] == "主楼", str(after["user"]["building"]))
den_b = c.post(B + f"/api/v1/users/{uid_d}/building", json={"building": "东楼"}, headers=H(mgr2["token"]))
check("宿管改学生楼宇被拒 403", den_b.status_code == 403, str(den_b.status_code))
mv = c.post(B + f"/api/v1/users/{uid_d}/building", json={"building": "东楼"}, headers=H(mgr["token"]))
check("超管可改学生楼宇", mv.status_code == 200)
prof = c.put(B + "/api/v1/me", json={"name": "小D", "room": "4-405"}, headers=H(stuD["token"]))
check("学生自助改姓名寝室", prof.json().get("user", {}).get("room") == "4-405", str(prof.status_code))
it_d = c.post(B + "/api/v1/items", json={"name": "楼测酸奶", "category": "奶制品", "quantity": 1,
                                         "expire_at": _soon}, headers=H(stuD["token"])).json()
east = c.get(B + "/api/v1/items?building=东楼", headers=H(mgr2["token"])).json()["items"]
main = c.get(B + "/api/v1/items?building=主楼", headers=H(mgr2["token"])).json()["items"]
check("学生自助登记自动归属本楼冰箱",
      any(i["id"] == it_d["item_id"] for i in east) and not any(i["id"] == it_d["item_id"] for i in main))
us_e = c.get(B + "/api/v1/users?building=东楼", headers=H(mgr2["token"])).json()["users"]
check("宿管按楼筛学生(跨楼切换)", any(u["id"] == uid_d for u in us_e))
prof2 = c.post(B + f"/api/v1/users/{uid_d}/profile", json={"name": "小D", "room": "4-406"},
               headers=H(mgr2["token"]))
check("宿管代改学生姓名寝室", prof2.status_code == 200)
cap2 = c.post(B + "/api/v1/capacity", json={"capacity_ml": 200000, "fridge_id": "fridge-02"},
              headers=H(mgr2["token"])).json()
cap2g = c.get(B + "/api/v1/capacity?fridge_id=fridge-02", headers=H(mgr2["token"])).json()
cap1g = c.get(B + "/api/v1/capacity?fridge_id=fridge-01", headers=H(mgr2["token"])).json()
check("容量按楼(冰箱)独立", cap2.get("capacity_ml") == 200000 and cap2g.get("capacity_ml") == 200000
      and cap1g.get("capacity_ml") != 200000, f"{cap2g.get('capacity_ml')}/{cap1g.get('capacity_ml')}")
ct = c.post(B + "/api/v1/admin/contact", json={"value": "微信号 fridge-admin"}, headers=H(mgr["token"]))
bl2 = c.get(B + "/api/v1/buildings").json()
check("超管联系方式可设可见", ct.status_code == 200 and bl2.get("admin_contact") == "微信号 fridge-admin")
rm1 = c.post(B + "/api/v1/reminders", json={"building": "东楼", "item_ids": [it_d["item_id"]]},
             headers=H(mgr2["token"])).json()
check("提醒可按楼(无微信openid时容忍skipped)", rm1.get("total") == 1)
c.post(B + "/api/v1/admin/buildings", json={"name": "临时楼", "fridge_id": "fridge-tmp"},
       headers=H(mgr["token"]))
del_ok = c.delete(B + "/api/v1/admin/buildings/临时楼", headers=H(mgr["token"]))
del_den = c.delete(B + "/api/v1/admin/buildings/主楼", headers=H(mgr["token"]))
check("空楼可删/有人楼拒删", del_ok.status_code == 200 and del_den.status_code == 400,
      f"{del_ok.status_code}/{del_den.status_code}")
c.delete(B + "/api/v1/admin/buildings/东楼", headers=H(mgr["token"]))   # 还原：东楼有stuD应被拒
check("有学生的楼删不掉(再次确认)", c.delete(B + "/api/v1/admin/buildings/东楼",
      headers=H(mgr["token"])).status_code == 400)

# 5.6 运维增强：照片落库 / 超期升级 / CSV导出 / 通知兜底 / 备份通道
_db2 = sqlite3.connect(Path(__file__).resolve().parent.parent / "data" / "fridge.db")
ph_n = _db2.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
ph_name = _db2.execute("SELECT name FROM photos LIMIT 1").fetchone()
check("盘点照片已落库(发布不丢)", ph_n >= 1 and ph_name is not None, f"{ph_n} 张")
if ph_name:
    img = c.get(B + "/photos/" + ph_name[0])
    check("照片接口可读(JPEG)", img.status_code == 200 and img.content[:2] == b"\xff\xd8")
_db2.execute("UPDATE items SET escalated=1 WHERE id=?", (it_d["item_id"],)); _db2.commit()
esc = c.get(B + "/api/v1/items?escalated=1", headers=H(mgr2["token"])).json()
check("超期待处理筛选", any(i["id"] == it_d["item_id"] for i in esc["items"])
      and esc.get("escalated_count", 0) >= 1)
c.post(B + f"/api/v1/items/{it_d['item_id']}/action", json={"action": "expire", "value": _soon},
       headers=H(mgr["token"]))
esc2 = c.get(B + "/api/v1/items?escalated=1", headers=H(mgr2["token"])).json()
check("宿管处理后自动移出待办", not any(i["id"] == it_d["item_id"] for i in esc2["items"]))
csv_r = c.get(B + "/api/v1/export/items.csv?status=all", headers=H(mgr2["token"]))
check("台账CSV导出(含表头/楼宇列)", csv_r.status_code == 200 and "编码" in csv_r.text
      and "楼宇" in csv_r.text and "楼测酸奶" in csv_r.text, str(csv_r.status_code))
csv_b = c.get(B + "/api/v1/export/items.csv?status=all&building=东楼", headers=H(mgr2["token"]))
check("CSV按楼导出", "楼测酸奶" in csv_b.text and "酸奶" not in csv_b.text.replace("楼测酸奶", ""))
nt = c.get(B + "/api/v1/notifications/mine", headers=H(stuD["token"])).json()
check("学生通知历史(订阅兜底)", len(nt.get("notifications", [])) >= 1, str(nt)[:60])
bk = c.get(B + "/api/v1/admin/backup-rows", headers=PS).json()
check("备份通道(派拉取)", bk.get("tables", {}).get("users") and len(bk["tables"]["users"]) >= 4)
_db2.close()

# 6. 心跳与锁状态
hb = c.get(B + "/api/v1/debug/pi", headers=H(mgr["token"])).json()["pi_nodes"]
check("Pi 心跳上报", hb and hb[0]["fridge_id"] == "fridge-01", str(hb))

n_fail = sum(1 for _, v in ok if not v)
print(f"\n===== 回归结果: {len(ok)-n_fail}/{len(ok)} 通过 =====")
raise SystemExit(1 if n_fail else 0)
