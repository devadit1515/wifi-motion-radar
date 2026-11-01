"""Linux RSSI backend: /proc/net/wireless polled in a tight loop.

Secondary on this project (development happens on Windows), but kept working and
tested against captured output because Linux is where per-frame RSSI and monitor mode
live, which is the natural next tier.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from radar.sources.base import Emit, Sample, Source, SourceUnavailable, StreamInfo, now

PROC_WIRELESS = Path("/proc/net/wireless")
_IW_SIGNAL = re.compile(r"signal:\s*(-?\d+)\s*dBm")
_IW_BSSID = re.compile(r"Connected to ([0-9a-f:]{17})")
_IW_FREQ = re.compile(r"freq:\s*(\d+(?:\.\d+)?)")
_IW_TX = re.compile(r"tx bitrate:\s*([\d.]+)\s*MBit/s")


def parse_proc_net_wireless(text: str) -> dict[str, dict[str, float]]:
    """Per-interface link quality, signal level (dBm) and noise from /proc/net/wireless."""
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        cols = rest.split()
        if len(cols) < 4:
            continue

        def num(s: str) -> float:
            return float(s.rstrip("."))

        out[iface.strip()] = {"link": num(cols[1]), "level": num(cols[2]), "noise": num(cols[3])}
    return out


def parse_iw_link(text: str) -> dict[str, Any]:
    if "Not connected" in text:
        return {"connected": False}

    def grab(rx: re.Pattern[str]) -> str | None:
        m = rx.search(text)
        return m.group(1) if m else None

    sig, freq, tx = grab(_IW_SIGNAL), grab(_IW_FREQ), grab(_IW_TX)
    return {
        "connected": True,
        "bssid": grab(_IW_BSSID),
        "signal_dbm": int(sig) if sig else None,
        "freq_mhz": float(freq) if freq else None,
        "tx_bitrate_mbps": float(tx) if tx else None,
    }


class LinuxWlanSource(Source):
    name = "linux-wireless"

    def __init__(self, iface: str | None = None, hz: float = 50.0) -> None:
        super().__init__()
        if not PROC_WIRELESS.exists():
            raise SourceUnavailable("/proc/net/wireless not present")
        ifaces = parse_proc_net_wireless(PROC_WIRELESS.read_text())
        if not ifaces:
            raise SourceUnavailable("no wireless interface in /proc/net/wireless")
        self.iface = iface or next(iter(ifaces))
        self.hz = hz

    def streams(self) -> list[StreamInfo]:
        return [
            StreamInfo("link.rssi", "rssi", "dBm", self.hz, "link", "Link RSSI", resample="hold"),
            StreamInfo("link.quality", "quality", "/70", self.hz, "link", "Link quality",
                       resample="hold"),  # fmt: skip
        ]

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "iface": self.iface}

    def start(self, emit: Emit) -> None:
        self._stop.clear()

        def tick() -> None:
            t = now()
            row = parse_proc_net_wireless(PROC_WIRELESS.read_text()).get(self.iface)
            emit(Sample("link.rssi", t, row["level"] if row else math.nan))
            emit(Sample("link.quality", t, row["link"] if row else math.nan))

        self._spawn(lambda: self._every(self.hz, tick), "linux-wireless")
