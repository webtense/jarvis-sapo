"""Frigate read-only MCP: the guarantees, checked with fakes (no network, no Frigate)."""

import json

import pytest

import frigate_readonly_mcp as fm


@pytest.fixture
def cams(monkeypatch):
    monkeypatch.setenv("FRIGATE_URL", "http://frigate.test:5000")
    monkeypatch.delenv("FRIGATE_EXCLUDE_CAMERAS", raising=False)
    calls = []

    def fake_get(path, query=None):
        calls.append((path, query))
        if path == "/api/config":
            return json.dumps({"cameras": {
                "Tejado": {}, "Zulo": {}, "comedor_14": {}, "Recibidor_239": {}}}).encode()
        if path == "/api/events":
            return b"[]"
        return b"\xff\xd8fake-jpeg"

    monkeypatch.setattr(fm, "_get", fake_get)
    return calls


def test_only_allowed_get_paths(cams):
    with pytest.raises(fm.FrigateError):
        fm._get("/api/cameras/Tejado/record/on")  # not in the allowlist
    with pytest.raises(fm.FrigateError):
        fm._get("/api/events/123/delete")


def test_indoor_cameras_are_refused_and_not_listed(cams):
    listing = fm.list_cameras()
    assert "Tejado" in listing and "Zulo" in listing
    assert "comedor_14" not in listing and "Recibidor_239" not in listing
    with pytest.raises(fm.FrigateError):
        fm.snapshot("comedor_14")


def test_unknown_camera_is_refused(cams):
    with pytest.raises(fm.FrigateError):
        fm.snapshot("No_existe")
    with pytest.raises(fm.FrigateError):
        fm.snapshot("../../etc")


def test_events_never_include_excluded_cameras(cams, monkeypatch):
    rows = [{"camera": "Tejado", "label": "person", "start_time": 0},
            {"camera": "comedor_14", "label": "person", "start_time": 0}]
    monkeypatch.setattr(fm, "_get", lambda p, q=None: json.dumps(rows).encode())
    out = fm.events(hours=6)
    assert "Tejado" in out and "comedor_14" not in out


def test_unconfigured_connection_fails_closed(monkeypatch):
    monkeypatch.delenv("FRIGATE_URL", raising=False)
    with pytest.raises(fm.FrigateError):
        fm._url()


def test_snapshot_is_image_and_not_saved(cams):
    data, name = fm.snapshot("Tejado")
    assert name == "Tejado" and data.startswith(b"\xff\xd8")


def test_control_characters_stripped_from_names(cams, monkeypatch):
    monkeypatch.setattr(fm, "_get", lambda p, q=None: json.dumps(
        {"cameras": {"Tej\x1b[31mado": {}}}).encode())
    assert "\x1b" not in fm.list_cameras()
