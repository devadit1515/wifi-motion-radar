"""End to end on the simulator: physics → streams → offline features → separability."""

import numpy as np

from radar.dsp.detect import DetectorConfig, MotionDetector
from radar.offline import label_for, samples_to_frame, session_windows
from radar.probe import ProbeRun, auc
from radar.sources.simulated import HomeSim, sim_streams, simulate


def test_simulator_emits_every_declared_stream_with_ground_truth():
    samples, labels = simulate(20, seed=3, schedule=[("empty", 10), ("walking", 10)])
    names = {s.stream for s in samples}
    declared = {s.name for s in sim_streams(HomeSim(seed=3).anchor_ids, 100.0)}
    assert declared <= names
    assert [lab["label"] for lab in labels] == ["empty", "walking"]
    assert abs(labels[0]["end"] - 10.0) < 0.05


def _windows(seed, schedule, hop=0.5, load=True):
    samples, labels = simulate(
        sum(s for _, s in schedule), seed=seed, schedule=schedule, load=load
    )
    infos = sim_streams(HomeSim(seed=seed).anchor_ids, 100.0)
    return session_windows(samples_to_frame(samples), infos, labels, hop_s=hop), infos


def _auc(w, stream, neg, pos):
    a = w.loc[w["label"] == neg, f"{stream}.act"].dropna().to_numpy()
    b = w.loc[w["label"] == pos, f"{stream}.act"].dropna().to_numpy()
    return auc(a, b)


def test_walking_separates_from_empty_on_rtt_but_still_does_not():
    rtt = ["rtt.gw", "rtt.h244", "rtt.h245", "rtt.h246"]
    w, _ = _windows(11, [("empty", 60), ("walking", 60)])
    walking = max(_auc(w, s, "empty", "walking") for s in rtt)
    w2, _ = _windows(11, [("empty", 60), ("still", 60)])
    still = max(_auc(w2, s, "empty", "still") for s in rtt)
    assert walking > 0.75
    # The honest limit, reproduced: a motionless person barely changes round trips.
    assert still < walking - 0.1


def test_household_traffic_is_a_confound_for_rtt():
    # With bursts on and nobody home, RTT activity still rises at times. This is why
    # the probe measures false alarms in the user's own home instead of assuming none.
    sched = [("empty", 240)]
    quiet, _ = _windows(23, sched, load=False)
    busy, _ = _windows(23, sched, load=True)
    assert busy["rtt.gw.act"].quantile(0.95) > quiet["rtt.gw.act"].quantile(0.95) + 0.2


def test_fused_detector_on_simulated_home():
    sched = [("empty", 60), ("walking", 60), ("empty", 60), ("walking", 60)]
    w, infos = _windows(23, sched, hop=0.25, load=False)
    det = MotionDetector(DetectorConfig(calib_s=15))
    det.set_streams({i.name: i.kind for i in infos})
    acts = [c for c in w.columns if c.endswith(".act")]
    w["motion"] = [
        det.update(r["t"], {c[:-4]: r[c] for c in acts}, r["t"]).state == "motion"
        for _, r in w.iterrows()
    ]
    scored = w[w["t"] > 20]
    assert scored.loc[scored["label"] == "walking", "motion"].mean() > 0.8
    assert scored.loc[scored["label"] == "empty", "motion"].mean() < 0.25


def test_windows_straddling_a_transition_are_unlabelled():
    labels = [{"start": 0, "end": 10, "label": "empty"}, {"start": 10, "end": 20, "label": "x"}]
    assert label_for(labels, 8, 10) == "empty"
    assert label_for(labels, 9, 11) is None


def test_probe_run_scores_streams_and_detector():
    phases = [{"label": "empty", "seconds": 10}, {"label": "walking", "seconds": 10}]
    run = ProbeRun(phases, t_start=0.0)
    rng = np.random.default_rng(0)
    t = 0.0
    while not run.done(t):
        lab = run.label_at(t)
        moving = lab == "walking"
        run.observe(t, {"good": rng.normal(1.0 if moving else 0.0, 0.2),
                        "bad": rng.normal(0.0, 0.2)}, motion=moving)  # fmt: skip
        t += 0.1
    res = run.result()
    assert res["streams"]["good"]["verdict"] == "strong"
    assert res["streams"]["bad"]["verdict"] in ("none", "weak")
    assert res["best"]["stream"] == "good"
    assert res["detector"]["hit_rate"] == 1.0 and res["detector"]["false_alarm_rate"] == 0.0
    assert res["streams"]["good"]["weight"] > res["streams"]["bad"]["weight"]
