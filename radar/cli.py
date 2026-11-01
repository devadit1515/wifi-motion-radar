"""Command line entry point: `radar serve | rates | anchors | simulate`."""

from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from pathlib import Path

DEFAULT_DATA = Path("data")


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from radar.engine import Engine
    from radar.server.app import SourceReq, create_app

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    if _already_running(url):
        # A second copy could get neither the port nor the microphone: reuse the first.
        print(f"Motion Radar is already running at {url}; opening it.")
        if args.open:
            webbrowser.open(url)
        return

    engine = Engine(args.data)
    if args.sonar:
        engine.sonar_enabled = True
    app = create_app(engine, SourceReq(kind=args.source))
    print(f"WiFi Motion Radar → {url}  (source: {args.source})")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _already_running(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as r:
            return r.status == 200
    except OSError:
        return False


def _build(source: str, data: Path):
    if source == "sim":
        from radar.sources.simulated import SimSource

        return SimSource()
    from radar.live import build_live_source

    return build_live_source(data)[0]


def _rates(args: argparse.Namespace) -> None:
    """Measure what each stream really delivers: poll rate vs refresh rate."""
    from radar.dsp.buffers import Ring, stream_stats
    from radar.sources.base import now

    src = _build(args.source, args.data)
    infos = {s.name: s for s in src.streams()}
    rings = {n: Ring(int(max(i.nominal_hz, 20) * (args.seconds + 5))) for n, i in infos.items()}
    print(f"Measuring {len(infos)} streams for {args.seconds:.0f} s ...", flush=True)
    src.start(lambda s: rings[s.stream].append(s.t, s.v) if s.stream in rings else None)
    t0 = now()
    time.sleep(args.seconds)
    src.stop()
    t1 = now()
    head = f"{'stream':<16}{'nominal':>9}{'polled':>9}{'refresh':>9}{'quant':>9}{'loss':>7}  last"
    print(head)
    print("-" * len(head))
    for name, info in infos.items():
        st = stream_stats(*rings[name].since(t0), t1, t1 - t0)
        q = f"{st.quant_step:.3g}" if st.quant_step else "-"
        last = f"{st.last:.4g} {info.unit}" if st.last is not None else "-"
        print(
            f"{name:<16}{info.nominal_hz:>8.1f}{st.poll_hz:>9.1f}{st.refresh_hz:>9.2f}"
            f"{q:>9}{st.loss:>7.1%}  {last}"
        )
    print("\nrefresh = value changes per second: the rate at which new information arrives.")


def _sonar_test(args: argparse.Namespace) -> None:
    """Run the sonar alone and report signal health and what it sees."""
    from radar.sonar.source import SonarSource

    src = SonarSource()
    if args.save:
        src.raw = []
    print(f"output: {src.out_name}\ninput:  {src.in_name} via {src.path}, {src.channels} ch")
    print("Playing a quiet 18.5-22 kHz sweep. Walk around in front of the PC.")
    src.start(lambda s: None)
    t_end = time.time() + args.seconds
    while time.time() < t_end:
        time.sleep(1.0)
        s = src.extras()["sonar"]
        h = s.get("health", {})
        tgt = s.get("target")
        where = (
            f"target at {tgt['r']:.2f} m, mic difference {tgt['path_diff']} m"
            if tgt else "no moving target"
        )  # fmt: skip
        print(
            f"  {h.get('status'):8s} input {h.get('input_db')} dBFS, "
            f"echo SNR {h.get('snr_db')} dB, motion {s.get('motion')}, {where}"
        )
    print("fixed echoes (walls, furniture) at:", src.extras()["sonar"].get("echoes"), "m")
    src.stop()
    if args.save and src.raw:
        import numpy as np

        np.save(args.save, np.concatenate(src.raw))
        print(f"saved {len(src.raw)} raw blocks to {args.save}")


def _anchors(args: argparse.Namespace) -> None:
    from radar.sources.anchors import AnchorStore, discover

    found = AnchorStore(args.data / "anchors.json").merge(discover())
    ping = None
    if sys.platform == "win32":
        from radar.sources.win_icmp import ping_once

        ping = ping_once
    print(f"{'id':<7}{'ip':<16}{'kind':<16}{'on':<4}{'rtt':>9}  name")
    for a in found:
        rtt = ping(a.ip) if ping else float("nan")
        r = f"{rtt:.2f}ms" if rtt == rtt else "no reply"
        print(f"{a.id:<7}{a.ip:<16}{a.kind:<16}{'yes' if a.enabled else 'no':<4}{r:>9}  {a.name}")


def _simulate(args: argparse.Namespace) -> None:
    from radar.sources.simulated import HomeSim, sim_streams, simulate
    from radar.store.sessions import SessionStore, write_session

    samples, labels = simulate(args.minutes * 60, seed=args.seed)
    store = SessionStore(args.data)
    streams = sim_streams(HomeSim(seed=args.seed).anchor_ids, 100.0)
    sid = write_session(
        store, samples, streams, labels,
        source={"kind": "sim", "seed": args.seed, "simulated": True},
        setup={"notes": "offline simulation", "simulated": True}, tag="sim",
    )  # fmt: skip
    print(f"wrote simulated session {sid}: {len(samples)} samples, {len(labels)} label intervals")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="radar", description="WiFi Motion Radar")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA, help="data directory")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the engine and dashboard")
    s.add_argument("--source", choices=["live", "sim"], default="live")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    s.add_argument("--sonar", action="store_true", help="also run the acoustic sonar")
    s.set_defaults(fn=_serve)

    so = sub.add_parser("sonar-test", help="check the speakers and microphone can carry the sonar")
    so.add_argument("--seconds", type=float, default=6.0)
    so.add_argument("--save", type=Path, help="also save the raw microphone audio (.npy)")
    so.set_defaults(fn=_sonar_test)

    r = sub.add_parser("rates", help="measure poll vs refresh rate of every stream")
    r.add_argument("--source", choices=["live", "sim"], default="live")
    r.add_argument("--seconds", type=float, default=20.0)
    r.set_defaults(fn=_rates)

    a = sub.add_parser("anchors", help="list probe anchors found on the network")
    a.set_defaults(fn=_anchors)

    m = sub.add_parser("simulate", help="write an offline simulated, labelled session")
    m.add_argument("--minutes", type=float, default=10.0)
    m.add_argument("--seed", type=int, default=7)
    m.set_defaults(fn=_simulate)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args.fn(args)


if __name__ == "__main__":
    main()
