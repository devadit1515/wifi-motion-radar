"""Screenshot dashboard views with headless Edge/Chrome over the DevTools protocol.

Unlike `--screenshot`, this keeps the page running in real time (WebSocket frames,
timers, animation) for `--wait` seconds before capturing. Used for README images and
for checking layouts at desktop and phone widths.

    python scripts/screenshot.py --out shots live probe system --scheme light --width 1440
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "chromium",
    "google-chrome",
]


def find_browser() -> str:
    for b in BROWSERS:
        if Path(b).exists() or shutil.which(b):
            return b
    raise SystemExit("no Edge or Chrome found")


class Page:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.n = 0

    async def call(self, method: str, **params):
        self.n += 1
        my = self.n
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})


async def run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="radar-shot-")
    port = args.port
    proc = subprocess.Popen(
        [find_browser(), "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}", "--hide-scrollbars", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        targets = []
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as r:
                    targets = [t for t in json.load(r) if t["type"] == "page"]
                if targets:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        async with websockets.connect(targets[0]["webSocketDebuggerUrl"], max_size=None) as ws:
            page = Page(ws)
            await page.call("Page.enable")
            await page.call(
                "Emulation.setDeviceMetricsOverride",
                width=args.width, height=args.height, deviceScaleFactor=args.scale,
                mobile=args.width < 600,
            )  # fmt: skip
            await page.call(
                "Emulation.setEmulatedMedia",
                features=[{"name": "prefers-color-scheme", "value": args.scheme}],
            )
            for view in args.views:
                await page.call("Page.navigate", url=f"{args.base}/#/{view}")
                await asyncio.sleep(args.wait)
                if args.probe_js:
                    res = await page.call(
                        "Runtime.evaluate", expression=args.probe_js, returnByValue=True
                    )
                    print(view, "→", res.get("result", {}).get("value"))
                metrics = await page.call("Page.getLayoutMetrics")
                h = min(int(metrics["cssContentSize"]["height"]), args.max_height)
                shot = await page.call(
                    "Page.captureScreenshot", format="png", captureBeyondViewport=True,
                    clip={"x": 0, "y": 0, "width": args.width, "height": h, "scale": 1},
                )  # fmt: skip
                path = out / f"{view}-{args.scheme}-{args.width}.png"
                path.write_bytes(base64.b64decode(shot["data"]))
                print("wrote", path)
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("views", nargs="+")
    p.add_argument("--base", default="http://127.0.0.1:8765")
    p.add_argument("--out", default="shots")
    p.add_argument("--width", type=int, default=1440)
    p.add_argument("--height", type=int, default=1000)
    p.add_argument("--max-height", type=int, default=2600)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--scheme", choices=["light", "dark"], default="light")
    p.add_argument("--wait", type=float, default=6.0)
    p.add_argument("--port", type=int, default=9333)
    p.add_argument("--probe-js", default="")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
