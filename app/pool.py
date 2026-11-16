"""Account pool: auto-registration, session keep-alive, concurrency scheduling
(condition-variable driven, low CPU)."""
import json
import os
import threading
import time
import traceback

from .upstream import ContentViolation, UpstreamError, Upstream


class NoAccount(RuntimeError):
    pass


class AccountPool:
    def __init__(self, config, stats, path, logs=None):
        self.config = config
        self.stats = stats
        self.path = path
        self.logs = logs
        self.upstream = Upstream(config)
        self.accounts = []
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._last_reg = 0.0
        self._save_interval = 10.0
        self._last_save = 0.0
        self._dirty = False
        self._load()

    # ---- logging ----
    def _log(self, level, msg, detail=None):
        if self.logs is not None:
            getattr(self.logs, level)("pool", msg, detail=detail)

    # ---- persistence ----
    def _load(self):
        if os.path.exists(self.path):
            try:
                disk = json.loads(open(self.path, "r", encoding="utf-8").read())
                if isinstance(disk, list):
                    self.accounts = disk
                    for a in self.accounts:
                        a.setdefault("inflight", 0)
            except Exception as e:
                self._log("warn", f"failed to load pool state: {e!r}",
                          detail=traceback.format_exc())

    def save(self, force=False):
        """Persist the pool. Throttled unless `force` is set to reduce disk churn."""
        now = time.time()
        if not force and now - self._last_save < self._save_interval:
            self._dirty = True
            return
        with self._lock:
            data = [{k: v for k, v in a.items() if k != "inflight"} for a in self.accounts]
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
            self._last_save = now
            self._dirty = False
        except Exception as e:
            self._log("error", f"failed to persist pool state: {e!r}",
                      detail=traceback.format_exc())

    # ---- lifecycle ----
    def start(self):
        threading.Thread(target=self._refill_loop, name="pool-refill", daemon=True).start()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self.save(force=True)

    def _refill_loop(self):
        cfg = self.config
        while not self._stop.is_set():
            try:
                self._maintain()
                if self._dirty:
                    self.save()
            except Exception as e:
                self._log("error", f"pool maintenance cycle failed: {e!r}",
                          detail=traceback.format_exc())
            self._stop.wait(cfg.get("pool", "refill_interval", default=5.0))

    def _maintain(self):
        cfg = self.config
        free = cfg.get("pool", "free_per_account", default=5)
        ttl = cfg.get("pool", "session_ttl", default=1500)
        target = cfg.get("pool", "target", default=16)
        minimum = cfg.get("pool", "min", default=8)
        maximum = cfg.get("pool", "max", default=40)
        reg_interval = cfg.get("pool", "reg_min_interval", default=3.0)
        now = time.time()

        # 1) Drop exhausted accounts
        with self._cond:
            for a in list(self.accounts):
                if a.get("credits", 0) <= 0 and a.get("inflight", 0) == 0:
                    self.accounts.remove(a)
                    self._log("info", f"dropping exhausted account {a.get('id')}")
            self._cond.notify_all()
            alive = [a for a in self.accounts if a.get("credits", 0) > 0]
            stale = [a for a in alive if a.get("inflight", 0) == 0 and now - a.get("ts", 0) > ttl]
            want = len(alive) < target and len(self.accounts) < maximum

        # 2) Session keep-alive (outside the lock to avoid blocking acquires)
        for a in stale:
            if self.upstream.relogin(a):
                a["ts"] = now
            else:
                self._log("warn", f"session keep-alive (relogin) failed for account {a.get('id')}")
                a["credits"] = 0

        # 3) Refill accounts (rate-limited to avoid triggering anti-abuse)
        if want and now - self._last_reg >= reg_interval:
            self._last_reg = now
            try:
                acct = self.upstream.register()
                acct["credits"] = free
                with self._cond:
                    self.accounts.append(acct)
                    self._cond.notify_all()
                self.stats.inc("registrations")
                self.save()
            except Exception as e:
                self._log("error", f"account registration failed: {e!r}",
                          detail=traceback.format_exc())
        elif len(alive) < minimum:
            # Speed up when below the minimum
            self._last_reg = min(self._last_reg, now - reg_interval * 0.5)

    # ---- scheduling ----
    def acquire(self, wait=True, timeout=None):
        if timeout is None:
            timeout = self.config.get("pool", "acquire_timeout", default=120)
        deadline = time.time() + timeout
        with self._cond:
            while True:
                for a in self.accounts:
                    if a.get("credits", 0) > 0 and a.get("inflight", 0) == 0:
                        a["inflight"] = a.get("inflight", 0) + 1
                        return a
                if not wait:
                    raise NoAccount("no account available")
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise NoAccount("timed out waiting for an available account")
                self._cond.wait(min(remaining, 2.0))

    def release(self, acct):
        with self._cond:
            acct["inflight"] = max(0, acct.get("inflight", 0) - 1)
            self._cond.notify_all()

    # ---- generation ----
    def generate(self, prompt, chicun, style):
        cfg = self.config
        poll_interval = cfg.get("upstream", "poll_interval", default=1.2)
        poll_max = cfg.get("upstream", "poll_max", default=60)
        free = cfg.get("pool", "free_per_account", default=5)
        retries = max(1, cfg.get("upstream", "max_retries", default=3))
        last_err = "unknown error"

        for attempt in range(retries):
            acct = self.acquire(wait=True)
            try:
                self.upstream.check_text(prompt, acct)
                taskid = self.upstream.submit(prompt, chicun, style, acct)
                acct["used"] = acct.get("used", 0) + 1
                acct["credits"] = max(0, free - acct["used"])
                acct["ts"] = time.time()
                self.save()

                for _ in range(poll_max):
                    time.sleep(poll_interval)
                    state, url = self.upstream.poll(taskid, acct)
                    if state == "done":
                        return url, taskid
                last_err = "generation timed out"
                self._log("warn", f"generation attempt {attempt + 1}/{retries} timed out (task {taskid})")
            except ContentViolation:
                raise
            except NoAccount:
                raise
            except UpstreamError as e:
                last_err = str(e)
                acct["credits"] = max(0, acct.get("credits", 0) - 1)
                self._log("warn", f"generation attempt {attempt + 1}/{retries} failed: {e}")
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                self._log("warn", f"generation attempt {attempt + 1}/{retries} failed: {e!r}",
                          detail=traceback.format_exc())
            finally:
                self.release(acct)

        self._log("error", f"generation gave up after {retries} attempts: {last_err}")
        raise UpstreamError(f"generation failed: {last_err}")

    # ---- stats ----
    def snapshot(self, probe=False):
        with self._lock:
            accounts = [dict(a) for a in self.accounts]
        alive = [a for a in accounts if a.get("credits", 0) > 0]
        idle = [a for a in alive if a.get("inflight", 0) == 0]
        return {
            "pool_size": len(accounts),
            "pool_alive": len(alive),
            "pool_idle": len(idle),
            "pool_total_credits": sum(a.get("credits", 0) for a in alive),
            "target_pool": self.config.get("pool", "target", default=16),
            "accounts": [
                {
                    "id": a.get("id"),
                    "nickname": a.get("nickname", ""),
                    "credits": a.get("credits", 0),
                    "used": a.get("used", 0),
                    "busy": a.get("inflight", 0) > 0,
                }
                for a in accounts
            ],
        }
