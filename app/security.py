"""密码哈希、API Key 校验、管理员会话。全部基于标准库。"""
import hashlib
import hmac
import secrets
import threading
import time

PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ROUNDS)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def generate_api_key() -> str:
    return "sk-mj-" + secrets.token_urlsafe(32)


def api_key_matches(presented: str, keys) -> bool:
    """常量时间比较，避免时序侧信道。"""
    if not presented:
        return False
    ok = False
    for k in keys:
        if not k.get("enabled", True):
            continue
        if hmac.compare_digest(presented, k.get("key", "")):
            ok = True
    return ok


class SessionStore:
    """内存态管理员会话，带过期与自动清理。"""

    COOKIE = "mj_admin"

    def __init__(self, ttl=86400):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._sessions = {}  # token -> expiry

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.time() + self.ttl
            self._gc_locked()
        return token

    def valid(self, token) -> bool:
        if not token:
            return False
        with self._lock:
            exp = self._sessions.get(token)
            if not exp:
                return False
            if exp < time.time():
                self._sessions.pop(token, None)
                return False
            return True

    def destroy(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def destroy_all(self):
        with self._lock:
            self._sessions.clear()

    def _gc_locked(self):
        now = time.time()
        for t in [t for t, e in self._sessions.items() if e < now]:
            self._sessions.pop(t, None)
