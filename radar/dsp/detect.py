"""Unsupervised motion detector (spec §6A).

Per stream, a robust baseline (median and MAD of the activity over recent quiet
windows) turns each window's activity into a z-score. The z-scores are fused with
per-stream weights (priors by stream kind, replaced by measured separability once a
probe has run). Two triggers raise MOTION: the fused z staying above `z_on` for a few
windows, or a CUSUM on the fused z crossing `cusum_h` (catches slow, sustained
disturbances that never spike). A hold time and a lower release threshold give
hysteresis so the state does not flicker.

The baseline only learns from windows judged quiet, so it follows slow drift (router
power management, temperature, a fan) without learning a walking person as normal.

What the detector can and cannot say: motion, yes. A motionless person is invisible to
RSSI and RTT, so after motion stops the state becomes SETTLED ("someone was moving N
minutes ago") and only decays to EMPTY after `presence_hold_s` of quiet.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

# Prior weight per stream kind before any probe has measured what works in this home.
# Tx frame rate is mostly our own probes, so it carries no motion information. Failed
# frames are sparse events: one failure in a quiet window is a huge z, so it gets little.
KIND_PRIOR = {
    "rtt": 1.0, "retry": 0.7, "fail": 0.1, "rssi": 0.4, "rate": 0.2,
    "quality": 0.0, "tx": 0.0, "truth": 0.0,
}  # fmt: skip


@dataclass
class DetectorConfig:
    z_on: float = 3.0
    z_off: float = 1.5
    on_windows: int = 3
    hold_s: float = 3.0
    presence_hold_s: float = 300.0
    cusum_k: float = 1.0
    cusum_h: float = 20.0
    calib_s: float = 20.0
    baseline_len: int = 3000  # ~5 min of windows at a 0.1 s hop
    z_clip: float = 15.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


class Baseline:
    """Robust running location/scale of one stream's activity during quiet."""

    def __init__(self, maxlen: int) -> None:
        self.values: deque[float] = deque(maxlen=maxlen)
        self.median = math.nan
        self.mad = math.nan
        self._since_fit = 0

    @property
    def ready(self) -> bool:
        return len(self.values) >= 30

    def add(self, a: float) -> None:
        if not math.isfinite(a):
            return
        self.values.append(a)
        self._since_fit += 1
        if self._since_fit >= 10 or not self.ready:
            self._fit()

    def _fit(self) -> None:
        arr = np.fromiter(self.values, float)
        self.median = float(np.median(arr))
        self.mad = float(np.median(np.abs(arr - self.median)))
        self._since_fit = 0

    def z(self, a: float) -> float:
        if not self.ready or not math.isfinite(a):
            return math.nan
        # 0.02 decades is the floor: a stream that was dead flat during calibration
        # should not turn a one-step change into an infinite z.
        return (a - self.median) / (1.4826 * self.mad + 0.02)


@dataclass
class DetectorOutput:
    state: str  # calibrating | empty | settled | motion
    z: float
    cusum: float
    p_motion: float
    stream_z: dict[str, float]
    last_motion_wall: float | None
    calib_progress: float
    events: list[dict[str, Any]] = field(default_factory=list)


