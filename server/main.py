import asyncio
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import Response
from pydantic import BaseModel

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # 抑制低光QR误检刷屏
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import colors
import db
import volume
from auth import Auth, check_pi_secret, make_jwt, sign_unlock
from wechat import WeChat

BASE = Path(__file__).resolve().parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))

# 云托管部署：敏感配置用环境变量注入，覆盖 config.yaml
for _sec, _key, _env in [("security", "jwt_secret", "JWT_SECRET"),
                         ("security", "pi_secret", "PI_SECRET"),
                         ("wechat", "appid", "WX_APPID"),
                         ("wechat", "secret", "WX_SECRET"),
                         ("wechat", "remind_template_id", "WX_TEMPLATE_ID")]:
    if os.environ.get(_env):
        CFG[_sec][_key] = os.environ[_env]
if os.environ.get("PRINT_PI_LOCAL") is not None:
    CFG["print"]["pi_local_api"] = os.environ["PRINT_PI_LOCAL"]
ALLOW_DB_TRANSFER = bool(os.environ.get("ALLOW_DB_TRANSFER"))

auth = Auth(CFG["security"]["jwt_secret"])
wx = WeChat(CFG["wechat"]["appid"], CFG["wechat"]["secret"],
            CFG["wechat"].get("remind_template_id", ""))
PI_SECRET = CFG["security"]["pi_secret"]
WARN_DAYS = CFG["rules"]["warn_days"]
UNLOCK_TTL = CFG["rules"]["unlock_valid_seconds"]
ADMIN_BOOTSTRAP_OPENIDS = set(CFG["bootstrap"].get("admin_openids") or [])
MANAGER_BOOTSTRAP_OPENIDS = set(CFG["bootstrap"].get("manager_openids") or [])
DEV_LOGIN = bool(CFG["wechat"].get("dev_login"))   # 仅内网调试：code 带 "dev:" 前缀时跳过微信验证
AUTO = CFG["rules"].get("auto_remind", {})
STOCK_KEEP_DAYS = CFG["rules"].get("stocktake_keep_days", 7)
ESCALATE_DAYS = int(CFG["rules"].get("overdue_escalate_days", 3))   # 超期N天进宿管"超期待处理"
PI_LOCAL = (CFG["print"].get("pi_local_api") or "").rstrip("/")
DEFAULT_CAPACITY_ML = int(CFG.get("fridge", {}).get("capacity_ml") or 150000)
PHOTO_DIR = Path(CFG["paths"].get("photo_dir") or (db.DATA_DIR / "photos"))
PHOTO_DIR.mkdir(parents=True, exist_ok=True)


async def auto_remind_loop():
    """每天固定批次时刻，自动向超期/临期物品的学生各推一条提醒。"""
    times = AUTO.get("times") or []
    fired = set()
    while True:
        now = datetime.now()
        key = (now.date().isoformat(), now.strftime("%H:%M"))
        if now.strftime("%H:%M") in times and key not in fired:
            fired.add(key)
            try:
                r = await do_remind(colors_want=["red", "yellow"], channel="auto")
                db.log_event("auto_remind", "system", f"{r['total']} 件")
            except Exception as e:
                print(f"[auto_remind] 失败: {e}")
        await asyncio.sleep(50)


