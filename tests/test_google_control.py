"""Writing to Google through the server: propose, ask aloud, act only on the
user's "yes". Same pattern as `test_home_control.py`.

The speech scheduler and Google are fakes (the network seam is
`google_actions._request`); no audio, no network, no real credentials. What
is pinned here is the part `test_google_actions.py` cannot see: that the
brain's tool only STAGES an action, that the spoken yes is read by plain word
matching and only after the whole question was heard, that nothing a model,
a transcript or another session says can perform a write, and that the panel
is off unless the user's own file turns it on.
"""
import asyncio
import importlib
import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import bridge  # noqa: E402
import google_actions  # noqa: E402
import home_actions  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
WRITE_TOKEN = "-".join(["fake", "write", "value", "for", "tests"])    # gitleaks:allow
ORIGIN = {"Origin": "http://localhost:5173"}

POLICY = {
    "enabled": True,
    "calendars": ["primary"],
    "task_lists": ["@default"],
    "limits": {"max_per_minute": 2, "max_text_chars": 200, "max_lead_days": 30},
}
TASK = {"action": "task_create", "title": "Comprar pan"}
DAY = date.today() + timedelta(days=3)
DAY_S = DAY.isoformat()
EVENT = {"action": "calendar_create", "title": "Cena", "start": DAY_S + "T21:00:00+02:00",
         "duration_minutes": 90, "location": "Bar Pepe"}


class FakeUtt:
    def __init__(self, done=True):
        self.done = done


class FakeSpeech:
    def __init__(self):
        self.said = []
        self.played = True

    async def say(self, text, priority=None, immediate=None):
        self.said.append((text, priority, immediate))
        return FakeUtt()

    async def wait_for(self, utt, timeout=60.0):
        return self.played


class StubBrain:
    ready = False

    def __init__(self, origin="user"):
        self.current_origin = origin
        self.label = None

    @property
    def turn_untrusted_source(self):
        return self.label

    @property
    def turn_is_tainted(self):
        return self.label is not None

    def mark_untrusted_content(self, source):
        self.label = self.label or source

    def mark_web_content(self):
        self.mark_untrusted_content("a web page")

    async def stop(self):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    for name in ("GOOGLE_TOKEN_FILE", "GOOGLE_WRITE_TOKEN_FILE", "JARVIS_GOOGLE_DRYRUN"):
        monkeypatch.delenv(name, raising=False)
    import server as server_module
    importlib.reload(server_module)

    now = [1000.0]
    monkeypatch.setattr(server_module, "bridge_state", bridge.Bridge(
        prepare=server_module.strip_markdown_for_tts, clock=lambda: now[0]))
    monkeypatch.setattr(server_module, "google_limits",
                        google_actions.Limits(lambda: now[0]))

    fake = FakeSpeech()
    monkeypatch.setattr(server_module, "speech", fake)
    server_module.voice_clients.add(object())

    real_spawn = server_module._spawn

    def spawn(coro):
        if getattr(getattr(coro, "cr_code", None), "co_name", "") == "_bridge_announced_when_spoken":
            coro.close()
            return None
        return real_spawn(coro)

    monkeypatch.setattr(server_module, "_spawn", spawn)

    calls = []

    def fake_request(method, host, path, *, query=None, body=None, _retry=True):
        calls.append((method, host, path, query, body))
        if path == "/tasks/v1/lists/@default/tasks":
            return {"id": "t1"}
        return {"id": "x"}

    monkeypatch.setattr(google_actions, "_request", fake_request)

    notified = []

    async def fake_notify(title, message, *, subtitle=""):
        notified.append((title, message, subtitle))
        return True

    monkeypatch.setattr(server_module.notifier, "available", lambda: True)
    monkeypatch.setattr(server_module.notifier, "notify", fake_notify)

    def write_policy(data=POLICY, mode=0o600):
        path = google_actions.data_paths.google_actions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        path.chmod(mode)

    def configure():
        token = tmp_path / "write_token.json"
        token.write_text(json.dumps({"access_token": WRITE_TOKEN}), encoding="utf-8")
        token.chmod(0o600)
        monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE", str(token))

    write_policy()
    configure()
    client = TestClient(server_module.app)
    return SimpleNamespace(server=server_module, speech=fake, calls=calls, now=now,
                           client=client, write_policy=write_policy, configure=configure,
                           notified=notified, monkeypatch=monkeypatch, tmp=tmp_path)