class MotionDetector:
    def __init__(self, cfg: DetectorConfig | None = None) -> None:
        self.cfg = cfg or DetectorConfig()
        self.weights: dict[str, float] = {}
        self.kinds: dict[str, str] = {}
        self.reset()

    def reset(self) -> None:
        self.baselines: dict[str, Baseline] = {}
        self.state = "calibrating"
        self.calib_start: float | None = None
        self.cusum = 0.0
        self.consec = 0
        self.z_smooth = 0.0
        self.last_t: float | None = None
        self.quiet_since: float | None = None
        self.last_motion_t: float | None = None
        self.last_motion_wall: float | None = None

    def soft_reset(self) -> None:
        """Clear the motion state but keep the learned baselines (used after a seek)."""
        if self.state == "calibrating":
            return
        self.state = "empty"
        self.cusum = 0.0
        self.consec = 0
        self.z_smooth = 0.0
        self.quiet_since = None
        self.last_motion_t = None
        self.last_motion_wall = None

    def calibrate(self) -> None:
        """Forget the baseline and relearn it from the next `calib_s` seconds."""
        self.reset()

    def set_streams(self, kinds: dict[str, str]) -> None:
        self.kinds = dict(kinds)

    def set_weights(self, weights: dict[str, float]) -> None:
        self.weights = {k: max(0.0, float(w)) for k, w in weights.items()}

    def weight(self, stream: str) -> float:
        if stream in self.weights:
            return self.weights[stream]
        return KIND_PRIOR.get(self.kinds.get(stream, ""), 0.0)

    def _baseline(self, stream: str) -> Baseline:
        b = self.baselines.get(stream)
        if b is None:
            b = self.baselines[stream] = Baseline(self.cfg.baseline_len)
        return b

    def _learn(self, activities: dict[str, float]) -> None:
        # Every stream keeps a baseline, weighted or not, so a probe that later raises
        # a stream's weight can use it straight away.
        for s, a in activities.items():
            if self.kinds.get(s) != "truth":
                self._baseline(s).add(a)

    def update(self, t: float, activities: dict[str, float], wall: float) -> DetectorOutput:
        cfg = self.cfg
        events: list[dict[str, Any]] = []

        if self.state == "calibrating":
            if self.calib_start is None:
                self.calib_start = t
            self._learn(activities)
            progress = min(1.0, (t - self.calib_start) / cfg.calib_s)
            ready = any(b.ready for b in self.baselines.values())
            if progress >= 1.0 and ready:
                self.state = "empty"
                events.append({"kind": "calibrated", "wall": wall})
            return DetectorOutput("calibrating", 0.0, 0.0, 0.0, {}, None, progress, events)

        stream_z: dict[str, float] = {}
        num = den = 0.0
        for s, a in activities.items():
            w = self.weight(s)
            if w <= 0:
                continue
            z = self._baseline(s).z(a)
            if not math.isfinite(z):
                continue
            z = max(-5.0, min(cfg.z_clip, z))
            stream_z[s] = z
            num += w * z
            den += w
        fused = num / den if den > 0 else 0.0

        dt = 0.1 if self.last_t is None else max(1e-3, t - self.last_t)
        self.last_t = t
        self.z_smooth += min(1.0, dt / 0.5) * (fused - self.z_smooth)
        self.cusum = min(cfg.cusum_h * 2, max(0.0, self.cusum + fused - cfg.cusum_k))
        self.consec = self.consec + 1 if fused > cfg.z_on else 0

        if self.state != "motion":
            if self.consec >= cfg.on_windows or self.cusum > cfg.cusum_h:
                self.state = "motion"
                self.quiet_since = None
                events.append({"kind": "motion_start", "wall": wall, "z": round(fused, 2)})
        else:
            # Release on the smoothed z: one noisy window above z_off must not restart
            # the hold timer, or motion can fail to clear for a long time.
            if self.z_smooth < cfg.z_off:
                if self.quiet_since is None:
                    self.quiet_since = t
                elif t - self.quiet_since >= cfg.hold_s:
                    self.state = "settled"
                    self.cusum = 0.0
                    events.append({"kind": "motion_end", "wall": wall})
            else:
                self.quiet_since = None

        if self.state == "motion":
            self.last_motion_t = t
            self.last_motion_wall = wall
        elif (
            self.state == "settled"
            and self.last_motion_t is not None
            and t - self.last_motion_t > cfg.presence_hold_s
        ):
            self.state = "empty"
            events.append({"kind": "empty", "wall": wall})

        # Learn from every window that is not motion, and not right after motion ends,
        # when someone may still be settling. Gating at z_on rather than z_off matters:
        # a tighter gate keeps only the low half of a drifting signal, so the baseline
        # would lag further behind each update until drift alone looked like motion.
        recently = self.last_motion_t is not None and t - self.last_motion_t < 5.0
        if self.state != "motion" and fused < cfg.z_on and not recently:
            self._learn(activities)

        p = 1.0 / (1.0 + math.exp(-1.5 * (fused - cfg.z_on)))
        return DetectorOutput(
            self.state, fused, self.cusum, p, stream_z, self.last_motion_wall, 1.0, events
        )

    def baseline_summary(self) -> dict[str, dict[str, float]]:
        return {
            s: {"median": b.median, "mad": b.mad, "n": len(b.values), "weight": self.weight(s)}
            for s, b in self.baselines.items()
            if b.ready
        }
