"""Persistent application log store: JSONL file + in-memory buffer.

Powers the admin console "Logs" tab. Every notable event is appended to
data/logs.jsonl (inside the Docker volume, so it survives restarts and
redeploys), mirrored to stdout/stderr so Coolify's container log stream
also shows it, auto-pruned after `logs.retention_days` (default 10),
capped at `logs.max_entries`, and can be purged manually from the UI.
"""
import json
import os
import sys
import threading
import time

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
_DEDUPE_WINDOW = 60.0     # identical messages inside this window bump `count`
_PRUNE_INTERVAL = 300.0   # seconds between retention/cap sweeps
_MAX_MSG = 2000
_MAX_DETAIL = 4000


class LogStore:
    def __init__(self, path, config=None):
        self.path = path
        self.config = config
        self._lock = threading.Lock()
        self._entries = []          # oldest -> newest
        self._dirty = False         # memory diverged from file (dedupe/caps)
        self._stop = threading.Event()
        self._load()
        threading.Thread(target=self._prune_loop, name="log-prune", daemon=True).start()

    # ---- config knobs (read live so UI changes apply without restart) ----
    def _retention_days(self):
        if self.config is None:
            return 10
        try:
            return max(0, int(self.config.get("logs", "retention_days", default=10)))
        except Exception:
            return 10

    def _max_entries(self):
        if self.config is None:
            return 5000
        try:
            return max(100, int(self.config.get("logs", "max_entries", default=5000)))
        except Exception:
            return 5000

    # ---- persistence ----
    def _load(self):
        if not os.path.exists(self.path):
            return
        changed = False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(e, dict) or "msg" not in e:
                        continue
                    e.setdefault("ts", 0.0)
                    e.setdefault("level", "INFO")
                    e.setdefault("source", "app")
                    e.setdefault("count", 1)
                    if self._entries:
                        last = self._entries[-1]
                        if (
                            last["level"] == e["level"]
                            and last["source"] == e["source"]
                            and last["msg"] == e["msg"]
                            and e["ts"] - last["ts"] <= _DEDUPE_WINDOW
                        ):
                            last["count"] += e.get("count", 1)
                            last["ts"] = max(last["ts"], e["ts"])
                            changed = True
                            continue
                    self._entries.append(e)
        except Exception:
            pass
        with self._lock:
            if self._prune_locked():
                changed = True
            if changed:
                self._rewrite_locked()

    def _append_file(self, entry):
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _rewrite_locked(self):
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for e in self._entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception:
            pass

    def _prune_locked(self):
        """Drop entries past retention or over the cap. Returns True if changed."""
        before = len(self._entries)
        days = self._retention_days()
        if days > 0:
            cutoff = time.time() - days * 86400
            self._entries = [e for e in self._entries if e.get("ts", 0) >= cutoff]
        cap = self._max_entries()
        if len(self._entries) > cap:
            self._entries = self._entries[-cap:]
        return len(self._entries) != before

    def _prune_loop(self):
        while not self._stop.wait(_PRUNE_INTERVAL):
            try:
                with self._lock:
                    changed = self._prune_locked()
                    if changed or self._dirty:
                        self._rewrite_locked()
            except Exception:
                pass

    # ---- writing ----
    def add(self, level, source, msg, detail=None):
        level = str(level or "INFO").upper()
        if level not in LEVELS:
            level = "INFO"
        msg = str(msg)[:_MAX_MSG]
        if detail:
            detail = str(detail)[:_MAX_DETAIL]
        now = time.time()
        created = False
        with self._lock:
            entry = None
            if self._entries:
                last = self._entries[-1]
                if (
                    last["level"] == level
                    and last["source"] == source
                    and last["msg"] == msg
                    and now - last["ts"] <= _DEDUPE_WINDOW
                ):
                    last["count"] = last.get("count", 1) + 1
                    last["ts"] = now
                    self._dirty = True
                    entry = last
            if entry is None:
                entry = {"ts": now, "level": level, "source": str(source), "msg": msg, "count": 1}
                if detail:
                    entry["detail"] = detail
                self._entries.append(entry)
                if len(self._entries) > self._max_entries():
                    overflow = len(self._entries) - self._max_entries()
                    del self._entries[:overflow]
                    self._dirty = True
                self._append_file(entry)
                created = True
        self._mirror(entry, created)
        return entry

    @staticmethod
    def _mirror(entry, created):
        # Only mirror newly created lines (not dedupe bumps) so the Coolify
        # container log stream stays readable instead of repeating forever.
        if not created:
            return
        level = entry["level"]
        if level == "DEBUG":
            return
        line = "[log] %s %s: %s" % (level, entry["source"], entry["msg"])
        try:
            if level in ("WARN", "ERROR"):
                print(line, file=sys.stderr, flush=True)
            else:
                print(line, file=sys.stdout, flush=True)
        except Exception:
            pass

    def debug(self, source, msg, detail=None):
        return self.add("DEBUG", source, msg, detail)

    def info(self, source, msg, detail=None):
        return self.add("INFO", source, msg, detail)

    def warn(self, source, msg, detail=None):
        return self.add("WARN", source, msg, detail)

    def error(self, source, msg, detail=None):
        return self.add("ERROR", source, msg, detail)

    # ---- reading ----
    def query(self, level=None, source=None, limit=200, offset=0):
        try:
            limit = max(1, min(int(limit or 200), 1000))
        except Exception:
            limit = 200
        try:
            offset = max(0, int(offset or 0))
        except Exception:
            offset = 0
        with self._lock:
            all_entries = list(self._entries)
        filtered = all_entries
        if level:
            lv = str(level).upper()
            filtered = [e for e in filtered if e.get("level") == lv]
        if source:
            filtered = [e for e in filtered if e.get("source") == source]
        filtered = list(reversed(filtered))  # newest first
        total = len(filtered)
        page = filtered[offset:offset + limit]
        levels = {lv: 0 for lv in LEVELS}
        for e in all_entries:
            lv = e.get("level", "INFO")
            levels[lv] = levels.get(lv, 0) + 1
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return {
            "entries": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "stats": {
                "total": len(all_entries),
                "file_bytes": size,
                "retention_days": self._retention_days(),
                "max_entries": self._max_entries(),
                "levels": levels,
            },
        }

    def clear(self):
        with self._lock:
            n = len(self._entries)
            self._entries = []
            self._dirty = False
            try:
                with open(self.path, "w", encoding="utf-8"):
                    pass
            except Exception:
                pass
        return n

    def stop(self):
        self._stop.set()
