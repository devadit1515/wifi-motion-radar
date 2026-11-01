"""Probe anchors: fixed devices on the home network that the prober pings.

The gateway is always an anchor. Mesh nodes and extenders are the interesting extra
ones: they are mains-powered, never move, and sit in different rooms, so the path to
each crosses a different part of the home. That gives a spatial signal (which link is
being disturbed) without monitor mode. They are recognised by sharing the router's
vendor prefix (OUI). Phones and laptops are listed but disabled by default because
their power-save behaviour makes round-trip times erratic.

MAC addresses stay in data/anchors.json on this machine. Recordings and the committed
sample only ever see the anchor id, kind and the name the user gave it.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_ARP_LINE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\s+(\w+)", re.M
)
_ROUTE_LINE = re.compile(
    r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d{1,3}(?:\.\d{1,3}){3})\s+(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)",
    re.M,
)


@dataclass
class Anchor:
    id: str
    ip: str
    kind: str  # gateway | infrastructure | client
    name: str
    enabled: bool = True
    mac: str | None = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "name": self.name, "enabled": self.enabled}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_mac(mac: str) -> str:
    return mac.lower().replace("-", ":")


def _oui(mac: str | None) -> str | None:
    return _norm_mac(mac)[:8] if mac else None


def parse_arp(text: str) -> list[tuple[str, str]]:
    """Dynamic unicast entries from `arp -a` output, as (ip, mac)."""
    out = []
    for ip, mac, kind in _ARP_LINE.findall(text):
        addr = ipaddress.ip_address(ip)
        mac = _norm_mac(mac)
        if kind.lower() != "dynamic" or addr.is_multicast or mac == "ff:ff:ff:ff:ff:ff":
            continue
        if ip.endswith(".255"):
            continue
        out.append((ip, mac))
    return out


def parse_route_print(text: str) -> str | None:
    """Default gateway with the lowest metric from `route print -4 0.0.0.0`."""
    routes = [(int(metric), gw) for gw, _iface, metric in _ROUTE_LINE.findall(text)]
    return min(routes)[1] if routes else None


def _run(args: list[str]) -> str:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=5, creationflags=flags
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def default_gateway() -> str | None:
    return parse_route_print(_run(["route", "print", "-4", "0.0.0.0"]))


def anchor_id(ip: str, gateway: str | None) -> str:
    return "gw" if ip == gateway else "h" + ip.rsplit(".", 1)[-1]


def classify(
    neighbours: list[tuple[str, str]], gateway: str | None, bssid: str | None
) -> list[Anchor]:
    macs = dict(neighbours)
    infra_ouis = {o for o in (_oui(macs.get(gateway or "")), _oui(bssid)) if o}
    anchors: list[Anchor] = []
    if gateway:
        anchors.append(Anchor("gw", gateway, "gateway", "Router", True, macs.get(gateway)))
    for ip, mac in sorted(neighbours, key=lambda p: ipaddress.ip_address(p[0])):
        if ip == gateway:
            continue
        octet = ip.rsplit(".", 1)[-1]
        if _oui(mac) in infra_ouis:
            anchors.append(Anchor(anchor_id(ip, gateway), ip, "infrastructure",
                                  f"Mesh node .{octet}", True, mac))  # fmt: skip
        else:
            anchors.append(Anchor(anchor_id(ip, gateway), ip, "client",
                                  f"Device .{octet}", False, mac))  # fmt: skip
    return anchors


def discover(bssid: str | None = None) -> list[Anchor]:
    gateway = default_gateway()
    return classify(parse_arp(_run(["arp", "-a"])), gateway, bssid)


class AnchorStore:
    """User choices (names, enabled flags) persisted locally and merged over discovery."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _key(a: Anchor) -> str:
        return a.mac or a.ip

    def merge(self, found: list[Anchor]) -> list[Anchor]:
        saved = self._load()
        for a in found:
            s = saved.get(self._key(a))
            if s:
                a.name = s.get("name", a.name)
                a.enabled = s.get("enabled", a.enabled)
        return found

    def update(self, anchors: list[Anchor], changes: dict[str, dict[str, Any]]) -> list[Anchor]:
        """Apply {anchor_id: {"name": ..., "enabled": ...}} and persist."""
        saved = self._load()
        for a in anchors:
            ch = changes.get(a.id)
            if not ch:
                continue
            if "name" in ch:
                a.name = str(ch["name"])[:40] or a.name
            if "enabled" in ch:
                a.enabled = bool(ch["enabled"])
            saved[self._key(a)] = {"name": a.name, "enabled": a.enabled}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        return anchors
