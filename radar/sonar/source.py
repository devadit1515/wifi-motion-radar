"""The sonar as a sensing source: plays the sweep, captures raw microphone audio, and
emits motion, range and mic-to-mic path difference, plus a rich frame for the Room view.

Devices are picked by name and driver, never by index: Windows renumbers devices when
a microphone is muted or unmuted. On Windows the capture path matters. The ordinary
paths run the microphone through voice processing that removes whatever the speakers
play, sweep included, so capture prefers WDM-KS (kernel streaming), which is raw.
WASAPI exclusive mode is avoided: on the development machine it reported 48 kHz while
delivering samples at 96 kHz.
"""

from __future__ import annotations

import collections
import logging
import math
import sys
import threading
import time
from typing import Any

import numpy as np

from radar.sonar.dsp import RangeProcessor, SonarConfig, SonarFrame
from radar.sources.base import Emit, Sample, Source, SourceUnavailable, StreamInfo, now

log = logging.getLogger("radar.sonar")


def _sd():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:
        raise SourceUnavailable(f"sounddevice is missing: pip install sounddevice ({e})") from e
    return sd


def pick_devices(sd, fs: int) -> tuple[int, int, str]:
    """(input index, output index, input path name)."""
    apis = [h["name"] for h in sd.query_hostapis()]
    devs = list(enumerate(sd.query_devices()))

    def api(d):
        return apis[d["hostapi"]]

    def ok_in(i, ch):
        try:
            sd.check_input_settings(device=i, samplerate=fs, channels=ch)
            return True
        except Exception:
            return False

    out = None
    for i, d in devs:
        if api(d) == "Windows WASAPI" and d["max_output_channels"] > 0 and "Speaker" in d["name"]:
            out = i
            break
    if out is None:
        out = sd.default.device[1]

    if sys.platform == "win32":
        for i, d in devs:
            is_mic = "Mic Array" in d["name"] or "Microphone" in d["name"]
            if (
                api(d) == "Windows WDM-KS"
                and is_mic
                and d["max_input_channels"] >= 1
                and ok_in(i, min(2, d["max_input_channels"]))
            ):
                return i, out, "WDM-KS (raw)"
        for i, d in devs:
            if api(d) == "Windows WASAPI" and d["max_input_channels"] > 0:
                return i, out, "WASAPI shared (voice processing may hide echoes)"
    return sd.default.device[0], out, "default"


