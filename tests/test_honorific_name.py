"""The "por mi nombre" (`HONORIFIC=name`) treatment option, end to end.

`i18n.py` and `brain.py` each get their own focused tests elsewhere
(`tests/test_i18n.py`, `tests/test_system_prompt_header.py`); this file is
the one place that exercises the closed-list validation on
`POST /api/settings/preferences`, which never existed before this option was
added — any string used to be accepted and fell back to "sir" silently.
"""

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

DASHBOARD_ORIGIN = "http://localhost:5173"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_PORT", "8340")
    monkeypatch.setenv("JARVIS_ENV_FILE", str(tmp_path / ".env"))
    # `_write_env_key` sets `os.environ[key] = value` directly, as the real
    # server does on a live save — it is not a `monkeypatch.setenv` call, so
    # monkeypatch would not otherwise know to undo it. Seeding both keys
    # through monkeypatch FIRST makes it track them anyway: on teardown it
    # restores whatever `HONORIFIC`/`USER_NAME` were before this test, no
    # matter how they changed meanwhile, so one test saving "name" cannot
    # leak into the next test file that assumes the default.
    monkeypatch.setenv("HONORIFIC", "sir")
    monkeypatch.setenv("USER_NAME", "")
    import data_paths
    importlib.reload(data_paths)
    import run_store
    importlib.reload(run_store)
    import server
    importlib.reload(server)
    run_store.init_db()
    with TestClient(server.app) as c:
        yield c, server


@pytest.mark.parametrize("value", ["sir", "ma'am", "none", "name"])
def test_every_real_option_is_accepted(env, value):
    c, server = env
    r = c.post("/api/settings/preferences",
               json={"user_name": "Andrés", "honorific": value},
               headers={"Origin": DASHBOARD_ORIGIN})
    assert r.status_code == 200, (value, r.text)
    _, parsed = server._read_env()
    assert parsed["HONORIFIC"] == value


@pytest.mark.parametrize("value", ["", "Sir", "SIR", "mister", "madam",
                                   "sir ", " sir", "name;drop", "nombre"])
def test_anything_outside_the_closed_list_is_refused(env, value):
    c, server = env
    r = c.post("/api/settings/preferences",
               json={"user_name": "Andrés", "honorific": value},
               headers={"Origin": DASHBOARD_ORIGIN})
    assert r.status_code == 400, (value, r.text)
    _, parsed = server._read_env()
    assert "HONORIFIC" not in parsed, (value, parsed)


def test_a_bad_honorific_leaves_the_name_unsaved_too(env):
    """The validation runs before anything is written — a bad honorific must
    not half-save the name either."""
    c, server = env
    r = c.post("/api/settings/preferences",
               json={"user_name": "Andrés", "honorific": "bogus"},
               headers={"Origin": DASHBOARD_ORIGIN})
    assert r.status_code == 400
    _, parsed = server._read_env()
    assert "USER_NAME" not in parsed


def test_name_with_no_user_name_saves_fine_and_behaves_as_none_at_runtime(env, monkeypatch):
    """The endpoint only validates the closed list; an empty name with
    HONORIFIC=name is a legal combination that `i18n.honorific_es` degrades
    to "none" for at runtime (tests/test_i18n.py covers that half)."""
    c, server = env
    r = c.post("/api/settings/preferences",
               json={"user_name": "", "honorific": "name"},
               headers={"Origin": DASHBOARD_ORIGIN})
    assert r.status_code == 200, r.text
    _, parsed = server._read_env()
    assert parsed["HONORIFIC"] == "name"
    assert parsed.get("USER_NAME", "") == ""


# ── el nombre solo para saludar ────────────────────────────────────────────

def test_the_name_is_used_to_greet_and_dropped_elsewhere(monkeypatch):
    import i18n
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    monkeypatch.setenv("HONORIFIC", "name")
    monkeypatch.setenv("USER_NAME", "Andrés")
    assert i18n.t("Good morning, sir.") == "Buenos días, Andrés."
    assert i18n.t("Terminal's open in jarvis, sir.") == "La terminal está abierta en jarvis."
    assert "Andrés" not in i18n.t("Cancelled, sir.")
