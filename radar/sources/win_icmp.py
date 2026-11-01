"""Active probing: ICMP echo to fixed anchors at a steady high rate (Tier 1b).

Uses IcmpSendEcho from iphlpapi.dll, which needs no admin rights (raw sockets do).
The round trip is timed with perf_counter around the blocking call rather than read
from the reply's RoundTripTime field, which only has 1 ms resolution; LAN round trips
over WiFi are around 2 ms, so the field would quantize away the signal.

Measured on the development laptop: 100.1 Hz sustained to the gateway, 0 loss,
RTT median 2.35 ms (p10 1.86, p90 2.94).
"""

from __future__ import annotations

import ctypes
import math
import socket
import struct
import sys
from ctypes import wintypes as wt
from typing import Any

from radar.sources.anchors import Anchor
from radar.sources.base import Emit, Sample, Source, SourceUnavailable, StreamInfo, now

IP_SUCCESS = 0


def _load_iphlpapi() -> Any:
    if sys.platform != "win32":
        raise SourceUnavailable("IcmpSendEcho is Windows-only")
    api = ctypes.WinDLL("iphlpapi.dll")
    api.IcmpCreateFile.restype = wt.HANDLE
    api.IcmpCloseHandle.argtypes = [wt.HANDLE]
    api.IcmpSendEcho.argtypes = [
        wt.HANDLE, wt.ULONG, ctypes.c_void_p, wt.WORD,
        ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, wt.DWORD,
    ]  # fmt: skip
    api.IcmpSendEcho.restype = wt.DWORD
    return api


class Pinger:
    """One ICMP handle aimed at one address. Not shared across threads."""

    def __init__(self, api: Any, ip: str, payload: int = 32, timeout_ms: int = 250) -> None:
        self._api = api
        self._h = api.IcmpCreateFile()
        if not self._h or self._h == wt.HANDLE(-1).value:
            raise SourceUnavailable("IcmpCreateFile failed")
        # IPAddr is the address in network byte order, read as a native ULONG.
        self._dst = int.from_bytes(socket.inet_aton(ip), "little")
        self._payload = ctypes.create_string_buffer(payload)
        self._reply = ctypes.create_string_buffer(64 + payload + 8)
        self.timeout_ms = timeout_ms

    def ping(self) -> float:
        """Round-trip time in milliseconds, or NaN if the probe was lost."""
        t0 = now()
        n = self._api.IcmpSendEcho(
            self._h, self._dst, self._payload, len(self._payload),
            None, self._reply, len(self._reply), self.timeout_ms,
        )  # fmt: skip
        t1 = now()
        if n == 0:
            return math.nan
        status = struct.unpack_from("<I", self._reply.raw, 4)[0]
        return (t1 - t0) * 1000.0 if status == IP_SUCCESS else math.nan

    def close(self) -> None:
        if self._h:
            self._api.IcmpCloseHandle(self._h)
            self._h = None


class IcmpSource(Source):
    """One prober thread per anchor, each at a fixed rate. The gateway gets the full
    rate; other anchors a lower one, to keep our own airtime use modest."""

    name = "icmp"

    def __init__(
        self,
        anchors: list[Anchor],
        hz: float = 100.0,
        aux_hz: float = 50.0,
        timeout_ms: int = 250,
    ) -> None:
        super().__init__()
        self._api = _load_iphlpapi()
        self.anchors = [a for a in anchors if a.enabled]
        self.hz = hz
        self.aux_hz = aux_hz
        self.timeout_ms = timeout_ms

    def rate(self, a: Anchor) -> float:
        return self.hz if a.kind == "gateway" else self.aux_hz

    def streams(self) -> list[StreamInfo]:
        return [
            StreamInfo(
                f"rtt.{a.id}", "rtt", "ms", self.rate(a), "probe", f"RTT · {a.name}",
                anchor=a.id, stab_tol=0.5,
            )  # fmt: skip
            for a in self.anchors
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "hz": self.hz,
            "aux_hz": self.aux_hz,
            "anchors": [a.public() for a in self.anchors],
        }

    def start(self, emit: Emit) -> None:
        self._stop.clear()
        for a in self.anchors:
            self._spawn(lambda a=a: self._probe(a, emit), f"icmp-{a.id}")

    def _probe(self, anchor: Anchor, emit: Emit) -> None:
        pinger = Pinger(self._api, anchor.ip, timeout_ms=self.timeout_ms)
        stream = f"rtt.{anchor.id}"

        def tick() -> None:
            t = now()
            emit(Sample(stream, t, pinger.ping()))

        try:
            self._every(self.rate(anchor), tick)
        finally:
            pinger.close()


def ping_once(ip: str, timeout_ms: int = 300) -> float:
    """Single probe, used to check that a candidate anchor answers before enabling it."""
    p = Pinger(_load_iphlpapi(), ip, timeout_ms=timeout_ms)
    try:
        return p.ping()
    finally:
        p.close()
