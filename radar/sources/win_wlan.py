"""Windows Native WiFi backend (wlanapi.dll via ctypes, no admin rights needed).

Three readings are available, and they behave very differently. Measured on a MediaTek
MT7925 under Windows 11 over 25 s of 100 Hz polling:

  * legacy RSSI opcode (0x10000102): never changed. The driver caches it.
  * realtime connection quality (opcode 19, Windows 11): link RSSI changed twice.
  * MAC/PHY statistics (0x10000101): retry and failure counters move continuously.

So link RSSI is exposed but treated as a slow stream, and the driver counters become
a fast one. The counters do not follow their documented meaning on this driver: at
idle "retry" climbs ~15k/s while ~40 frames/s are sent, and "multiple retry" is exactly
10% of "retry". A retry ratio is therefore meaningless, so the stream is the log of
the retry counter's rate, and the feasibility probe decides whether it carries motion.

The parsers are pure functions over bytes so they are tested against buffers captured
from real hardware.
"""

from __future__ import annotations

import ctypes
import hashlib
import math
import sys
from ctypes import wintypes as wt
from typing import Any

from radar.sources.base import Emit, Sample, Source, SourceUnavailable, StreamInfo, now

OP_CURRENT_CONNECTION = 7
OP_CHANNEL_NUMBER = 8
OP_REALTIME_CONNECTION_QUALITY = 19
OP_STATISTICS = 0x10000101
OP_RSSI = 0x10000102

INTERFACE_STATE_CONNECTED = 1


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [
        ("InterfaceGuid", GUID),
        ("strInterfaceDescription", ctypes.c_wchar * 256),
        ("isState", ctypes.c_uint),
    ]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", wt.DWORD),
        ("dwIndex", wt.DWORD),
        ("InterfaceInfo", WLAN_INTERFACE_INFO * 1),
    ]


class DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", wt.ULONG), ("ucSSID", ctypes.c_ubyte * 32)]


class WLAN_ASSOCIATION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", ctypes.c_uint),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11PhyType", ctypes.c_uint),
        ("uDot11PhyIndex", wt.ULONG),
        ("wlanSignalQuality", wt.ULONG),
        ("ulRxRate", wt.ULONG),
        ("ulTxRate", wt.ULONG),
    ]


class WLAN_SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("bSecurityEnabled", wt.BOOL),
        ("bOneXEnabled", wt.BOOL),
        ("dot11AuthAlgorithm", ctypes.c_uint),
        ("dot11CipherAlgorithm", ctypes.c_uint),
    ]


class WLAN_CONNECTION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("isState", ctypes.c_uint),
        ("wlanConnectionMode", ctypes.c_uint),
        ("strProfileName", ctypes.c_wchar * 256),
        ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES),
        ("wlanSecurityAttributes", WLAN_SECURITY_ATTRIBUTES),
    ]


class WLAN_RATE_SET(ctypes.Structure):
    _fields_ = [("uRateSetLength", wt.ULONG), ("usRateSet", wt.USHORT * 126)]


class WLAN_RTCQ_LINK_INFO(ctypes.Structure):
    _fields_ = [
        ("ucLinkID", ctypes.c_uint8),
        ("ulChannelCenterFrequencyMhz", wt.ULONG),
        ("ulBandwidth", wt.ULONG),
        ("lRssi", wt.LONG),
        ("wlanRateSet", WLAN_RATE_SET),
    ]


class WLAN_REALTIME_CONNECTION_QUALITY(ctypes.Structure):
    _fields_ = [
        ("dot11PhyType", ctypes.c_uint),
        ("ulLinkQuality", wt.ULONG),
        ("ulRxRate", wt.ULONG),  # kbit/s
        ("ulTxRate", wt.ULONG),
        ("bIsMLOConnection", wt.BOOL),
        ("ulNumLinks", wt.ULONG),
        ("linksInfo", WLAN_RTCQ_LINK_INFO * 1),
    ]


_MAC_FIELDS = (
    "tx", "rx", "wep_excluded", "tkip_local_mic_failures", "tkip_replays", "tkip_icv_errors",
    "ccmp_replays", "ccmp_decrypt_errors", "wep_undecryptable", "wep_icv_errors",
    "decrypt_success", "decrypt_failure",
)  # fmt: skip

