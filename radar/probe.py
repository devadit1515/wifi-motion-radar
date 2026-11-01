"""Feasibility probe: a guided A/B test that answers "does this work in my home, and
which signal carries it?" in about two minutes.

The user follows timed prompts (leave the room, then walk). Every window's activity is
logged per stream with the phase it fell in, skipping a few settling seconds after each
prompt. At the end each stream gets a ROC AUC for "walking windows have higher activity
than empty windows", and the detector's own hit rate and false-alarm rate on the same
windows are reported. Stream weights for the fused detector are derived from the AUCs.

Windows overlap (2 s long, 0.1 s hop), so neighbouring windows are correlated: the AUC
is a descriptive separability score for this home, not a significance test.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import rankdata

DEFAULT_PROTOCOL: list[dict[str, Any]] = [
    {
        "label": "empty",
        "seconds": 60,
        "prompt": "Leave the room, or stand completely still well away from the laptop.",
    },
    {
        "label": "walking",
        "seconds": 60,
        "prompt": "Walk around normally, crossing between the laptop and the router.",
    },
]

LEAD_IN_S = 5.0
SETTLE_S = 3.0


def auc(neg: np.ndarray, pos: np.ndarray) -> float:
    """P(pos > neg) via the Mann-Whitney rank sum (ties count half)."""
    if len(neg) == 0 or len(pos) == 0:
        return math.nan
    ranks = rankdata(np.concatenate([neg, pos]))
    r_pos = ranks[len(neg) :].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(neg) * len(pos)))


def verdict(a: float) -> str:
    if not math.isfinite(a):
        return "no data"
    if a >= 0.9:
        return "strong"
    if a >= 0.75:
        return "usable"
    if a >= 0.6:
        return "weak"
    return "none"


def weight_from_auc(a: float) -> float:
    if not math.isfinite(a):
        return 0.0
    return round(max(0.0, min(1.0, (a - 0.55) / 0.4)) ** 2, 3)


@dataclass
class ProbeRun:
    phases: list[dict[str, Any]]
    t_start: float
    obs: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    det: dict[str, list[bool]] = field(default_factory=dict)
    cancelled: bool = False
    wall_start: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.bounds = []
        t = self.t_start + LEAD_IN_S
        for p in self.phases:
            self.bounds.append((t, t + float(p["seconds"])))
            t += float(p["seconds"])
        self.t_end = t

    def status(self, t: float) -> dict[str, Any]:
        if t < self.t_start + LEAD_IN_S:
            return {
                "stage": "lead_in",
                "remaining": round(self.t_start + LEAD_IN_S - t, 1),
                "next": self.phases[0],
                "index": -1,
                "total": len(self.phases),
            }
        for i, (a, b) in enumerate(self.bounds):
            if a <= t < b:
                return {
                    "stage": "phase",
                    "index": i,
                    "total": len(self.phases),
                    "phase": self.phases[i],
                    "remaining": round(b - t, 1),
                    "elapsed": round(t - a, 1),
                    "settling": t - a < SETTLE_S,
                }
        return {"stage": "done", "index": len(self.phases), "total": len(self.phases)}

    def label_at(self, t: float) -> str | None:
        for i, (a, b) in enumerate(self.bounds):
            if a <= t < b:
                return self.phases[i]["label"]
        return None

    def observe(self, t: float, activities: dict[str, float], motion: bool) -> None:
        for i, (a, b) in enumerate(self.bounds):
            if a + SETTLE_S <= t < b:
                label = self.phases[i]["label"]
                for s, v in activities.items():
                    if math.isfinite(v):
                        self.obs.setdefault(s, {}).setdefault(label, []).append(v)
                self.det.setdefault(label, []).append(motion)
                return

    def done(self, t: float) -> bool:
        return t >= self.t_end

    def result(self, still_label: str = "empty", move_label: str = "walking") -> dict[str, Any]:
        streams = {}
        for s, by_label in self.obs.items():
            neg = np.asarray(by_label.get(still_label, []))
            pos = np.asarray(by_label.get(move_label, []))
            a = auc(neg, pos)
            ratio = (
                10 ** (float(np.median(pos)) - float(np.median(neg)))
                if len(neg) and len(pos)
                else math.nan
            )
            streams[s] = {
                "auc": round(a, 4) if math.isfinite(a) else None,
                "verdict": verdict(a),
                "spread_ratio": round(ratio, 3) if math.isfinite(ratio) else None,
                "n_still": int(len(neg)),
                "n_moving": int(len(pos)),
                "weight": weight_from_auc(a),
            }
        ranked = sorted(streams.items(), key=lambda kv: -(kv[1]["auc"] or 0))
        best = ranked[0] if ranked else None
        hits = self.det.get(move_label, [])
        alarms = self.det.get(still_label, [])
        return {
            "wall_start": self.wall_start,
            "phases": self.phases,
            "streams": dict(ranked),
            "best": {"stream": best[0], **best[1]} if best else None,
            "detector": {
                "hit_rate": round(float(np.mean(hits)), 4) if hits else None,
                "false_alarm_rate": round(float(np.mean(alarms)), 4) if alarms else None,
            },
            "overall": verdict(best[1]["auc"] if best and best[1]["auc"] is not None else math.nan),
        }
