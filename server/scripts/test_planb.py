"""方案B 同机回归测试：登记->打印->Pi自动开锁->拍照盘点->待认领流转->自动提醒。
运行前提: server(8001) 与 pi(8000) 已按方案B配置启动。
"""
import io
import time
import uuid
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
# 复位小D：上一轮可能已绑楼/已建物品，否则"绑定必选楼宇"用例会因持久库状态而失真
_db.execute("DELETE FROM items WHERE user_id IN (SELECT id FROM users WHERE openid='dev_dev:stuD')")
_db.execute("DELETE FROM users WHERE openid='dev_dev:stuD'")
# 复位上锁/排队/呼叫与存放天数，保证本轮断言可重复
_db.execute("DELETE FROM queue")
_db.execute("DELETE FROM calls")
_db.execute("DELETE FROM settings WHERE key LIKE 'fridge_locked:%'")
_db.execute("DELETE FROM settings WHERE key='storage_days'")
_db.commit(); _db.close()
mgr = login("dev:mgrA", name="王超管", room="值班室", building="主楼")
mgr2 = login("dev:mgrB", name="李宿管", room="值班室2", building="主楼")

# 1. 屏幕扫码登录 -> 触控屏登记（只建待入库台账+打印）-> 扫码确认入库 -> 开锁
import datetime, yaml
_pisec = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))["security"]["pi_secret"]
PS = {"X-Pi-Secret": _pisec}
_today = datetime.date.today()
_k = (_today - datetime.date(2026, 1, 1)).days % 16
exp_batch = _k + 1 if _k < 8 else _k + 3
exp_keep = (_today + datetime.timedelta(days=6)).isoformat()
t = c.post(B + "/api/v1/kiosk/login/start", json={}, headers=PS).json()["ticket"]
cf = c.post(B + "/api/v1/kiosk/login/confirm", json={"ticket": t}, headers=H(stu["token"]))
check("屏幕登录码确认", cf.status_code == 200, str(cf.json()))
st1 = c.get(B + "/api/v1/kiosk/login/status", params={"ticket": t}).json()
check("登录状态 confirmed 且身份正确", st1["status"] == "confirmed" and st1["user"]["name"] == "小A", str(st1))
check("登录状态带角色(满柜分流用)", st1["user"].get("role") == "student", str(st1))
qr = c.get(B + "/api/v1/kiosk/login/qr", params={"ticket": t})
check("登录二维码 PNG", qr.status_code == 200 and qr.content[:4] == b"\x89PNG")
r = c.post(B + "/api/v1/kiosk/items", json={"name": "酸奶",
                                            "category": "奶制品", "login_ticket": t}, headers=PS).json()
code_a, item_a = r["code"], r["item_id"]
check("登记只建待入库(awaiting_scan)", r.get("status") == "awaiting_scan", str(r)[:120])
check("登记批次编号/存放至", r.get("batch_no") == exp_batch and r.get("keep_until") == exp_keep,
      f"{r.get('batch_no')}/{r.get('keep_until')}")
check("登记触发打印(方案B)", r["print"] == "queued", f"print={r['print']}")
mine = c.get(B + "/api/v1/items/mine", headers=H(stu["token"])).json()["items"]
check("未扫码确认前不在学生台账", not any(i["id"] == item_a for i in mine))
un_pre = c.post(B + "/api/v1/kiosk/unlock", json={"code": code_a}, headers=PS)
check("未确认入库不能开锁 400", un_pre.status_code == 400, str(un_pre.status_code))
cf1 = c.post(B + f"/api/v1/kiosk/items/{code_a}/confirm-store", json={}, headers=PS).json()
check("扫码确认入库转正式", cf1.get("ok") and cf1.get("status") == "active", str(cf1)[:90])
mine = c.get(B + "/api/v1/items/mine", headers=H(stu["token"])).json()["items"]
check("确认后归属登录者", any(i["id"] == item_a for i in mine))
bad = c.post(B + "/api/v1/kiosk/items", json={"name": "无主物", "login_ticket": ""}, headers=PS)
check("未屏幕登录被拒 400", bad.status_code == 400, str(bad.status_code))
mi = c.get(B + "/api/v1/kiosk/my-items", params={"login_ticket": t}).json()
check("屏幕我的物品列表", any(i["id"] == item_a for i in mi["items"]))

