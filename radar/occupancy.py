"""Day-to-day usefulness: the occupancy timeline, the event log, and Away Guard."""

from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any


class Timeline:
    """Per-minute motion summary. Persisted per day for live data; kept in memory only
    for simulated or replayed data so those never pollute the real history."""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self.memory: deque[dict[str, Any]] = deque(maxlen=60 * 24)
        self._minute: int | None = None
        self._acc = {"n": 0, "motion": 0, "z_max": 0.0}

    def add(self, wall: float, state: str, z: float) -> None:
        minute = int(wall // 60)
        if self._minute is not None and minute != self._minute:
            self._close()
        self._minute = minute
        self._acc["n"] += 1
        self._acc["motion"] += state == "motion"
        self._acc["z_max"] = max(self._acc["z_max"], z)

    def _close(self) -> None:
        if self._minute is None or self._acc["n"] == 0:
            return
        row = {
            "minute": self._minute * 60,
            "motion": round(self._acc["motion"] / self._acc["n"], 3),
            "z_max": round(self._acc["z_max"], 2),
        }
        self.memory.append(row)
        if self.root is not None:
            day = time.strftime("%Y-%m-%d", time.localtime(row["minute"]))
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.root / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        self._acc = {"n": 0, "motion": 0, "z_max": 0.0}

    def day(self, date: str | None = None) -> list[dict[str, Any]]:
        date = date or time.strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []
        if self.root is not None:
            try:
                text = (self.root / f"{date}.jsonl").read_text(encoding="utf-8")
                rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            except OSError:
                rows = []
        else:
            rows = [
                r for r in self.memory
                if time.strftime("%Y-%m-%d", time.localtime(r["minute"])) == date
            ]  # fmt: skip
        if self._minute is not None and self._acc["n"]:
            cur_day = time.strftime("%Y-%m-%d", time.localtime(self._minute * 60))
            if cur_day == date:
                rows.append(
                    {
                        "minute": self._minute * 60,
                        "motion": round(self._acc["motion"] / self._acc["n"], 3),
                        "z_max": round(self._acc["z_max"], 2),
                        "open": True,
                    }
                )
        return rows


class EventLog:
    def __init__(self, path: Path | None, maxlen: int = 500) -> None:
        self.path = path
        self.items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        if path is not None:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()[-maxlen:]
                self.items.extend(json.loads(x) for x in lines if x.strip())
            except (OSError, ValueError):
                pass

    def add(self, ev: dict[str, Any]) -> None:
        self.items.append(ev)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev) + "\n")

    def recent(self, n: int = 100) -> list[dict[str, Any]]:
        return list(self.items)[-n:]


class Guard:
    """Away Guard: when armed, the start of motion raises an alert, and optionally
    POSTs a short text to a webhook (an ntfy.sh topic URL works as-is)."""

    COOLDOWN_S = 120.0

    def __init__(self, armed: bool = False, webhook: str | None = None) -> None:
        self.armed = armed
        self.webhook = webhook
        self.armed_at: float | None = time.time() if armed else None
        self._last_alert = 0.0

    def set(self, armed: bool | None = None, webhook: str | None = None) -> None:
        if armed is not None and armed != self.armed:
            self.armed = armed
            self.armed_at = time.time() if armed else None
        if webhook is not None:
            self.webhook = webhook.strip() or None

    def to_dict(self) -> dict[str, Any]:
        return {"armed": self.armed, "armed_at": self.armed_at, "webhook": self.webhook}

    def check(self, ev: dict[str, Any]) -> dict[str, Any] | None:
        if not self.armed or ev.get("kind") != "motion_start":
            return None
        wall = ev["wall"]
        if wall - self._last_alert < self.COOLDOWN_S:
            return None
        self._last_alert = wall
        alert = {"kind": "guard_alert", "wall": wall, "z": ev.get("z")}
        if self.webhook:
            msg = "Motion detected at " + time.strftime("%H:%M:%S", time.localtime(wall))
            threading.Thread(target=self._post, args=(self.webhook, msg), daemon=True).start()
        return alert

    @staticmethod
    def _post(url: str, msg: str) -> None:
        if not url.startswith(("https://", "http://")):
            return
        req = urllib.request.Request(
            url, data=msg.encode(), method="POST", headers={"Title": "WiFi Motion Radar"}
        )
        with contextlib.suppress(OSError):
            urllib.request.urlopen(req, timeout=5).close()
