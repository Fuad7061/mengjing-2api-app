"""Configuration: defaults + atomic persistence + thread-safe access."""
import copy
import json
import os
import threading
from pathlib import Path

DEFAULT_ADMIN_PASSWORD = "admin123"

DEFAULTS = {
    "admin_password_hash": None,
    "must_change_password": True,
    "api_keys": [],
    "require_api_key": True,
    "docs_public": True,
    "pool": {
        "target": 16,
        "min": 8,
        "max": 40,
        "free_per_account": 5,
        "reg_min_interval": 3.0,
        "refill_interval": 5.0,
        "session_ttl": 1500,
        "acquire_timeout": 120,
    },
    "upstream": {
        "host": "huahuashi.durl.info",
        "user_agent": "Apache-HttpClient/4.5 (Java/1.8)",
        "timeout": 90,
        "poll_interval": 1.2,
        "poll_max": 60,
        "max_retries": 3,
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8788,
    },
    "logs": {
        "retention_days": 10,   # 0 = keep forever
        "max_entries": 5000,
    },
    "styles_cache_ttl": 300,
    "session_ttl": 86400,
}

# Whitelist of fields editable from the admin console (key = config path, value = type caster)
EDITABLE = {
    "require_api_key": bool,
    "docs_public": bool,
    "styles_cache_ttl": int,
    "pool.target": int,
    "pool.min": int,
    "pool.max": int,
    "pool.free_per_account": int,
    "pool.reg_min_interval": float,
    "pool.refill_interval": float,
    "pool.session_ttl": int,
    "pool.acquire_timeout": int,
    "upstream.host": str,
    "upstream.user_agent": str,
    "upstream.timeout": int,
    "upstream.poll_interval": float,
    "upstream.poll_max": int,
    "upstream.max_retries": int,
    "logs.retention_days": int,
    "logs.max_entries": int,
}


def _deep_merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


class Config:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    # ---- persistence ----
    def load(self):
        if self.path.exists():
            try:
                disk = json.loads(self.path.read_text("utf-8"))
                if isinstance(disk, dict):
                    _deep_merge(self.data, disk)
            except Exception:
                pass

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self.path)

    def ensure_admin_password(self):
        from .security import hash_password
        with self._lock:
            if not self.data.get("admin_password_hash"):
                self.data["admin_password_hash"] = hash_password(DEFAULT_ADMIN_PASSWORD)
                self.data["must_change_password"] = True
                self.save()

    # ---- read / write ----
    def get(self, *keys, default=None):
        with self._lock:
            cur = self.data
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return cur

    def set(self, *keys, value):
        with self._lock:
            cur = self.data
            for k in keys[:-1]:
                cur = cur.setdefault(k, {})
            cur[keys[-1]] = value

    def snapshot(self, redact=True):
        with self._lock:
            data = copy.deepcopy(self.data)
        if redact:
            data.pop("admin_password_hash", None)
            # Only expose the leading/trailing fragments of each key
            for k in data.get("api_keys", []):
                k["key_masked"] = _mask(k.get("key", ""))
                k.pop("key", None)
        return data

    def apply_settings(self, incoming):
        """Apply admin-submitted settings against the whitelist; return (applied, errors)."""
        applied, errors = [], []
        for dotted, caster in EDITABLE.items():
            section, _, leaf = dotted.partition(".")
            if section == leaf:  # top level
                if dotted not in incoming:
                    continue
                raw = incoming[dotted]
                target = (leaf,)
            else:
                sub = incoming.get(section)
                if not isinstance(sub, dict) or leaf not in sub:
                    continue
                raw = sub[leaf]
                target = (section, leaf)
            try:
                val = caster(raw)
                if caster is int and val < 0:
                    raise ValueError("must be >= 0")
                self.set(*target, value=val)
                applied.append(dotted)
            except Exception as e:
                errors.append(f"{dotted}: {e}")
        if applied:
            self.save()
        return applied, errors


def _mask(key):
    if len(key) <= 10:
        return key[:2] + "***"
    return f"{key[:7]}...{key[-4:]}"
