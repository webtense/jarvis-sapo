"""The SapoFit row of the Connections section: states and times, never text.

The cursor file is written with a message-looking field on purpose: the panel
must not show it, and must not show the token or its path either.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import admin_panel  # noqa: E402
import data_paths  # noqa: E402

FAKE_TOKEN = "sfk_EJEMPLO_" + "b" * 35  # gitleaks:allow (token de prueba)
MARKER = "MARCA_DE_TEXTO_CROAC_NO_MOSTRAR"


def _write_token(tmp_path, monkeypatch):
    data_paths.brain_home().mkdir(parents=True, exist_ok=True)
    path = tmp_path / "sapofit.json"
    path.write_text(json.dumps({"url": "https://sapofit.invalid", "token": FAKE_TOKEN}),
                    encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setenv("SAPOFIT_TOKEN_FILE", str(path))
    return path


def test_with_nothing_configured_it_says_so_and_nothing_more(monkeypatch):
    monkeypatch.delenv("SAPOFIT_TOKEN_FILE", raising=False)
    out = admin_panel._sapofit_status({}, False)
    assert out == {"configured": False, "token_file": "missing", "watch_enabled": False,
                   "watch_running": False, "cursor": "", "last_read_at": ""}


def test_a_configured_watch_shows_its_cursor_and_time_and_no_text(tmp_path, monkeypatch):
    token = _write_token(tmp_path, monkeypatch)
    data_paths.brain_home().mkdir(parents=True, exist_ok=True)
    (data_paths.brain_home() / "sapofit-alerts.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8")
    (data_paths.brain_home() / "sapofit-cursor.json").write_text(json.dumps({
        "cursor": "1729500000000:abc", "seen": ["abc"], "pending": 0,
        "last_read_at": 1729500060.0, "text": MARKER}), encoding="utf-8")
    out = admin_panel._sapofit_status({}, True)
    assert out["configured"] is True and out["token_file"] == "ok"
    assert out["watch_enabled"] is True and out["watch_running"] is True
    assert out["cursor"] == "1729500000000:abc"
    # 1729500060 is 2024-10-21 UTC; the local zone can move it one day at most.
    assert out["last_read_at"].startswith("2024-10-2")
    dumped = json.dumps(out)
    assert MARKER not in dumped and FAKE_TOKEN not in dumped and str(token) not in dumped


def test_a_switched_off_watch_is_reported_off(tmp_path, monkeypatch):
    _write_token(tmp_path, monkeypatch)
    (data_paths.brain_home() / "sapofit-alerts.json").write_text(
        json.dumps({"enabled": False}), encoding="utf-8")
    out = admin_panel._sapofit_status({}, False)
    assert out["configured"] is True
    assert out["watch_enabled"] is False


def test_a_token_others_can_read_is_reported_as_permissions(tmp_path, monkeypatch):
    path = _write_token(tmp_path, monkeypatch)
    os.chmod(path, 0o644)
    assert admin_panel._sapofit_status({}, False)["token_file"] == "permissions"


def test_the_connections_report_carries_the_sapofit_row(monkeypatch):
    monkeypatch.delenv("SAPOFIT_TOKEN_FILE", raising=False)
    report = admin_panel.connections_report({}, [], True)
    assert set(report["sapofit"]) == {"configured", "token_file", "watch_enabled",
                                      "watch_running", "cursor", "last_read_at"}
    assert report["sapofit"]["watch_running"] is True
