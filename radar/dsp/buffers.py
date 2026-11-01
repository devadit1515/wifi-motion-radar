"""Thread-safe ring buffers, one per stream, plus the stream health statistics.

Each source thread appends to its own streams; the engine thread reads windows. The
sequence counter lets the engine ship "samples since last frame" to the dashboard
without resending anything.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np


class Ring:
    def __init__(self, capacity: int) -> None:
        self.cap = capacity
        self.t = np.zeros(capacity)
        self.v = np.zeros(capacity)
        self.seq = 0  # total samples ever written
        self._lock = threading.Lock()

    def append(self, t: float, v: float) -> None:
        with self._lock:
            i = self.seq % self.cap
            self.t[i] = t
            self.v[i] = v
            self.seq += 1

    def _ordered(self, start_seq: int, end_seq: int) -> tuple[np.ndarray, np.ndarray]:
        if end_seq <= start_seq:
            return np.empty(0), np.empty(0)
        a, b = start_seq % self.cap, end_seq % self.cap
        if a < b:
            return self.t[a:b].copy(), self.v[a:b].copy()
        return (
            np.concatenate([self.t[a:], self.t[:b]]),
            np.concatenate([self.v[a:], self.v[:b]]),
        )

    def after(self, seq: int, limit: int | None = None) -> tuple[np.ndarray, np.ndarray, int]:
        """Samples written after sequence number `seq`, and the new sequence number."""
        with self._lock:
            end = self.seq
            start = max(seq, end - self.cap)
            if limit is not None:
                start = max(start, end - limit)
            t, v = self._ordered(start, end)
        return t, v, end

    def since(self, t0: float) -> tuple[np.ndarray, np.ndarray]:
        """All samples with timestamp >= t0 (binary search over the logical order)."""
        with self._lock:
            end = self.seq
            lo = max(0, end - self.cap)
            hi = end
            while lo < hi:
                mid = (lo + hi) // 2
                if self.t[mid % self.cap] < t0:
                    lo = mid + 1
                else:
                    hi = mid
            return self._ordered(lo, end)

    def last(self) -> tuple[float, float] | None:
        with self._lock:
            if self.seq == 0:
                return None
            i = (self.seq - 1) % self.cap
            return float(self.t[i]), float(self.v[i])

    def clear(self) -> None:
        with self._lock:
            self.seq = 0


@dataclass
class StreamStats:
    """What a stream actually delivers, as opposed to what it is polled at."""

    poll_hz: float  # samples per second
    refresh_hz: float  # value changes per second: the real information rate
    quant_step: float | None  # smallest non-zero step between consecutive values
    loss: float  # fraction of NaN samples
    last: float | None
    age_s: float | None  # time since the last sample

    def to_dict(self) -> dict[str, float | None]:
        def clean(x: float | None) -> float | None:
            return None if x is None or not math.isfinite(x) else round(x, 4)

        return {k: clean(v) for k, v in self.__dict__.items()}


def stream_stats(t: np.ndarray, v: np.ndarray, now_t: float, span: float) -> StreamStats:
    if len(t) == 0:
        return StreamStats(0.0, 0.0, None, 1.0, None, None)
    duration = max(min(span, now_t - t[0]), 1e-6)
    ok = ~np.isnan(v)
    vals = v[ok]
    changes = int(np.count_nonzero(np.diff(vals))) if len(vals) > 1 else 0
    steps = np.abs(np.diff(vals))
    steps = steps[steps > 1e-9]
    quant = float(steps.min()) if len(steps) else None
    return StreamStats(
        poll_hz=len(t) / duration,
        refresh_hz=changes / duration,
        quant_step=quant,
        loss=float(1.0 - ok.mean()),
        last=float(vals[-1]) if len(vals) else None,
        age_s=float(now_t - t[-1]),
    )
