"""Sonar range processing on synthetic echoes with known geometry."""

from dataclasses import replace

import numpy as np
import pytest

from radar.sonar.dsp import SPEED_OF_SOUND, RangeProcessor, SonarConfig

CFG = SonarConfig()
P = CFG.period
LAG = 1083  # where the direct path landed on the real machine


def shift(x: np.ndarray, d: float) -> np.ndarray:
    """Circular fractional delay: the sweep repeats, so echoes wrap around."""
    f = np.fft.rfftfreq(len(x))
    return np.fft.irfft(np.fft.rfft(x) * np.exp(-2j * np.pi * f * d), n=len(x))


def excess(r: float) -> float:
    return (r - CFG.device_offset_m) * 2 * CFG.fs / SPEED_OF_SOUND


def frames(n, target=None, direct_gap=31.0, mic_gap=12.0, noise=1e-4, seed=0, channels=2):
    """Yield (period, channels) blocks: direct path, fixed echoes, optional mover.

    target(i) -> range in metres for frame i, or None for an empty room. On mic 1 the
    direct path and fixed echoes arrive `direct_gap` samples late (as measured on the
    real machine) and the mover's echo `mic_gap` samples late: they differ because the
    speaker and the person sit at different angles to the two microphones.
    """
    rng = np.random.default_rng(seed)
    proc = RangeProcessor(CFG, channels)
    tx = proc.tx.astype(float)
    static = [(LAG, 1.0), (LAG + excess(0.6), 0.2), (LAG + excess(1.4), 0.08),
              (LAG + excess(3.2), 0.05)]  # fmt: skip
    for i in range(n):
        cols = []
        for c in range(channels):
            x = sum(a * shift(tx, d + direct_gap * c) for d, a in static)
            r = target(i) if target else None
            if r is not None:
                x = x + 0.02 * shift(tx, LAG + excess(r) + mic_gap * c)
            cols.append(x + noise * rng.standard_normal(P))
        yield np.column_stack(cols).astype(np.float32)


def run(gen, channels=2):
    proc = RangeProcessor(CFG, channels)
    return proc, [proc.process(b) for b in gen]


def test_still_room_has_no_target_and_locks_on_the_direct_path():
    proc, out = run(frames(60))
    assert proc.lag == LAG
    last = out[-1]
    assert last.health.status == "ok" and last.health.snr_db > 30
    assert all(f.target is None for f in out[20:])
    assert abs(last.motion) < 0.5
    assert any(abs(e - 0.6) < 0.1 for e in last.echoes)  # the fixed echo is reported


def test_walking_person_is_found_at_the_right_distance_and_side():
    def walk(i):
        return None if i < 30 else 2.0 + 0.15 * np.sin(i / 3)

    proc, out = run(frames(90, target=walk))
    seen = [f.target for f in out[40:] if f.target is not None]
    assert len(seen) > 40
    r = np.median([t.r for t in seen])
    assert abs(r - 2.0) < 0.25
    pd = np.median([t.path_diff for t in seen if t.path_diff is not None])
    # The mover's echo reaches mic 1 twelve samples after mic 0.
    assert pd == pytest.approx(12 * SPEED_OF_SOUND / CFG.fs, abs=0.03)
    assert proc.direct_diff_m == pytest.approx(31 * SPEED_OF_SOUND / CFG.fs, abs=0.01)
    assert np.median([f.motion for f in out[40:]]) > 0.3


def test_a_person_standing_still_is_found_by_their_sway():
    # Sway of 0.15 samples of path (about half a millimetre of body movement) at
    # 0.3 Hz: far too small for the frame-to-frame detector, visible to presence.
    def sway(i):
        return 1.5 + 0.15 / (2 * CFG.fs / SPEED_OF_SOUND) * np.sin(2 * np.pi * 0.3 * i * P / CFG.fs)

    # Switch the walking tracker off so only the presence channel can report.
    proc = RangeProcessor(replace(CFG, start_ratio=1e9), 2)
    out = [proc.process(b) for b in frames(150, target=sway)]
    late = [f.target for f in out[60:]]
    assert sum(t is not None for t in late) > 0.8 * len(late)
    assert all(t.still and not t.moving for t in late if t)
    assert abs(np.median([t.r for t in late if t]) - 1.5) < 0.2
    assert np.median([f.presence for f in out[60:]]) > np.log10(CFG.presence_ratio)


def test_dead_microphone_is_reported_as_silent():
    proc = RangeProcessor(CFG, 2)
    f = proc.process(np.zeros((P, 2), np.float32))
    assert f.health.status == "silent" and "mute" in f.health.message


def test_dropped_samples_trigger_a_relock():
    blocks = list(frames(80))
    stream = np.concatenate(blocks)
    stream = np.concatenate([stream[: 40 * P], stream[40 * P + 300 :]])  # lose 300 samples
    proc = RangeProcessor(CFG, 2)
    n = len(stream) // P
    for i in range(n):
        proc.process(stream[i * P : (i + 1) * P])
    assert proc.lag is not None and proc.lag != LAG


def test_single_microphone_gives_distance_without_direction():
    def walk(i):
        return None if i < 30 else 1.5 + 0.1 * np.sin(i / 3)

    _, out = run(frames(80, target=walk, channels=1), channels=1)
    seen = [f.target for f in out[40:] if f.target]
    assert seen and all(t.path_diff is None for t in seen)
    assert abs(np.median([t.r for t in seen]) - 1.5) < 0.25