async def overdue_escalate_loop():
    """每 6 小时扫一遍：超期 ESCALATE_DAYS 天仍在库的物品打上 escalated 标记，
    进入宿管「超期待处理」清单（只提醒人，不自动清理）。"""
    while True:
        try:
            cutoff = (datetime.now() - timedelta(days=ESCALATE_DAYS)).strftime("%Y-%m-%d")
            ids = [r["id"] for r in rows(
                "SELECT id FROM items WHERE status='active' AND escalated=0 AND expire_at < ?",
                (cutoff,))]
            if ids:
                c = db.conn()
                for i in ids:
                    c.execute("UPDATE items SET escalated=1 WHERE id=?", (i,))
                db.log_event("overdue_escalate", "system", f"{len(ids)} 件超期未处理，进入宿管待办")
        except Exception as e:
            print(f"[overdue_escalate] 失败: {e}")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    tasks = [asyncio.create_task(overdue_escalate_loop())]
    if AUTO.get("enabled"):
        tasks.append(asyncio.create_task(auto_remind_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="宿舍冰箱管理后端", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

me_dep = auth.user
manager_dep = auth.require_role("manager", "admin")   # 物品域：宿管+超管
admin_dep = auth.require_role("admin")                # 用户/日志/角色：仅超管


# ---------- 工具 ----------
def rows(sql, args=()):
    return [dict(r) for r in db.conn().execute(sql, args).fetchall()]


def gen_code():
    return uuid.uuid4().hex[:8].upper()


# ---------- 楼宇（注册选楼 / 宿管筛选 / 超管维护） ----------
@app.get("/api/v1/buildings")
def list_buildings():
    """公开：登录绑定页要选楼（彼时尚无 token）；顺带返回超管联系方式供学生页展示。"""
    return {"buildings": rows("SELECT * FROM buildings ORDER BY name"),
            "admin_contact": db.get_setting("admin_contact")}


class BuildingReq(BaseModel):
    name: str
    fridge_id: str


@app.post("/api/v1/admin/buildings")
def add_building(req: BuildingReq, admin: dict = Depends(admin_dep)):
    name, fridge = req.name.strip(), req.fridge_id.strip()
    if not name or not fridge:
        raise HTTPException(400, "楼宇名和冰箱编号不能为空")
    if db.conn().execute("SELECT name FROM buildings WHERE name=?", (name,)).fetchone():
        raise HTTPException(400, "楼宇已存在")
    db.conn().execute("INSERT INTO buildings(name, fridge_id) VALUES(?, ?)", (name, fridge))
    db.log_event("building_add", admin["openid"], f"{name} -> {fridge}")
    return {"ok": True}


@app.delete("/api/v1/admin/buildings/{name}")
def delete_building(name: str, admin: dict = Depends(admin_dep)):
    if not db.conn().execute("SELECT name FROM buildings WHERE name=?", (name,)).fetchone():
        raise HTTPException(404, "楼宇不存在")
    n = db.conn().execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    if n <= 1:
        raise HTTPException(400, "至少保留一栋楼")
    used = db.conn().execute(
        "SELECT COUNT(*) FROM users WHERE building=?", (name,)).fetchone()[0]
    if used:
        raise HTTPException(400, f"还有 {used} 名学生属于该楼，请先迁移后再删除")
    db.conn().execute("DELETE FROM buildings WHERE name=?", (name,))
    db.log_event("building_del", admin["openid"], name)
    return {"ok": True}


class ContactReq(BaseModel):
    value: str


@app.post("/api/v1/admin/contact")
def set_admin_contact(req: ContactReq, admin: dict = Depends(admin_dep)):
    db.set_setting("admin_contact", req.value.strip())
    db.log_event("contact_set", admin["openid"], req.value.strip() or "(清空)")
    return {"ok": True}


# ---------- 登录 / 账号 ----------
class LoginReq(BaseModel):
    code: str
    name: str = ""
    room: str = ""
    phone: str = ""
    building: str = ""


@app.post("/api/v1/wx/login")
async def login(req: LoginReq):
    name, room = req.name.strip(), req.room.strip()
    building = req.building.strip()
    if DEV_LOGIN and req.code.startswith("dev:"):
        openid = "dev_" + req.code[4:]
    else:
        try:
            openid = await wx.code2session(req.code)
        except Exception as e:
            raise HTTPException(400, str(e))
    c = db.conn()
    row = c.execute("SELECT * FROM users WHERE openid=?", (openid,)).fetchone()
    if building and not c.execute("SELECT name FROM buildings WHERE name=?", (building,)).fetchone():
        raise HTTPException(400, "楼宇不存在，请重新选择")
    # 楼宇首次补选可由学生自己完成（历史账号）；改楼只能超管操作（/users/{id}/building）
    bound = bool(row and row["name"] and row["room"] and row["building"])
    if not bound and not (name and room and building):
        raise HTTPException(400, "NEED_BIND:请填写姓名、房间号并选择楼宇")
    if row:
        if openid in ADMIN_BOOTSTRAP_OPENIDS:
            role = "admin"
        elif openid in MANAGER_BOOTSTRAP_OPENIDS and row["role"] == "student":
            role = "manager"
        else:
            role = row["role"]
        sets, args = [], []
        for field, val in [("name", name), ("room", room), ("phone", req.phone)]:
            if val:
                sets.append(f"{field}=?"); args.append(val)
        if building and not row["building"]:
            sets.append("building=?"); args.append(building)
        if role != row["role"]:
            sets.append("role=?"); args.append(role)
        if sets:
            args.append(openid)
            c.execute(f"UPDATE users SET {','.join(sets)} WHERE openid=?", args)
        user = dict(c.execute("SELECT * FROM users WHERE openid=?", (openid,)).fetchone())
    else:
        role = "admin" if openid in ADMIN_BOOTSTRAP_OPENIDS else (
            "manager" if openid in MANAGER_BOOTSTRAP_OPENIDS else "student")
        cur = c.execute("INSERT INTO users(openid,name,room,phone,role,building) VALUES(?,?,?,?,?,?)",
                        (openid, name, room, req.phone, role, building))
        user = dict(c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
        db.log_event("register", openid, f"role={role} name={name} room={room} building={building}")
    token = make_jwt(openid, role, CFG["security"]["jwt_secret"],
                     CFG["security"]["jwt_expire_hours"])
    return {"token": token, "role": role, "user": user}


@app.get("/api/v1/me")
def me(user: dict = Depends(me_dep)):
    return {"user": user, "role": user["role"]}


class ProfileReq(BaseModel):
    name: str = ""
    room: str = ""


@app.put("/api/v1/me")
def update_me(req: ProfileReq, user: dict = Depends(me_dep)):
    """学生自助只能改姓名/寝室号；楼宇错了要联系超管。"""
    name, room = req.name.strip(), req.room.strip()
    if not name or not room:
        raise HTTPException(400, "姓名和寝室号都不能为空")
    db.conn().execute("UPDATE users SET name=?, room=? WHERE id=?",
                      (name, room, user["id"]))
    db.log_event("profile_edit", user["openid"], f"{name} {room}")
    row = db.conn().execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"user": dict(row)}


# ---------- 物品登记（共用）----------
class ItemReq(BaseModel):
    name: str
    expire_at: str                       # YYYY-MM-DD
    category: str = ""
    quantity: int = 1
    fridge_id: str = "fridge-01"


UNCLAIMED_OPENID = "unclaimed"


def enforce_quota(user_id: int, role: str):
    """学生同时在库物品默认最多 5 件，超出需宿管/超管在账号里调额度。"""
    if role != "student":
        return
    c = db.conn()
    row = c.execute("SELECT quota FROM users WHERE id=?", (user_id,)).fetchone()
    quota = (row["quota"] if row and row["quota"] else 5)
    n = c.execute("SELECT COUNT(*) FROM items WHERE user_id=? AND status='active'",
                  (user_id,)).fetchone()[0]
    if n >= quota:
        raise HTTPException(400, f"存放已达上限 {quota} 件，请联系宿管增加额度")


def ensure_unclaimed_user() -> dict:
    """触控屏登记时物品先挂在这个系统账号下，学生在小程序认领后转移。"""
    c = db.conn()
    row = c.execute("SELECT * FROM users WHERE openid=?", (UNCLAIMED_OPENID,)).fetchone()
    if not row:
        c.execute("INSERT INTO users(openid,name,room,role) VALUES(?,?,?,?)",
                  (UNCLAIMED_OPENID, "未认领", "-", "student"))
        row = c.execute("SELECT * FROM users WHERE openid=?", (UNCLAIMED_OPENID,)).fetchone()
    return dict(row)


async def new_item(name: str, category: str, quantity: int, expire_at: str,
                   fridge_id: str, user_id: int, actor: str, status: str = "active") -> dict:
    if not name.strip():
        raise HTTPException(400, "物品名不能为空")
    try:
        expire_at = datetime.strptime(expire_at[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "到期日格式应为 YYYY-MM-DD")
    code = gen_code()
    c = db.conn()
    cur = c.execute(
        "INSERT INTO items(code,user_id,fridge_id,name,category,quantity,expire_at,status) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (code, user_id, fridge_id, name.strip(), category, quantity, expire_at, status))
    item_id = cur.lastrowid
    db.log_event("item_add", actor, f"{code} {name} 到期{expire_at}")
    print_status = "disabled"          # 未配置方案B本机派 API
    if PI_LOCAL:
        try:
            async with httpx.AsyncClient(timeout=5) as cl:
                rr = await cl.post(f"{PI_LOCAL}/api/print", json={
                    "code": code, "name": name, "expire_at": expire_at})
                print_status = "queued" if rr.status_code == 200 else "failed"
        except Exception:
            print_status = "pi_unreachable"
    return {"item_id": item_id, "code": code,
            "color": colors.color_of(expire_at, WARN_DAYS), "print": print_status}


# ---------- 学生：补录登记（主入口在触控屏）+ 我的物品 ----------
def _building_fridge(user: dict, fallback: str) -> str:
    """学生自助登记走自己楼宇的冰箱；未分楼时沿用请求里的 fridge_id。"""
    if user.get("building"):
        row = db.conn().execute("SELECT fridge_id FROM buildings WHERE name=?",
                                (user["building"],)).fetchone()
        if row:
            return row["fridge_id"]
    return fallback


@app.post("/api/v1/items")
async def create_item(req: ItemReq, user: dict = Depends(me_dep)):
    enforce_quota(user["id"], user["role"])
    return await new_item(req.name, req.category, req.quantity, req.expire_at,
                          _building_fridge(user, req.fridge_id), user["id"], user["openid"])


@app.post("/api/v1/items/{code}/claim")
async def claim_item(code: str, user: dict = Depends(me_dep)):
    """认领触控屏登记的未认领物品（扫码或输码）。"""
    row = db.conn().execute("SELECT * FROM items WHERE code=?", (code.strip(),)).fetchone()
    if not row:
        raise HTTPException(404, "标签编码不存在")
    if row["status"] in ("taken_out", "removed"):
        raise HTTPException(400, "物品已归档，无法认领")
    un = ensure_unclaimed_user()
    if row["user_id"] == user["id"]:
        return {"ok": True, "msg": "已是你的物品", "item_id": row["id"], "name": row["name"]}
    if row["user_id"] != un["id"]:
        owner = db.conn().execute("SELECT name FROM users WHERE id=?", (row["user_id"],)).fetchone()
        raise HTTPException(400, f"该物品已有主人（{owner['name'] if owner else '他人'}）")
    enforce_quota(user["id"], user["role"])
    db.conn().execute("UPDATE items SET user_id=?, status='active' WHERE id=?",
                      (user["id"], row["id"]))
    db.log_event("item_claim", user["openid"], f"{row['code']} {row['name']} 认领入个人台账")
    return {"ok": True, "item_id": row["id"], "name": row["name"]}


@app.get("/api/v1/items/mine")
def my_items(user: dict = Depends(me_dep)):
    items = rows("SELECT * FROM items WHERE user_id=? AND status='active'", (user["id"],))
    return {"items": volume.attach(colors.decorate(items, WARN_DAYS)), "warn_days": WARN_DAYS}


@app.get("/api/v1/items/{item_id}/unlock")
def unlock_status(item_id: int, user: dict = Depends(me_dep)):
    item = db.conn().execute("SELECT user_id FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        raise HTTPException(404, "物品不存在")
    if user["role"] == "student" and item["user_id"] != user["id"]:
        raise HTTPException(403, "只能查看自己的物品")
    row = db.conn().execute(
        "SELECT status,result_reason,acked_at FROM unlock_commands "
        "WHERE item_id=? ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
    return dict(row) if row else {"status": "none"}


@app.post("/api/v1/items/{code}/unlock")
def request_unlock(code: str, user: dict = Depends(me_dep)):
    """取出开锁：物品主人或宿管/超管凭标签编码发起，复用一次性令牌轮询链路。"""
    row = db.conn().execute("SELECT * FROM items WHERE code=?", (code.strip(),)).fetchone()
    if not row:
        raise HTTPException(404, "标签编码不存在")
    if row["status"] != "active":
        raise HTTPException(400, "物品不在库（已取出登记或已清理）")
    if user["role"] == "student" and row["user_id"] != user["id"]:
        raise HTTPException(403, "只能取出自己的物品")
    ts = int(time.time())
    token = sign_unlock(row["code"], ts, PI_SECRET)
    db.conn().execute(
        "INSERT INTO unlock_commands(item_id,fridge_id,code,ts,token) VALUES(?,?,?,?,?)",
        (row["id"], row["fridge_id"], row["code"], ts, token))
    db.log_event("takeout_unlock", user["openid"], f"{row['code']} {row['name']}")
    return {"ok": True, "item_id": row["id"], "code": row["code"]}


@app.get("/api/v1/items/{item_id}/qr")
def item_qr(item_id: int, user: dict = Depends(me_dep)):
    """学生手机屏幕出示物品二维码，对准冰箱触控屏摄像头即可取出。"""
    row = db.conn().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "物品不存在")
    if user["role"] == "student" and row["user_id"] != user["id"]:
        raise HTTPException(403, "只能出示自己的物品")
    import io
    import base64
    from qrcode import QRCode
    q = QRCode(box_size=10, border=2)
    q.add_data(row["code"])
    buf = io.BytesIO()
    q.make_image(fill_color="black", back_color="white").save(buf, "PNG")
    return {"code": row["code"], "name": row["name"],
            "png_base64": base64.b64encode(buf.getvalue()).decode()}


# ---------- 宿管：全部存放 + 手动提醒 ----------
@app.get("/api/v1/items")
def all_items(color: Optional[str] = Query(None), status: str = "active",
              q: Optional[str] = Query(None), sort: str = "urgency",
              building: Optional[str] = Query(None),
              escalated: Optional[int] = Query(None),
              _: dict = Depends(manager_dep)):
    where, args = "i.status=?", [status]
    if status == "archived":
        where, args = "i.status IN ('taken_out','removed')", []
    if escalated:
        where += " AND i.escalated=1"
    if building:
        where += " AND i.fridge_id IN (SELECT fridge_id FROM buildings WHERE name=?)"
        args.append(building)
    if q:
        where += " AND (i.name LIKE ? OR u.name LIKE ? OR u.room LIKE ?)"
        like = f"%{q}%"; args += [like, like, like]
    items = rows("SELECT i.*, u.name AS owner_name, u.room AS owner_room "
                 f"FROM items i JOIN users u ON u.id=i.user_id WHERE {where}", args)
    items = volume.attach(colors.decorate(items, WARN_DAYS))
    if color:
        items = [it for it in items if it["color"] == color]
    if sort == "expire":
        items.sort(key=lambda x: x.get("expire_at") or "9999-12-31")
    elif sort == "created":
        items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    elif sort == "volume":
        items.sort(key=lambda x: x.get("vol_est_ml") or 0, reverse=True)
    # sort=urgency：沿用 decorate 的 红→黄→绿、到期日升序
    esc_count = db.conn().execute(
        "SELECT COUNT(*) FROM items WHERE status='active' AND escalated=1").fetchone()[0]
    return {"items": items, "warn_days": WARN_DAYS, "escalated_count": esc_count,
            "counts": {c: sum(1 for it in items if it["color"] == c)
                       for c in ("red", "yellow", "green")}}


async def do_remind(item_ids: list[int] | None = None, colors_want: list[str] | None = None,
                    actor_id: int | None = None, channel: str = "subscribe",
                    building: str = "") -> dict:
    c = db.conn()
    fridge_sql = (" AND i.fridge_id IN (SELECT fridge_id FROM buildings WHERE name=?)",
                  [building]) if building else ("", [])
    if item_ids:
        ph = ",".join("?" * len(item_ids))
        sel = rows(f"SELECT i.*, u.openid AS owner_openid, u.name AS owner_name "
                   f"FROM items i JOIN users u ON u.id=i.user_id "
                   f"WHERE i.id IN ({ph}) AND i.status='active'{fridge_sql[0]}",
                   (*item_ids, *fridge_sql[1]))
    else:
        want = set(colors_want) or {"red", "yellow"}
        sel = rows("SELECT i.*, u.openid AS owner_openid, u.name AS owner_name "
                   "FROM items i JOIN users u ON u.id=i.user_id "
                   f"WHERE i.status='active'{fridge_sql[0]}", fridge_sql[1])
        sel = [it for it in sel if colors.color_of(it["expire_at"], WARN_DAYS) in want]
    results = []
    for it in sel:
        color = colors.color_of(it["expire_at"], WARN_DAYS)
        ok, msg = await wx.send_remind(it["owner_openid"], it["name"],
                                       it["expire_at"], colors.label(color))
        c.execute("INSERT INTO reminders(item_id,user_id,actor_id,channel,status,detail) "
                  "VALUES(?,?,?,?,?,?)",
                  (it["id"], it["user_id"], actor_id, channel,
                   "sent" if ok else "skipped", msg))
        results.append({"item_id": it["id"], "name": it["name"],
                        "owner": it["owner_name"], "color": color, "sent": ok, "msg": msg})
    return {"total": len(results), "sent": sum(1 for r in results if r["sent"]), "results": results}


class RemindReq(BaseModel):
    item_ids: list[int] = []
    colors: list[str] = []          # item_ids 为空时，按颜色批量提醒（默认红+黄）
    building: str = ""              # 只提醒某栋楼（宿管页"提醒本楼"）


@app.post("/api/v1/reminders")
async def send_reminders(req: RemindReq, actor: dict = Depends(manager_dep)):
    d = await do_remind(item_ids=req.item_ids or None, colors_want=req.colors,
                        actor_id=actor["id"], building=req.building.strip())
    db.log_event("remind", actor["openid"], f"批量提醒 {d['total']} 件")
    return d


# ---------- 物品操作：本人（标记取出/改到期日）+ 宿管（待认领流转/清理留痕）----------
class ItemActionReq(BaseModel):
    action: str                        # taken | rename | expire | pending_claim | removed | restore
    photo: str = ""                    # 处理照片 URL（如 /photos/xxx.jpg，可选）
    note: str = ""                     # 备注：谁处理、公示期等
    value: str = ""                    # action=expire 时：新的到期日 YYYY-MM-DD


@app.post("/api/v1/items/{item_id}/action")
def item_action(item_id: int, req: ItemActionReq, user: dict = Depends(me_dep)):
    c = db.conn()
    row = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "物品不存在")
    is_manager = user["role"] in ("manager", "admin")
    is_owner = row["user_id"] == user["id"]

    if req.action == "taken":          # 学生本人（或宿管代操作）：取出登记
        if not (is_owner or is_manager):
            raise HTTPException(403, "只能操作自己的物品")
        if row["status"] != "active":
            raise HTTPException(400, "物品不在库")
        c.execute("UPDATE items SET status='taken_out' WHERE id=?", (item_id,))
        c.execute("UPDATE items SET escalated=0 WHERE id=?", (item_id,))
        db.log_event("item_taken", user["openid"], f"{row['code']} {row['name']}")
        return {"ok": True, "status": "taken_out"}

    if req.action == "rename":         # 学生本人（或宿管）：改名
        if not (is_owner or is_manager):
            raise HTTPException(403, "只能操作自己的物品")
        new_name = req.value.strip()
        if not new_name or len(new_name) > 30:
            raise HTTPException(400, "物品名需为 1~30 字")
        c.execute("UPDATE items SET name=? WHERE id=?", (new_name, item_id))
        db.log_event("item_rename", user["openid"],
                     f"{row['code']} {row['name']} -> {new_name}")
        return {"ok": True, "name": new_name}

    if req.action == "expire":         # 到期日只有宿管/超管能改（学生取出后重新存放即可）
        if not is_manager:
            raise HTTPException(403, "到期日仅宿管可修改")
        try:
            new_date = datetime.strptime(req.value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "到期日格式应为 YYYY-MM-DD")
        c.execute("UPDATE items SET expire_at=? WHERE id=?", (new_date, item_id))
        c.execute("UPDATE items SET escalated=0 WHERE id=?", (item_id,))
        db.log_event("item_expire_edit", user["openid"],
                     f"{row['code']} {row['name']} {row['expire_at']} -> {new_date}")
        return {"ok": True, "expire_at": new_date}

    if not is_manager:
        raise HTTPException(403, "需要宿管权限")
    target = {"pending_claim": "pending_claim", "removed": "removed", "restore": "active"}
    if req.action not in target:
        raise HTTPException(400, "非法操作")
    c.execute("UPDATE items SET status=? WHERE id=?", (target[req.action], item_id))
    c.execute("UPDATE items SET escalated=0 WHERE id=?", (item_id,))
    db.log_event(f"item_{req.action}", user["openid"],
                 f"{row['code']} {row['name']} photo={req.photo} note={req.note}")
    return {"ok": True, "status": target[req.action]}


# ---------- 宿管：容量与体积修正 ----------
def _capacity_key(fridge_id: str) -> str:
    return f"capacity_ml:{fridge_id}"


@app.get("/api/v1/capacity")
def get_capacity(fridge_id: str = Query("fridge-01"), user: dict = Depends(me_dep)):
    ph = ",".join("?" * len(volume.STOCK_STATUSES))
    items = rows(f"SELECT * FROM items WHERE status IN ({ph}) AND fridge_id=?",
                 (*volume.STOCK_STATUSES, fridge_id))
    used = sum(volume.est_ml(it) for it in items)
    cap = int(db.get_setting(_capacity_key(fridge_id),
                             db.get_setting("capacity_ml", str(DEFAULT_CAPACITY_ML))))
    return {"capacity_ml": cap, "used_ml": used, "item_count": len(items),
            "fridge_id": fridge_id,
            "util_pct": round(used * 100.0 / cap, 1) if cap else 0.0}


class CapacityReq(BaseModel):
    capacity_ml: int
    fridge_id: str = "fridge-01"


@app.post("/api/v1/capacity")
def set_capacity(req: CapacityReq, user: dict = Depends(manager_dep)):
    if not (1000 <= req.capacity_ml <= 2_000_000):
        raise HTTPException(400, "容量需在 1L~2000L 之间")
    db.set_setting(_capacity_key(req.fridge_id), req.capacity_ml)
    db.log_event("capacity_set", user["openid"], f"{req.fridge_id} -> {req.capacity_ml} mL")
    return {"ok": True, "capacity_ml": req.capacity_ml, "fridge_id": req.fridge_id}


class VolumeReq(BaseModel):
    volume_ml: int                        # 0 = 恢复按类别估算


@app.post("/api/v1/items/{item_id}/volume")
def set_volume(item_id: int, req: VolumeReq, user: dict = Depends(manager_dep)):
    if not (0 <= req.volume_ml <= 100_000):
        raise HTTPException(400, "单件体积请在 0~100L 之间（0=按类别估算）")
    row = db.conn().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "物品不存在")
    db.conn().execute("UPDATE items SET volume_ml=? WHERE id=?",
                      (req.volume_ml or None, item_id))
    db.log_event("volume_fix", user["openid"],
                 f"{row['code']} {row['name']} -> {req.volume_ml} mL")
    return {"ok": True}


# ---------- 宿管：拍照批量盘点 ----------
def _store_photo(name: str, img):
    """照片双写：MySQL（云端持久，发布不丢）+ 本地文件（派上/开发环境直读）。"""
    try:
        cv2.imwrite(str(PHOTO_DIR / name), img)
    except Exception:
        pass
    ok, buf = cv2.imencode(".jpg", img)
    if ok:
        db.save_photo(name, buf.tobytes())


_PHOTO_NAME_RE = re.compile(r"^[\w.\-]{1,128}$")


@app.get("/photos/{name}")
def get_photo(name: str):
    if not _PHOTO_NAME_RE.match(name):
        raise HTTPException(400, "非法照片名")
    blob = db.load_photo(name)
    if blob is None:
        f = PHOTO_DIR / name
        if f.exists():
            blob = f.read_bytes()
    if blob is None:
        raise HTTPException(404, "照片不存在或已过期清理")
    return Response(content=blob, media_type="image/jpeg")


def _stocktake_core(img, name: str, fridge_id: str, user: dict) -> dict:
    detector = cv2.QRCodeDetector()

    def decode_all(im):
        try:
            ok, decoded, _, _ = detector.detectAndDecodeMulti(im)
            return {c.strip() for c in decoded if ok and c and c.strip()}
        except Exception as e:
            print(f"[stocktake] QR 解码异常: {e}")
            return set()

    # 两遍扫描：原图 + 2x 放大图，合并结果（冰箱实拍中小而模糊的标签更易命中）
    codes = decode_all(img)
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    codes |= decode_all(big)

    sql = ("SELECT i.*, u.name AS owner_name, u.room AS owner_room "
           "FROM items i JOIN users u ON u.id=i.user_id WHERE i.status='active'")
    args: list = []
    if fridge_id:
        sql += " AND i.fridge_id=?"
        args.append(fridge_id)
    active = colors.decorate(rows(sql, args), WARN_DAYS)
    found = [it for it in active if it["code"] in codes]
    missing = [it for it in active if it["code"] not in codes]
    unknown = codes - {it["code"] for it in active}   # 照片里有但台账没有 -> 无登记物品
    db.log_event("stocktake", user["openid"],
                 f"识别{len(found)} 未见{len(missing)} 无登记{len(unknown)} 照片{name}")

    # 顺带清理过期盘点照片（库+本地文件）
    db.delete_old_photos(STOCK_KEEP_DAYS)
    cutoff = time.time() - STOCK_KEEP_DAYS * 86400
    for f in PHOTO_DIR.glob("stocktake_*"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)

    def brief(it):
        return {k: it[k] for k in ("id", "code", "name", "owner_name", "owner_room",
                                   "expire_at", "color", "color_label")}
    return {"photo_url": f"/photos/{name}", "total_in_photo_qr": len(codes),
            "found": [brief(it) for it in found],
            "missing": [brief(it) for it in missing],
            "unknown_codes": sorted(unknown)}


@app.post("/api/v1/stocktake")
async def stocktake(file: UploadFile = File(...), fridge_id: str = Form(""),
                    user: dict = Depends(manager_dep)):
    raw = np.frombuffer(await file.read(), np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "图片无法解析")
    name = f"stocktake_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
    _store_photo(name, img)
    return _stocktake_core(img, name, fridge_id, user)


class StocktakeB64Req(BaseModel):
    image_b64: str
    fridge_id: str = ""


@app.post("/api/v1/stocktake_b64")
async def stocktake_b64(req: StocktakeB64Req, user: dict = Depends(manager_dep)):
    """小程序云托管 callContainer 通道：图片走 base64 JSON（不支持 multipart 上传）。"""
    import base64
    try:
        raw = base64.b64decode(req.image_b64)
    except Exception:
        raise HTTPException(400, "图片数据无效")
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "图片过大或为空")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "图片无法解析")
    name = f"stocktake_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
    _store_photo(name, img)
    return _stocktake_core(img, name, req.fridge_id, user)


# ---------- 账号管理（列表对宿管开放：按楼筛学生、改姓名寝室；角色/改楼/额度仍分权） ----------
@app.get("/api/v1/users")
def list_users(q: Optional[str] = Query(None), building: Optional[str] = Query(None),
               _: dict = Depends(manager_dep)):
    sql, args = "SELECT * FROM users WHERE openid<>?", [UNCLAIMED_OPENID]
    if building:
        sql += " AND building=?"; args.append(building)
    if q:
        sql += " AND (name LIKE ? OR room LIKE ?)"
        like = f"%{q}%"; args += [like, like]
    return {"users": rows(sql + " ORDER BY id", args)}


class UserProfileReq(BaseModel):
    name: str
    room: str


@app.post("/api/v1/users/{user_id}/profile")
def set_user_profile(user_id: int, req: UserProfileReq,
                     mgr: dict = Depends(manager_dep)):
    """宿管代学生更正姓名/寝室号（改楼不在这里，超管专用）。"""
    name, room = req.name.strip(), req.room.strip()
    if not name or not room:
        raise HTTPException(400, "姓名和寝室号都不能为空")
    row = db.conn().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    db.conn().execute("UPDATE users SET name=?, room=? WHERE id=?", (name, room, user_id))
    db.log_event("user_profile_fix", mgr["openid"],
                 f"user#{user_id} -> {name} {room}")
    return {"ok": True}


class UserBuildingReq(BaseModel):
    building: str


@app.post("/api/v1/users/{user_id}/building")
def set_user_building(user_id: int, req: UserBuildingReq,
                      admin: dict = Depends(admin_dep)):
    """学生注册选错楼的唯一更正通道（联系超管操作）。只改归属，已在库物品留在原冰箱。"""
    building = req.building.strip()
    if not db.conn().execute("SELECT name FROM buildings WHERE name=?", (building,)).fetchone():
        raise HTTPException(400, "楼宇不存在")
    row = db.conn().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    db.conn().execute("UPDATE users SET building=? WHERE id=?", (building, user_id))
    db.log_event("user_building_change", admin["openid"],
                 f"user#{user_id} {row['name']} {row['building'] or '(未分楼)'} -> {building}")
    return {"ok": True}


class RoleReq(BaseModel):
    role: str


@app.post("/api/v1/users/{user_id}/role")
def set_role(user_id: int, req: RoleReq, admin: dict = Depends(admin_dep)):
    if req.role not in ("student", "manager", "admin"):
        raise HTTPException(400, "非法角色")
    if user_id == admin["id"]:
        raise HTTPException(400, "不能修改自己的角色（防止自锁）")
    if not db.conn().execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
        raise HTTPException(404, "用户不存在")
    db.conn().execute("UPDATE users SET role=? WHERE id=?", (req.role, user_id))
    db.log_event("role_change", admin["openid"], f"user#{user_id} -> {req.role}")
    return {"ok": True}


class QuotaReq(BaseModel):
    quota: int


@app.post("/api/v1/users/{user_id}/quota")
def set_quota(user_id: int, req: QuotaReq, user: dict = Depends(manager_dep)):
    if not (1 <= req.quota <= 50):
        raise HTTPException(400, "额度需在 1~50 件之间")
    if not db.conn().execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
        raise HTTPException(404, "用户不存在")
    db.conn().execute("UPDATE users SET quota=? WHERE id=?", (req.quota, user_id))
    db.log_event("quota_set", user["openid"], f"user#{user_id} -> {req.quota} 件")
    return {"ok": True, "quota": req.quota}


@app.get("/api/v1/debug/pi")
def debug_pi(_: dict = Depends(admin_dep)):
    nodes = rows("SELECT * FROM pi_nodes ORDER BY last_seen DESC")
    cmds = rows("SELECT * FROM unlock_commands ORDER BY id DESC LIMIT 50")
    return {"pi_nodes": nodes, "unlock_commands": cmds}


@app.get("/api/v1/debug/events")
def debug_events(limit: int = 50, _: dict = Depends(admin_dep)):
    return {"events": rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)),
            "reminders": rows("SELECT r.*, i.name AS item_name FROM reminders r "
                              "LEFT JOIN items i ON i.id=r.item_id ORDER BY r.id DESC LIMIT 30")}