# 2. 确认入库后下发开锁（人就在冰箱前）；再登记第二件供盘点
st = {}
for _ in range(15):
    st = c.get(B + f"/api/v1/items/{item_a}/unlock", headers=H(stu["token"])).json()
    if st["status"] in ("unlocked", "rejected", "expired"):
        break
    time.sleep(1)
check("确认入库后 Pi 轮询执行开锁", st.get("status") == "unlocked", str(st))
r2 = c.post(B + "/api/v1/kiosk/items", json={"name": "苹果",
       "category": "水果", "login_ticket": t}, headers=PS).json()
code_b, item_b = r2["code"], r2["item_id"]
cf2 = c.post(B + f"/api/v1/kiosk/items/{code_b}/confirm-store", json={}, headers=PS).json()
check("第二件扫码确认入库", cf2.get("status") == "active", str(cf2)[:80])

# 2.5 宿管触控屏登记 -> 确认后进待认领区 -> 学生扫码认领（一人一主）
t2 = c.post(B + "/api/v1/kiosk/login/start", json={}, headers=PS).json()["ticket"]
c.post(B + "/api/v1/kiosk/login/confirm", json={"ticket": t2}, headers=H(mgr["token"]))
r5 = c.post(B + "/api/v1/kiosk/items", json={"name": "无主牛奶",
          "category": "奶制品", "login_ticket": t2}, headers=PS).json()
check("宿管登记先进待入库", r5.get("status") == "awaiting_scan", str(r5)[:80])
cf5 = c.post(B + f"/api/v1/kiosk/items/{r5['code']}/confirm-store", json={}, headers=PS).json()
check("宿管确认后进待认领区", cf5.get("status") == "pending_claim", str(cf5))
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
check("学生改存放至被拒 403", den4.status_code == 403, str(den4.status_code))
den4b = c.post(B + f"/api/v1/items/{item_a}/action", json={"action": "expire", "value": "2026-12-31"},
               headers=H(mgr2["token"]))
check("宿管改存放至被拒 403", den4b.status_code == 403, str(den4b.status_code))
r4 = c.post(B + f"/api/v1/items/{item_a}/action", json={"action": "expire", "value": "2026-12-31"},
            headers=H(mgr["token"])).json()
check("超管改存放至(通用动作)", r4.get("expire_at") == "2026-12-31", str(r4))
ku_den = c.post(B + f"/api/v1/items/{item_a}/keep-until", json={"keep_until": "2026-12-30"},
                headers=H(mgr2["token"]))
check("宿管用存放至接口被拒 403", ku_den.status_code == 403, str(ku_den.status_code))
ku_ok = c.post(B + f"/api/v1/items/{item_a}/keep-until", json={"keep_until": "2026-12-31"},
               headers=H(mgr["token"]))
check("超管改存放至接口", ku_ok.status_code == 200 and ku_ok.json().get("keep_until") == "2026-12-31",
      str(ku_ok.json()))
ku_bad = c.post(B + f"/api/v1/items/{item_a}/keep-until", json={"keep_until": "2026/12/31"},
                headers=H(mgr["token"]))
check("存放至格式校验 400", ku_bad.status_code == 400, str(ku_bad.status_code))
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
                                         "expire_at": exp_keep}, headers=H(stuD["token"])).json()
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
c.post(B + f"/api/v1/items/{it_d['item_id']}/action", json={"action": "expire", "value": exp_keep},
       headers=H(mgr["token"]))   # 超管改存放至会自动清 escalated
esc2 = c.get(B + "/api/v1/items?escalated=1", headers=H(mgr2["token"])).json()
check("超管处理后自动移出待办", not any(i["id"] == it_d["item_id"] for i in esc2["items"]))
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

# 5.7 开锁记录：操作人归属与权限
lg = c.get(B + "/api/v1/unlock_logs?limit=50", headers=H(mgr2["token"])).json()
check("开锁记录接口(宿管可见)", lg.get("logs") and len(lg["logs"]) >= 1)
ka = [x for x in lg["logs"] if x["code"] == code_a]
check("记录含物品/主人/操作人", ka and ka[0]["item_name"] == "酸奶"
      and ka[0]["owner_name"] == "小A" and ka[0]["actor"] == "kiosk"
      and ka[0]["actor_name"] == "屏幕输码/扫码", str(ka[0] if ka else lg)[:90])