def log(env):
    path = google_actions.data_paths.google_actions_log_path()
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text("utf-8").splitlines()]


async def propose(env, args=None):
    return await env.server.tool_google_propose(dict(TASK if args is None else args))


async def announce(env):
    b = env.server.bridge_state
    for pid in list(b.proposals):
        await env.server._bridge_announced_when_spoken(pid, FakeUtt(), b.brain_epoch)


def googles(env):
    return [p for p in env.server.bridge_state.proposals.values() if p.kind == "google"]


async def say(env, words):
    return await env.server._bridge_take_utterance(words)


# --- off by default -----------------------------------------------------------

@pytest.mark.asyncio
async def test_without_a_file_nothing_is_proposed_said_or_sent(env):
    google_actions.data_paths.google_actions_path().unlink()
    text = await propose(env)
    assert "not available" in text and "switched off" in text
    assert env.speech.said == [] and googles(env) == []
    assert env.calls == [] and log(env) == []


@pytest.mark.asyncio
async def test_a_switched_off_file_is_off(env):
    env.write_policy({**POLICY, "enabled": False})
    assert "not available" in await propose(env)
    assert env.calls == []


# --- the brain's tool only stages ---------------------------------------------

@pytest.mark.asyncio
async def test_proposing_asks_aloud_and_sends_nothing(env):
    text = await propose(env)
    assert "NOTHING has been done" in text and "end your turn" in text
    assert env.calls == []
    assert [s[0] for s in env.speech.said] == ["¿Añado Comprar pan a sus tareas, señor?"]
    (p,) = googles(env)
    assert p.kind == "google" and p.payload["kind"] == "task_create"
    assert [e["result"] for e in log(env)] == ["proposed"]
    assert log(env)[0]["confirmed_by"] == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"action": "task_create", "title": "x", "attendees": ["a@b.com"]},
    {"action": "task_create", "title": "avisa a juan@example.com"},
    {"action": "calendar_create", "title": "x", "start": DAY_S + "T10:00:00",
     "sendUpdates": "all"},
    {"action": "task_create", "task_list": "other", "title": "x"},
    {"action": "note_append", "doc_id": "../../etc/passwd", "text": "x"},
    {"action": "unknown_action"},
    {},
])
async def test_a_refused_proposal_stages_nothing_and_is_logged(env, args):
    text = await propose(env, args)
    assert "Refused" in text
    assert env.speech.said == [] and googles(env) == []
    assert env.calls == []
    (entry,) = log(env)
    assert entry["result"].startswith("refused") and entry["confirmed_by"] == "none"


@pytest.mark.asyncio
async def test_only_one_action_waits_at_a_time(env):
    await propose(env)
    await propose(env, {"action": "task_create", "title": "Otra tarea"})
    (p,) = googles(env)
    assert p.payload["fields"]["title"] == "Otra tarea"


@pytest.mark.asyncio
async def test_proposals_are_rate_limited(env):
    results = [await propose(env) for _ in range(google_actions.PROPOSALS_PER_5_MIN + 2)]
    assert sum("Too many requests" in r for r in results) == 2
    assert env.calls == []


@pytest.mark.asyncio
async def test_with_nobody_to_hear_it_nothing_waits(env):
    env.server.voice_clients.clear()
    text = await propose(env)
    assert "Nobody can hear" in text and googles(env) == []