_PHY_FIELDS = (
    "tx", "mcast_tx", "failed", "retry", "multiple_retry", "max_tx_lifetime_exceeded",
    "tx_fragments", "rts_success", "rts_failure", "ack_failure", "rx", "mcast_rx",
    "promiscuous_rx", "max_rx_lifetime_exceeded", "duplicates", "rx_fragments",
    "promiscuous_rx_fragments", "fcs_errors",
)  # fmt: skip


class WLAN_MAC_FRAME_STATISTICS(ctypes.Structure):
    _fields_ = [(f, ctypes.c_uint64) for f in _MAC_FIELDS]


class WLAN_PHY_FRAME_STATISTICS(ctypes.Structure):
    _fields_ = [(f, ctypes.c_uint64) for f in _PHY_FIELDS]


class WLAN_STATISTICS_HEADER(ctypes.Structure):
    """WLAN_STATISTICS without its trailing variable-length PhyCounters array."""

    _fields_ = [
        ("ullFourWayHandshakeFailures", ctypes.c_uint64),
        ("ullTKIPCounterMeasuresInvoked", ctypes.c_uint64),
        ("ullReserved", ctypes.c_uint64),
        ("MacUcastCounters", WLAN_MAC_FRAME_STATISTICS),
        ("MacMcastCounters", WLAN_MAC_FRAME_STATISTICS),
        ("dwNumberOfPhys", wt.DWORD),
    ]


def _mac(raw: Any) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def bssid_tag(bssid: str | None) -> str | None:
    """Short one-way tag for a BSSID. A raw BSSID can geolocate a home, so recordings
    store this tag instead: enough to notice a roam or a router swap, nothing more."""
    if not bssid:
        return None
    return hashlib.sha256(bssid.lower().encode()).hexdigest()[:8]


def parse_connection(buf: bytes) -> dict[str, Any]:
    c = WLAN_CONNECTION_ATTRIBUTES.from_buffer_copy(buf)
    a = c.wlanAssociationAttributes
    ssid = bytes(a.dot11Ssid.ucSSID[: a.dot11Ssid.uSSIDLength]).decode("utf-8", "replace")
    return {
        "state": c.isState,
        "ssid": ssid,
        "bssid": _mac(a.dot11Bssid),
        "phy_type": a.dot11PhyType,
        "signal_quality": a.wlanSignalQuality,
        "rx_rate_mbps": a.ulRxRate / 1000.0,
        "tx_rate_mbps": a.ulTxRate / 1000.0,
    }


def parse_rtcq(buf: bytes) -> dict[str, Any]:
    head = WLAN_REALTIME_CONNECTION_QUALITY.from_buffer_copy(buf)
    first = WLAN_REALTIME_CONNECTION_QUALITY.linksInfo.offset
    size = ctypes.sizeof(WLAN_RTCQ_LINK_INFO)
    links = []
    for i in range(max(1, head.ulNumLinks)):
        off = first + i * size
        if off + size > len(buf):
            break
        li = WLAN_RTCQ_LINK_INFO.from_buffer_copy(buf, off)
        links.append(
            {
                "freq_mhz": li.ulChannelCenterFrequencyMhz,
                "bandwidth": li.ulBandwidth,
                "rssi_dbm": li.lRssi,
            }
        )
    return {
        "phy_type": head.dot11PhyType,
        "link_quality": head.ulLinkQuality,
        "rx_rate_mbps": head.ulRxRate / 1000.0,
        "tx_rate_mbps": head.ulTxRate / 1000.0,
        "mlo": bool(head.bIsMLOConnection),
        "links": links,
    }


def parse_statistics(buf: bytes) -> dict[str, int]:
    """Sum the PHY counters across PHYs (only the active one moves) plus unicast MAC."""
    head = WLAN_STATISTICS_HEADER.from_buffer_copy(buf)
    first = ctypes.sizeof(WLAN_STATISTICS_HEADER)
    size = ctypes.sizeof(WLAN_PHY_FRAME_STATISTICS)
    totals = dict.fromkeys(_PHY_FIELDS, 0)
    for i in range(head.dwNumberOfPhys):
        off = first + i * size
        if off + size > len(buf):
            break
        phy = WLAN_PHY_FRAME_STATISTICS.from_buffer_copy(buf, off)
        for f in _PHY_FIELDS:
            totals[f] += getattr(phy, f)
    totals["mac_tx"] = head.MacUcastCounters.tx
    totals["mac_rx"] = head.MacUcastCounters.rx
    totals["n_phys"] = head.dwNumberOfPhys
    return totals


