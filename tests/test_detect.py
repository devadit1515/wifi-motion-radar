"""Detector state machine on synthetic activity sequences (0.1 s hop)."""

import numpy as np

from radar.dsp.detect import DetectorConfig, MotionDetector

HOP = 0.1


def run(det, acts, t0=0.0):
    outs = []
    for i, a in enumerate(acts):
        t = t0 + i * HOP
        outs.append(det.update(t, {"rtt.gw": a}, wall=1000.0 + t))
    return outs


def make(**kw):
    det = MotionDetector(DetectorConfig(calib_s=10.0, **kw))
    det.set_streams({"rtt.gw": "rtt"})
    return det


def test_calibrates_then_detects_a_step_and_releases_with_hysteresis():
    rng = np.random.default_rng(0)
    det = make(presence_hold_s=20.0)
    quiet = rng.normal(-0.6, 0.04, 150)
    outs = run(det, quiet)
    assert outs[0].state == "calibrating"
    assert outs[-1].state == "empty"

    moving = rng.normal(-0.1, 0.05, 50)  # +0.5 decades: a clear broadening
    outs = run(det, moving, t0=15.0)
    first_motion = next(i for i, o in enumerate(outs) if o.state == "motion")
    assert first_motion <= 4  # within ~0.4 s
    assert any(e["kind"] == "motion_start" for o in outs for e in o.events)

    outs = run(det, rng.normal(-0.6, 0.04, 100), t0=20.0)
    states = [o.state for o in outs]
    assert states[0] == "motion"  # held through the hold time
    assert "settled" in states
    assert outs[-1].last_motion_wall is not None

    outs = run(det, rng.normal(-0.6, 0.04, 250), t0=30.0)
    assert outs[-1].state == "empty"


def test_a_single_spike_does_not_trigger():
    rng = np.random.default_rng(1)
    det = make(cusum_h=1e9)
    run(det, rng.normal(-0.6, 0.04, 150))
    spike = np.full(30, -0.6)
    spike[10] = 1.5
    outs = run(det, spike, t0=15.0)
    assert all(o.state != "motion" for o in outs)


def test_quiet_hour_has_no_false_alarms():
    rng = np.random.default_rng(2)
    det = make()
    outs = run(det, rng.normal(-0.6, 0.04, 36_000))  # one hour of windows
    assert sum(e["kind"] == "motion_start" for o in outs for e in o.events) == 0


def test_baseline_tracks_slow_drift_without_alarming():
    rng = np.random.default_rng(3)
    det = make()
    drift = np.linspace(0, 0.3, 6000)  # +0.3 decades over 10 minutes
    acts = -0.6 + drift + rng.normal(0, 0.04, 6000)
    outs = run(det, acts)
    assert sum(e["kind"] == "motion_start" for o in outs for e in o.events) == 0


def test_streams_with_zero_weight_are_ignored():
    det = MotionDetector(DetectorConfig(calib_s=1.0))
    det.set_streams({"drv.tx": "tx", "truth.state": "truth"})
    for i in range(50):
        det.update(i * HOP, {"drv.tx": 0.0}, 0.0)
    out = det.update(5.0, {"drv.tx": 10.0}, 0.0)
    assert out.z == 0.0 and out.state != "motion"