@pytest.mark.asyncio
async def test_with_nobody_to_hear_it_the_panel_may_hold_it_if_the_file_says_so(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    env.server.voice_clients.clear()
    text = await propose(env)
    assert "Pending" in text and len(googles(env)) == 1
    assert "asking him aloud" not in text


@pytest.mark.asyncio
async def test_no_audit_log_no_proposal(env):
    path = google_actions.data_paths.google_actions_log_path()
    path.mkdir(parents=True, exist_ok=True)
    text = await propose(env)
    assert "audit log" in text and googles(env) == [] and env.speech.said == []


@pytest.mark.asyncio
async def test_a_dry_run_is_announced_to_the_brain(env):
    env.monkeypatch.setenv("JARVIS_GOOGLE_DRYRUN", "1")
    assert "dry run" in (await propose(env)).lower()


# --- the user's yes -----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_spoken_yes_after_hearing_the_whole_question_does_it(env):
    await propose(env)
    await announce(env)
    assert env.calls == []
    assert await say(env, "sí") is True
    (call,) = env.calls
    method, host, path, query, body = call
    assert method == "POST" and path == "/tasks/v1/lists/@default/tasks"
    assert body == {"title": "Comprar pan"}
    assert env.speech.said[-1][0] == "Hecho, señor."
    assert googles(env) == []
    assert [(e["result"], e["confirmed_by"]) for e in log(env)] == [
        ("proposed", "none"), ("ok", "voice")]


@pytest.mark.asyncio
async def test_a_yes_before_the_question_was_heard_goes_to_the_brain(env):
    await propose(env)
    assert await say(env, "sí") is False
    assert env.calls == [] and len(googles(env)) == 1


@pytest.mark.asyncio
async def test_only_the_strict_action_words_confirm_a_google_write(env):
    await propose(env)
    await announce(env)
    assert await say(env, "ok") is False          # too weak for an action
    assert env.calls == [] and len(googles(env)) == 1
    assert await say(env, "sí") is True
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_a_plain_no_rejects_and_sends_nothing(env):
    await propose(env)
    await announce(env)
    assert await say(env, "no") is True
    assert env.calls == [] and googles(env) == []
    assert log(env)[-1]["result"] == "rejected"


@pytest.mark.asyncio
async def test_an_old_request_expires_even_if_it_is_decided_late(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    await propose(env)
    pid = googles(env)[0].id
    env.now[0] += env.server.GOOGLE_PROPOSAL_TTL + 1
    r = env.client.post(f"/api/google/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 200 and "caducado" in r.json()["text"]
    assert env.calls == [] and log(env)[-1]["result"] == "expired"


@pytest.mark.asyncio
async def test_no_audit_log_at_the_moment_of_acting_means_no_action(env):
    await propose(env)
    await announce(env)
    path = google_actions.data_paths.google_actions_log_path()
    path.unlink()
    path.mkdir()
    await say(env, "sí")
    assert env.calls == []


# --- the whole action is said, and only then is a "yes" listened for --------------

@pytest.mark.asyncio
async def test_a_calendar_event_is_said_with_its_day_hours_and_place(env):
    await propose(env, EVENT)
    (said,) = [s[0] for s in env.speech.said]
    assert said.startswith("¿Creo el evento Cena, el ") and "de 21:00 a 22:30" in said
    assert said.endswith("en Bar Pepe, en su calendario, señor?")


@pytest.mark.asyncio
async def test_a_confirmed_event_is_one_post_with_no_attendees_and_no_notification(env):
    await propose(env, EVENT)
    await announce(env)
    assert await say(env, "sí") is True
    (call,) = env.calls
    method, host, path, query, body = call
    assert (method, host, path) == ("POST", "www.googleapis.com",
                                    "/calendar/v3/calendars/primary/events")
    assert query == {"sendUpdates": "none"}
    assert set(body) == {"summary", "start", "end", "location"}


@pytest.mark.asyncio
async def test_a_yes_is_not_listened_for_while_the_brain_is_still_talking(env, monkeypatch):
    monkeypatch.setattr(bridge, "SPEAK_WAIT_MAX", 0.2)
    lock = asyncio.Lock()
    await lock.acquire()
    env.server.brain_instance = SimpleNamespace(_turn_lock=lock)
    await propose(env)
    pid = googles(env)[0].id
    await env.server._bridge_announced_when_spoken(pid, FakeUtt(), env.server.bridge_state.brain_epoch)
    assert await say(env, "sí") is False and env.calls == [] and len(googles(env)) == 1
    lock.release()
    await env.server._bridge_announced_when_spoken(pid, FakeUtt(), env.server.bridge_state.brain_epoch)
    assert await say(env, "sí") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_anything_else_said_after_the_question_disarms_it(env):
    await propose(env)
    await announce(env)
    env.server.bridge_state.note_brain_turn()       # the brain started talking
    assert await say(env, "sí") is False
    assert env.calls == [] and len(googles(env)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["qué hora es", "sí, pero a las nueve", "vale", "ok", "si",
                                   "claro", "yes no", "hazlo no"])
async def test_what_is_not_a_plain_yes_is_not_one(env, words):
    await propose(env)
    await announce(env)
    assert await say(env, words) is False
    assert env.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["sí", "hazlo", "adelante", "confirma", "vale, hazlo",
                                   "yes", "do it"])
async def test_the_strict_yes_words_do_it(env, words):
    await propose(env)
    await announce(env)
    assert await say(env, words) is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_the_opt_in_strict_mode_asks_for_two_words(env, monkeypatch):
    monkeypatch.setenv("JARVIS_GOOGLE_CONFIRM_STRICT", "1")
    await propose(env)
    await announce(env)
    assert await say(env, "sí") is False and env.calls == []
    assert await say(env, "sí, hazlo") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_the_outcome_sentences(env, monkeypatch):
    sentences = {}
    for code, text in (("not_our_note", "Esa no es una de mis notas, señor"),
                       ("reauth", "El permiso de Google ha caducado, señor"),
                       ("http_403", "Google lo ha rechazado, señor."),
                       ("unreachable", "No he podido contactar con Google, señor."),
                       ("GOOGLE_WRITE_TOKEN_FILE is not set.", "no está bien configurada")):
        monkeypatch.setattr(google_actions, "perform",
                            lambda a, code=code: google_actions.Outcome(False, code))
        await propose(env)
        await announce(env)
        pid = googles(env)[0].id
        sentences[code] = await env.server._google_decide(pid, True, via="voice")
        assert text in sentences[code], (code, sentences[code])
        env.now[0] += 120
        env.server.google_limits = google_actions.Limits(lambda: env.now[0])


# --- the panel ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_panel_cannot_confirm_unless_the_users_file_allows_it(env):
    await propose(env)
    pid = googles(env)[0].id
    r = env.client.post(f"/api/google/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 200 and "de viva voz" in r.json()["text"]
    assert env.calls == [] and len(googles(env)) == 1
    await announce(env)
    assert await say(env, "sí") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_the_panel_can_always_reject(env):
    await propose(env)
    pid = googles(env)[0].id
    r = env.client.post(f"/api/google/{pid}/reject", headers=ORIGIN)
    assert r.status_code == 200 and googles(env) == [] and env.calls == []
    assert log(env)[-1]["result"] == "rejected" and log(env)[-1]["confirmed_by"] == "panel"


@pytest.mark.asyncio
async def test_with_panel_confirm_on_the_panel_acts_and_is_logged_as_the_panel(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    await propose(env)
    pid = googles(env)[0].id
    r = env.client.post(f"/api/google/{pid}/confirm", headers=ORIGIN)
    assert r.json()["text"] == "Hecho, señor."
    assert len(env.calls) == 1
    assert (log(env)[-1]["result"], log(env)[-1]["confirmed_by"]) == ("ok", "panel")


@pytest.mark.asyncio
async def test_the_panel_routes_need_the_dashboards_origin_and_a_real_decision(env):
    await propose(env)
    pid = googles(env)[0].id
    assert env.client.post(f"/api/google/{pid}/confirm").status_code in (401, 403)
    assert env.client.post(f"/api/google/{pid}/maybe", headers=ORIGIN).status_code == 404
    assert env.client.post("/api/google/zzzzzz/confirm", headers=ORIGIN).status_code == 404
    assert env.calls == []


@pytest.mark.asyncio
async def test_a_home_pid_is_not_a_google_action_and_vice_versa(env, monkeypatch):
    monkeypatch.setattr(env.server, "home_limits", home_actions.Limits(lambda: env.now[0]))
    home_policy = {"enabled": True, "entities": {"light.salon": "la luz"},
                  "services": {"light": ["turn_off"]}}
    home_actions.data_paths.home_actions_path().write_text(json.dumps(home_policy))
    home_actions.data_paths.home_actions_path().chmod(0o600)
    home_text = await env.server.tool_home_propose({"entity_id": "light.salon", "service": "turn_off"})
    assert "Pending" in home_text
    (home_p,) = [p for p in env.server.bridge_state.proposals.values() if p.kind == "home"]
    await propose(env)
    (google_p,) = googles(env)
    r = env.client.post(f"/api/google/{home_p.id}/confirm", headers=ORIGIN)
    assert r.status_code == 404
    r = env.client.post(f"/api/home/{google_p.id}/confirm", headers=ORIGIN)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_the_pending_view_shows_the_sentence_and_nothing_secret(env):
    await propose(env)
    snap = env.client.get("/api/bridge/pending").json()
    assert snap["proposals"] == []
    (item,) = snap["google"]
    assert item["text"] == "¿Añado Comprar pan a sus tareas, señor?" and set(item) == {"id", "text", "age"}
    assert WRITE_TOKEN not in json.dumps(snap)


# --- the gate: only the user, only a clean turn -------------------------------

def _tool(env, brain, **arguments):
    env.server.brain_instance = brain
    token = env.server.data_paths.ensure_tool_token()
    r = env.client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                        json={"tool": "google_propose", "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_turn_the_user_drove_may_propose(env):
    out = _tool(env, StubBrain("user"), **TASK)
    assert out["ok"] is True and "Pending" in out["text"] and len(googles(env)) == 1
    assert env.calls == []


@pytest.mark.parametrize("origin", ["watcher", "event", "bridge", "", None])
def test_nothing_proposes_off_the_users_own_turn(env, origin):
    out = _tool(env, StubBrain(origin), **TASK)
    assert out["ok"] is False and "not_allowed_from_event" in out["text"]
    assert googles(env) == [] and env.speech.said == [] and env.calls == []


@pytest.mark.parametrize("source", ["a web page", "another session's transcript",
                                    "a file in one of your projects",
                                    "the output of a run"])
def test_a_turn_that_read_something_foreign_cannot_propose(env, source):
    brain = StubBrain("user")
    brain.mark_untrusted_content(source)
    out = _tool(env, brain, **TASK)
    assert out["ok"] is False and "untrusted_content_in_this_turn" in out["text"]
    assert googles(env) == [] and env.speech.said == [] and env.calls == []


def test_the_tool_needs_the_tool_token(env):
    r = env.client.post("/internal/tool", json={"tool": "google_propose", "arguments": TASK})
    assert r.status_code in (401, 403)
    assert googles(env) == []


def test_the_bridge_token_does_not_open_the_google_tool(env):
    token = env.server.data_paths.ensure_bridge_token()
    r = env.client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                        json={"tool": "google_propose", "arguments": TASK})
    assert r.status_code in (401, 403)
    assert googles(env) == []


# --- it is wired the way the rest of the tools are ----------------------------

def test_the_tool_is_registered_allowed_and_described(env):
    import brain
    import jarvis_mcp
    assert "google_propose" in env.server.TOOL_HANDLERS
    assert "google_propose" in env.server.ACTING_TOOLS
    assert "google_propose" in env.server.TAINT_EXEMPT_TOOLS
    assert "mcp__jarvis__google_propose" in brain.ALLOWED_TOOLS
    spec = next(t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "google_propose")
    assert "action" in spec["inputSchema"]["properties"]
    assert spec["inputSchema"]["required"] == ["action"]
    assert "NOTHING happens" in spec["description"]


def test_the_tool_is_described_to_the_brain_only_when_google_writing_is_configured(env):
    import jarvis_mcp

    def listed():
        reply = jarvis_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return {t["name"] for t in reply["result"]["tools"]}

    assert "google_propose" in listed()
    google_actions.data_paths.google_actions_path().unlink()
    names = listed()
    assert "google_propose" not in names


def test_the_persona_in_spanish_documents_the_tool():
    text = (REPO / "jarvis_home" / "CLAUDE.es.md").read_text("utf-8")
    assert "google_propose" in text and "señor" in text