# ---------- 树莓派对接（共享密钥头 X-Pi-Secret）----------
def _pi_auth(x_pi_secret: Optional[str] = Header(None)):
    check_pi_secret(x_pi_secret, PI_SECRET)


# ---------- 触控屏扫码登录（一次性 ticket，小程序扫码确认）----------
LOGIN_TICKETS: dict[str, dict] = {}
LOGIN_PENDING_TTL = 120      # 出码后未扫码的有效期（秒）
LOGIN_SESSION_TTL = 600      # 确认后屏幕会话有效期（秒）


def _ticket_user(ticket: str) -> Optional[dict]:
    t = LOGIN_TICKETS.get(ticket or "")
    if not t or t["status"] != "confirmed" or not t["user"]:
        return None
    if time.time() - t["confirmed_at"] > LOGIN_SESSION_TTL:
        LOGIN_TICKETS.pop(ticket, None)
        return None
    return t["user"]


@app.post("/api/v1/kiosk/login/start")
def login_start(_: None = Depends(_pi_auth)):
    ticket = uuid.uuid4().hex
    now = time.time()
    for k in [k for k, v in LOGIN_TICKETS.items() if now - v["created"] > LOGIN_SESSION_TTL * 2]:
        LOGIN_TICKETS.pop(k, None)
    LOGIN_TICKETS[ticket] = {"status": "pending", "user": None,
                             "created": now, "confirmed_at": 0.0}
    return {"ticket": ticket, "qr_text": f"fridge-login:{ticket}",
            "expires_in": LOGIN_PENDING_TTL}


