"""HTTP 服务：路由、鉴权、管理 API、静态页面。

标准库 ThreadingHTTPServer 实现，HTTP/1.1 长连接 + 线程池。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from . import __version__
from .config import Config
from .pool import AccountPool, NoAccount
from .security import (
    SessionStore,
    api_key_matches,
    generate_api_key,
    hash_password,
    verify_password,
)
from .upstream import ContentViolation, STYLES, UpstreamError, map_size

WEB_DIR = Path(__file__).parent / "web"
MAX_BODY = 2 * 1024 * 1024
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8"}


class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.start = time.time()
        self.requests = 0
        self.success = 0
        self.errors = 0
        self.registrations = 0

    def inc(self, field, n=1):
        with self._lock:
            setattr(self, field, getattr(self, field, 0) + n)

    def snapshot(self):
        with self._lock:
            return {
                "requests": self.requests,
                "success": self.success,
                "errors": self.errors,
                "registrations": self.registrations,
                "uptime_s": round(time.time() - self.start, 1),
            }


class App:
    def __init__(self):
        data_dir = os.environ.get("DATA_DIR") or os.path.join(os.getcwd(), "data")
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.config = Config(os.path.join(data_dir, "config.json"))
        self.config.ensure_admin_password()
        self.sessions = SessionStore(ttl=self.config.get("session_ttl", default=86400))
        self.stats = Stats()
        self.pool = AccountPool(self.config, self.stats, os.path.join(data_dir, "pool.json"))
        self.static = self._load_static()

    def _load_static(self):
        out = {}
        for f in WEB_DIR.glob("*"):
            if f.suffix in STATIC_TYPES:
                out[f.name] = (STATIC_TYPES[f.suffix], f.read_bytes())
        return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mengjing-2api/" + __version__
    app = None  # 由 run() 注入

    # ---- 基础设施 ----
    def log_message(self, fmt, *args):
        pass  # 静默，避免高频轮询刷屏

    def log_error(self, fmt, *args):
        sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None, raw=False):
        if not raw and isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def _json(self, code, obj, headers=None):
        self._send(code, obj, "application/json; charset=utf-8", headers)

    def _redirect(self, location):
        self._send(302, b"", "text/plain", {"Location": location})

    def _static(self, name):
        item = self.app.static.get(name)
        if not item:
            self._json(404, {"error": "not found"})
            return
        ctype, data = item
        self._send(200, data, ctype, raw=True)

    def _cookies(self):
        raw = self.headers.get("Cookie", "")
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return {}
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return {}

    # ---- 鉴权 ----
    def _is_admin(self):
        return self.app.sessions.valid(self._cookies().get(SessionStore.COOKIE))

    def _presented_key(self):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return self.headers.get("x-api-key", "").strip()

    def _api_ok(self):
        if not self.app.config.get("require_api_key", default=True):
            return True
        return api_key_matches(self._presented_key(), self.app.config.get("api_keys", default=[]))

    # ---- 路由 ----
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key", "Access-Control-Allow-Methods": "GET, POST, OPTIONS"})

    def _dispatch(self, method):
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/") or "/"
        query = parse_qs(parts.query)
        try:
            if path.startswith("/admin/api"):
                return self._admin_api(method, path, query)
            if path.startswith("/v1"):
                return self._v1(method, path, query)
            return self._pages(method, path)
        except NoAccount as e:
            return self._json(429, {"error": {"message": str(e), "type": "rate_limit_error"}})
        except ContentViolation as e:
            return self._json(400, {"error": {"message": str(e), "type": "invalid_request_error"}})
        except UpstreamError as e:
            return self._json(502, {"error": {"message": str(e), "type": "upstream_error"}})
        except Exception as e:  # noqa: BLE001
            self.log_error("dispatch error: %r", e)
            return self._json(500, {"error": {"message": "internal error", "type": "server_error"}})

    # ---- 页面 ----
    def _pages(self, method, path):
        if method != "GET":
            return self._json(405, {"error": "method not allowed"})
        if path in ("/", "/index.html", "/admin"):
            if not self._is_admin():
                return self._redirect("/login")
            return self._static("index.html")
        if path == "/login":
            if self._is_admin():
                return self._redirect("/")
            return self._static("login.html")
        if path == "/docs":
            if not self.app.config.get("docs_public", default=True) and not self._is_admin():
                return self._redirect("/login")
            return self._static("docs.html")
        if path == "/logout":
            self.app.sessions.destroy(self._cookies().get(SessionStore.COOKIE))
            return self._redirect("/login")
        if path == "/health":
            snap = self.app.stats.snapshot()
            pool = self.app.pool.snapshot()
            return self._json(200, {"status": "ok", **snap, **pool, "version": __version__})
        if path.startswith("/assets/"):
            return self._static(path.rsplit("/", 1)[-1])
        if path == "/favicon.ico":
            return self._send(204, b"", "text/plain")
        return self._json(404, {"error": "not found"})

    # ---- OpenAI 兼容 API ----
    def _v1(self, method, path, query):
        cors = {"Access-Control-Allow-Origin": "*"}
        if not self._api_ok():
            return self._json(401, {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}}, cors)
        if path == "/v1/models" and method == "GET":
            return self._json(200, {"object": "list", "data": [{"id": "qwen-image3", "object": "model", "owned_by": "mengjing-2api"}]}, cors)
        if path == "/v1/styles" and method == "GET":
            return self._json(200, {"object": "list", "data": STYLES}, cors)
        if path == "/v1/pool" and method == "GET":
            return self._json(200, self.app.pool.snapshot(), cors)
        if path == "/v1/images/generations" and method == "POST":
            return self._generate()
        return self._json(404, {"error": {"message": "unknown endpoint", "type": "invalid_request_error"}}, cors)

    def _generate(self):
        body = self._body()
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return self._json(400, {"error": {"message": "prompt is required", "type": "invalid_request_error"}})
        size = body.get("size") or "1024x1024"
        style = body.get("style") or body.get("fengge") or "二次元"
        self.app.stats.inc("requests")
        t0 = time.perf_counter()
        try:
            url, taskid = self.app.pool.generate(prompt, map_size(size), style)
        except (ContentViolation, NoAccount):
            self.app.stats.inc("errors")
            raise
        except Exception:
            self.app.stats.inc("errors")
            raise
        self.app.stats.inc("success")
        return self._json(200, {
            "created": int(time.time()),
            "data": [{"url": url, "b64_json": None}],
            "task_id": taskid,
            "elapsed_s": round(time.perf_counter() - t0, 1),
            "upstream": "qwen_image3 (dashscope)",
        }, {"Access-Control-Allow-Origin": "*"})

    # ---- 管理 API ----
    def _admin_api(self, method, path, query):
        sub = path[len("/admin/api"):] or "/"
        # 无需登录
        if sub == "/login" and method == "POST":
            return self._login()
        if sub == "/me" and method == "GET":
            return self._json(200, {"authenticated": self._is_admin(), "must_change_password": self.app.config.get("must_change_password", default=False)})
        if sub == "/logout" and method == "POST":
            self.app.sessions.destroy(self._cookies().get(SessionStore.COOKIE))
            return self._json(200, {"ok": True}, {"Set-Cookie": f"{SessionStore.COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
        # 需要登录
        if not self._is_admin():
            return self._json(401, {"error": "unauthorized"})
        if sub == "/config" and method == "GET":
            return self._json(200, {"config": self.app.config.snapshot(), "stats": self.app.stats.snapshot(), "pool": self.app.pool.snapshot(), "version": __version__})
        if sub == "/config" and method == "PUT":
            applied, errors = self.app.config.apply_settings(self._body())
            return self._json(200, {"ok": not errors, "applied": applied, "errors": errors})
        if sub == "/password" and method == "POST":
            return self._change_password()
        if sub == "/keys" and method == "GET":
            keys = self.app.config.get("api_keys", default=[])
            return self._json(200, {"keys": keys, "require_api_key": self.app.config.get("require_api_key", default=True)})
        if sub == "/keys" and method == "POST":
            return self._create_key()
        if sub == "/keys/update" and method == "POST":
            return self._update_key()
        if sub == "/keys/delete" and method == "POST":
            return self._delete_key()
        if sub == "/pool" and method == "GET":
            return self._json(200, self.app.pool.snapshot())
        if sub == "/pool/refill" and method == "POST":
            return self._force_refill()
        if sub == "/stats" and method == "GET":
            return self._json(200, {"stats": self.app.stats.snapshot(), "pool": self.app.pool.snapshot()})
        return self._json(404, {"error": "not found"})

    def _login(self):
        body = self._body()
        password = body.get("password", "")
        stored = self.app.config.get("admin_password_hash")
        if not verify_password(password, stored):
            time.sleep(0.6)  # 抑制暴力破解
            return self._json(401, {"error": "密码错误"})
        token = self.app.sessions.create()
        cookie = f"{SessionStore.COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={self.app.config.get('session_ttl', default=86400)}"
        return self._json(200, {"ok": True, "must_change_password": self.app.config.get("must_change_password", default=False)}, {"Set-Cookie": cookie})

    def _change_password(self):
        body = self._body()
        current = body.get("current", "")
        new = body.get("new", "")
        if not verify_password(current, self.app.config.get("admin_password_hash")):
            return self._json(400, {"error": "当前密码错误"})
        if len(new) < 6:
            return self._json(400, {"error": "新密码至少 6 位"})
        self.app.config.set("admin_password_hash", value=hash_password(new))
        self.app.config.set("must_change_password", value=False)
        self.app.config.save()
        self.app.sessions.destroy_all()
        return self._json(200, {"ok": True})

    def _create_key(self):
        body = self._body()
        name = (body.get("name") or "default").strip()[:40]
        key = {"key": generate_api_key(), "name": name, "enabled": True, "created": int(time.time())}
        keys = self.app.config.get("api_keys", default=[])
        keys.append(key)
        self.app.config.set("api_keys", value=keys)
        self.app.config.save()
        return self._json(200, {"ok": True, "key": key})

    def _update_key(self):
        body = self._body()
        target = body.get("key", "")
        enabled = bool(body.get("enabled", True))
        keys = self.app.config.get("api_keys", default=[])
        found = False
        for k in keys:
            if k.get("key") == target:
                k["enabled"] = enabled
                found = True
        if found:
            self.app.config.set("api_keys", value=keys)
            self.app.config.save()
        return self._json(200 if found else 404, {"ok": found})

    def _delete_key(self):
        body = self._body()
        target = body.get("key", "")
        keys = self.app.config.get("api_keys", default=[])
        remaining = [k for k in keys if k.get("key") != target]
        self.app.config.set("api_keys", value=remaining)
        self.app.config.save()
        return self._json(200, {"ok": len(remaining) != len(keys)})

    def _force_refill(self):
        def worker():
            try:
                acct = self.app.pool.upstream.register()
                acct["credits"] = self.app.config.get("pool", "free_per_account", default=5)
                with self.app.pool._cond:
                    self.app.pool.accounts.append(acct)
                    self.app.pool._cond.notify_all()
                self.app.stats.inc("registrations")
                self.app.pool.save()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()
        return self._json(200, {"ok": True, "message": "正在补充账号"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def run():
    app = App()
    Handler.app = app
    app.pool.start()

    port = int(os.environ.get("PORT") or app.config.get("server", "port") or 8788)
    host = os.environ.get("HOST") or app.config.get("server", "host") or "0.0.0.0"

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    httpd = Server((host, port), Handler)
    print(f"AI梦境画画师 2API 网关 v{__version__}")
    print(f"  监听:      http://{host}:{port}")
    print(f"  控制台:    http://{host}:{port}/admin   (默认密码 admin123)")
    print(f"  文档:      http://{host}:{port}/docs")
    print(f"  数据目录:  {app.data_dir}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭…")
    finally:
        app.pool.stop()
        httpd.server_close()


if __name__ == "__main__":
    run()