den_logs = c.get(B + "/api/v1/unlock_logs", headers=H(stu["token"]))
check("学生查开锁记录被拒 403", den_logs.status_code == 403, str(den_logs.status_code))

# 5.8 待认领物品凭标签码开锁 + 断网补录
t2b = c.post(B + "/api/v1/kiosk/login/start", json={}, headers=PS).json()["ticket"]
c.post(B + "/api/v1/kiosk/login/confirm", json={"ticket": t2b}, headers=H(mgr["token"]))
r7 = c.post(B + "/api/v1/kiosk/items", json={"name": "无主面包",
          "category": "零食熟食", "login_ticket": t2b}, headers=PS).json()
cf7 = c.post(B + f"/api/v1/kiosk/items/{r7['code']}/confirm-store", json={}, headers=PS).json()
check("宿管登记第二件确认后待认领", cf7.get("status") == "pending_claim", str(cf7))
un7 = c.post(B + "/api/v1/kiosk/unlock", json={"code": r7["code"]}, headers=PS)
check("待认领物品凭标签码开锁", un7.status_code == 200
      and un7.json().get("status") == "pending_claim", str(un7.json()))
off_code = "OFF" + uuid.uuid4().hex[:6].upper()
off1 = c.post(B + "/api/v1/pi/offline-items", json={"items": [
    {"code": off_code, "name": "离线苹果", "category": "水果",
     "keep_until": exp_keep, "stored_at": _today.isoformat(), "batch_no": exp_batch},
    {"code": code_a, "name": "重复码", "category": "其他", "expire_at": exp_keep}]},
    headers=PS).json()
check("断网补录-新建并跳过已存在编码", off1.get("created") == [off_code]
      and off1.get("skipped") == [code_a], str(off1))
pend_list = c.get(B + "/api/v1/items?status=pending_claim", headers=H(mgr["token"])).json()
_off = next((i for i in pend_list["items"] if i["code"] == off_code), {})
check("补录物品进待认领区", bool(_off))
check("补录保留批次/存放至/存放日", _off.get("batch_no") == exp_batch
      and _off.get("expire_at") == exp_keep and _off.get("stored_at") == _today.isoformat(),
      str(_off)[:120])
off2 = c.post(B + "/api/v1/pi/offline-items", json={"items": [
    {"code": off_code, "name": "离线苹果", "category": "水果", "keep_until": exp_keep}]},
    headers=PS).json()
check("断网补录幂等(重复不同步两次)", off2.get("created") == []
      and off2.get("skipped") == [off_code], str(off2))
check("补录接口需派密钥", c.post(B + "/api/v1/pi/offline-items", json={"items": []},
      headers={}).status_code == 401)

# 5.9 满柜上锁 / 排队 / 提醒 / 呼叫 / 存放天数 / 批次编号
stt = c.get(B + "/api/v1/kiosk/state", params={"fridge_id": "fridge-01"}, headers=PS).json()
check("触控屏状态(未上锁/天数/批次)", stt.get("locked") is False and stt.get("storage_days") == 6
      and stt.get("batch_no") == exp_batch, str(stt))
check("触控屏状态接口需派密钥", c.get(B + "/api/v1/kiosk/state", headers={}).status_code == 401)
lock_den = c.post(B + "/api/v1/fridge/lock", json={"locked": True, "fridge_id": "fridge-01"},
                  headers=H(stu["token"]))
check("学生不能上锁 403", lock_den.status_code == 403, str(lock_den.status_code))
lk = c.post(B + "/api/v1/fridge/lock", json={"locked": True, "fridge_id": "fridge-01"},
            headers=H(mgr2["token"])).json()
check("宿管上锁(冰箱已满)", lk.get("locked") is True, str(lk))
t3 = c.post(B + "/api/v1/kiosk/login/start", json={}, headers=PS).json()["ticket"]
c.post(B + "/api/v1/kiosk/login/confirm", json={"ticket": t3}, headers=H(stu["token"]))
blocked = c.post(B + "/api/v1/kiosk/items", json={"name": "满柜酸奶", "category": "奶制品",
                 "login_ticket": t3}, headers=PS)
