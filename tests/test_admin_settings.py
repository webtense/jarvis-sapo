"""La pantalla de administración: lo que puede escribir y lo que puede leer.

Dos mitades, y las dos son sobre el MISMO riesgo. `/admin.html` es una
página web que escribe en el `.env` del equipo, así que:

1. **La escritura tiene esquema cerrado.** `POST /api/admin/settings` no es
   "escribe esta variable": es una lista fija de claves, cada una con una
   enumeración o un rango de enteros. Las pruebas de abajo no comprueban las
   dos claves que se me ocurrieron al escribirlas, comprueban la CLASE: toda
   clave que NO está en `ADMIN_SETTABLE_KEYS` se rechaza (y `JARVIS_CLAUDE_PATH`
   — el binario que el cerebro lanza — está entre ellas por nombre, porque es
   el peor caso concreto), y toda clave que SÍ está tiene declarada una
   enumeración o un rango, encontrados en el propio servidor y no escritos
   aquí.

2. **La lectura no devuelve secretos.** Ninguna de las cuatro rutas de
   lectura puede contener un token, una URL o una ruta de fichero: se
   comprueba contra respuestas construidas con ficheros de prueba que llevan
   valores reconocibles dentro, y se busca cada uno de esos valores en el
   JSON entero.

El `.env` que se escribe es el temporal de `JARVIS_ENV_FILE`; ninguna prueba
puede tocar la configuración del desarrollador.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    monkeypatch.setenv("JARVIS_ENV_FILE", str(tmp_path / ".env"))
    import data_paths
    importlib.reload(data_paths)
    import run_store
    importlib.reload(run_store)
    import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    return server_module


@pytest.fixture
def client(server):
    with TestClient(server.app) as c:
        yield c


def save(client, settings: dict):
    return client.post("/api/admin/settings", json={"settings": settings},
                       headers=ORIGIN)


# --- the schema is closed ------------------------------------------------

def test_the_schema_is_not_empty_and_covers_every_key_it_claims(server):
    """A schema found empty would make every parametrised test below pass
    without asking anything."""
    keys = server.ADMIN_SETTABLE_KEYS
    assert len(keys) >= 9, sorted(keys)
    for key in keys:
        declared = (key in server.ADMIN_ENUM_VALUES
                    or key in server.ADMIN_INT_RANGES
                    or key in server.ADMIN_TEXT_KEYS)
        assert declared, f"{key} is writable with no type declared"
        assert key in server.ADMIN_APPLIES, f"{key} never says when it applies"
    for key in server.ADMIN_APPLIES:
        assert key in keys, f"{key} says when it applies but cannot be saved"
    # Everything the screen may write, the writer may write.
    assert keys <= server.SETTABLE_ENV_KEYS


@pytest.mark.parametrize("key", [
    # The concrete worst cases, by name: the binary the brain is spawned
    # from, the root every containment check is measured against, and the
    # one key on this surface that IS a credential.
    "JARVIS_CLAUDE_PATH", "JARVIS_PROJECT_ROOTS", "FISH_API_KEY",
    "FISH_VOICE_ID", "JARVIS_DATA_DIR", "PATH", "JARVIS_SKIP_PERMISSIONS",
    "JARVIS_ALLOWED_ORIGINS", "HA_TOKEN_FILE", "HA_ACTIONS_TOKEN_FILE",
    "GOOGLE_TOKEN_FILE", "ANTHROPIC_API_KEY",
])
def test_a_key_this_screen_does_not_own_is_refused(client, server, key):
    assert key not in server.ADMIN_SETTABLE_KEYS, key
    r = save(client, {key: "whatever"})
    assert r.status_code == 400, r.text
    assert r.json()["key"] == key
    _, parsed = server._read_env()
    assert key not in parsed, f"{key} reached .env"


def test_a_foreign_key_alongside_a_good_one_saves_neither(client, server):
    """Validated in full before the first write, so a form cannot be half
    applied — the same rule `/api/settings/preferences` already follows."""
    r = save(client, {"HONORIFIC": "none", "JARVIS_CLAUDE_PATH": "/tmp/evil"})
    assert r.status_code == 400, r.text
    _, parsed = server._read_env()
    assert "JARVIS_CLAUDE_PATH" not in parsed
    assert "HONORIFIC" not in parsed


def test_an_out_of_range_value_alongside_a_good_one_saves_neither(client, server):
    r = save(client, {"JARVIS_STT": "local", "JARVIS_WAKE_WINDOW_SEC": "99999"})
    assert r.status_code == 400, r.text
    _, parsed = server._read_env()
    assert "JARVIS_STT" not in parsed
    assert "JARVIS_WAKE_WINDOW_SEC" not in parsed


# --- enumerations --------------------------------------------------------

def _enum_keys(server):
    return sorted(server.ADMIN_ENUM_VALUES)


@pytest.mark.parametrize("bad", ["", " ", "On", "BROWSER", "yes", "1", "true",
                                 "local; rm -rf /", "../../etc/passwd"])
def test_a_value_outside_the_enumeration_is_refused(client, server, bad):
    for key in _enum_keys(server):
        if bad in server.ADMIN_ENUM_VALUES[key]:
            continue
        r = save(client, {key: bad})
        assert r.status_code == 400, (key, bad, r.text)
        _, parsed = server._read_env()
        assert key not in parsed, (key, bad)


def test_every_value_the_enumeration_declares_is_accepted(client, server):
    """A closed list that refuses its own members is a broken list."""
    for key in _enum_keys(server):
        for value in server.ADMIN_ENUM_VALUES[key]:
            r = save(client, {key: value})
            assert r.status_code == 200, (key, value, r.text)
            _, parsed = server._read_env()
            assert parsed[key] == value, (key, value)


# --- integers ------------------------------------------------------------

def test_the_integer_ranges_are_real_ranges(server):
    for key, (low, high) in server.ADMIN_INT_RANGES.items():
        assert isinstance(low, int) and isinstance(high, int), key
        assert 0 < low < high, (key, low, high)


def test_an_integer_outside_its_range_is_refused(client, server):
    for key, (low, high) in server.ADMIN_INT_RANGES.items():
        for bad in (str(low - 1), str(high + 1), "0", "999999999"):
            r = save(client, {key: bad})
            assert r.status_code == 400, (key, bad, r.text)
            _, parsed = server._read_env()
            assert key not in parsed, (key, bad)


@pytest.mark.parametrize("bad", ["", "-5", "+5", "5.0", "5e1", " 5", "5 ",
                                 "0x10", "nan", "inf", "٥", "５"])
def test_a_value_that_is_not_a_plain_whole_number_is_refused(client, server, bad):
    """`str.isdigit()` is true for Arabic-Indic and full-width digits, and
    `int()` accepts both — so the check asks for ASCII first. A `+5` or a
    ` 5 ` would round-trip into `.env` as a different string than the one
    the page believes it saved."""
    for key in server.ADMIN_INT_RANGES:
        r = save(client, {key: bad})
        assert r.status_code == 400, (key, bad, r.text)


def test_the_ends_of_every_range_are_accepted(client, server):
    for key, (low, high) in server.ADMIN_INT_RANGES.items():
        for value in (str(low), str(high)):
            r = save(client, {key: value})
            assert r.status_code == 200, (key, value, r.text)
            _, parsed = server._read_env()
            assert parsed[key] == value


# --- free text -----------------------------------------------------------

def test_the_only_free_text_is_bounded_and_single_line(client, server):
    assert server.ADMIN_TEXT_KEYS == ("USER_NAME",)
    assert "USER_NAME" in server.ENV_NAME_KEYS          # 64 characters, not 500
    for bad in ("x" * 200, "Tony\nFISH_API_KEY=stolen", "a\x1b[2Jb",
                "a\x85b", " Tony", "Tony "):
        r = save(client, {"USER_NAME": bad})
        assert r.status_code == 400, (bad, r.text)
    r = save(client, {"USER_NAME": "Étienne d'Arcy-Smith"})
    assert r.status_code == 200, r.text


# --- what reaches .env, and what comes back ------------------------------

def test_an_ordinary_save_reaches_the_env_file(client, server, tmp_path):
    r = save(client, {"JARVIS_WAKE_WINDOW_SEC": "45", "JARVIS_BARGE_IN": "on"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == ["JARVIS_BARGE_IN", "JARVIS_WAKE_WINDOW_SEC"]
    assert set(body["applies"]) == set(body["saved"])
    _, parsed = server._read_env()
    assert parsed["JARVIS_WAKE_WINDOW_SEC"] == "45"
    assert parsed["JARVIS_BARGE_IN"] == "on"
    # And the file the test redirected, not the developer's.
    assert (tmp_path / ".env").is_file()
    assert "JARVIS_WAKE_WINDOW_SEC=45" in (tmp_path / ".env").read_text()


def test_an_empty_save_is_refused_rather_than_reported_as_done(client):
    r = client.post("/api/admin/settings", json={"settings": {}}, headers=ORIGIN)
    assert r.status_code == 400, r.text


def test_the_answer_never_repeats_the_value_it_was_given(client):
    """Nothing on this screen is secret today. A route that echoes what it
    was handed is a route that will echo the wrong thing the day one is."""
    r = save(client, {"USER_NAME": "Zzqxwv"})
    assert r.status_code == 200, r.text
    assert "Zzqxwv" not in r.text


def test_the_current_values_come_back_with_the_schema(client, server):
    save(client, {"JARVIS_STT": "local"})
    r = client.get("/api/admin/settings", headers=ORIGIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["values"]["JARVIS_STT"] == "local"
    assert set(body["schema"]["enums"]) == set(server.ADMIN_ENUM_VALUES)
    assert set(body["schema"]["ints"]) == set(server.ADMIN_INT_RANGES)
    assert set(body["values"]) == set(server.ADMIN_SETTABLE_KEYS)


def test_no_settings_route_hands_back_a_key(client, server):
    """`FISH_API_KEY` is in `.env` and is NOT one of this screen's keys. It
    must not come back through the screen's own read either."""
    server._write_env_key("FISH_API_KEY", "fish-key-abcdef123456")
    r = client.get("/api/admin/settings", headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert "fish-key-abcdef123456" not in r.text
    assert "FISH_API_KEY" not in r.json()["values"]


# --- the read-only routes keep their secrets -----------------------------

SECRET_URL = "https://mcp.example.invalid/sEcReT-Path-9931"  # gitleaks:allow valor de prueba falso
SECRET_TOKEN = "ha-token-value-77771111"  # gitleaks:allow valor de prueba falso


@pytest.fixture
def wired(server, tmp_path, monkeypatch):
    """A connections file, a token file and an audit log, all with
    recognisable values inside, at the real paths `data_paths` resolves."""
    import data_paths
    home = data_paths.brain_home()
    home.mkdir(parents=True, exist_ok=True)

    token = tmp_path / "ha_token"
    token.write_text(SECRET_TOKEN)
    os.chmod(token, 0o600)
    google = tmp_path / "google.json"
    google.write_text(json.dumps({
        "access_token": "google-access-99", "refresh_token": "google-refresh-99",
        "client_id": "cid-99", "client_secret": "csecret-99",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    }))
    os.chmod(google, 0o600)

    data_paths.connections_path().write_text(json.dumps({"mcpServers": {
        "casa": {"command": "/opt/nowhere/python3",
                 "args": ["/opt/nowhere/ha_readonly_mcp.py"],
                 "env": {"HA_URL": "http://ha.example.invalid:8123",
                         "HA_TOKEN_FILE": str(token)}},
        "google": {"command": "/opt/nowhere/python3",
                   "env": {"GOOGLE_TOKEN_FILE": str(google)}},
        "faraway": {"type": "http", "url": SECRET_URL,
                    "headers": {"Authorization": "Basic c2VjcmV0OnBhc3M="}},
    }}))
    data_paths.home_actions_path().write_text(json.dumps({
        "enabled": True,
        "entities": {"light.salon": "la luz del salón",
                     "light.cocina": "la luz de la cocina",
                     "switch.cafetera": "la cafetera"},
        "services": {"light": ["turn_on", "turn_off"], "switch": ["turn_on"]},
    }))
    os.chmod(data_paths.home_actions_path(), 0o600)
    data_paths.home_actions_log_path().write_text("\n".join(
        json.dumps({"ts": "2026-10-21T09:00:0%d+00:00" % i,
                    "entity": "light.salon", "service": "light.turn_on",
                    "params": {}, "confirmed_by": "voice", "result": "ok",
                    "dryrun": False}) for i in range(5)) + "\n")
    data_paths.ha_exclude_path().write_text(json.dumps({
        "entity_ids": ["light.habitacion_101"], "patterns": [], "domains": ["lock"]}))
    os.chmod(data_paths.ha_exclude_path(), 0o600)
    # The probe must not go out over the network from a unit test.
    monkeypatch.setattr(server, "_probe_remote", _never_answers)
    return server


async def _never_answers(url: str) -> str:
    return "down"


READ_ROUTES = ["/api/admin/connections", "/api/admin/home",
               "/api/admin/system", "/api/admin/settings"]


@pytest.mark.parametrize("route", READ_ROUTES)
def test_no_read_route_returns_a_secret(client, wired, route, tmp_path):
    r = client.get(route, headers=ORIGIN)
    assert r.status_code == 200, r.text
    body = r.text
    for secret in (SECRET_TOKEN, SECRET_URL, "google-access-99",
                   "google-refresh-99", "csecret-99", "cid-99",
                   "c2VjcmV0OnBhc3M=", str(tmp_path / "ha_token"),
                   "/opt/nowhere/python3", "ha.example.invalid"):
        assert secret not in body, f"{route} leaked {secret!r}"


def test_the_connections_report_names_the_servers_and_nothing_else(client, wired):
    body = client.get("/api/admin/connections", headers=ORIGIN).json()
    names = sorted(s["name"] for s in body["servers"])
    assert names == ["casa", "faraway", "google"]
    kinds = {s["name"]: s["kind"] for s in body["servers"]}
    assert kinds == {"casa": "local", "google": "local", "faraway": "remote"}
    # The local probe is honest about a command that is not there.
    assert {s["name"]: s["status"] for s in body["servers"]}["casa"] == "missing"
    # Each entry carries exactly three fields: nothing can leak in a fourth.
    for entry in body["servers"]:
        assert set(entry) == {"name", "kind", "status"}


def test_the_home_assistant_and_google_states_are_states_not_values(client, wired):
    body = client.get("/api/admin/connections", headers=ORIGIN).json()
    assert body["home_assistant"]["configured"] is True
    assert body["home_assistant"]["read_token"] == "ok"
    assert body["google"]["credentials_complete"] is True
    assert body["google"]["scopes"] == [
        "https://www.googleapis.com/auth/gmail.readonly"]
    # JARVIS never records which account was authorised, so it never claims one.
    assert body["google"]["account_known"] is False
    assert "account" not in body["google"]


def test_a_token_file_other_people_can_read_is_reported_as_such(client, wired,
                                                                tmp_path):
    os.chmod(tmp_path / "ha_token", 0o644)
    body = client.get("/api/admin/connections", headers=ORIGIN).json()
    assert body["home_assistant"]["read_token"] == "permissions"


def test_the_house_is_counted_by_domain_and_named(client, wired):
    body = client.get("/api/admin/home", headers=ORIGIN).json()
    assert body["enabled"] is True
    assert body["by_domain"] == {"light": 2, "switch": 1}
    assert body["names"] == ["la cafetera", "la luz de la cocina",
                             "la luz del salón"]
    assert body["excluded"] == {"valid": True, "entity_ids": 1, "patterns": 0,
                                "domains": 1}


def test_the_audit_tail_is_the_last_lines_and_only_three_fields(client, wired):
    import data_paths
    many = "\n".join(json.dumps({"ts": f"t{i}", "entity": "light.salon",
                                 "service": "light.turn_on", "result": "ok",
                                 "confirmed_by": "voice", "dryrun": False})
                     for i in range(60))
    data_paths.home_actions_log_path().write_text(many + "\n")
    body = client.get("/api/admin/home", headers=ORIGIN).json()
    assert len(body["log"]) == 20
    assert body["log"][-1]["ts"] == "t59"
    for line in body["log"]:
        assert set(line) == {"ts", "entity", "result", "dryrun"}


def test_a_log_line_that_is_not_json_is_skipped_not_shown(client, wired):
    import data_paths
    data_paths.home_actions_log_path().write_text(
        "not json at all\n" + json.dumps(
            {"ts": "t", "entity": "light.salon", "result": "ok"}) + "\n")
    body = client.get("/api/admin/home", headers=ORIGIN).json()
    assert [line["entity"] for line in body["log"]] == ["light.salon"]


def test_text_out_of_a_file_arrives_without_control_characters(client, wired):
    """The page paints these with `textContent`, which is the real defence.
    Stripping them here as well means a terminal, a log line or a copy-paste
    of this JSON cannot be driven by somebody's entity name either."""
    import data_paths
    data_paths.home_actions_path().write_text(json.dumps({
        "enabled": True,
        "entities": {"light.salon": "la luz\u001b[2J del salón"},
        "services": {"light": ["turn_on"]}}))
    os.chmod(data_paths.home_actions_path(), 0o600)
    body = client.get("/api/admin/home", headers=ORIGIN).json()
    assert body["names"] == ["la luz?[2J del salón"]


def test_the_house_says_it_is_off_when_there_is_no_allow_list(client, server):
    body = client.get("/api/admin/home", headers=ORIGIN).json()
    assert body["enabled"] is False
    assert body["reason"]
    assert body["by_domain"] == {} and body["names"] == []


def test_the_system_report_reads_the_version_and_never_writes_it(client, server):
    before = (ROOT / "CHANGELOG.md").read_bytes()
    body = client.get("/api/admin/system", headers=ORIGIN).json()
    assert body["version"].startswith("ubuntu-"), body["version"]
    assert (ROOT / "CHANGELOG.md").read_bytes() == before
    assert body["service"] in ("systemd", "manual")
    assert set(body["listening"]) == {"mode", "wake", "wake_window_sec",
                                      "barge_in", "model", "warnings"}


# --- the fixed paths -----------------------------------------------------

def test_the_only_files_read_are_the_four_fixed_ones(client, wired, monkeypatch):
    """No path reaches `admin_panel` from a request. The proof is a watch on
    `open` while every read route is called: each file touched under the data
    directory must be one of the four this screen is allowed to read."""
    import data_paths
    import builtins
    home = data_paths.brain_home().resolve()
    allowed = {data_paths.connections_path().resolve(),
               data_paths.home_actions_path().resolve(),
               data_paths.home_actions_log_path().resolve(),
               data_paths.ha_exclude_path().resolve()}
    seen: list[Path] = []
    real_open = builtins.open

    def watched(file, *args, **kwargs):
        try:
            path = Path(file).resolve()
        except (TypeError, ValueError, OSError):
            path = None
        if path is not None and home in path.parents:
            seen.append(path)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watched)
    for route in READ_ROUTES:
        assert client.get(route, headers=ORIGIN).status_code == 200, route
    assert seen, "the watch saw nothing: it is not watching"
    assert set(seen) <= allowed, sorted(str(p) for p in set(seen) - allowed)