class SonarSource(Source):
    name = "sonar"

    def __init__(self, cfg: SonarConfig | None = None) -> None:
        super().__init__()
        self.cfg = cfg or SonarConfig()
        self.sd = _sd()
        self.in_dev, self.out_dev, self.path = pick_devices(self.sd, self.cfg.fs)
        info = self.sd.query_devices(self.in_dev)
        self.channels = min(2, int(info["max_input_channels"]))
        self.in_name = info["name"]
        self.out_name = self.sd.query_devices(self.out_dev)["name"]
        self.proc = RangeProcessor(self.cfg, self.channels)
        self._latest: SonarFrame | None = None
        self._lock = threading.Lock()
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._have = 0
        self._out_pos = 0
        self._streams: list[Any] = []
        self._fps = 0.0
        self.error: str | None = None
        self.raw: list[np.ndarray] | None = None  # set to a list to keep every block

    def streams(self) -> list[StreamInfo]:
        hz = self.cfg.fs / self.cfg.period
        return [
            StreamInfo("sonar.motion", "sonar", "share", hz, "sonar", "Sonar motion",
                       stab_tol=0.1, activity="level"),  # fmt: skip
            StreamInfo("sonar.presence", "sonar_presence", "log ratio", hz, "sonar",
                       "Sonar presence", stab_tol=0.1, activity="level"),  # fmt: skip
            StreamInfo("sonar.range", "sonar_range", "m", hz, "sonar", "Sonar distance",
                       resample="hold", stab_tol=0.1),  # fmt: skip
            StreamInfo("sonar.path_diff", "sonar_bearing", "m", hz, "sonar", "Sonar mic difference",
                       resample="hold", stab_tol=0.02),  # fmt: skip
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "input": self.in_name,
            "output": self.out_name,
            "capture_path": self.path,
            "channels": self.channels,
            "band_hz": [self.cfg.f0, self.cfg.f1],
            "error": self.error,
        }

    # ---- audio callbacks (PortAudio threads: keep them tiny) ---------------------
    def _out_cb(self, out, frames, t, status) -> None:
        idx = (self._out_pos + np.arange(frames)) % self.cfg.period
        out[:] = self.proc.tx[idx, None]
        self._out_pos = (self._out_pos + frames) % self.cfg.period

    def _in_cb(self, data, frames, t, status) -> None:
        self._chunks.append(data[:, : self.channels].copy())

    # ---- lifecycle -------------------------------------------------------------
    def start(self, emit: Emit) -> None:
        self._stop.clear()
        sd = self.sd
        try:
            o = sd.OutputStream(device=self.out_dev, samplerate=self.cfg.fs, channels=2,
                                dtype="float32", callback=self._out_cb)  # fmt: skip
            i = sd.InputStream(device=self.in_dev, samplerate=self.cfg.fs, channels=self.channels,
                               dtype="float32", callback=self._in_cb)  # fmt: skip
            o.start()
            i.start()
            self._streams = [i, o]
        except Exception as e:  # device busy, unplugged, wrong format
            self.error = str(e)
            self.notify("sonar", f"Sonar could not start: {e}")
            log.warning("sonar could not start: %s", e)
            return
        self._spawn(lambda: self._work(emit), "sonar")

    def stop(self, timeout: float = 2.0) -> None:
        super().stop(timeout)
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams = []

    def _work(self, emit: Emit) -> None:
        P = self.cfg.period
        buf = np.zeros((0, self.channels), np.float32)
        frames = 0
        t_rate = time.perf_counter()
        while not self._stop.is_set():
            while self._chunks:
                buf = np.concatenate([buf, self._chunks.popleft()])
            if len(buf) < P:
                time.sleep(0.005)
                continue
            # Never fall far behind real time: if the worker stalled, skip whole
            # periods (keeps the sweep phase) instead of processing a backlog.
            if len(buf) > 8 * P:
                skip = (len(buf) // P - 2) * P
                buf = buf[skip:]
            block, buf = buf[:P], buf[P:]
            if self.raw is not None:
                self.raw.append(block)
            fr = self.proc.process(block)
            t = now()
            with self._lock:
                self._latest = fr
            emit(Sample("sonar.motion", t, fr.motion))
            emit(Sample("sonar.presence", t, fr.presence))
            tgt = fr.target
            emit(Sample("sonar.range", t, tgt.r if tgt else math.nan))
            pd = tgt.path_diff if tgt and tgt.path_diff is not None else math.nan
            emit(Sample("sonar.path_diff", t, pd))
            frames += 1
            el = time.perf_counter() - t_rate
            if el > 2.0:
                self._fps = frames / el
                frames = 0
                t_rate = time.perf_counter()

    def extras(self) -> dict[str, Any]:
        with self._lock:
            fr = self._latest
        cfg = self.cfg
        base: dict[str, Any] = {
            "bin_m": cfg.bin_m,
            "r_min": cfg.min_range_m,
            "r_max": cfg.max_range_m,
            "channels": self.channels,
            "fps": round(self._fps, 1),
            "capture_path": self.path,
            "error": self.error,
        }
        if fr is None:
            base["health"] = {"status": "starting" if not self.error else "error",
                              "input_db": None, "snr_db": None,
                              "message": self.error or "Starting audio."}  # fmt: skip
            return {"sonar": base}
        tgt = fr.target
        base.update(
            health=fr.health.to_dict(),
            motion=None if not math.isfinite(fr.motion) else round(fr.motion, 3),
            presence=None if not math.isfinite(fr.presence) else round(fr.presence, 3),
            bins=fr.bins,
            static_db=fr.static_db,
            echoes=fr.echoes,
            direct_diff_m=None if fr.direct_diff_m is None else round(fr.direct_diff_m, 3),
            target=None
            if tgt is None
            else {
                "r": round(tgt.r, 3),
                "path_diff": None if tgt.path_diff is None else round(tgt.path_diff, 4),
                "strength": round(tgt.strength, 1),
                "moving": tgt.moving,
                "still": tgt.still,
            },
        )
        return {"sonar": base}
