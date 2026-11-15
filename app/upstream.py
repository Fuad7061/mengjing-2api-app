"""Upstream protocol client: register / login / text moderation / submit / poll.

Uses only the standard library (http.client + ssl); no third-party dependencies.
"""
import http.client
import json
import re
import ssl
import uuid

PACKAGE = "com.fnm.huahuashi"
VERSION = "2.0.1"

# Size mapping (upstream `chicun` parameter)
SIZES = {
    "1024x1024": "1*1",
    "768x1344": "9*16",
    "1344x768": "16*9",
    # Common OpenAI size aliases
    "512x512": "1*1",
    "1024x1792": "9*16",
    "1792x1024": "16*9",
}

# Aspect ratios exposed by the API (English-facing)
RATIOS = {
    "1:1": "1*1",
    "9:16": "9*16",
    "16:9": "16*9",
    "1*1": "1*1",
    "9*16": "9*16",
    "16*9": "16*9",
}

# English style names exposed to clients.
DEFAULT_STYLE = "Anime"
STYLES = [
    "Anime", "Chinese", "Realistic", "Illustration", "Oil Painting",
    "Watercolor", "3D", "Hand-drawn", "Pixel", "Cyberpunk", "Vaporwave",
    "Flat", "Healing", "Dark", "Minimalist",
]

# English style name -> upstream Chinese `fengge` value.
STYLE_MAP = {
    "anime": "二次元",
    "chinese": "国风",
    "guofeng": "国风",
    "realistic": "写实",
    "illustration": "插画",
    "oil painting": "油画",
    "oil": "油画",
    "watercolor": "水彩",
    "3d": "3D",
    "hand-drawn": "手绘",
    "handdrawn": "手绘",
    "hand drawn": "手绘",
    "pixel": "像素",
    "cyberpunk": "赛博朋克",
    "vaporwave": "蒸汽波",
    "flat": "扁平",
    "healing": "治愈",
    "dark": "暗黑",
    "minimalist": "极简",
    "minimal": "极简",
}

_RSA_ACTIONS = ("bdtoken", "aliDmh", "tempUser", "dlUser")


class UpstreamError(RuntimeError):
    pass


class ContentViolation(UpstreamError):
    """Prompt triggered the platform's content filter."""


class Upstream:
    def __init__(self, config):
        self.config = config

    # ---- low level ----
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

    # ---- accounts ----
    def register(self):
        """Register a temporary account without verification."""
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
            raise UpstreamError(f"registration failed: {j}")
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
        """Log in again to refresh the JSESSIONID session."""
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

    # ---- generation ----
    def check_text(self, prompt, acct):
        j, _ = self.post_form("/ctext!isweigui.action", [("textinfo", prompt)], acct["cookie"])
        if str(j.get("st")) == "-1":
            if self.relogin(acct):
                j, _ = self.post_form("/ctext!isweigui.action", [("textinfo", prompt)], acct["cookie"])
        if str(j.get("st")) != "1":
            raise UpstreamError(f"text moderation failed: {j}")
        if j.get("weigui"):
            raise ContentViolation("prompt triggered the platform's content filter, please rephrase")

    def submit(self, prompt, chicun, style, acct):
        parts = [("miaoshu", prompt), ("chicun", chicun), ("fengge", style)]
        j, _ = self.post_form("/qwenpic!zhizuo.action", parts, acct["cookie"])
        if str(j.get("st")) == "-1" and self.relogin(acct):
            j, _ = self.post_form("/qwenpic!zhizuo.action", parts, acct["cookie"])
        if str(j.get("st")) != "1":
            raise UpstreamError(f"task submission failed: {j}")
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
        raise UpstreamError(f"task failed: {j}")


def map_style(style: str) -> str:
    """Map a requested style to the upstream Chinese `fengge` value."""
    if not style:
        return STYLE_MAP[DEFAULT_STYLE.lower()]
    style = str(style).strip()
    # Pass Chinese values through unchanged.
    if style in STYLE_MAP.values():
        return style
    return STYLE_MAP.get(style.lower(), STYLE_MAP[DEFAULT_STYLE.lower()])


def map_ratio(ratio: str):
    """Map a requested aspect ratio (e.g. "9:16") to the upstream `chicun`."""
    if not ratio:
        return SIZES["1024x1024"]
    ratio = str(ratio).strip().lower().replace(" ", "")
    return RATIOS.get(ratio, SIZES["1024x1024"])


def map_size(size: str):
    """Map a requested size to the upstream `chicun` parameter."""
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
