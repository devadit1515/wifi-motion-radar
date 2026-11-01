"""The sample model and the Source interface that every sensing backend implements.

Every backend, real or simulated, reduces to the same thing: named scalar streams of
(timestamp, value) pairs. A lost probe or missing reading is a NaN value, never a gap,
so loss itself stays measurable.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

# One monotonic clock for the whole process. Wall time is only attached at the edges
# (recording metadata, event log) so that NTP adjustments never bend a signal.
now = time.perf_counter


@dataclass(slots=True, frozen=True)
class Sample:
    stream: str
    t: float  # seconds on the `now()` clock
    v: float  # NaN marks a lost probe or unavailable reading


@dataclass(slots=True)
class StreamInfo:
    name: str  # e.g. "rtt.gw", "link.rssi", "drv.retry"
    kind: str  # rtt | rssi | quality | rate | retry | tx | fail | truth
    unit: str
    nominal_hz: float
    group: str  # probe | link | driver | truth
    label: str
    anchor: str | None = None
    # How to put this stream on a uniform grid: "linear" for sampled quantities,
    # "hold" for values a driver reports as a step function (cached RSSI, PHY rate).
    resample: str = "linear"
    # Half-width of the "stable" band used by the stability feature, in stream units.
    stab_tol: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Emit = Callable[[Sample], None]
Notify = Callable[[str, str], None]


class SourceUnavailable(RuntimeError):
    """The backend cannot run on this machine (wrong OS, no adapter, not connected)."""


class Source(ABC):
    kind = "live"  # live | sim | replay
    name = "source"

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.notify: Notify = lambda kind, msg: None

    @abstractmethod
    def streams(self) -> list[StreamInfo]: ...

    @abstractmethod
    def start(self, emit: Emit) -> None: ...

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for th in self._threads:
            th.join(timeout)
        self._threads.clear()

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name}

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        th = threading.Thread(target=target, name=name, daemon=True)
        th.start()
        self._threads.append(th)

    def _every(self, hz: float, tick: Callable[[], None]) -> None:
        """Call `tick` at a fixed rate until stopped.

        Uses time.sleep rather than Event.wait: on Windows, sleep has a high-resolution
        timer while lock timeouts round to the 15.6 ms scheduler tick, which would cap a
        100 Hz prober at about 64 Hz. If a tick overruns, the schedule resyncs instead of
        firing a burst of catch-up ticks, so the sample spacing stays honest.
        """
        period = 1.0 / hz
        nxt = now()
        while not self._stop.is_set():
            tick()
            nxt += period
            delay = nxt - now()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = now()


class CompositeSource(Source):
    """Runs several sources as one (e.g. the WLAN poller plus the ICMP probers)."""

    def __init__(self, parts: list[Source], kind: str = "live", name: str = "live") -> None:
        super().__init__()
        self.parts = parts
        self.kind = kind
        self.name = name

    def streams(self) -> list[StreamInfo]:
        return [s for p in self.parts for s in p.streams()]

    def start(self, emit: Emit) -> None:
        for p in self.parts:
            p.notify = self.notify
            p.start(emit)

    def stop(self, timeout: float = 2.0) -> None:
        for p in self.parts:
            p.stop(timeout)

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "parts": [p.describe() for p in self.parts]}
