"""Put an irregular, possibly lossy stream onto a uniform time grid."""

from __future__ import annotations

import numpy as np


def to_grid(
    t: np.ndarray,
    v: np.ndarray,
    t0: float,
    t1: float,
    hz: float,
    mode: str = "linear",
    max_gap: float = 0.5,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Resample (t, v) onto [t0, t1) at `hz`.

    Lost samples (NaN) are skipped and bridged. Grid points further than `max_gap`
    seconds from any real sample are left as NaN, then filled from the nearest
    neighbour so feature code always gets a finite array. The returned quality is the
    fraction of grid points that were backed by real data, so callers can refuse to
    trust a window that was mostly invented.

    mode="linear" interpolates; mode="hold" keeps the last reported value, which is
    the faithful model for a driver that caches a reading and refreshes it in steps.
    """
    n = max(1, int(round((t1 - t0) * hz)))
    grid = t0 + np.arange(n) / hz
    ok = ~np.isnan(v)
    tv, vv = t[ok], v[ok]
    if len(tv) == 0:
        return grid, np.full(n, np.nan), 0.0

    if mode == "hold":
        idx = np.searchsorted(tv, grid, side="right") - 1
        y = np.where(idx >= 0, vv[np.clip(idx, 0, None)], np.nan)
        # A held value is valid for max_gap after it was reported.
        age = grid - tv[np.clip(idx, 0, None)]
        y[(idx < 0) | (age > max_gap)] = np.nan
    else:
        y = np.interp(grid, tv, vv, left=np.nan, right=np.nan)
        j = np.searchsorted(tv, grid)
        prev_gap = np.where(j > 0, grid - tv[np.clip(j - 1, 0, None)], np.inf)
        next_gap = np.where(j < len(tv), tv[np.clip(j, None, len(tv) - 1)] - grid, np.inf)
        y[np.minimum(prev_gap, next_gap) > max_gap] = np.nan
        # The newest grid points sit after the last sample (pipeline latency); hold it.
        tail = (grid > tv[-1]) & (grid - tv[-1] <= max_gap)
        y[tail] = vv[-1]

    valid = ~np.isnan(y)
    quality = float(valid.mean())
    if quality == 0.0:
        return grid, y, 0.0
    if quality < 1.0:
        idx = np.where(valid, np.arange(n), 0)
        np.maximum.accumulate(idx, out=idx)
        y = y[idx]
        first = int(np.argmax(valid))
        y[:first] = y[first]
    return grid, y, quality