@app.get("/api/v1/kiosk/login/qr")
def login_qr(ticket: str):
    import io
    from fastapi.responses import Response
    from qrcode import QRCode
    if ticket not in LOGIN_TICKETS:
        raise HTTPException(404, "登录码不存在")
    q = QRCode(box_size=10, border=2)
    q.add_data(f"fridge-login:{ticket}")
    buf = io.BytesIO()
    q.make_image(fill_color="#101820", back_color="white").save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


class TicketReq(BaseModel):
    ticket: str


@app.post("/api/v1/kiosk/login/confirm")
def login_confirm(req: TicketReq, user: dict = Depends(me_dep)):
    t = LOGIN_TICKETS.get(req.ticket)
    if not t:
        raise HTTPException(400, "登录码无效，请在屏幕上重新获取")
    if t["status"] == "pending" and time.time() - t["created"] > LOGIN_PENDING_TTL:
        raise HTTPException(400, "登录码已过期，请在屏幕上重新获取")
    if t["status"] == "confirmed" and t["user"]["id"] != user["id"]:
        raise HTTPException(400, "该登录码已被他人使用")
    t.update(status="confirmed", confirmed_at=time.time(),
             user={"id": user["id"], "openid": user["openid"],
                   "name": user["name"], "room": user["room"]})
    db.log_event("kiosk_login", user["openid"], f"{user['name']} 屏幕登录")
    return {"ok": True, "name": user["name"]}


