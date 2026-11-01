"""Capture raw wlanapi buffers from this machine as test fixtures.

The SSID, profile name and BSSID are overwritten before anything is written, so the
fixtures are safe to commit. Run on Windows with WiFi connected:

    python scripts/capture_fixtures.py
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from radar.sources.win_wlan import (
    DOT11_SSID,
    OP_CURRENT_CONNECTION,
    OP_REALTIME_CONNECTION_QUALITY,
    OP_STATISTICS,
    WLAN_ASSOCIATION_ATTRIBUTES,
    WLAN_CONNECTION_ATTRIBUTES,
    WlanClient,
)

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
FAKE_NAME = "TestNet"
FAKE_BSSID = bytes([0x02, 0x00, 0x5E, 0x00, 0x00, 0x01])  # locally administered


def scrub_connection(buf: bytes) -> bytes:
    b = bytearray(buf)
    name_off = WLAN_CONNECTION_ATTRIBUTES.strProfileName.offset
    b[name_off : name_off + 512] = FAKE_NAME.encode("utf-16-le").ljust(512, b"\0")
    assoc = WLAN_CONNECTION_ATTRIBUTES.wlanAssociationAttributes.offset
    ssid = assoc + WLAN_ASSOCIATION_ATTRIBUTES.dot11Ssid.offset
    b[ssid : ssid + 4] = len(FAKE_NAME).to_bytes(4, "little")
    ucssid = ssid + DOT11_SSID.ucSSID.offset
    b[ucssid : ucssid + 32] = FAKE_NAME.encode().ljust(32, b"\0")
    bssid = assoc + WLAN_ASSOCIATION_ATTRIBUTES.dot11Bssid.offset
    b[bssid : bssid + 6] = FAKE_BSSID
    assert len(b) == ctypes.sizeof(WLAN_CONNECTION_ATTRIBUTES)
    return bytes(b)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = WlanClient()
    conn = c.query(OP_CURRENT_CONNECTION)
    rtcq = c.query(OP_REALTIME_CONNECTION_QUALITY)
    stats = c.query(OP_STATISTICS)
    if conn:
        (OUT / "wlan_connection.bin").write_bytes(scrub_connection(conn))
    if rtcq:
        (OUT / "wlan_rtcq.bin").write_bytes(rtcq)
    if stats:
        (OUT / "wlan_statistics.bin").write_bytes(stats)
    c.close()
    print(f"wrote fixtures to {OUT}")


if __name__ == "__main__":
    main()
