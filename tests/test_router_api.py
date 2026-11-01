"""Endpoints del router: decidir y valorar, con una aplicación mínima y una base temporal."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from router_api import mount_router_routes


def _client(tmp_path):
    app = FastAPI()
    mount_router_routes(app, db_path=tmp_path / "router.db")
    return TestClient(app)


def test_decide_returns_route_and_reason(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/router/decide", json={"text": "enciende la luz del salón"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "ha"
    assert body["reason"] == "orden de casa"
    assert isinstance(body["id"], int)


def test_rate_marks_the_question(tmp_path):
    client = _client(tmp_path)
    qid = client.post("/api/router/decide", json={"text": "qué ves en la piscina"}).json()["id"]
    r = client.post("/api/router/rate", json={"id": qid, "good": True})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_rate_unknown_question_is_404(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/router/rate", json={"id": 999, "good": False})
    assert r.status_code == 404


def test_empty_text_is_rejected(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/router/decide", json={"text": ""})
    assert r.status_code == 422


def test_text_too_long_is_rejected(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/router/decide", json={"text": "a" * 1001})
    assert r.status_code == 422


def test_rate_requires_boolean_and_positive_id(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/router/rate", json={"id": 0, "good": True}).status_code == 422
    assert client.post("/api/router/rate", json={"id": 1, "good": "sí"}).status_code == 422
