"""账号池：自动注册、会话保鲜、并发调度（条件变量驱动，低 CPU）。"""
import json
import os
import threading
import time

from .upstream import ContentViolation, UpstreamError, Upstream


class NoAccount(RuntimeError):
    pass


class AccountPool:
    def __init__(self, config, stats, path):
        self.config = config
        self.stats = stats
        self.path = path
        self.upstream = Upstream(config)
        self.accounts = []
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._last_reg = 0.0
        self._load()

    # ---- 持久化 ----
    def _load(self):
        if os.path.exists(self.path):
            try:
                disk = json.loads(open(self.path, "r", encoding="utf-8").read())
                if isinstance(disk, list):
                    self.accounts = disk
                    for a in self.accounts:
                        a.setdefault("inflight", 0)
            except Exception:
                pass

    def save(self):
        with self._lock:
            data = [{k: v for k, v in a.items() if k != "inflight"} for a in self.accounts]
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---- 生命周期 ----
    def start(self):
        threading.Thread(target=self._refill_loop, name="pool-refill", daemon=True).start()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def _refill_loop(self):
        cfg = self.config
        while not self._stop.is_set():
            try:
                self._maintain()
            except Exception:
                pass
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

        # 1) 清理无额度账号
        with self._cond:
            for a in list(self.accounts):
                if a.get("credits", 0) <= 0 and a.get("inflight", 0) == 0:
                    self.accounts.remove(a)
            self._cond.notify_all()
            alive = [a for a in self.accounts if a.get("credits", 0) > 0]
            stale = [a for a in alive if a.get("inflight", 0) == 0 and now - a.get("ts", 0) > ttl]
            want = len(alive) < target and len(self.accounts) < maximum

        # 2) 会话保鲜（放在锁外，避免阻塞取号）
        for a in stale:
            if self.upstream.relogin(a):
                a["ts"] = now
            else:
                a["credits"] = 0

        # 3) 补充账号（带最小间隔，避免被风控）
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
            except Exception:
                pass
        elif len(alive) < minimum:
            # 低于下限时提速
            self._last_reg = min(self._last_reg, now - reg_interval * 0.5)

    # ---- 调度 ----
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
                    raise NoAccount("当前没有可用账号")
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise NoAccount("等待可用账号超时")
                self._cond.wait(min(remaining, 1.0))

    def release(self, acct):
        with self._cond:
            acct["inflight"] = max(0, acct.get("inflight", 0) - 1)
            self._cond.notify_all()

    # ---- 生成 ----
    def generate(self, prompt, chicun, style):
        cfg = self.config
        poll_interval = cfg.get("upstream", "poll_interval", default=1.2)
        poll_max = cfg.get("upstream", "poll_max", default=60)
        free = cfg.get("pool", "free_per_account", default=5)
        retries = max(1, cfg.get("upstream", "max_retries", default=3))
        last_err = "未知错误"

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
                last_err = "生成超时"
            except ContentViolation:
                raise
            except NoAccount:
                raise
            except UpstreamError as e:
                last_err = str(e)
                acct["credits"] = max(0, acct.get("credits", 0) - 1)
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
            finally:
                self.release(acct)

        raise UpstreamError(f"生成失败：{last_err}")

    # ---- 统计 ----
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