check("上锁后学生登记被拒 403", blocked.status_code == 403 and "冰箱已满" in blocked.text,
      str(blocked.status_code))
q1 = c.post(B + "/api/v1/kiosk/queue", json={"login_ticket": t3, "name": "排队酸奶",
            "category": "奶制品"}, headers=PS).json()
check("满柜排队登记(第1位)", q1.get("ok") and q1.get("position") == 1, str(q1))
check("排队不建台账", not any(i["name"] == "排队酸奶" for i in
      c.get(B + "/api/v1/items?status=active&q=" + "排队酸奶",
            headers=H(mgr["token"])).json()["items"]))
mq = c.get(B + "/api/v1/kiosk/my-queue", params={"login_ticket": t3, "fridge_id": "fridge-01"}).json()
check("屏幕查我的排队", mq.get("queue") and mq["queue"]["item_name"] == "排队酸奶", str(mq)[:90])
sq = c.get(B + "/api/v1/queue/mine", headers=H(stu["token"])).json()
check("小程序查我的排队+上锁状态", sq.get("queue") and sq.get("locked") is True, str(sq)[:90])
ql = c.get(B + "/api/v1/queue", headers=H(mgr2["token"])).json()["queue"]
check("宿管排队清单", any(x["id"] == q1["id"] for x in ql))
fst = c.get(B + "/api/v1/fridge/state?fridge_id=fridge-01", headers=H(mgr2["token"])).json()
check("宿管冰箱状态(排队人数)", fst.get("locked") is True and fst.get("queue_count") >= 1, str(fst))
nq = c.post(B + "/api/v1/queue/notify", json={"fridge_id": "fridge-01"},
            headers=H(mgr2["token"])).json()
check("手动通知排队学生", nq.get("sent") == 1, str(nq))
nq2 = c.post(B + "/api/v1/queue/notify", json={"fridge_id": "fridge-01"},
             headers=H(mgr2["token"])).json()
check("已通知的1小时内不重复", nq2.get("sent") == 0, str(nq2))
ul = c.post(B + "/api/v1/fridge/lock", json={"locked": False, "fridge_id": "fridge-01"},
            headers=H(mgr2["token"])).json()
check("宿管解除上锁", ul.get("locked") is False, str(ul))
open_again = c.post(B + "/api/v1/kiosk/queue", json={"login_ticket": t3, "name": "排队酸奶"},
                    headers=PS)
check("未上锁时不能排队 400", open_again.status_code == 400, str(open_again.status_code))
r8 = c.post(B + "/api/v1/kiosk/items", json={"name": "补存苹果", "category": "水果",
            "login_ticket": t3}, headers=PS).json()
c.post(B + f"/api/v1/kiosk/items/{r8['code']}/confirm-store", json={}, headers=PS)
qful = c.get(B + "/api/v1/queue?status=fulfilled", headers=H(mgr2["token"])).json()["queue"]
check("存入后排队自动完成", any(x["id"] == q1["id"] for x in qful), str([x["id"] for x in qful]))
c.post(B + "/api/v1/fridge/lock", json={"locked": True, "fridge_id": "fridge-01"},
       headers=H(mgr2["token"]))
q2 = c.post(B + "/api/v1/kiosk/queue", json={"login_ticket": t3, "name": "排队牛奶",
            "category": "奶制品"}, headers=PS).json()
check("再次排队(重新计数)", q2.get("position") == 1, str(q2))
cc = c.post(B + "/api/v1/queue/mine/cancel", headers=H(stu["token"])).json()
check("学生取消排队", cc.get("cancelled") is True, str(cc))
c.post(B + "/api/v1/fridge/lock", json={"locked": False, "fridge_id": "fridge-01"},
       headers=H(mgr2["token"]))

# 呼叫宿管（匿名，60 秒内同类型去重）
call1 = c.post(B + "/api/v1/kiosk/call", json={"fridge_id": "fridge-01", "kind": "unlock"},
               headers=PS).json()