@app.get("/api/v1/kiosk/login/status")
def login_status(ticket: str):
    t = LOGIN_TICKETS.get(ticket)
    if not t:
        return {"status": "expired"}
    now = time.time()
    if t["status"] == "pending" and now - t["created"] > LOGIN_PENDING_TTL:
        return {"status": "expired"}
    if t["status"] == "confirmed" and now - t["confirmed_at"] > LOGIN_SESSION_TTL:
        return {"status": "expired"}
    u = t["user"]
    return {"status": t["status"],
            "user": {"name": u["name"], "room": u["room"]} if t["status"] == "confirmed" else None}


@app.get("/api/v1/admin/export-rows")
def admin_export_rows(_: None = Depends(_pi_auth)):
    """一次性数据迁移：逐表导出行数据 JSON（SQLite/MySQL 通用，仅迁移通道开启时可用）。"""
    if not ALLOW_DB_TRANSFER:
        raise HTTPException(403, "迁移通道未开启")
    return {"tables": {t: rows(f"SELECT * FROM {t}") for t in db.TABLES}}


@app.post("/api/v1/admin/import-rows")
async def admin_import_rows(req: Request):
    """一次性数据迁移：清空目标库各表后按行导入（含原主键 id）。"""
    if not ALLOW_DB_TRANSFER:
        raise HTTPException(403, "迁移通道未开启")
    check_pi_secret(req.headers.get("X-Pi-Secret"), PI_SECRET)
    body = await req.json()
    tables = body.get("tables") or {}
    c = db.conn()
    counts = {}
    for t in db.TABLES:
        rs = tables.get(t) or []
        c.execute(f"DELETE FROM {t}")
        for r in rs:
            cols = list(r.keys())
            # 反引号包裹列名：key 等 MySQL 保留字需要，SQLite 也接受反引号
            col_sql = ",".join(f"`{k}`" for k in cols)
            c.execute(f"INSERT INTO {t}({col_sql}) VALUES({','.join('?' * len(cols))})",
                      tuple(r[k] for k in cols))
        counts[t] = len(rs)
    return {"ok": True, "counts": counts}


