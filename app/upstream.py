"""上游协议客户端：注册 / 登录 / 敏感词检测 / 提交任务 / 轮询结果。

全部使用标准库 http.client + ssl，不引入第三方依赖。
"""
import http.client
import json
import re
import ssl
import uuid

PACKAGE = "com.fnm.huahuashi"
VERSION = "2.0.1"

# 尺寸映射（chicun 参数）
SIZES = {
    "1024x1024": "1*1",
    "768x1344": "9*16",
    "1344x768": "16*9",
    # 兼容常见 OpenAI 尺寸别名
    "512x512": "1*1",
    "1024x1792": "9*16",
    "1792x1024": "16*9",
}

DEFAULT_STYLE = "二次元"
STYLES = [
    "二次元", "国风", "写实", "插画", "油画", "水彩", "3D", "手绘",
    "像素", "赛博朋克", "蒸汽波", "扁平", "治愈", "暗黑", "极简",
]

_RSA_ACTIONS = ("bdtoken", "aliDmh", "tempUser", "dlUser")


class UpstreamError(RuntimeError):
    pass


class ContentViolation(UpstreamError):
    """提示词命中平台敏感词。"""


class Upstream:
    def __init__(self, config):
        self.config = config

    # ---- 底层 ----
    @staticmethod
    def _ctx():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def post_form(self, path, parts, cookie=None):
        host = self.config.get("upstream", "host", default="huahuashi.durl.info")
        ua = self.config.get("upstream", "user_agent", default="Apache-HttpClient/4.5")
        timeout = self.config.get("upstream", "timeout", default=90)
        boundary = "----MJ" + uuid.uuid4().hex
        body = b""
        for k, v in parts:
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode()
            body += str(v).encode("utf-8")
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": ua,
            "Accept": "*/*",
            "Connection": "close",
        }
        if cookie:
            headers["Cookie"] = cookie
        conn = http.client.HTTPSConnection(host, timeout=timeout, context=self._ctx())
        try:
            conn.request("POST", path, body, headers)
            resp = conn.getresponse()
            raw = resp.read()
            setc = resp.getheader("Set-Cookie")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            data = {"st": "-1", "raw": raw[:200].decode("utf-8", "replace")}
        jar = cookie or ""
        if setc and not cookie:
            kv = setc.split(";")[0].strip()
            if "=" in kv:
                jar = kv
        return data, jar

    # ---- 账号 ----
    def register(self):
        """免验证注册一个临时账号。"""
        androidid = uuid.uuid4().hex
        imei = uuid.uuid4().hex[:15]
        mac = ":".join("%02x" % (uuid.uuid4().int >> i & 0xFF) for i in range(0, 48, 8))
        oaid = uuid.uuid4().hex
        parts = [
            ("qudao", "1"),
            ("vcode", VERSION),
            ("androidid", androidid),
            ("imei", imei),
            ("mac", mac),
            ("oaid", oaid),
            ("jixing", "Pixel 6"),
        ]
        j, _ = self.post_form("/yh!tempUser.action", parts)
        if str(j.get("st")) != "1":
            raise UpstreamError(f"注册失败: {j}")
        acct = {
            "id": str(j["id"]),
            "password": str(j.get("mima", "")),
            "nickname": str(j.get("nicheng", "")),
            "androidid": androidid,
            "imei": imei,
            "mac": mac,
            "oaid": oaid,
            "jixing": "Pixel 6",
            "cookie": "",
            "credits": self.config.get("pool", "free_per_account", default=5),
            "used": 0,
            "ts": 0.0,
            "inflight": 0,
        }
        self.relogin(acct)
        return acct

    def relogin(self, acct):
        """重新登录以刷新 JSESSIONID 会话。"""
        parts = [
            ("id", acct["id"]),
            ("mm", acct.get("password", "")),
            ("qudao", "1"),
            ("vcode", VERSION),
            ("jixing", acct.get("jixing", "Pixel 6")),
            ("oaid", acct.get("oaid", "")),
        ]
        try:
            j, jar = self.post_form("/yh!dlUser.action", parts)
        except Exception:
            return False
        if str(j.get("st")) == "1" and jar:
            acct["cookie"] = jar
            return True
        return False

    # ---- 生成 ----
    def check_text(self, prompt, acct):
        j, _ = self.post_form("/ctext!isweigui.action", [("textinfo", prompt)], acct["cookie"])
        if str(j.get("st")) == "-1":
            if self.relogin(acct):
                j, _ = self.post_form("/ctext!isweigui.action", [("textinfo", prompt)], acct["cookie"])
        if str(j.get("st")) != "1":
            raise UpstreamError(f"敏感词检测失败: {j}")
        if j.get("weigui"):
            raise ContentViolation("提示词命中平台敏感词，请修改后重试")

    def submit(self, prompt, chicun, style, acct):
        parts = [("miaoshu", prompt), ("chicun", chicun), ("fengge", style)]
        j, _ = self.post_form("/qwenpic!zhizuo.action", parts, acct["cookie"])
        if str(j.get("st")) == "-1" and self.relogin(acct):
            j, _ = self.post_form("/qwenpic!zhizuo.action", parts, acct["cookie"])
        if str(j.get("st")) != "1":
            raise UpstreamError(f"提交任务失败: {j}")
        return str(j.get("taskid"))

    def poll(self, taskid, acct):
        j, _ = self.post_form("/qwenpic!jieguo.action", [("taskid", taskid)], acct["cookie"])
        st = str(j.get("st"))
        if st == "1":
            url = j.get("picurl") or ""
            if url:
                return "done", url
            return "pending", None
        if st == "2":
            return "pending", None
        if st == "-1" and self.relogin(acct):
            return "pending", None
        raise UpstreamError(f"任务失败: {j}")


def map_size(size: str):
    """把请求尺寸映射为上游 chicun 参数。"""
    if not size:
        return SIZES["1024x1024"]
    size = size.strip().lower().replace(" ", "")
    if size in SIZES:
        return SIZES[size]
    m = re.match(r"^(\d+)[x*](\d+)$", size)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w > h * 1.1:
            return "16*9"
        if h > w * 1.1:
            return "9*16"
        return "1*1"
    return SIZES["1024x1024"]
