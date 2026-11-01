"""macOS RSSI backend via CoreWLAN (pyobjc). Untested: no Mac in the loop.

`airport` and `wdutil` are deprecated or removed on recent macOS, so this talks to
CoreWLAN directly. Recent macOS versions also require Location Services permission
before CoreWLAN returns network details.
"""

from __future__ import annotations

import math
from typing import Any

from radar.sources.base import Emit, Sample, Source, SourceUnavailable, StreamInfo, now


class MacWlanSource(Source):
    name = "corewlan"

    def __init__(self, hz: float = 10.0) -> None:
        super().__init__()
        try:
            import CoreWLAN  # type: ignore[import-not-found]
        except ImportError as e:
            raise SourceUnavailable("pyobjc-framework-CoreWLAN is not installed") from e
        self._iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
        if self._iface is None:
            raise SourceUnavailable("no WiFi interface")
        self.hz = hz

    def streams(self) -> list[StreamInfo]:
        return [
            StreamInfo("link.rssi", "rssi", "dBm", self.hz, "link", "Link RSSI", resample="hold"),
            StreamInfo("link.rate", "rate", "Mbps", self.hz, "link", "Tx rate", resample="hold",
                       stab_tol=50.0),  # fmt: skip
        ]

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name}

    def start(self, emit: Emit) -> None:
        self._stop.clear()

        def tick() -> None:
            t = now()
            rssi = self._iface.rssiValue()
            emit(Sample("link.rssi", t, float(rssi) if rssi else math.nan))
            emit(Sample("link.rate", t, float(self._iface.transmitRate())))

        self._spawn(lambda: self._every(self.hz, tick), "corewlan")
