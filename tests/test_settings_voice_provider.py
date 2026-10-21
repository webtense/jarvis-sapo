"""El panel de Ajustes necesita saber qué voz habla de verdad.

Con Piper local la clave de Fish es opcional: el punto rojo y el asistente de
primer arranque solo tienen sentido cuando no hay NINGUNA voz. Por eso el
endpoint de estado expone `voice_provider` (el mismo `tts.provider()` que usa
el servidor para hablar) y el frontend decide con él.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

    import data_paths
    importlib.reload(data_paths)
    import run_store
    importlib.reload(run_store)
    import server
    importlib.reload(server)
    run_store.init_db()

    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setattr(server, "_env_file_path", lambda: env_file)

    with TestClient(server.app,
                    headers={"Origin": "http://localhost:5173"}) as c:
        yield c, server


@pytest.mark.parametrize("provider", ["fish", "piper", "none"])
def test_status_reports_the_active_voice(client, monkeypatch, provider):
    c, server = client
    monkeypatch.setattr(server.tts, "provider", lambda: provider)
    body = c.get("/api/settings/status").json()
    assert body["voice_provider"] == provider


def test_a_missing_fish_key_is_not_a_missing_voice(client, monkeypatch):
    """Piper habla y no hay clave de Fish: son dos hechos independientes y el
    estado tiene que poder decir ambos."""
    c, server = client
    monkeypatch.setattr(server.tts, "provider", lambda: "piper")
    body = c.get("/api/settings/status").json()
    assert body["voice_provider"] == "piper"
    assert body["env_keys_set"]["fish_audio"] is False
