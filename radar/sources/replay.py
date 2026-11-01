"""Replays a recorded session through the live pipeline.

Unlike the other sources this one has no thread: it is clocked by the engine, which
advances session time hop by hop. That keeps the detector's cadence identical at any
playback speed, and makes seeking and pausing exact.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from radar.sources.base import Emit, Sample, Source, StreamInfo
from radar.store.sessions import SessionStore


class ReplaySource(Source):
    kind = "replay"
    name = "replay"
    clocked = True

    def __init__(self, store: SessionStore, sid: str, speed: float = 1.0) -> None:
        super().__init__()
        self.sid = sid
        self.meta = store.meta(sid)
        df = store.samples(sid).sort_values("t", kind="stable")
        self._names = df["stream"].astype("category")
        self._codes = self._names.cat.codes.to_numpy()
        self._cats = list(self._names.cat.categories)
        self._t = df["t"].to_numpy(dtype=float)
        self._v = df["v"].to_numpy(dtype=float)
        self._infos = [StreamInfo(**s) for s in self.meta["streams"]]
        self.labels: list[dict[str, Any]] = self.meta.get("labels", [])
        self.duration = float(self.meta.get("duration") or (self._t[-1] if len(self._t) else 0))
        self.speed = speed
        self.paused = False
        self.vt = 0.0
        self._pos = 0
        self._emit: Emit | None = None

    def streams(self) -> list[StreamInfo]:
        return self._infos

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "session": self.sid,
            "duration": self.duration,
            "labels": self.labels,
            "recorded_source": self.meta.get("source", {}),
        }

    def start(self, emit: Emit) -> None:
        self._emit = emit

    def pump(self, until: float) -> None:
        """Emit every sample with session time <= until."""
        if self._emit is None:
            return
        end = int(np.searchsorted(self._t, until, side="right"))
        for i in range(self._pos, end):
            self._emit(Sample(self._cats[self._codes[i]], float(self._t[i]), float(self._v[i])))
        self._pos = end
        self.vt = until

    def seek(self, t: float) -> None:
        t = min(max(0.0, t), self.duration)
        self._pos = int(np.searchsorted(self._t, t, side="left"))
        self.vt = t

    @property
    def finished(self) -> bool:
        return self.vt >= self.duration

    def label_at(self, t: float) -> str | None:
        for lab in self.labels:
            if lab["start"] <= t < lab["end"]:
                return lab["label"]
        return None

    def status(self) -> dict[str, Any]:
        return {
            "session": self.sid,
            "t": round(self.vt, 2),
            "duration": round(self.duration, 2),
            "speed": self.speed,
            "paused": self.paused,
            "label": self.label_at(self.vt),
        }
