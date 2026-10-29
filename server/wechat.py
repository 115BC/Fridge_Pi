"""微信小程序服务端能力：登录换 openid、access_token 缓存、订阅消息推送。"""
import os
import time
import httpx

API = "https://api.weixin.qq.com"
_token_cache = {"token": "", "exp": 0.0}


def _on_cloud_run() -> bool:
    """云托管会注入 COS_*/MYSQL_ADDRESS 等特征变量；其微信 API 内网直连用私有 CA。"""
    return bool(os.environ.get("COS_BUCKET") or os.environ.get("MYSQL_ADDRESS")
                or os.environ.get("TENCENT_CLOUD_RUN"))


def _client(timeout: int = 8) -> httpx.AsyncClient:
    """云托管内 api.weixin.qq.com 走内网直连(私有CA自签证书)，命中时放宽 TLS 校验；
    本地/树莓派等普通环境保持严格校验。"""
    if _on_cloud_run():
        return httpx.AsyncClient(timeout=timeout, trust_env=False, verify=False)
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


class WeChat:
    def __init__(self, appid: str, secret: str, template_id: str = ""):
        self.appid = appid
        self.secret = secret
        self.template_id = template_id
        self.dev_mode = not (appid and secret)

    async def code2session(self, code: str) -> str:
        """用 wx.login 的 code 换 openid。开发模式直接把 code 当 openid。"""
        if self.dev_mode:
            return f"dev_{code}"
        async with _client(8) as cl:
            r = await cl.get(f"{API}/sns/jscode2session", params={
                "appid": self.appid, "secret": self.secret,
                "js_code": code, "grant_type": "authorization_code"})
            d = r.json()
        if "openid" not in d:
            raise RuntimeError(f"code2session 失败: {d}")
        return d["openid"]

    async def access_token(self) -> str:
        if self.dev_mode:
            return ""
        if _token_cache["token"] and time.time() < _token_cache["exp"]:
            return _token_cache["token"]
        async with _client(8) as cl:
            r = await cl.get(f"{API}/cgi-bin/token", params={
                "grant_type": "client_credential",
                "appid": self.appid, "secret": self.secret})
            d = r.json()
        if "access_token" not in d:
            raise RuntimeError(f"获取 access_token 失败: {d}")
        _token_cache.update(token=d["access_token"], exp=time.time() + d.get("expires_in", 7200) - 300)
        return _token_cache["token"]

    async def _send(self, openid: str, thing1: str, time2: str, number3: str,
                    thing5: str, page: str = "pages/student/student") -> tuple[bool, str]:
        """模板字段：thing1 名称 / time2 日期 / number3 数字 / thing5 提示。"""
        if self.dev_mode or not self.template_id:
            return False, f"[dev] 应向 {openid} 推送：{thing1} {time2} {thing5}"
        token = await self.access_token()
        data = {
            "thing1": {"value": (thing1 or "-")[:20]},
            "time2": {"value": (time2 or "-")[:20]},
            "number3": {"value": (number3 or "0")[:10]},
            "thing5": {"value": (thing5 or "-")[:20]},
        }
        async with _client(8) as cl:
            r = await cl.post(f"{API}/cgi-bin/message/subscribe/send",
                              params={"access_token": token},
                              json={"touser": openid, "template_id": self.template_id,
                                    "page": page, "data": data})
            d = r.json()
        ok = d.get("errcode") == 0
        return ok, ("已推送" if ok else f"推送失败: {d}")

    async def send_remind(self, openid: str, item_name: str, expire_at: str,
                          color_label: str, page: str = "pages/student/student") -> tuple[bool, str]:
        """到期提醒。"""
        from datetime import date, datetime
        try:
            exp = datetime.strptime(expire_at[:10], "%Y-%m-%d").date()
        except ValueError:
            exp = date.today()
        days = (exp - date.today()).days
        tip = f"已超期{-days}天，请尽快取出" if days < 0 else f"{color_label}，请及时取出"
        return await self._send(openid, item_name, expire_at[:10],
                                str(max(days, 0)), tip, page)

    async def send_queue(self, openid: str, item_name: str, when: str,
                         position: int) -> tuple[bool, str]:
        """满柜排队：冰箱空出后叫号。"""
        return await self._send(openid, item_name, when, str(position),
                                "冰箱已空出，请尽快到触控屏完成存入")

    async def send_cleaned(self, openid: str, item_name: str, when: str) -> tuple[bool, str]:
        """物品已被宿管清理。"""
        return await self._send(openid, item_name, when, "0",
                                "已被宿管清理，请联系宿管")

    async def send_batch(self, openid: str, item_name: str, keep_until: str,
                         overdue_days: int) -> tuple[bool, str]:
        """超期提醒：存放至已过 N 天。"""
        return await self._send(openid, item_name, keep_until, str(max(0, overdue_days)),
                                "已过存放期，请尽快取出")

    async def send_batch_mgr(self, openid: str, thing1: str, when: str, count: int,
                             tip: str) -> tuple[bool, str]:
        """宿管提醒（批次清理）。"""
        return await self._send(openid, thing1, when, str(count), tip,
                                page="pages/manager/manager")
