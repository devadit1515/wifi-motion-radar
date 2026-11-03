"""Only the engine's own pages and the hosted dashboard may use it from a browser."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from radar.engine import Engine
from radar.server.app import SourceReq, create_app
from radar.server.guard import HOSTED_ORIGINS

SITE = HOSTED_ORIGINS[0]
LOCAL = {"host": "127.0.0.1:8765"}


@pytest.fixture
def client(tmp_path):
    app = create_app(Engine(tmp_path), SourceReq(kind="sim"), loopback_only=True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_own_pages_and_tools_without_origin_are_allowed(client):
    assert client.get("/api/health", headers=LOCAL).status_code == 200
    own = {**LOCAL, "origin": "http://127.0.0.1:8765"}
    assert client.get("/api/health", headers=own).status_code == 200


def test_hosted_dashboard_gets_cors_and_the_private_network_opt_in(client):
    r = client.get("/api/health", headers={**LOCAL, "origin": SITE})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == SITE
    pre = client.options("/api/calibrate", headers={
        **LOCAL, "origin": SITE, "access-control-request-method": "POST",
        "access-control-request-headers": "content-type",
        "access-control-request-private-network": "true",
    })  # fmt: skip
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-private-network"] == "true"


def test_any_other_website_is_refused(client):
    evil = {**LOCAL, "origin": "https://evil.example"}
    assert client.get("/api/health", headers=evil).status_code == 403
    r = client.post("/api/record/start", json={}, headers=evil)
    assert r.status_code == 403
    assert "not allowed" in r.json()["detail"]
    assert client.get("/api/health", headers={**LOCAL, "origin": "null"}).status_code == 403


def test_dns_rebinding_is_refused(client):
    # evil.example resolved to 127.0.0.1: same-origin as far as the browser knows.
    host = {"host": "evil.example:8765", "origin": "http://evil.example:8765"}
    assert client.get("/api/health", headers=host).status_code == 403


def test_websocket_checks_the_origin(client):
    with client.websocket_connect("/ws/live", headers={**LOCAL, "origin": SITE}) as ws:
        assert ws.receive_json()["type"] == "hello"
    evil = {**LOCAL, "origin": "https://evil.example"}
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/live", headers=evil):
        pass