class KioskUnlockReq(BaseModel):
    code: str


class KioskItemReq(BaseModel):
    name: str
    category: str = "其他"
    expire_at: str
    quantity: int = 1
    fridge_id: str = "fridge-01"
    login_ticket: str = ""


@app.post("/api/v1/kiosk/items")
async def kiosk_create_item(req: KioskItemReq, _: None = Depends(_pi_auth)):
    """触控屏拍照登记：物品归属=屏幕扫码登录者；建台账+打印标签+直接下发开锁。"""
    user = _ticket_user(req.login_ticket)
    if not user:
        raise HTTPException(400, "请先在屏幕上扫码登录后再登记")
    role_row = db.conn().execute("SELECT role FROM users WHERE id=?", (user["id"],)).fetchone()
    role = role_row["role"] if role_row else "student"
    if role in ("manager", "admin"):
        # 宿管/超管登记的多为无主物品：挂"未认领"名下，直接进待认领区，等学生扫码认领
        owner = ensure_unclaimed_user()
        d = await new_item(req.name, req.category, req.quantity, req.expire_at,
                           req.fridge_id, owner["id"], f"kiosk-mgr:{user['name']}",
                           status="pending_claim")
        d["status"] = "pending_claim"
    else:
        enforce_quota(user["id"], role)
        d = await new_item(req.name, req.category, req.quantity, req.expire_at,
                           req.fridge_id, user["id"], f"kiosk:{user['name']}")
    ts = int(time.time())
    token = sign_unlock(d["code"], ts, PI_SECRET)
    db.conn().execute(
        "INSERT INTO unlock_commands(item_id,fridge_id,code,ts,token) VALUES(?,?,?,?,?)",
        (d["item_id"], req.fridge_id, d["code"], ts, token))
    db.log_event("kiosk_add", user["openid"], f"{d['code']} {req.name} 开锁存入")
    return d


