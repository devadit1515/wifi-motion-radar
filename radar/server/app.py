"""HTTP + WebSocket API for the dashboard.

Binds to 127.0.0.1 by default: this is a sensor pointed at a home, so exposing it to
the LAN (to watch from a phone) is an explicit --host choice.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from radar.engine import Engine
from radar.live import build_live_source
from radar.server.guard import OriginGuard, allowed_origins
from radar.sources.anchors import AnchorStore, discover
from radar.sources.base import SourceUnavailable
from radar.sources.replay import ReplaySource
from radar.sources.simulated import SimSource

log = logging.getLogger("radar.server")

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
EVAL_RESULTS = Path(__file__).resolve().parents[2] / "eval" / "report" / "results.json"


class SourceReq(BaseModel):
    kind: str
    session: str | None = None
    speed: float = 1.0
    seed: int = 7


class RecordStartReq(BaseModel):
    setup: dict[str, Any] = {}
    tag: str = ""


class LabelReq(BaseModel):
    label: str | None = None


class ProbeReq(BaseModel):
    phases: list[dict[str, Any]] | None = None


class ReplayReq(BaseModel):
    paused: bool | None = None
    speed: float | None = None
    seek: float | None = None


class SimReq(BaseModel):
    mode: str


class GuardReq(BaseModel):
    armed: bool | None = None
    webhook: str | None = None


class AnchorsReq(BaseModel):
    changes: dict[str, dict[str, Any]]


class SessionPatch(BaseModel):
    labels: list[dict[str, Any]] | None = None
    setup: dict[str, Any] | None = None


def _conflict(e: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


class SonarReq(BaseModel):
    enabled: bool


class RoomReq(BaseModel):
    width: float
    depth: float
    pc_x: float
    mic_spacing: float | None = None  # metres; sets how far a path difference turns the dot
    pd_center: float | None = None  # path difference measured straight in front of the PC
    flip: bool = False


DEFAULT_ROOM = {
    "width": 3.5, "depth": 4.0, "pc_x": 1.75, "mic_spacing": None, "pd_center": None,
    "flip": False,
}  # fmt: skip


def switch_source(engine: Engine, req: SourceReq) -> None:
    if req.kind == "live":
        src, anchors = build_live_source(engine.data_dir, sonar=engine.sonar_enabled)
        engine.set_source(src, anchors)
    elif req.kind == "sim":
        engine.set_source(SimSource(seed=req.seed))
    elif req.kind == "replay":
        if not req.session:
            raise ValueError("replay needs a session id")
        engine.set_source(ReplaySource(engine.store, req.session, speed=req.speed))
    else:
        raise ValueError(f"unknown source {req.kind}")


def overview(engine: Engine, sid: str, points: int = 1200) -> dict[str, Any]:
    """Min/max-bucketed copy of every stream, small enough to draw a whole session."""
    df = engine.store.samples(sid)
    out: dict[str, Any] = {}
    for name, g in df.groupby("stream", observed=True):
        t = g["t"].to_numpy(float)
        v = g["v"].to_numpy(float)
        if len(t) == 0:
            continue
        edges = np.linspace(t[0], t[-1] + 1e-9, points // 2 + 1)
        idx = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, len(edges) - 2)
        ts, lo, hi = [], [], []
        for b in np.unique(idx):
            vb = v[idx == b]
            vb = vb[np.isfinite(vb)]
            ts.append(round(float(edges[b]), 2))
            lo.append(round(float(vb.min()), 4) if len(vb) else None)
            hi.append(round(float(vb.max()), 4) if len(vb) else None)
        out[str(name)] = {"t": ts, "lo": lo, "hi": hi}
    return out


def create_app(
    engine: Engine, initial: SourceReq | None = None, loopback_only: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine.start()
        if engine.source is None:
            req = initial or SourceReq(kind="sim")
            try:
                switch_source(engine, req)
            except SourceUnavailable as e:
                log.warning("live source unavailable (%s); falling back to the simulator", e)
                switch_source(engine, SourceReq(kind="sim"))
        yield
        engine.stop()

    app = FastAPI(title="WiFi Motion Radar", lifespan=lifespan)
    # The hosted dashboard is a page on another origin calling this machine's engine.
    origins = allowed_origins()
    app.add_middleware(
        CORSMiddleware, allow_origins=sorted(origins), allow_methods=["*"], allow_headers=["*"],
        allow_private_network=True,
    )  # fmt: skip
    app.add_middleware(OriginGuard, origins=origins, loopback_only=loopback_only)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "source": engine.kind}

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        return engine.capabilities()

    @app.post("/api/source")
    def set_source(req: SourceReq) -> dict[str, Any]:
        try:
            switch_source(engine, req)
        except (SourceUnavailable, ValueError, KeyError) as e:
            raise _conflict(e) from e
        return engine.capabilities()

    @app.post("/api/calibrate")
    def calibrate() -> dict[str, Any]:
        engine.calibrate()
        return {"ok": True}

    @app.get("/api/detector")
    def get_detector() -> dict[str, Any]:
        return engine.detector.cfg.to_dict()

    @app.patch("/api/detector")
    def patch_detector(patch: dict[str, Any]) -> dict[str, Any]:
        return engine.set_detector(patch)

    @app.get("/api/anchors")
    def anchors() -> list[dict[str, Any]]:
        bssid = None
        if engine.kind == "live":
            bssid = getattr(engine.source.parts[0], "bssid", None)  # type: ignore[union-attr]
        found = AnchorStore(engine.data_dir / "anchors.json").merge(discover(bssid))
        return [a.to_dict() | {"mac": None} for a in found]

    @app.patch("/api/anchors")
    def patch_anchors(req: AnchorsReq) -> list[dict[str, Any]]:
        store = AnchorStore(engine.data_dir / "anchors.json")
        found = store.update(store.merge(discover()), req.changes)
        if engine.kind == "live":
            try:
                switch_source(engine, SourceReq(kind="live"))
            except SourceUnavailable as e:
                raise _conflict(e) from e
        return [a.to_dict() | {"mac": None} for a in found]

    @app.post("/api/record/start")
    def record_start(req: RecordStartReq) -> dict[str, Any]:
        try:
            return engine.record_start(req.setup, req.tag)
        except RuntimeError as e:
            raise _conflict(e) from e

    @app.post("/api/record/label")
    def record_label(req: LabelReq) -> dict[str, Any]:
        try:
            return engine.record_label(req.label)
        except RuntimeError as e:
            raise _conflict(e) from e

    @app.post("/api/record/stop")
    def record_stop() -> dict[str, Any]:
        return engine.record_stop() or {}

    @app.get("/api/sessions")
    def sessions() -> list[dict[str, Any]]:
        return engine.store.list()

    @app.get("/api/sessions/{sid}")
    def session(sid: str) -> dict[str, Any]:
        try:
            return engine.store.meta(sid)
        except KeyError as e:
            raise HTTPException(404, "no such session") from e

    @app.get("/api/sessions/{sid}/overview")
    def session_overview(sid: str) -> dict[str, Any]:
        try:
            return {"meta": engine.store.meta(sid), "streams": overview(engine, sid)}
        except (KeyError, OSError) as e:
            raise HTTPException(404, "no such session") from e

    @app.patch("/api/sessions/{sid}")
    def patch_session(sid: str, patch: SessionPatch) -> dict[str, Any]:
        try:
            return engine.store.update(sid, patch.model_dump(exclude_none=True))
        except KeyError as e:
            raise HTTPException(404, "no such session") from e

    @app.delete("/api/sessions/{sid}")
    def delete_session(sid: str) -> dict[str, Any]:
        try:
            engine.store.delete(sid)
        except (KeyError, OSError) as e:
            raise HTTPException(404, "no such session") from e
        return {"ok": True}

    @app.post("/api/probe/start")
    def probe_start(req: ProbeReq) -> dict[str, Any]:
        try:
            return engine.probe_start(req.phases)
        except RuntimeError as e:
            raise _conflict(e) from e

    @app.post("/api/probe/cancel")
    def probe_cancel() -> dict[str, Any]:
        engine.probe_cancel()
        return {"ok": True}

    @app.get("/api/probe/last")
    def probe_last() -> dict[str, Any] | None:
        return engine.last_probe

    @app.post("/api/replay")
    def replay(req: ReplayReq) -> dict[str, Any]:
        try:
            return engine.replay_control(req.paused, req.speed, req.seek)
        except RuntimeError as e:
            raise _conflict(e) from e

    @app.post("/api/sim")
    def sim(req: SimReq) -> dict[str, Any]:
        try:
            return engine.sim_mode(req.mode)
        except (RuntimeError, ValueError) as e:
            raise _conflict(e) from e

    @app.get("/api/timeline")
    def timeline(date: str | None = None) -> dict[str, Any]:
        return {"source": engine.kind, "minutes": engine.timeline.day(date)}

    @app.get("/api/events")
    def events(n: int = 100) -> list[dict[str, Any]]:
        return engine.events.recent(n)

    @app.post("/api/guard")
    def guard(req: GuardReq) -> dict[str, Any]:
        return engine.set_guard(req.armed, req.webhook)

    @app.post("/api/sonar")
    def sonar(req: SonarReq) -> dict[str, Any]:
        """Turn the acoustic sonar on or off. It runs as part of the live source."""
        engine.sonar_enabled = req.enabled
        engine.save_cfg()
        if req.enabled or engine.kind == "live":
            try:
                switch_source(engine, SourceReq(kind="live"))
            except SourceUnavailable as e:
                raise _conflict(e) from e
        return {"enabled": engine.sonar_enabled, "source": engine.capabilities()["source"]}

    room_path = engine.data_dir / "room.json"

    @app.get("/api/room")
    def get_room() -> dict[str, Any]:
        try:
            return {**DEFAULT_ROOM, **json.loads(room_path.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return dict(DEFAULT_ROOM)

    @app.put("/api/room")
    def put_room(req: RoomReq) -> dict[str, Any]:
        room = req.model_dump()
        room["width"] = min(max(room["width"], 1.0), 15.0)
        room["depth"] = min(max(room["depth"], 1.0), 15.0)
        room["pc_x"] = min(max(room["pc_x"], 0.0), room["width"])
        room_path.write_text(json.dumps(room, indent=2), encoding="utf-8")
        return room

    @app.get("/api/lab")
    def lab() -> dict[str, Any]:
        try:
            return json.loads(EVAL_RESULTS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"available": False}

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        await ws.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)

        def put(text: str) -> None:
            if queue.full():  # slow client: drop the oldest frame, never block the engine
                queue.get_nowait()
            queue.put_nowait(text)

        def push(text: str) -> None:
            loop.call_soon_threadsafe(put, text)

        await ws.send_text(json.dumps(engine.hello()))
        engine.subscribe(push)
        try:
            while True:
                await ws.send_text(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            engine.unsubscribe(push)

    if (WEB_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            f = (WEB_DIST / path).resolve()
            if path and f.is_file() and WEB_DIST in f.parents:
                return FileResponse(f)
            return FileResponse(WEB_DIST / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def not_built() -> HTMLResponse:
            return HTMLResponse(
                "<h1>WiFi Motion Radar</h1><p>The dashboard is not built yet. Run "
                "<code>npm --prefix web install</code> then <code>npm --prefix web run build"
                "</code>, or use the dev server with <code>npm --prefix web run dev</code>.</p>"
            )

    return app