class WlanClient:
    """Thin wrapper over a wlanapi handle bound to one interface."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise SourceUnavailable("wlanapi is Windows-only")
        try:
            api = ctypes.WinDLL("wlanapi.dll")
        except OSError as e:
            raise SourceUnavailable(f"wlanapi.dll not loadable: {e}") from e
        api.WlanOpenHandle.argtypes = [
            wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.HANDLE)
        ]
        api.WlanEnumInterfaces.argtypes = [
            wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_INFO_LIST))
        ]
        api.WlanQueryInterface.argtypes = [
            wt.HANDLE, ctypes.POINTER(GUID), ctypes.c_uint, ctypes.c_void_p,
            ctypes.POINTER(wt.DWORD), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int),
        ]  # fmt: skip
        api.WlanFreeMemory.argtypes = [ctypes.c_void_p]
        api.WlanCloseHandle.argtypes = [wt.HANDLE, ctypes.c_void_p]
        self._api = api
        self._h = wt.HANDLE()
        version = wt.DWORD()
        rc = api.WlanOpenHandle(2, None, ctypes.byref(version), ctypes.byref(self._h))
        if rc != 0:
            raise SourceUnavailable(f"WlanOpenHandle failed ({rc}); is the WLAN service running?")
        self.interfaces = self._enum()
        connected = [i for i in self.interfaces if i["state"] == INTERFACE_STATE_CONNECTED]
        if not connected:
            self.close()
            raise SourceUnavailable("no connected WiFi interface")
        self.interface = connected[0]
        self._guid = connected[0]["guid"]

    def _enum(self) -> list[dict[str, Any]]:
        plist = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        if self._api.WlanEnumInterfaces(self._h, None, ctypes.byref(plist)) != 0:
            return []
        try:
            n = plist.contents.dwNumberOfItems
            arr = ctypes.cast(
                ctypes.byref(plist.contents.InterfaceInfo), ctypes.POINTER(WLAN_INTERFACE_INFO * n)
            ).contents
            return [
                {
                    "guid": GUID.from_buffer_copy(info.InterfaceGuid),
                    "description": info.strInterfaceDescription,
                    "state": info.isState,
                }
                for info in arr
            ]
        finally:
            self._api.WlanFreeMemory(plist)

    def query(self, opcode: int) -> bytes | None:
        size = wt.DWORD()
        ptr = ctypes.c_void_p()
        vtype = ctypes.c_int()
        rc = self._api.WlanQueryInterface(
            self._h, ctypes.byref(self._guid), opcode, None,
            ctypes.byref(size), ctypes.byref(ptr), ctypes.byref(vtype),
        )  # fmt: skip
        if rc != 0 or not ptr.value:
            return None
        try:
            return ctypes.string_at(ptr.value, size.value)
        finally:
            self._api.WlanFreeMemory(ptr)

    def connection(self) -> dict[str, Any] | None:
        b = self.query(OP_CURRENT_CONNECTION)
        return parse_connection(b) if b else None

    def rtcq(self) -> dict[str, Any] | None:
        b = self.query(OP_REALTIME_CONNECTION_QUALITY)
        return parse_rtcq(b) if b else None

    def statistics(self) -> dict[str, int] | None:
        b = self.query(OP_STATISTICS)
        return parse_statistics(b) if b else None

    def rssi(self) -> int | None:
        b = self.query(OP_RSSI)
        return int.from_bytes(b[:4], "little", signed=True) if b and len(b) >= 4 else None

    def channel(self) -> int | None:
        b = self.query(OP_CHANNEL_NUMBER)
        return int.from_bytes(b[:4], "little") if b and len(b) >= 4 else None

    def close(self) -> None:
        if self._h:
            self._api.WlanCloseHandle(self._h, None)
            self._h = wt.HANDLE()


class WlanSource(Source):
    """Polls the connected link and the driver counters from a single thread.

    One thread owns the handle, so there is no question of concurrent use. Counters are
    polled at `hz`; the link readings every `link_every` ticks.
    """

    name = "wlan"

    def __init__(self, hz: float = 20.0, link_every: int = 2) -> None:
        super().__init__()
        self.hz = hz
        self.link_every = link_every
        self.client = WlanClient()
        self.has_rtcq = self.client.rtcq() is not None
        self.has_stats = self.client.statistics() is not None
        conn = self.client.connection() or {}
        self.ssid = conn.get("ssid")
        self.bssid = conn.get("bssid")
        self.channel = self.client.channel()

    def streams(self) -> list[StreamInfo]:
        link_hz = self.hz / self.link_every
        out = [
            StreamInfo("link.rssi", "rssi", "dBm", link_hz, "link", "Link RSSI", resample="hold"),
            StreamInfo(
                "link.quality", "quality", "%", link_hz, "link", "Link quality", resample="hold"
            ),
            StreamInfo(
                "link.rate", "rate", "Mbps", link_hz, "link", "PHY rx rate",
                resample="hold", stab_tol=50.0,
            ),  # fmt: skip
        ]
        if self.has_stats:
            out += [
                StreamInfo(
                    "drv.retry", "retry", "log10/s", self.hz, "driver", "Retry counter rate",
                    stab_tol=0.05,
                ),  # fmt: skip
                StreamInfo("drv.tx", "tx", "frames/s", self.hz, "driver", "Tx frames", stab_tol=50),
                StreamInfo(
                    "drv.fail", "fail", "frames/s", self.hz, "driver", "Failed frames", stab_tol=1
                ),
            ]
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "adapter": self.client.interface["description"],
            "ssid": self.ssid,
            "bssid_tag": bssid_tag(self.bssid),
            "channel": self.channel,
            "realtime_quality_api": self.has_rtcq,
            "driver_counters": self.has_stats,
        }

    def start(self, emit: Emit) -> None:
        self._stop.clear()
        self._spawn(lambda: self._loop(emit), "wlan-poll")

    def _loop(self, emit: Emit) -> None:
        prev: dict[str, int] | None = None
        prev_t = 0.0
        tick = 0
        last_bssid_check = now()

        def poll() -> None:
            nonlocal prev, prev_t, tick, last_bssid_check
            t = now()
            if self.has_stats:
                s = self.client.statistics()
                if s is not None and prev is not None and t > prev_t:
                    d_tx = s["tx"] - prev["tx"]
                    d_retry = s["retry"] - prev["retry"]
                    d_fail = s["failed"] - prev["failed"]
                    dt = t - prev_t
                    # A negative delta means the driver reset its counters (reconnect).
                    if d_tx >= 0 and d_retry >= 0 and d_fail >= 0:
                        emit(Sample("drv.retry", t, math.log10(1.0 + d_retry / dt)))
                        emit(Sample("drv.tx", t, d_tx / dt))
                        emit(Sample("drv.fail", t, d_fail / dt))
                if s is not None:
                    prev, prev_t = s, t
            if tick % self.link_every == 0:
                self._poll_link(emit, t)
            tick += 1
            if t - last_bssid_check > 2.0:
                last_bssid_check = t
                conn = self.client.connection()
                if conn and conn["bssid"] != self.bssid:
                    old = bssid_tag(self.bssid)
                    self.bssid = conn["bssid"]
                    self.channel = self.client.channel()
                    self.notify("roam", f"Roamed from AP {old} to {bssid_tag(self.bssid)}")

        try:
            self._every(self.hz, poll)
        finally:
            self.client.close()

    def _poll_link(self, emit: Emit, t: float) -> None:
        q = self.client.rtcq() if self.has_rtcq else None
        if q is not None and q["links"]:
            emit(Sample("link.rssi", t, float(q["links"][0]["rssi_dbm"])))
            emit(Sample("link.quality", t, float(q["link_quality"])))
            emit(Sample("link.rate", t, q["rx_rate_mbps"]))
            return
        conn = self.client.connection()
        rssi = self.client.rssi()
        emit(Sample("link.rssi", t, float(rssi) if rssi is not None else math.nan))
        if conn:
            emit(Sample("link.quality", t, float(conn["signal_quality"])))
            emit(Sample("link.rate", t, conn["rx_rate_mbps"]))
