"""Run the exact live feature pipeline over a recorded session, as fast as possible.

This is the bridge from recordings to the dataset: one row per window with every
stream's features and activity, plus the label. A window only gets a label if it lies
entirely inside one label interval; windows that straddle a transition are left
unlabelled so they never leak into training or evaluation.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from radar.dsp.features import FEATURE_NAMES, activity, window_features
from radar.dsp.resample import to_grid
from radar.sources.base import Sample, StreamInfo


def label_for(labels: list[dict[str, Any]], t0: float, t1: float) -> str | None:
    for lab in labels:
        if lab["start"] <= t0 and t1 <= lab["end"]:
            return lab["label"]
    return None


def samples_to_frame(samples: list[Sample]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stream": [s.stream for s in samples],
            "t": np.fromiter((s.t for s in samples), float, len(samples)),
            "v": np.fromiter((s.v for s in samples), float, len(samples)),
        }
    )


def session_windows(
    df: pd.DataFrame,
    infos: list[StreamInfo],
    labels: list[dict[str, Any]],
    window_s: float = 2.0,
    hop_s: float = 0.5,
) -> pd.DataFrame:
    use = [i for i in infos if i.kind != "truth"]
    series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for name, g in df.groupby("stream", observed=True):
        g = g.sort_values("t", kind="stable")
        series[str(name)] = (g["t"].to_numpy(float), g["v"].to_numpy(float))
    if df.empty:
        return pd.DataFrame()
    t_end = float(df["t"].max())
    rows = []
    for t in np.arange(window_s, t_end + 1e-9, hop_s):
        row: dict[str, Any] = {"t": float(t), "label": label_for(labels, t - window_s, t)}
        for info in use:
            tt, vv = series.get(info.name, (np.empty(0), np.empty(0)))
            a, b = np.searchsorted(tt, [t - window_s - 0.25, t], side="right")
            fs = min(info.nominal_hz, 100.0)
            _, y, q = to_grid(
                tt[a:b], vv[a:b], t - window_s, t, fs, info.resample, max(0.5, 3 / fs)
            )
            row[f"{info.name}.q"] = q
            if q < 0.5:
                for k in FEATURE_NAMES:
                    row[f"{info.name}.{k}"] = np.nan
                row[f"{info.name}.act"] = np.nan
                continue
            feats = window_features(y, fs, info.stab_tol)
            for k, v in feats.items():
                row[f"{info.name}.{k}"] = v
            row[f"{info.name}.act"] = activity(feats, info.stab_tol)
        rows.append(row)
    return pd.DataFrame(rows)
