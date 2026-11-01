"""Per-window features (spec §5) and the scalar "activity" the detector runs on."""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import detrend

# Motion-relevant frequency bands in Hz. Body-scale movement and gait sit in the low
# band; multipath fading from a walking person spreads energy well above it.
BANDS: dict[str, tuple[float, float]] = {
    "b_low": (0.3, 2.0),
    "b_mid": (2.0, 6.0),
    "b_high": (6.0, math.inf),
}
MIN_FREQ = 0.3

FEATURE_NAMES = (
    "std", "mad", "p2p", "spread", "cross_rate", "max_slope", "stability", "level",
    "energy", "b_low", "b_mid", "b_high", "entropy", "centroid", "dominant",
)  # fmt: skip


def spectrum(x: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """One-sided power spectral density of a detrended, Hann-windowed window."""
    n = len(x)
    w = np.hanning(n)
    X = np.fft.rfft(detrend(x, type="linear") * w)
    psd = (np.abs(X) ** 2) / (fs * np.sum(w**2))
    psd[1:-1] *= 2
    return np.fft.rfftfreq(n, 1.0 / fs), psd


def spread(x: np.ndarray) -> float:
    """Half the 10-90 percentile range of the detrended window.

    Close to the standard deviation for Gaussian noise, but one stray 20 ms ping
    cannot move it, while a sustained broadening (what motion does to RTT) does.
    """
    xd = detrend(x, type="linear")
    p10, p90 = np.percentile(xd, [10, 90])
    return float(0.5 * (p90 - p10) / 1.2816)


def window_features(x: np.ndarray, fs: float, stab_tol: float = 1.0) -> dict[str, float]:
    n = len(x)
    if n < 8:
        return dict.fromkeys(FEATURE_NAMES, math.nan)
    xd = detrend(x, type="linear")
    diffs = np.abs(np.diff(x))
    med = float(np.median(x))
    secs = n / fs

    f, psd = spectrum(x, fs)
    df = f[1] - f[0]
    keep = f >= MIN_FREQ
    fk, pk = f[keep], psd[keep]
    energy = float(pk.sum() * df)
    feats = {
        "std": float(xd.std()),
        "mad": float(diffs.mean()),
        "p2p": float(np.ptp(xd)),
        "spread": spread(x),
        "cross_rate": float(np.count_nonzero(np.diff(np.signbit(xd)))) / secs,
        "max_slope": float(diffs.max() * fs),
        "stability": float(np.mean(np.abs(x - med) <= stab_tol)),
        "level": med,
        "energy": energy,
    }
    for name, (lo, hi) in BANDS.items():
        m = (fk >= lo) & (fk < hi)
        feats[name] = float(pk[m].sum() * df)
    if energy > 0 and len(pk) > 1:
        p = pk / pk.sum()
        nz = p[p > 0]
        feats["entropy"] = float(-(nz * np.log(nz)).sum() / math.log(len(p)))
        feats["centroid"] = float((fk * p).sum())
        feats["dominant"] = float(fk[int(np.argmax(pk))])
    else:
        feats["entropy"] = feats["centroid"] = feats["dominant"] = 0.0
    return feats


def activity(feats: dict[str, float], stab_tol: float) -> float:
    """log10 of the robust spread, floored at a tenth of the stream's tolerance so a
    perfectly flat (cached, quantized) stream reads as a finite quiet value."""
    s = feats.get("spread", math.nan)
    if not math.isfinite(s):
        return math.nan
    return math.log10(max(s, 0.0) + 0.1 * stab_tol)


def log_spectrum_bins(x: np.ndarray, fs: float, n_bins: int = 48) -> list[float]:
    """Spectrum folded into log-spaced bins (dB) for the dashboard waterfall."""
    f, psd = spectrum(x, fs)
    edges = np.geomspace(MIN_FREQ, fs / 2, n_bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (f >= lo) & (f < hi)
        p = psd[m].mean() if m.any() else psd[np.argmin(np.abs(f - lo))]
        out.append(round(10 * math.log10(p + 1e-12), 2))
    return out