class TakeoutDoneReq(BaseModel):
    code: str
    taken: bool


@app.post("/api/v1/kiosk/takeout-done")
def kiosk_takeout_done(req: TakeoutDoneReq, _: None = Depends(_pi_auth)):
    """触控屏取出后的确认页：taken=true 归档为已取出；false 表示继续存放，台账不动。"""
    row = db.conn().execute("SELECT * FROM items WHERE code=?", (req.code.strip(),)).fetchone()
    if not row:
        raise HTTPException(404, "标签编码不存在")
    if row["status"] not in ("active", "pending_claim"):
        return {"ok": True, "status": row["status"]}
    if req.taken:
        db.conn().execute("UPDATE items SET status='taken_out' WHERE id=?", (row["id"],))
        db.log_event("kiosk_takeout_done", "kiosk", f"{row['code']} {row['name']} 确认已取出")
        return {"ok": True, "status": "taken_out"}
    db.log_event("kiosk_keep_storing", "kiosk", f"{row['code']} {row['name']} 继续存放")
    return {"ok": True, "status": row["status"]}


@app.get("/api/v1/kiosk/my-items")
def kiosk_my_items(login_ticket: str):
    """屏幕取出屏的「我的物品」列表（凭已确认的登录 ticket）。"""
    user = _ticket_user(login_ticket)
    if not user:
        raise HTTPException(401, "登录已失效，请重新扫码")
    items = rows("SELECT * FROM items WHERE user_id=? AND status='active'", (user["id"],))
    return {"items": volume.attach(colors.decorate(items, WARN_DAYS)), "user": user}


@app.get("/api/v1/admin/storage-probe")
def storage_probe(_: None = Depends(_pi_auth)):
    """诊断：/app/data 是否独立挂载（比较设备号 + 读 mounts）。"""
    import os
    data_st = os.stat(db.DATA_DIR)
    root_st = os.stat("/")
    mounts = [l for l in open("/proc/mounts").read().splitlines()
              if "data" in l or "nfs" in l or "cpfs" in l or "cfs" in l]
    return {"data_dir": str(db.DATA_DIR), "same_device_as_root": data_st.st_dev == root_st.st_dev,
            "db_exists": db.DB_PATH.exists(), "mounts": mounts[:6]}


