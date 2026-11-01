"""Resampling, features and ring buffer behaviour on signals with known answers."""

import math

import numpy as np
import pytest

from radar.dsp.buffers import Ring, stream_stats
from radar.dsp.features import activity, log_spectrum_bins, spread, window_features
from radar.dsp.resample import to_grid


def test_linear_resample_recovers_a_ramp():
    t = np.linspace(0, 2, 41)
    _, y, q = to_grid(t, 3 * t, 0.0, 2.0, 50)
    assert q == 1.0
    np.testing.assert_allclose(y, 3 * (np.arange(100) / 50), atol=1e-9)


def test_lost_samples_are_bridged_but_long_gaps_lower_quality():
    t = np.arange(0, 2, 0.01)
    v = np.ones_like(t)
    v[50:60] = np.nan  # 100 ms of loss: bridged
    _, y, q = to_grid(t, v, 0, 2, 100, max_gap=0.5)
    assert q == 1.0 and np.all(y == 1)
    t2 = np.concatenate([t[:50], t[150:]])  # 1 s hole
    _, y2, q2 = to_grid(t2, np.ones_like(t2), 0, 2, 100, max_gap=0.2)
    assert 0.5 < q2 < 1.0
    assert np.all(np.isfinite(y2))


def test_hold_resample_is_a_step_function():
    t = np.array([0.0, 1.0])
    _, y, _ = to_grid(t, np.array([-60.0, -50.0]), 0, 2, 10, mode="hold", max_gap=5)
    assert np.all(y[:10] == -60) and np.all(y[10:] == -50)


def test_empty_stream_has_zero_quality():
    _, y, q = to_grid(np.array([]), np.array([]), 0, 2, 10)
    assert q == 0.0 and np.all(np.isnan(y))


def test_dominant_frequency_and_band_energy():
    fs = 100.0
    t = np.arange(200) / fs
    x = np.sin(2 * math.pi * 4.0 * t)
    f = window_features(x, fs)
    assert f["dominant"] == pytest.approx(4.0, abs=0.5)
    assert f["b_mid"] > 10 * f["b_low"] and f["b_mid"] > 10 * f["b_high"]


def test_spread_matches_std_for_gaussian_and_ignores_one_spike():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.3, 2000)
    assert spread(x) == pytest.approx(0.3, rel=0.1)
    spiked = x.copy()
    spiked[100] = 50.0
    assert spread(spiked) == pytest.approx(spread(x), rel=0.05)
    assert np.std(spiked) > 3 * np.std(x)


def test_flat_stream_is_stable_and_activity_is_floored():
    x = np.full(200, -58.0)
    f = window_features(x, 10.0, stab_tol=1.0)
    assert f["stability"] == 1.0 and f["std"] < 1e-9
    assert activity(f, 1.0) == pytest.approx(math.log10(0.1))


def test_log_spectrum_bins_shape():
    bins = log_spectrum_bins(np.random.default_rng(1).normal(size=200), 100.0, n_bins=48)
    assert len(bins) == 48 and all(math.isfinite(b) for b in bins)


def test_ring_wraps_and_returns_ordered_windows():
    r = Ring(10)
    for i in range(25):
        r.append(float(i), float(i))
    t, v = r.since(18.0)
    assert list(t) == [18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0]
    t2, _, seq = r.after(20)
    assert list(t2) == [20.0, 21.0, 22.0, 23.0, 24.0] and seq == 25
    t3, _, _ = r.after(0)  # older than capacity: only what is still held
    assert len(t3) == 10


def test_stream_stats_separates_poll_rate_from_refresh_rate():
    # Polled at 10 Hz for 10 s but the value only changes once a second, in 1 dB steps.
    t = np.arange(0, 10, 0.1)
    v = np.floor(t) % 2 - 60
    st = stream_stats(t, v, now_t=10.0, span=10.0)
    assert st.poll_hz == pytest.approx(10, rel=0.02)
    assert st.refresh_hz == pytest.approx(0.9, abs=0.15)
    assert st.quant_step == 1.0
    assert st.loss == 0.0