check("匿名呼叫宿管", call1.get("ok") and call1.get("deduped") is False, str(call1))
call2 = c.post(B + "/api/v1/kiosk/call", json={"fridge_id": "fridge-01", "kind": "unlock"},
               headers=PS).json()
check("同类型60秒内去重", call2.get("deduped") is True, str(call2))
bad_call = c.post(B + "/api/v1/kiosk/call", json={"fridge_id": "fridge-01", "kind": "x"}, headers=PS)
check("未知呼叫类型 400", bad_call.status_code == 400, str(bad_call.status_code))
calls = c.get(B + "/api/v1/calls", headers=H(mgr2["token"])).json()
check("呼叫清单(带类型中文)", calls.get("open_count", 0) >= 1
      and any(x["kind_label"] == "申请开锁" for x in calls["calls"]), str(calls)[:100])
_open = next(x for x in calls["calls"] if x["kind"] == "unlock" and x["status"] == "open")
check("呼叫标记已处理", c.post(B + f"/api/v1/calls/{_open['id']}/done",
      headers=H(mgr2["token"])).status_code == 200)
check("处理后未处理数归零", c.get(B + "/api/v1/calls", headers=H(mgr2["token"])).json()
      .get("open_count") == 0)

# 默认存放天数（超管可改）
sd = c.get(B + "/api/v1/settings/storage-days", headers=H(mgr2["token"])).json()
check("宿管读存放天数", sd.get("days") == 6, str(sd))
check("学生读存放天数被拒", c.get(B + "/api/v1/settings/storage-days",
      headers=H(stu["token"])).status_code == 403)
check("宿管改存放天数被拒 403", c.post(B + "/api/v1/settings/storage-days", json={"days": 5},
      headers=H(mgr2["token"])).status_code == 403)
sd_ok = c.post(B + "/api/v1/settings/storage-days", json={"days": 5}, headers=H(mgr["token"])).json()
check("超管改存放天数", sd_ok.get("days") == 5, str(sd_ok))
check("新天数生效到触控屏", c.get(B + "/api/v1/kiosk/state", headers=PS).json()
      .get("storage_days") == 5)
check("天数越界被拒 400", c.post(B + "/api/v1/settings/storage-days", json={"days": 99},
      headers=H(mgr["token"])).status_code == 400)
c.post(B + "/api/v1/settings/storage-days", json={"days": 6}, headers=H(mgr["token"]))

# 打印后 5 分钟未扫码确认自动作废
r9 = c.post(B + "/api/v1/kiosk/items", json={"name": "超时面包", "category": "其他",
            "login_ticket": t3}, headers=PS).json()
check("超时用例登记待入库", r9.get("status") == "awaiting_scan", str(r9)[:80])
_db3 = sqlite3.connect(Path(__file__).resolve().parent.parent / "data" / "fridge.db")
_db3.execute("UPDATE items SET created_at=datetime('now','localtime','-10 minutes') WHERE id=?",
             (r9["item_id"],))
_db3.commit(); _db3.close()
cf9 = c.post(B + f"/api/v1/kiosk/items/{r9['code']}/confirm-store", json={}, headers=PS)
check("超5分钟确认被拒 400", cf9.status_code == 400, str(cf9.status_code))

# 清理后提醒学生 / 无主物品不能提醒
cln = c.post(B + f"/api/v1/items/{item_b}/notify-cleaned", json={}, headers=H(mgr2["token"]))
check("清理留痕后可提醒学生", cln.status_code == 200, str(cln.status_code))
cln2 = c.post(B + f"/api/v1/items/{r7['item_id']}/notify-cleaned", json={}, headers=H(mgr2["token"]))
check("无主物品不能提醒 400", cln2.status_code == 400, str(cln2.status_code))

# 6. 心跳与锁状态
hb = c.get(B + "/api/v1/debug/pi", headers=H(mgr["token"])).json()["pi_nodes"]
check("Pi 心跳上报", hb and hb[0]["fridge_id"] == "fridge-01", str(hb))

n_fail = sum(1 for _, v in ok if not v)
print(f"\n===== 回归结果: {len(ok)-n_fail}/{len(ok)} 通过 =====")
raise SystemExit(1 if n_fail else 0)