@app.post("/api/v1/kiosk/unlock")
def kiosk_unlock(req: KioskUnlockReq, _: None = Depends(_pi_auth)):
    """触控屏取出：本机设备即 trusted（面前有人输码/扫标签），复用一次性令牌轮询链路。"""
    row = db.conn().execute("SELECT * FROM items WHERE code=?", (req.code.strip(),)).fetchone()
    if not row:
        raise HTTPException(404, "标签编码不存在")
    if row["status"] != "active":
        raise HTTPException(400, "物品不在库（已取出登记或已清理）")
    ts = int(time.time())
    token = sign_unlock(row["code"], ts, PI_SECRET)
    db.conn().execute(
        "INSERT INTO unlock_commands(item_id,fridge_id,code,ts,token) VALUES(?,?,?,?,?)",
        (row["id"], row["fridge_id"], row["code"], ts, token))
    db.log_event("kiosk_unlock", "kiosk", f"{row['code']} {row['name']}")
    return {"ok": True, "code": row["code"], "name": row["name"]}


@app.get("/api/v1/pi/pending-unlocks")
def pending_unlocks(fridge_id: str, _: None = Depends(_pi_auth)):
    c = db.conn()
    now = int(time.time())
    # 超时未被领取/未回执的指令置为 expired
    c.execute("UPDATE unlock_commands SET status='expired' "
              "WHERE status='pending' AND ts < ?", (now - UNLOCK_TTL,))
    c.execute("UPDATE unlock_commands SET status='expired' "
              "WHERE status='dispatched' AND ts < ?", (now - UNLOCK_TTL,))
    pend = rows("SELECT * FROM unlock_commands WHERE fridge_id=? AND status='pending' "
                "ORDER BY id", (fridge_id,))
    ids = [p["id"] for p in pend]
    if ids:
        c.execute(f"UPDATE unlock_commands SET status='dispatched' "
                  f"WHERE id IN ({','.join('?' * len(ids))})", ids)
    return {"commands": [{"code": p["code"], "ts": p["ts"], "token": p["token"]} for p in pend]}


class UnlockResult(BaseModel):
    code: str
    status: str                 # unlocked | rejected
    reason: str = ""


@app.post("/api/v1/pi/unlock-result")
def unlock_result(req: UnlockResult, _: None = Depends(_pi_auth)):
    st = "unlocked" if req.status == "unlocked" else "rejected"
    db.conn().execute("UPDATE unlock_commands SET status=?, result_reason=?, "
                      "acked_at=datetime('now','localtime') WHERE code=?",
                      (st, req.reason, req.code))
    db.log_event("unlock_ack", req.code, f"{st} {req.reason}")
    return {"ok": True}


class Heartbeat(BaseModel):
    fridge_id: str
    is_locked: bool = True
    mock: bool = False
    version: str = ""
    extra: dict = {}


@app.post("/api/v1/pi/heartbeat")
def heartbeat(req: Heartbeat, _: None = Depends(_pi_auth)):
    import json
    db.conn().execute(
        "INSERT INTO pi_nodes(fridge_id,is_locked,mock,version,payload,last_seen) "
        "VALUES(?,?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(fridge_id) DO UPDATE SET is_locked=excluded.is_locked, "
        "mock=excluded.mock, version=excluded.version, payload=excluded.payload, "
        "last_seen=excluded.last_seen",
        (req.fridge_id, int(req.is_locked), int(req.mock), req.version,
         json.dumps(req.extra, ensure_ascii=False)))
    return {"ok": True}


@app.get("/api/v1/export/items.csv")
def export_items_csv(status: str = "all", building: str = "",
                     user: dict = Depends(manager_dep)):
    """台账导出（Excel 可直接打开）。status=all|active|pending_claim|archived。"""
    import csv as _csv
    import io
    sql = ("SELECT i.*, u.name AS owner_name, u.room AS owner_room, u.building AS owner_building "
           "FROM items i JOIN users u ON u.id=i.user_id WHERE 1=1")
    args: list = []
    if building:
        sql += " AND i.fridge_id IN (SELECT fridge_id FROM buildings WHERE name=?)"
        args.append(building)
    if status == "active":
        sql += " AND i.status='active'"
    elif status == "pending_claim":
        sql += " AND i.status='pending_claim'"
    elif status == "archived":
        sql += " AND i.status IN ('taken_out','removed')"
    sql += " ORDER BY i.id"
    st_label = {"active": "在库", "pending_claim": "待认领",
                "taken_out": "已取出", "removed": "已清理"}
    items = volume.attach(colors.decorate(rows(sql, args), WARN_DAYS))
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["编码", "物品名", "类别", "数量", "主人", "寝室", "楼宇", "冰箱",
                "到期日", "状态", "紧急度", "估算体积(L)", "登记时间"])
    for it in items:
        w.writerow([it["code"], it["name"], it["category"], it["quantity"],
                    it["owner_name"], it["owner_room"], it.get("owner_building") or "未分楼",
                    it["fridge_id"], it["expire_at"], st_label.get(it["status"], it["status"]),
                    it.get("color_label", ""), round((it.get("vol_est_ml") or 0) / 1000, 1),
                    it["created_at"]])
    db.log_event("export_csv", user["openid"], f"status={status} building={building or '全部'}")
    return Response(content="\ufeff" + buf.getvalue(),
                    media_type="text/csv; charset=utf-8")


@app.get("/api/v1/notifications/mine")
def my_notifications(user: dict = Depends(me_dep)):
    """订阅兜底：微信推送历史（含未送达原因），学生页可直接查看，不依赖订阅额度。"""
    rs = rows("SELECT r.created_at, r.status, r.channel, r.detail, "
              "i.name AS item_name, i.expire_at "
              "FROM reminders r LEFT JOIN items i ON i.id=r.item_id "
              "WHERE r.user_id=? ORDER BY r.id DESC LIMIT 20", (user["id"],))
    return {"notifications": rs}


@app.get("/api/v1/admin/backup-rows")
def admin_backup_rows(_: None = Depends(_pi_auth)):
    """每日冷备通道：仅 X-Pi-Secret 鉴权（不受迁移开关限制），供树莓派 cron 拉取存档。"""
    return {"ts": int(time.time()),
            "tables": {t: rows(f"SELECT * FROM {t}") for t in db.TABLES}}


@app.get("/healthz")
def healthz():
    return {"ok": True, "build": "ops-2026-10-10-1",
            "backend": "mysql" if db.USING_MYSQL else "sqlite"}


@app.get("/api/v1/admin/env-probe")
def env_probe(x_pi_secret: Optional[str] = Header(None)):
    """临时诊断：确认云托管出口代理/CA 环境（仅 Pi 密钥可用）。"""
    check_pi_secret(x_pi_secret, PI_SECRET)
    import ssl
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy",
            "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
    return {"env": {k: os.environ.get(k) for k in keys if os.environ.get(k)},
            "default_ca_paths": ssl.get_default_verify_paths()._asdict()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG["server"]["host"], port=CFG["server"]["port"])
