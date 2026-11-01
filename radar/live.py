"""Assemble the real sensing stack for whichever OS this is running on."""

from __future__ import annotations

import sys
from pathlib import Path

from radar.sources.anchors import Anchor, AnchorStore, discover
from radar.sources.base import CompositeSource, Source, SourceUnavailable


def build_live_source(
    data_dir: Path, hz: float = 100.0, aux_hz: float = 50.0, sonar: bool = False
) -> tuple[Source, list[Anchor]]:
    parts: list[Source] = []
    problems: list[str] = []
    anchors: list[Anchor] = []

    if sonar:
        from radar.sonar.source import SonarSource

        try:
            parts.append(SonarSource())
        except SourceUnavailable as e:
            problems.append(f"sonar: {e}")

    if sys.platform == "win32":
        from radar.sources.win_icmp import IcmpSource
        from radar.sources.win_wlan import WlanSource

        bssid = None
        try:
            wlan = WlanSource()
            parts.append(wlan)
            bssid = wlan.bssid
        except SourceUnavailable as e:
            problems.append(f"wlan: {e}")
        anchors = AnchorStore(data_dir / "anchors.json").merge(discover(bssid))
        if anchors:
            try:
                parts.append(IcmpSource(anchors, hz=hz, aux_hz=aux_hz))
            except SourceUnavailable as e:
                problems.append(f"icmp: {e}")
        else:
            problems.append("icmp: no default gateway found")
    elif sys.platform.startswith("linux"):
        from radar.sources.linux import LinuxWlanSource

        try:
            parts.append(LinuxWlanSource())
        except SourceUnavailable as e:
            problems.append(f"linux: {e}")
    elif sys.platform == "darwin":
        from radar.sources.macos import MacWlanSource

        try:
            parts.append(MacWlanSource())
        except SourceUnavailable as e:
            problems.append(f"macos: {e}")

    if not parts:
        raise SourceUnavailable("; ".join(problems) or f"unsupported platform {sys.platform}")
    src = CompositeSource(parts, kind="live", name="live")
    return src, anchors
