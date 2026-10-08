"""微信小程序服务端能力：登录换 openid、access_token 缓存、订阅消息推送。"""
import time
import httpx

API = "https://api.weixin.qq.com"
_token_cache = {"token": "", "exp": 0.0}


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
        async with httpx.AsyncClient(timeout=8) as cl:
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
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.get(f"{API}/cgi-bin/token", params={
                "grant_type": "client_credential",
                "appid": self.appid, "secret": self.secret})
            d = r.json()
        if "access_token" not in d:
            raise RuntimeError(f"获取 access_token 失败: {d}")
        _token_cache.update(token=d["access_token"], exp=time.time() + d.get("expires_in", 7200) - 300)
        return _token_cache["token"]

    async def send_remind(self, openid: str, item_name: str, expire_at: str,
                          color_label: str, page: str = "pages/student/student") -> tuple[bool, str]:
        """发送到期提醒订阅消息。返回 (是否真实发送, 说明)。"""
        if self.dev_mode or not self.template_id:
            return False, f"[dev] 应向 {openid} 推送：{item_name} {color_label}（到期 {expire_at}）"
        token = await self.access_token()
        # 模板字段名需与所选订阅消息模板一致，这里给常见占位，部署时按模板调整
        data = {
            "thing1": {"value": item_name[:20]},
            "character_string2": {"value": color_label},
            "date3": {"value": expire_at[:10]},
            "thing4": {"value": "请及时取出，逾期将移入待认领区"},
        }
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.post(f"{API}/cgi-bin/message/subscribe/send",
                              params={"access_token": token},
                              json={"touser": openid, "template_id": self.template_id,
                                    "page": page, "data": data})
            d = r.json()
        ok = d.get("errcode") == 0
        return ok, ("已推送" if ok else f"推送失败: {d}")
