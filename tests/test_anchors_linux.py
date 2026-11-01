"""Text parsers: `arp -a`, `route print`, /proc/net/wireless, `iw dev link`.

All addresses below are documentation/locally-administered examples.
"""

from radar.sources.anchors import AnchorStore, classify, parse_arp, parse_route_print
from radar.sources.linux import parse_iw_link, parse_proc_net_wireless

ARP = """
Interface: 192.168.0.54 --- 0xe
  Internet Address      Physical Address      Type
  192.168.0.1           02-aa-00-00-00-01     dynamic
  192.168.0.51          06-11-22-33-44-55     dynamic
  192.168.0.244         0a-4e-26-00-16-f7     dynamic
  192.168.0.245         0a-4e-26-00-2c-19     dynamic
  192.168.0.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
  239.255.255.250       01-00-5e-7f-ff-fa     static
"""

ROUTE = """
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.0.1     192.168.0.54     35
          0.0.0.0          0.0.0.0       10.0.0.1         10.0.0.7     50
===========================================================================
"""


def test_parse_arp_keeps_dynamic_unicast_only():
    rows = parse_arp(ARP)
    assert ("192.168.0.1", "02:aa:00:00:00:01") in rows
    assert all(not ip.startswith(("224.", "239.")) for ip, _ in rows)
    assert all(not ip.endswith(".255") for ip, _ in rows)
    assert len(rows) == 4


def test_parse_route_print_picks_lowest_metric():
    assert parse_route_print(ROUTE) == "192.168.0.1"
    assert parse_route_print("no routes here") is None


def test_classify_uses_router_vendor_prefix_for_mesh_nodes():
    # The AP's BSSID shares the mesh nodes' OUI (0a:4e:26), as on a real mesh system.
    anchors = classify(parse_arp(ARP), "192.168.0.1", bssid="0a:4e:26:00:25:a5")
    by_id = {a.id: a for a in anchors}
    assert by_id["gw"].kind == "gateway" and by_id["gw"].enabled
    assert by_id["h244"].kind == "infrastructure" and by_id["h244"].enabled
    assert by_id["h51"].kind == "client" and not by_id["h51"].enabled


def test_anchor_store_persists_names_and_flags(tmp_path):
    store = AnchorStore(tmp_path / "anchors.json")
    anchors = classify(parse_arp(ARP), "192.168.0.1", bssid=None)
    store.update(anchors, {"h51": {"name": "TV", "enabled": True}})
    again = store.merge(classify(parse_arp(ARP), "192.168.0.1", bssid=None))
    tv = next(a for a in again if a.id == "h51")
    assert tv.name == "TV" and tv.enabled


PROC = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
wlp2s0: 0000   54.  -56.  -256        0      0      0      0     12        0
"""

IW = """Connected to 02:00:5e:00:00:01 (on wlp2s0)
        SSID: TestNet
        freq: 5240.0
        RX: 1234 bytes (10 packets)
        signal: -56 dBm
        tx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
"""


def test_parse_proc_net_wireless():
    row = parse_proc_net_wireless(PROC)["wlp2s0"]
    assert row["level"] == -56 and row["link"] == 54


def test_parse_iw_link():
    d = parse_iw_link(IW)
    assert d["signal_dbm"] == -56
    assert d["bssid"] == "02:00:5e:00:00:01"
    assert d["freq_mhz"] == 5240.0
    assert d["tx_bitrate_mbps"] == 866.7
    assert parse_iw_link("Not connected.") == {"connected": False}
