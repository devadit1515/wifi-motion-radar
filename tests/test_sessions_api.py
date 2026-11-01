"""Session storage round trip, and the API + WebSocket on the simulated source."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from radar.engine import Engine
from radar.server.app import SourceReq, create_app
from radar.sources.base import Sample, StreamInfo
from radar.sources.simulated import HomeSim, sim_streams, simulate
from radar.store.sessions import Recorder, SessionStore, write_session


def test_recorder_round_trip(tmp_path):
    store = SessionStore(tmp_path)
    info = StreamInfo("rtt.gw", "rtt", "ms", 100, "probe", "RTT")
    rec = Recorder(store, [info], {"kind": "test"}, t0=100.0, meta={"room": "A"})
    rec.set_label("empty", 100.0)
    for i in range(500):
        rec.add(Sample("rtt.gw", 100.0 + i / 100, 2.0 + (i % 7) / 10))
    rec.set_label("walking", 103.0)
    meta = rec.stop(105.0)
    assert meta["labels"] == [
        {"start": 0.0, "end": 3.0, "label": "empty"},
        {"start": 3.0, "end": 5.0, "label": "walking"},
    ]
    df = store.samples(rec.id)
    assert len(df) == 500 and df["t"].iloc[0] == 0.0
    listed = store.list()
    assert listed[0]["id"] == rec.id
    assert listed[0]["label_seconds"] == {"empty": 3.0, "walking": 2.0}

    store.update(rec.id, {"labels": [{"start": 0, "end": 5, "label": "empty"}]})
    assert store.meta(rec.id)["labels"] == [{"start": 0.0, "end": 5.0, "label": "empty"}]
    store.delete(rec.id)
    assert store.list() == []


def test_session_ids_reject_path_tricks(tmp_path):
    store = SessionStore(tmp_path)
    with pytest.raises(KeyError):
        store.meta("../config")


def test_write_session_from_simulation(tmp_path):
    store = SessionStore(tmp_path)
    samples, labels = simulate(10, seed=1, schedule=[("walking", 10)])
    sid = write_session(store, samples, sim_streams(HomeSim(seed=1).anchor_ids, 100.0), labels,
                        source={"kind": "sim"})  # fmt: skip
    assert store.meta(sid)["labels"][0]["label"] == "walking"
    assert len(store.samples(sid)) == len(samples)


def _wait_frames(ws, n, kind="frame"):
    got = []
    while len(got) < n:
        msg = json.loads(ws.receive_text())
        if msg["type"] == kind:
            got.append(msg)
    return got


def test_api_and_websocket_on_simulator(tmp_path):
    engine = Engine(tmp_path)
    app = create_app(engine, SourceReq(kind="sim"))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["source"] == "sim"
        caps = client.get("/api/capabilities").json()
        assert caps["source"]["simulated"] is True
        assert any(s["name"] == "rtt.gw" for s in caps["streams"])

        with client.websocket_connect("/ws/live") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["type"] == "hello" and hello["protocol"]
            frames = _wait_frames(ws, 5)
            f = frames[-1]
            assert f["det"]["state"] in ("calibrating", "empty", "settled", "motion")
            assert "truth" in f and "rtt.gw" in f["streams"]
            assert any("rtt.gw" in fr["traces"] for fr in frames)

        assert client.post("/api/sim", json={"mode": "walking"}).json()["mode"] == "walking"

        client.post("/api/record/start", json={"setup": {"notes": "test"}})
        client.post("/api/record/label", json={"label": "walking"})
        time.sleep(1.5)
        stopped = client.post("/api/record/stop").json()
        assert stopped["n"] > 100
        sessions = client.get("/api/sessions").json()
        assert len(sessions) == 1
        sid = sessions[0]["id"]
        ov = client.get(f"/api/sessions/{sid}/overview").json()
        assert "rtt.gw" in ov["streams"]

        caps = client.post("/api/source", json={"kind": "replay", "session": sid}).json()
        assert caps["source"]["kind"] == "replay"
        with client.websocket_connect("/ws/live") as ws:
            json.loads(ws.receive_text())
            frames = _wait_frames(ws, 3)
            assert frames[-1]["replay"]["session"] == sid
        st = client.post("/api/replay", json={"paused": True, "seek": 0.2, "speed": 4}).json()
        assert st["paused"] and st["speed"] == 4

        assert client.post("/api/sim", json={"mode": "walking"}).status_code == 409
        assert client.post("/api/source", json={"kind": "bogus"}).status_code == 409
