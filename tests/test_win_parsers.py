"""wlanapi struct layouts and parsers, against buffers captured from a real MT7925."""

import ctypes
from pathlib import Path

from radar.sources.win_wlan import (
    WLAN_CONNECTION_ATTRIBUTES,
    WLAN_PHY_FRAME_STATISTICS,
    WLAN_REALTIME_CONNECTION_QUALITY,
    WLAN_STATISTICS_HEADER,
    bssid_tag,
    parse_connection,
    parse_rtcq,
    parse_statistics,
)

FIX = Path(__file__).parent / "fixtures"


def test_struct_sizes_match_windows_sdk():
    # Sizes reported by WlanQueryInterface on Windows 11 for these opcodes.
    assert ctypes.sizeof(WLAN_CONNECTION_ATTRIBUTES) == 604
    assert ctypes.sizeof(WLAN_REALTIME_CONNECTION_QUALITY) == 296
    assert ctypes.sizeof(WLAN_STATISTICS_HEADER) == 224
    assert ctypes.sizeof(WLAN_PHY_FRAME_STATISTICS) == 144


def test_parse_connection_fixture_is_scrubbed():
    c = parse_connection((FIX / "wlan_connection.bin").read_bytes())
    assert c["ssid"] == "TestNet"
    assert c["bssid"] == "02:00:5e:00:00:01"
    assert c["state"] == 1  # connected
    assert 0 <= c["signal_quality"] <= 100
    assert c["rx_rate_mbps"] > 0


def test_parse_rtcq_fixture():
    q = parse_rtcq((FIX / "wlan_rtcq.bin").read_bytes())
    assert 0 <= q["link_quality"] <= 100
    assert q["rx_rate_mbps"] > 0
    assert len(q["links"]) >= 1
    link = q["links"][0]
    assert -100 < link["rssi_dbm"] < 0
    assert 2400 <= link["freq_mhz"] <= 7200


def test_parse_statistics_fixture():
    s = parse_statistics((FIX / "wlan_statistics.bin").read_bytes())
    assert s["n_phys"] == 7
    assert s["tx"] > 0
    assert s["retry"] >= 0


def test_connection_fixture_profile_name_is_scrubbed():
    raw = (FIX / "wlan_connection.bin").read_bytes()
    c = WLAN_CONNECTION_ATTRIBUTES.from_buffer_copy(raw)
    assert c.strProfileName == "TestNet"


def test_bssid_tag_is_short_stable_and_case_insensitive():
    tag = bssid_tag("AA:BB:CC:DD:EE:FF")
    assert tag == bssid_tag("aa:bb:cc:dd:ee:ff")
    assert len(tag) == 8
    int(tag, 16)
    assert bssid_tag(None) is None
