"""The live engine: sources → ring buffers → windows → features → detector → frames.

Sources push samples from their own threads into per-stream ring buffers. One engine
thread wakes every hop (0.1 s), cuts a 2 s window from every stream, resamples it,
computes features and activity, updates the detector, and publishes one JSON frame to
every subscriber (the WebSocket clients). Recording, the feasibility probe, the
occupancy timeline and Away Guard all hang off that same loop, so everything the
dashboard shows comes from a single, consistent pipeline.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from radar.dsp.buffers import Ring, stream_stats
from radar.dsp.detect import DetectorConfig, MotionDetector
from radar.dsp.features import activity, log_spectrum_bins, window_features
from radar.dsp.resample import to_grid
from radar.occupancy import EventLog, Guard, Timeline
from radar.probe import DEFAULT_PROTOCOL, ProbeRun
from radar.sources.base import Sample, Source, StreamInfo, now
from radar.store.sessions import Recorder, SessionStore

log = logging.getLogger("radar.engine")

WINDOW_S = 2.0
HOP_S = 0.1
RING_SECONDS = 60.0
HELLO_HISTORY_S = 20.0
STATE_CODE = {"calibrating": 0, "empty": 1, "settled": 2, "motion": 3}

Subscriber = Callable[[str], None]


def _num(x: float | None, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def _series(t: np.ndarray, v: np.ndarray, nd: int = 4) -> list[list[float | None]]:
    return [[round(float(a), 3) for a in t], [_num(b, nd) for b in v]]


class Engine:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.store = SessionStore(data_dir)
        self.cfg_path = data_dir / "config.json"
        cfg = self._load_cfg()
        self.detector = MotionDetector(DetectorConfig(**cfg.get("detector", {})))
        self.live_weights: dict[str, float] = cfg.get("weights", {})
        self.guard = Guard(**cfg.get("guard", {}))
        self.last_probe: dict[str, Any] | None = cfg.get("last_probe")
        self.sonar_enabled: bool = bool(cfg.get("sonar", False))

        self._live_events = EventLog(data_dir / "events.jsonl")
        self._mem_events = EventLog(None)
        self._live_timeline = Timeline(data_dir / "timeline")
        self._mem_timeline = Timeline(None)

        self.source: Source | None = None
        self.infos: dict[str, StreamInfo] = {}
        self.rings: dict[str, Ring] = {}
        self._sent: dict[str, int] = {}
        self.recorder: Recorder | None = None
        self.probe: ProbeRun | None = None
        self._probe_recording = False
        self.det_hist: deque[list[float]] = deque(maxlen=int(120 / HOP_S))
        self.subscribers: set[Subscriber] = set()
        self.seq = 0
        self.anchors: list[Any] = []
        self._lock = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None

    # ---- config ----------------------------------------------------------------
    def _load_cfg(self) -> dict[str, Any]:
        try:
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_cfg(self) -> None:
        cfg = {
            "detector": self.detector.cfg.to_dict(),
            "weights": self.live_weights,
            "guard": {"armed": self.guard.armed, "webhook": self.guard.webhook},
            "last_probe": self.last_probe,
            "sonar": self.sonar_enabled,
        }
        self.cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    # ---- lifecycle -------------------------------------------------------------
    @property
    def kind(self) -> str:
        return self.source.kind if self.source else "none"

    @property
    def events(self) -> EventLog:
        return self._live_events if self.kind == "live" else self._mem_events

    @property
    def timeline(self) -> Timeline:
        return self._live_timeline if self.kind == "live" else self._mem_timeline

    def clock(self) -> float:
        src = self.source
        if src is not None and getattr(src, "clocked", False):
            return float(src.vt)  # type: ignore[attr-defined]
        return now()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(2.0)
        with self._lock:
            if self.recorder:
                self.record_stop()
            if self.source:
                self.source.stop()

    def set_source(self, source: Source, anchors: list[Any] | None = None) -> None:
        with self._lock:
            if self.probe:
                self.probe_cancel()
            if self.recorder:
                self.record_stop()
            if self.source:
                self.source.stop()
            self.source = source
            self.anchors = anchors or []
            self.infos = {s.name: s for s in source.streams()}
            self.rings = {
                name: Ring(int(max(info.nominal_hz * 1.25, 20) * RING_SECONDS))
                for name, info in self.infos.items()
            }
            self._sent = dict.fromkeys(self.infos, 0)
            self.det_hist.clear()
            self.detector.reset()
            self.detector.set_streams({n: i.kind for n, i in self.infos.items()})
            self.detector.set_weights(self.live_weights if source.kind == "live" else {})
            source.notify = self._notify
            source.start(self.ingest)
        self._broadcast(json.dumps(self.hello()))

    def ingest(self, s: Sample) -> None:
        ring = self.rings.get(s.stream)
        if ring is not None:
            ring.append(s.t, s.v)
        rec = self.recorder
        if rec is not None:
            rec.add(s)

    def _notify(self, kind: str, msg: str) -> None:
        self.events.add({"kind": kind, "wall": time.time(), "detail": msg})

    # ---- subscribers -----------------------------------------------------------
    def subscribe(self, cb: Subscriber) -> None:
        self.subscribers.add(cb)

    def unsubscribe(self, cb: Subscriber) -> None:
        self.subscribers.discard(cb)

    def _broadcast(self, text: str) -> None:
        for cb in list(self.subscribers):
            try:
                cb(text)
            except Exception:  # a dead client must never stall the loop
                self.subscribers.discard(cb)

    # ---- main loop -------------------------------------------------------------
    def _loop(self) -> None:
        nxt = time.perf_counter()
        while self._running:
            try:
                with self._lock:
                    frame = self._tick()
                if frame is not None:
                    self._broadcast(json.dumps(frame))
            except Exception:
                log.exception("engine tick failed")
            nxt += HOP_S
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.perf_counter()

    def _tick(self) -> dict[str, Any] | None:
        src = self.source
        if src is None:
            return None
        if getattr(src, "clocked", False):
            if src.paused or src.finished:  # type: ignore[attr-defined]
                if self.seq % 5 == 0:
                    self.seq += 1
                    return {"type": "replay", "replay": src.status()}  # type: ignore[attr-defined]
                self.seq += 1
                return None
            speed = float(src.speed)  # type: ignore[attr-defined]
            steps = max(1, round(speed))
            dt = HOP_S * speed / steps
            events: list[dict[str, Any]] = []
            for _ in range(steps):
                t = src.vt + dt  # type: ignore[attr-defined]
                src.pump(t)  # type: ignore[attr-defined]
                result = self._analyze(t)
                events += result[-1]
            return self._frame(t, *result[:-1], events)
        t = now()
        result = self._analyze(t)
        return self._frame(t, *result)

    def _wall(self, t: float) -> float:
        src = self.source
        if src is not None and src.kind == "replay":
            return float(src.meta.get("created", 0.0)) + t  # type: ignore[attr-defined]
        return time.time()

    def _analyze(self, t: float):
        acts: dict[str, float] = {}
        quals: dict[str, float] = {}
        grids: dict[str, tuple[np.ndarray, float]] = {}
        for name, info in self.infos.items():
            if info.kind == "truth":
                continue
            tt, vv = self.rings[name].since(t - WINDOW_S - 0.25)
            fs = min(info.nominal_hz, 100.0)
            _, y, q = to_grid(tt, vv, t - WINDOW_S, t, fs, info.resample, max(0.5, 3 / fs))
            quals[name] = q
            if q < 0.5:
                continue
            feats = window_features(y, fs, info.stab_tol)
            acts[name] = (
                feats["level"] if info.activity == "level" else activity(feats, info.stab_tol)
            )
            grids[name] = (y, fs)

        wall = self._wall(t)
        out = self.detector.update(t, acts, wall)
        events = [dict(ev, source=self.kind) for ev in out.events]

        if self.probe is not None:
            events += self._probe_step(t, acts, out.state == "motion")

        for ev in list(events):
            alert = self.guard.check(ev) if self.kind == "live" else None
            if alert:
                events.append(dict(alert, source=self.kind))
        for ev in events:
            self.events.add(ev)
        if out.state != "calibrating":
            self.timeline.add(wall, out.state, out.z)
        self.det_hist.append([round(t, 3), round(out.z, 3), STATE_CODE[out.state]])
        return acts, quals, grids, out, events

    def _primary_stream(self, grids: dict[str, tuple[np.ndarray, float]]) -> str | None:
        fast = [n for n, (_, fs) in grids.items() if fs >= 40]
        pool = fast or list(grids)
        if not pool:
            return None
        return max(pool, key=lambda n: (self.detector.weight(n), self.infos[n].nominal_hz))

    def _frame(self, t, acts, quals, grids, out, events) -> dict[str, Any]:
        self.seq += 1
        self.flush_recorder()
        streams = {
            name: {
                "a": _num(acts.get(name)),
                "z": _num(out.stream_z.get(name), 3),
                "w": round(self.detector.weight(name), 3),
                "q": round(quals.get(name, 0.0), 2),
            }
            for name, info in self.infos.items()
            if info.kind != "truth"
        }
        traces = {}
        for name, ring in self.rings.items():
            tt, vv, seq = ring.after(self._sent[name], limit=600)
            self._sent[name] = seq
            if len(tt):
                traces[name] = _series(tt, vv)
        frame: dict[str, Any] = {
            "type": "frame",
            "seq": self.seq,
            "t": round(t, 3),
            "wall": round(self._wall(t), 3),
            "source": self.kind,
            "det": {
                "state": out.state,
                "z": _num(out.z, 3),
                "cusum": _num(out.cusum, 2),
                "p": _num(out.p_motion, 3),
                "last_motion": out.last_motion_wall,
                "calib": round(out.calib_progress, 3),
            },
            "streams": streams,
            "traces": traces,
            "events": events,
        }
        primary = self._primary_stream(grids)
        if primary:
            y, fs = grids[primary]
            frame["spec"] = {"stream": primary, "fs": fs, "bins": log_spectrum_bins(y, fs)}
        src = self.source
        if src is not None and hasattr(src, "truth"):
            frame["truth"] = src.truth()  # type: ignore[attr-defined]
        if src is not None:
            frame.update(src.extras())
        if src is not None and src.kind == "replay":
            frame["replay"] = src.status()  # type: ignore[attr-defined]
        if self.recorder:
            frame["rec"] = self._rec_status(t)
        if self.probe:
            frame["probe"] = self.probe.status(t)
        if self.seq % 10 == 0:
            frame["stats"] = self.stream_stats(t)
        return frame

    # ---- stats / capabilities ---------------------------------------------------
    def stream_stats(self, t: float | None = None, span: float = 10.0) -> dict[str, Any]:
        t = self.clock() if t is None else t
        return {
            name: stream_stats(*ring.since(t - span), t, span).to_dict()
            for name, ring in self.rings.items()
        }

    def capabilities(self) -> dict[str, Any]:
        with self._lock:
            src = self.source
            stats = self.stream_stats()
            return {
                "source": src.describe() if src else None,
                "streams": [
                    {**info.to_dict(), "stats": stats.get(name)}
                    for name, info in self.infos.items()
                ],
                "anchors": [a.to_dict() | {"mac": None} for a in self.anchors],
                "detector": {
                    "config": self.detector.cfg.to_dict(),
                    "baselines": self.detector.baseline_summary(),
                    "weights": {n: self.detector.weight(n) for n in self.infos},
                },
                "last_probe": self.last_probe,
            }

    def hello(self) -> dict[str, Any]:
        with self._lock:
            t = self.clock()
            history = {}
            for name, ring in self.rings.items():
                tt, vv = ring.since(t - HELLO_HISTORY_S)
                if len(tt) > 1200:
                    step = int(math.ceil(len(tt) / 1200))
                    tt, vv = tt[::step], vv[::step]
                history[name] = _series(tt, vv)
            src = self.source
            return {
                "type": "hello",
                "t": round(t, 3),
                "source": src.describe() if src else None,
                "streams": [i.to_dict() for i in self.infos.values()],
                "detector": self.detector.cfg.to_dict(),
                "guard": self.guard.to_dict(),
                "rec": self._rec_status(t) if self.recorder else None,
                "probe": self.probe.status(t) if self.probe else None,
                "last_probe": self.last_probe,
                "protocol": DEFAULT_PROTOCOL,
                "history": history,
                "det_history": list(self.det_hist),
                "events": self.events.recent(60),
                "window_s": WINDOW_S,
                "hop_s": HOP_S,
                "sonar_enabled": self.sonar_enabled,
            }

    # ---- controls --------------------------------------------------------------
    def calibrate(self) -> None:
        with self._lock:
            self.detector.calibrate()
            self.events.add({"kind": "calibrate", "wall": time.time(), "source": self.kind})

    def set_detector(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cfg = self.detector.cfg
            for k, v in patch.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, type(getattr(cfg, k))(v))
            self.save_cfg()
            return cfg.to_dict()

    def set_guard(self, armed: bool | None, webhook: str | None) -> dict[str, Any]:
        with self._lock:
            self.guard.set(armed, webhook)
            if armed is not None:
                self.events.add(
                    {"kind": "guard_armed" if armed else "guard_disarmed", "wall": time.time(),
                     "source": self.kind}
                )  # fmt: skip
            self.save_cfg()
            return self.guard.to_dict()

    def _rec_status(self, t: float) -> dict[str, Any]:
        rec = self.recorder
        assert rec is not None
        return {
            "id": rec.id,
            "elapsed": round(rec.elapsed(t), 1),
            "label": rec.label,
            "n": rec.n,
            "probe": self._probe_recording,
        }

    def record_start(self, setup: dict[str, Any] | None = None, tag: str = "") -> dict[str, Any]:
        with self._lock:
            if self.source is None:
                raise RuntimeError("no source")
            if self.source.kind == "replay":
                raise RuntimeError("cannot record a replay")
            if self.recorder:
                return self._rec_status(self.clock())
            self.recorder = Recorder(
                self.store, list(self.infos.values()), self.source.describe(),
                t0=self.clock(), meta=setup, tag=tag,
            )  # fmt: skip
            self.events.add({"kind": "rec_start", "wall": time.time(), "source": self.kind,
                             "detail": self.recorder.id})  # fmt: skip
            return self._rec_status(self.clock())

    def record_label(self, label: str | None) -> dict[str, Any]:
        with self._lock:
            if not self.recorder:
                raise RuntimeError("not recording")
            self.recorder.set_label(label, self.clock())
            return self._rec_status(self.clock())

    def record_stop(self) -> dict[str, Any] | None:
        with self._lock:
            rec, self.recorder = self.recorder, None
            self._probe_recording = False
            if rec is None:
                return None
            meta = rec.stop(self.clock())
            self.events.add({"kind": "rec_stop", "wall": time.time(), "source": self.kind,
                             "detail": rec.id})  # fmt: skip
            return {"id": rec.id, "duration": meta.get("duration"), "n": meta.get("n_samples")}

    def flush_recorder(self) -> None:
        rec = self.recorder
        if rec is not None:
            rec.flush()

    # ---- feasibility probe -----------------------------------------------------
    def probe_start(self, phases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        with self._lock:
            if self.source is None or self.source.kind == "replay":
                raise RuntimeError("probe needs a live or simulated source")
            t = self.clock()
            self.probe = ProbeRun(phases or DEFAULT_PROTOCOL, t)
            if self.recorder is None:
                self.record_start({"purpose": "feasibility probe"}, tag="probe")
                self._probe_recording = True
            return self.probe.status(t)

    def probe_cancel(self) -> None:
        with self._lock:
            self.probe = None
            if self._probe_recording:
                self.record_stop()

    def _probe_step(self, t: float, acts: dict[str, float], motion: bool) -> list[dict[str, Any]]:
        probe = self.probe
        assert probe is not None
        probe.observe(t, acts, motion)
        if self._probe_recording and self.recorder is not None:
            label = probe.label_at(t)
            if label != self.recorder.label:
                self.recorder.set_label(label, t)
        if not probe.done(t):
            return []
        result = probe.result()
        result["source"] = self.kind
        self.probe = None
        if self._probe_recording:
            stopped = self.record_stop()
            result["session"] = stopped["id"] if stopped else None
        weights = {s: r["weight"] for s, r in result["streams"].items()}
        if sum(weights.values()) > 0:
            self.detector.set_weights(weights)
            if self.kind == "live":
                self.live_weights = weights
        result["applied_weights"] = sum(weights.values()) > 0
        self.last_probe = result
        probes_dir = self.data_dir / "probes"
        probes_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (probes_dir / f"{stamp}-{self.kind}.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        self.save_cfg()
        return [{"kind": "probe_done", "wall": time.time(), "result": result}]

    # ---- replay / sim ----------------------------------------------------------
    def replay_control(
        self,
        paused: bool | None = None,
        speed: float | None = None,
        seek: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            src = self.source
            if src is None or src.kind != "replay":
                raise RuntimeError("not replaying")
            if paused is not None:
                src.paused = paused  # type: ignore[attr-defined]
            if speed is not None:
                src.speed = max(0.25, min(16.0, float(speed)))  # type: ignore[attr-defined]
            if seek is not None:
                src.seek(seek)  # type: ignore[attr-defined]
                for ring in self.rings.values():
                    ring.clear()
                self._sent = dict.fromkeys(self.infos, 0)
                self.detector.soft_reset()
            return src.status()  # type: ignore[attr-defined]

    def sim_mode(self, mode: str) -> dict[str, Any]:
        with self._lock:
            src = self.source
            if src is None or src.kind != "sim":
                raise RuntimeError("not simulating")
            src.sim.set_mode(mode)  # type: ignore[attr-defined]
            return src.truth()  # type: ignore[attr-defined]
