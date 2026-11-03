"""Who may talk to the engine from a browser.

The engine reads this machine's WiFi driver and microphone, so it answers its own pages
and the hosted dashboard, and nothing else. Any other website open in the same browser
is refused: it cannot read the sensors, start a recording or change settings.

Two checks, both cheap:
- Origin. Browsers attach it to every cross-site request and WebSocket. It must be the
  engine's own origin or one of the allowed dashboard origins.
- Host. A malicious page can point its own domain name at 127.0.0.1 (DNS rebinding) and
  so look same-origin; its requests still carry that domain in the Host header. When the
  engine listens on loopback only, Host must be a loopback name.

Chrome also asks a local server to opt in before a public website may reach it
(Private Network Access); the CORS layer behind this guard gives allowed origins that
opt-in.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

HOSTED_ORIGINS = ("https://devadit1515.github.io",)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def allowed_origins() -> set[str]:
    extra = os.environ.get("RADAR_ALLOW_ORIGINS", "")
    return set(HOSTED_ORIGINS) | {o.strip().rstrip("/") for o in extra.split(",") if o.strip()}


def _hostname(netloc: str) -> str:
    return (urlsplit(f"//{netloc}").hostname or "").lower()


class OriginGuard:
    """Pure ASGI middleware; add it last so it wraps CORS and sees every request."""

    def __init__(self, app, origins: set[str], loopback_only: bool) -> None:
        self.app = app
        self.origins = origins
        self.loopback_only = loopback_only

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        origin = headers.get("origin")
        if not self._ok(host, origin):
            await self._refuse(scope, send)
            return
        await self.app(scope, receive, send)

    def _ok(self, host: str, origin: str | None) -> bool:
        if self.loopback_only and _hostname(host) not in LOOPBACK:
            return False
        if origin is None or origin == "null":
            # No Origin: not a cross-site browser request (curl, the launcher's own
            # health check, a same-origin GET). "null" comes from sandboxed or file
            # pages, which have no business here.
            return origin is None
        if origin.rstrip("/") in self.origins:
            return True
        parts = urlsplit(origin)
        return parts.netloc.lower() == host.lower()  # the engine's own pages

    async def _refuse(self, scope, send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": "This website is not allowed to use Motion Radar."}).encode()
        await send({
            "type": "http.response.start", "status": 403,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
        })  # fmt: skip
        await send({"type": "http.response.body", "body": body})