def test_the_screen_cannot_ask_for_a_path(server):
    """The read routes take no argument at all — there is nothing to point
    somewhere else. Asserted on the routes themselves rather than by trying
    one query string, so a parameter added later fails here."""
    import inspect
    for route in server.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/admin/") or "GET" not in getattr(route, "methods", set()):
            continue
        params = inspect.signature(route.endpoint).parameters
        assert not params, (path, sorted(params))


def test_the_house_says_whether_the_panel_may_confirm(client, wired):
    """`panel_confirm` es una decisión del usuario en SU fichero; la pantalla
    la lee para saber si ofrece Confirmar, nunca la cambia."""
    import data_paths
    path = data_paths.home_actions_path()
    assert client.get("/api/admin/home", headers=ORIGIN).json()["panel_confirm"] is False
    body = json.loads(path.read_text())
    body["panel_confirm"] = True
    path.write_text(json.dumps(body))
    os.chmod(path, 0o600)
    assert client.get("/api/admin/home", headers=ORIGIN).json()["panel_confirm"] is True
    assert json.loads(path.read_text()) == body, "the screen rewrote the allow-list"


def test_the_house_is_not_confirmable_from_the_panel_when_it_is_off(client, server):
    assert client.get("/api/admin/home", headers=ORIGIN).json()["panel_confirm"] is False


def test_the_admin_page_builds_no_markup_from_strings():
    """La página pinta nombres de entidades, de servidores y líneas de un
    registro que JARVIS no escribe: nada de ello puede pasar por un analizador
    de HTML. Se busca en TODO lo que hay en `src/admin/` y en `admin.html`."""
    banned = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
              "createContextualFragment", "DOMParser")
    sources = sorted((ROOT / "frontend" / "src" / "admin").rglob("*.ts"))
    assert sources, "no admin sources found: the scan is broken"
    sources.append(ROOT / "frontend" / "admin.html")
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.name} uses {word}"


def test_the_mute_button_is_on_the_main_page_and_not_in_the_menu():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    start = html.index('id="menu-dropdown"')
    menu = html[start:html.index("</div>", start)]
    assert 'id="btn-settings"' in menu, "the slice is not the menu"
    assert 'id="mute-toggle"' in html
    assert 'id="mute-toggle"' not in menu
    assert 'aria-pressed="false"' in html
