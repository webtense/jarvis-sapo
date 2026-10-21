"""Home control through the server: propose, ask aloud, act only on the user's "yes".

The speech scheduler, the notifier and Home Assistant are fakes (the network
seam is `home_actions._open`); no audio, no desktop, no network, no keys. What
is pinned here is the part `test_home_actions.py` cannot see: that the brain's
tool only STAGES an action, that the spoken yes is read by plain word matching
and only after the whole question was heard, that nothing a model, a transcript
or another session says can perform an action, and that the panel is off unless
the user's own file turns it on.
"""
import asyncio
import importlib
import inspect
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import bridge  # noqa: E402
import home_actions  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ACT_TOKEN = "-".join(["fake", "action", "value", "for", "tests"])    # gitleaks:allow
ORIGIN = {"Origin": "http://localhost:5173"}

POLICY = {
    "enabled": True,
    "entities": {"light.salon": "la luz del salón", "switch.cafetera": "la cafetera",
                 "climate.salon": "la calefacción del salón"},
    "services": {"light": ["turn_on", "turn_off"], "switch": ["turn_on", "turn_off"],
                 "climate": ["set_temperature"]},
    "limits": {"max_per_minute": 2, "climate": {"min_temp": 17, "max_temp": 23}},
}
OFF = {"entity_id": "light.salon", "service": "turn_off"}


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


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class StubBrain:
    """What `/internal/tool` reads of the brain: its origin and its taint."""
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
    for name in ("HA_URL", "HA_TOKEN", "HA_TOKEN_FILE", "HA_ACTIONS_URL",
                 "HA_ACTIONS_TOKEN_FILE", "JARVIS_HOME_DRYRUN"):
        monkeypatch.delenv(name, raising=False)
    import server as server_module
    importlib.reload(server_module)

    now = [1000.0]
    monkeypatch.setattr(server_module, "bridge_state", bridge.Bridge(
        prepare=server_module.strip_markdown_for_tts, clock=lambda: now[0]))
    monkeypatch.setattr(server_module, "home_limits",
                        home_actions.Limits(lambda: now[0]))

    fake = FakeSpeech()
    monkeypatch.setattr(server_module, "speech", fake)
    server_module.voice_clients.add(object())

    # The announcement task is driven by the test (`announce`), not spawned.
    real_spawn = server_module._spawn

    def spawn(coro):
        if getattr(getattr(coro, "cr_code", None), "co_name", "") == "_bridge_announced_when_spoken":
            coro.close()
            return None
        return real_spawn(coro)

    monkeypatch.setattr(server_module, "_spawn", spawn)

    calls = []

    def fake_open(req, timeout):
        calls.append(req)
        return _Resp(b"[]")

    monkeypatch.setattr(home_actions, "_open", fake_open)

    notified = []

    async def fake_notify(title, message, *, subtitle=""):
        notified.append((title, message, subtitle))
        return True

    monkeypatch.setattr(server_module.notifier, "available", lambda: True)
    monkeypatch.setattr(server_module.notifier, "notify", fake_notify)

    def write_policy(data=POLICY, mode=0o600):
        path = home_actions.data_paths.home_actions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        path.chmod(mode)

    def configure():
        token = tmp_path / "act_token"
        token.write_text(ACT_TOKEN + "\n")
        token.chmod(0o600)
        monkeypatch.setenv("HA_ACTIONS_URL", "http://ha.invalid:8123")
        monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE", str(token))

    write_policy()
    configure()
    client = TestClient(server_module.app)
    return SimpleNamespace(server=server_module, speech=fake, calls=calls, now=now,
                           client=client, write_policy=write_policy, configure=configure,
                           notified=notified, monkeypatch=monkeypatch, tmp=tmp_path)


def log(env):
    path = home_actions.data_paths.home_actions_log_path()
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text("utf-8").splitlines()]


async def propose(env, args=None):
    return await env.server.tool_home_propose(dict(OFF if args is None else args))


async def announce(env):
    b = env.server.bridge_state
    for pid in list(b.proposals):
        await env.server._bridge_announced_when_spoken(pid, FakeUtt(), b.brain_epoch)


def homes(env):
    return [p for p in env.server.bridge_state.proposals.values() if p.kind == "home"]


async def say(env, words):
    return await env.server._bridge_take_utterance(words)


# --- off by default -----------------------------------------------------------

@pytest.mark.asyncio
async def test_without_a_file_nothing_is_proposed_said_or_sent(env):
    home_actions.data_paths.home_actions_path().unlink()
    text = await propose(env)
    assert "not available" in text and "switched off" in text
    assert env.speech.said == [] and env.server.bridge_state.proposals == {}
    assert env.calls == [] and log(env) == []
    assert str(env.tmp) not in text


@pytest.mark.asyncio
async def test_a_switched_off_file_is_off(env):
    env.write_policy({**POLICY, "enabled": False})
    assert "not available" in await propose(env)
    assert env.calls == []


# --- the brain's tool only stages -----------------------------------------------

@pytest.mark.asyncio
async def test_proposing_asks_aloud_and_sends_nothing(env):
    text = await propose(env)
    assert "NOTHING has been done" in text and "end your turn" in text
    assert env.calls == []
    assert [s[0] for s in env.speech.said] == ["¿Apago la luz del salón, señor?"]
    assert env.speech.said[0][2] is False                       # queued, not over the brain
    (p,) = homes(env)
    assert p.kind == "home" and p.payload == {
        "entity_id": "light.salon", "service": "turn_off", "params": {}}
    assert [e["result"] for e in log(env)] == ["proposed"]
    assert log(env)[0]["confirmed_by"] == "none"


@pytest.mark.asyncio
async def test_the_question_says_the_parameters_too(env):
    await propose(env, {"entity_id": "climate.salon", "service": "set_temperature",
                        "temperature": 21.5})
    assert env.speech.said[0][0] == "¿Pongo la calefacción del salón a 21,5 grados, señor?"


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"entity_id": "alarm_control_panel.casa", "service": "alarm_disarm"},
    {"entity_id": "lock.puerta", "service": "unlock"},
    {"entity_id": "camera.entrada", "service": "turn_off"},
    {"entity_id": "homeassistant.restart", "service": "restart"},
    {"entity_id": "script.todo", "service": "turn_on"},
    {"entity_id": "light.cocina", "service": "turn_off"},          # not listed
    {"entity_id": "light.salon", "service": "toggle"},             # service not listed
    {"entity_id": "LIGHT.salon", "service": "turn_off"},
    {"entity_id": "light.salon/../lock.x", "service": "turn_off"},
    {"entity_id": "light.salon", "service": "turn_off/../../homeassistant/restart"},
    {"entity_id": "climate.salon", "service": "set_temperature", "temperature": 35},
    {"entity_id": "climate.salon", "service": "set_temperature"},
    {"entity_id": "light.salon", "service": "turn_off", "area_id": "all"},
    {"entity_id": "light.salon", "service": "turn_off", "entity_ids": ["lock.x"]},
    {"service": "turn_off"}, {"entity_id": "light.salon"}, {},
])
async def test_a_refused_proposal_stages_nothing_and_is_logged(env, args):
    text = await propose(env, args)
    assert text.startswith("Refused:") or "Refused" in text
    assert env.speech.said == [] and env.server.bridge_state.proposals == {}
    assert env.calls == []
    (entry,) = log(env)
    assert entry["result"].startswith("refused") and entry["confirmed_by"] == "none"


@pytest.mark.asyncio
async def test_the_audit_cannot_be_hijacked_by_the_proposal_text(env):
    await propose(env, {"entity_id": "light.salon\nlock.front", "service": "x\r\ny"})
    assert len(log(env)) == 1


@pytest.mark.asyncio
async def test_only_one_action_waits_at_a_time(env):
    await propose(env)
    await propose(env, {"entity_id": "switch.cafetera", "service": "turn_on"})
    (p,) = homes(env)
    assert p.payload["entity_id"] == "switch.cafetera"


@pytest.mark.asyncio
async def test_proposals_are_rate_limited(env):
    results = [await propose(env) for _ in range(home_actions.PROPOSALS_PER_5_MIN + 2)]
    assert sum("Too many requests" in r for r in results) == 2
    assert env.calls == []


@pytest.mark.asyncio
async def test_with_nobody_to_hear_it_nothing_waits(env):
    env.server.voice_clients.clear()
    text = await propose(env)
    assert "Nobody can hear" in text and homes(env) == []
    assert env.speech.said == []


@pytest.mark.asyncio
async def test_with_nobody_to_hear_it_the_panel_may_hold_it_if_the_file_says_so(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    env.server.voice_clients.clear()
    text = await propose(env)
    assert "Pending" in text and len(homes(env)) == 1
    # Finding #3 of the security audit: with nobody to hear JARVIS, the tool
    # result must not claim it is "asking him aloud" — the brain must not
    # believe a question was ever spoken when it was not.
    assert "asking him aloud" not in text
    assert "nobody can hear" in text.lower() and "panel" in text.lower()


@pytest.mark.asyncio
async def test_with_somebody_to_hear_it_the_message_says_it_was_asked_aloud(env):
    text = await propose(env)
    assert "asking him aloud" in text


@pytest.mark.asyncio
async def test_no_audit_log_no_proposal(env):
    path = home_actions.data_paths.home_actions_log_path()
    path.mkdir(parents=True, exist_ok=True)
    text = await propose(env)
    assert "audit log" in text and homes(env) == [] and env.speech.said == []


@pytest.mark.asyncio
async def test_a_dry_run_is_announced_to_the_brain(env):
    env.monkeypatch.setenv("JARVIS_HOME_DRYRUN", "1")
    assert "dry run" in (await propose(env)).lower()


# --- the user's yes ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_spoken_yes_after_hearing_the_whole_question_does_it(env):
    await propose(env)
    await announce(env)
    assert env.calls == []
    assert await say(env, "sí") is True
    (req,) = env.calls
    assert req.get_method() == "POST"
    assert req.full_url == "http://ha.invalid:8123/api/services/light/turn_off"
    assert json.loads(req.data) == {"entity_id": "light.salon"}
    assert req.get_header("Authorization") == f"Bearer {ACT_TOKEN}"
    assert env.speech.said[-1][0] == "Hecho, señor."
    assert env.server.bridge_state.proposals == {}
    assert [(e["result"], e["confirmed_by"]) for e in log(env)] == [
        ("proposed", "none"), ("ok", "voice")]


@pytest.mark.asyncio
async def test_a_yes_before_the_question_was_heard_goes_to_the_brain(env):
    await propose(env)                                  # queued, never announced
    assert await say(env, "sí") is False
    assert env.calls == [] and len(homes(env)) == 1


@pytest.mark.asyncio
async def test_a_cancelled_announcement_never_listens_for_a_yes(env):
    await propose(env)
    env.speech.played = False
    await announce(env)
    assert await say(env, "sí") is False and env.calls == []


@pytest.mark.asyncio
async def test_a_yes_meant_for_the_brain_does_not_count(env):
    await propose(env)
    await announce(env)
    env.server.bridge_state.note_brain_turn()           # the brain starts talking
    assert await say(env, "sí") is False
    assert env.calls == []


@pytest.mark.asyncio
async def test_a_yes_after_the_window_does_not_count(env):
    await propose(env)
    await announce(env)
    env.now[0] += bridge.HOME_CONFIRM_WINDOW + 1
    assert await say(env, "sí") is False and env.calls == []
    assert len(homes(env)) == 1                         # still pending, decidable elsewhere


@pytest.mark.asyncio
async def test_the_window_is_shorter_than_a_memorys(env):
    assert bridge.HOME_CONFIRM_WINDOW < bridge.CONFIRM_WINDOW


@pytest.mark.asyncio
async def test_a_no_does_nothing(env):
    await propose(env)
    await announce(env)
    assert await say(env, "no") is True
    assert env.calls == [] and homes(env) == []
    assert env.speech.said[-1][0] == "Entendido, señor. No he hecho nada."
    assert [e["result"] for e in log(env)] == ["proposed", "rejected"]


@pytest.mark.asyncio
@pytest.mark.parametrize("words", [
    "guárdalo", "save it", "keep", "vale pero antes dime la hora",
    "sí y apaga también el garaje", "sí no", "quizá", "qué tiempo hace",
    "sí sí sí sí sí", "claro", "dale",
    # Narrower than the memory flow on purpose (see bridge._YES_ACTION_WORDS):
    # a word that is a perfectly good "yes" to "shall I keep this memory?" is
    # NOT deliberate enough to move something in the real world, and a bare
    # "vale" is the word most easily mistaken for a filler, so it never
    # counts alone — only together with another affirmative.
    # ("confirm" is NOT here: in English it is one of the deliberate words —
    # see test_the_yeses_that_do_count — and in Spanish the word is "confirma".)
    "si", "ok", "okay", "sure", "confirmo", "correcto", "vale"])
async def test_only_a_plain_yes_counts(env, words):
    await propose(env)
    await announce(env)
    assert await say(env, words) is False
    assert env.calls == [] and len(homes(env)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["sí", "Sí.", "adelante", "confirma", "confirmado",
                                   "hazlo", "sí, hazlo", "vale hazlo", "yes",
                                   "confirm", "do it", "go ahead", "sí por favor"])
async def test_the_yeses_that_do_count(env, words):
    await propose(env)
    await announce(env)
    assert await say(env, words) is True
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_a_yes_does_it_once_only(env):
    await propose(env)
    await announce(env)
    assert await say(env, "sí") is True
    assert await say(env, "sí") is False
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_a_memory_proposal_is_not_confused_with_an_action(env):
    """Both kinds use the same pending-and-confirm machinery; a yes decides
    the LAST thing asked about and only that."""
    b = env.server.bridge_state
    mem = b.propose("El puerto es el 8123", "demo")
    await env.server._bridge_announced_when_spoken(mem.id, FakeUtt(), b.brain_epoch)
    env.now[0] += 1
    await propose(env)
    await env.server._bridge_announced_when_spoken(homes(env)[0].id, FakeUtt(),
                                                   b.brain_epoch)
    assert b.awaiting_decision().kind == "home"
    assert await say(env, "sí") is True
    assert len(env.calls) == 1
    assert mem.id in b.proposals                        # the memory is still waiting
    snap = b.snapshot()
    assert [p["id"] for p in snap["proposals"]] == [mem.id] and snap["home"] == []


# --- things that changed after the question -------------------------------------------

@pytest.mark.asyncio
async def test_if_the_list_changed_meanwhile_it_is_not_done(env):
    await propose(env)
    await announce(env)
    env.write_policy({**POLICY, "entities": {"switch.cafetera": "la cafetera"}})
    assert await say(env, "sí") is True
    assert env.calls == []
    assert env.speech.said[-1][0].startswith("Ya no puedo hacerlo, señor.")
    assert log(env)[-1]["result"].startswith("refused")


@pytest.mark.asyncio
async def test_if_home_control_was_switched_off_meanwhile_it_is_not_done(env):
    await propose(env)
    await announce(env)
    home_actions.data_paths.home_actions_path().unlink()
    assert await say(env, "sí") is True
    assert env.calls == []


@pytest.mark.asyncio
async def test_actions_per_minute_are_limited_at_the_moment_of_acting(env):
    outcomes = []
    for _ in range(3):
        await propose(env, {"entity_id": "switch.cafetera", "service": "turn_on"})
        await announce(env)
        await say(env, "sí")
        outcomes.append(env.speech.said[-1][0])
        env.now[0] += 1
    assert outcomes == ["Hecho, señor.", "Hecho, señor.",
                        "Demasiadas acciones en un minuto, señor. Espero."]
    assert len(env.calls) == 2
    env.now[0] += 61
    await propose(env, {"entity_id": "switch.cafetera", "service": "turn_on"})
    await announce(env)
    await say(env, "sí")
    assert len(env.calls) == 3


@pytest.mark.asyncio
async def test_a_dry_run_says_what_it_would_do_and_touches_nothing(env):
    env.monkeypatch.setenv("JARVIS_HOME_DRYRUN", "1")
    await propose(env)
    await announce(env)
    await say(env, "sí")
    assert env.calls == []
    assert env.speech.said[-1][0] == "Simulacro, señor: no he tocado nada."
    last = log(env)[-1]
    assert last["result"] == "dryrun" and last["dryrun"] is True


@pytest.mark.asyncio
async def test_home_assistant_failures_are_said_briefly_and_logged(env):
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(ACT_TOKEN.encode()))

    env.monkeypatch.setattr(home_actions, "_open", boom)
    await propose(env)
    await announce(env)
    await say(env, "sí")
    assert env.speech.said[-1][0] == "Home Assistant lo ha rechazado, señor."
    assert log(env)[-1]["result"] == "http_401"
    assert ACT_TOKEN not in json.dumps(env.speech.said)

    def down(req, timeout):
        raise urllib.error.URLError("down")

    env.monkeypatch.setattr(home_actions, "_open", down)
    env.now[0] += 100
    await propose(env)
    await announce(env)
    await say(env, "sí")
    assert env.speech.said[-1][0] == "No he podido contactar con Home Assistant, señor."


@pytest.mark.asyncio
async def test_a_missing_token_is_said_without_details(env):
    env.monkeypatch.delenv("HA_ACTIONS_TOKEN_FILE")
    await propose(env)
    await announce(env)
    await say(env, "sí")
    assert env.calls == []
    assert env.speech.said[-1][0] == \
        "No he podido hacerlo, señor: el control de la casa no está bien configurado."


@pytest.mark.asyncio
async def test_no_audit_log_at_the_moment_of_acting_means_no_action(env):
    await propose(env)
    await announce(env)
    path = home_actions.data_paths.home_actions_log_path()
    path.unlink()
    path.mkdir()
    await say(env, "sí")
    assert env.calls == []
    assert env.speech.said[-1][0] == "No puedo dejar constancia, señor, así que no lo hago."


@pytest.mark.asyncio
async def test_an_old_request_expires_even_if_it_is_decided_late(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    await propose(env)
    pid = homes(env)[0].id
    env.now[0] += env.server.HOME_PROPOSAL_TTL + 1
    r = env.client.post(f"/api/home/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 200 and "caducado" in r.json()["text"]
    assert env.calls == [] and log(env)[-1]["result"] == "expired"


# --- the panel ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_panel_cannot_confirm_unless_the_users_file_allows_it(env):
    await propose(env)
    pid = homes(env)[0].id
    r = env.client.post(f"/api/home/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 200 and "de viva voz" in r.json()["text"]
    assert env.calls == [] and len(homes(env)) == 1        # not consumed: voice still works
    await announce(env)
    assert await say(env, "sí") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_the_panel_can_always_reject(env):
    await propose(env)
    pid = homes(env)[0].id
    r = env.client.post(f"/api/home/{pid}/reject", headers=ORIGIN)
    assert r.status_code == 200 and homes(env) == [] and env.calls == []
    assert log(env)[-1]["result"] == "rejected" and log(env)[-1]["confirmed_by"] == "panel"


@pytest.mark.asyncio
async def test_with_panel_confirm_on_the_panel_acts_and_is_logged_as_the_panel(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    await propose(env)
    pid = homes(env)[0].id
    r = env.client.post(f"/api/home/{pid}/confirm", headers=ORIGIN)
    assert r.json()["text"] == "Hecho, señor."
    assert len(env.calls) == 1
    assert (log(env)[-1]["result"], log(env)[-1]["confirmed_by"]) == ("ok", "panel")


@pytest.mark.asyncio
async def test_the_panel_routes_need_the_dashboards_origin_and_a_real_decision(env):
    await propose(env)
    pid = homes(env)[0].id
    assert env.client.post(f"/api/home/{pid}/confirm").status_code in (401, 403)
    assert env.client.post(f"/api/home/{pid}/maybe", headers=ORIGIN).status_code == 404
    assert env.client.post("/api/home/zzzzzz/confirm", headers=ORIGIN).status_code == 404
    assert env.calls == []


@pytest.mark.asyncio
async def test_the_two_kinds_have_their_own_panel_routes(env):
    mem = env.server.bridge_state.propose("un hecho", "demo")
    await propose(env)
    pid = homes(env)[0].id
    # a home pid is not a memory...
    r = env.client.post(f"/api/bridge/memory/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 404 and env.calls == [] and len(homes(env)) == 1
    # ...and a memory pid is not an action
    r = env.client.post(f"/api/home/{mem.id}/confirm", headers=ORIGIN)
    assert r.status_code == 404 and mem.id in env.server.bridge_state.proposals


@pytest.mark.asyncio
async def test_the_pending_view_shows_the_sentence_and_nothing_secret(env):
    await propose(env)
    snap = env.client.get("/api/bridge/pending").json()
    assert snap["proposals"] == []
    (item,) = snap["home"]
    assert item["text"] == "¿Apago la luz del salón, señor?" and set(item) == {"id", "text", "age"}
    assert ACT_TOKEN not in json.dumps(snap)


# --- the gate: only the user, only a clean turn -----------------------------------------

def _tool(env, brain, **arguments):
    env.server.brain_instance = brain
    token = env.server.data_paths.ensure_tool_token()
    r = env.client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                        json={"tool": "home_propose", "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_turn_the_user_drove_may_propose(env):
    out = _tool(env, StubBrain("user"), **OFF)
    assert out["ok"] is True and "Pending" in out["text"] and len(homes(env)) == 1
    assert env.calls == []


@pytest.mark.parametrize("origin", ["watcher", "event", "bridge", "", None])
def test_nothing_proposes_off_the_users_own_turn(env, origin):
    out = _tool(env, StubBrain(origin), **OFF)
    assert out["ok"] is False and "not_allowed_from_event" in out["text"]
    assert homes(env) == [] and env.speech.said == [] and env.calls == []


def test_no_brain_no_proposal(env):
    out = _tool(env, None, **OFF)
    assert out["ok"] is False and homes(env) == []


@pytest.mark.parametrize("source", ["a web page", "another session's transcript",
                                    "a file in one of your projects",
                                    "the output of a run", "Home Assistant"])
def test_a_turn_that_read_something_foreign_cannot_propose(env, source):
    brain = StubBrain("user")
    brain.mark_untrusted_content(source)
    out = _tool(env, brain, **OFF)
    assert out["ok"] is False and "untrusted_content_in_this_turn" in out["text"]
    assert homes(env) == [] and env.speech.said == [] and env.calls == []


def test_the_user_asking_again_in_a_clean_turn_works(env):
    brain = StubBrain("user")
    brain.mark_untrusted_content("a web page")
    assert _tool(env, brain, **OFF)["ok"] is False
    assert _tool(env, StubBrain("user"), **OFF)["ok"] is True


def test_the_tool_needs_the_tool_token(env):
    r = env.client.post("/internal/tool", json={"tool": "home_propose", "arguments": OFF})
    assert r.status_code in (401, 403)
    r = env.client.post("/internal/tool", headers={"Authorization": "Bearer nope"},
                        json={"tool": "home_propose", "arguments": OFF})
    assert r.status_code in (401, 403)
    assert homes(env) == []


def test_the_bridge_token_does_not_open_the_home_tool(env):
    token = env.server.data_paths.ensure_bridge_token()
    r = env.client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                        json={"tool": "home_propose", "arguments": OFF})
    assert r.status_code in (401, 403)
    assert homes(env) == []


# --- it is wired the way the rest of the tools are --------------------------------------

def test_the_tool_is_registered_allowed_and_described(env):
    import brain
    import jarvis_mcp
    assert "home_propose" in env.server.TOOL_HANDLERS
    assert "home_propose" in env.server.ACTING_TOOLS
    assert "home_propose" in env.server.TAINT_EXEMPT_TOOLS
    assert "mcp__jarvis__home_propose" in brain.ALLOWED_TOOLS
    spec = next(t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "home_propose")
    assert set(spec["inputSchema"]["properties"]) == {
        "entity_id", "service", "temperature", "brightness_pct", "volume_level"}
    assert spec["inputSchema"]["required"] == ["entity_id", "service"]
    assert "NOTHING happens" in spec["description"]
    assert len(spec["description"]) < 600          # it costs context on every turn


def test_the_tool_is_described_to_the_brain_only_when_home_control_is_configured(env):
    import jarvis_mcp

    def listed():
        reply = jarvis_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return {t["name"] for t in reply["result"]["tools"]}

    assert "home_propose" in listed()                      # env wrote a policy file
    home_actions.data_paths.home_actions_path().unlink()
    names = listed()
    assert "home_propose" not in names
    # google_propose and whatsapp_propose are their own independent gates (no
    # google-actions.json / whatsapp-actions.json in this fixture) and are
    # hidden the same way; see test_google_control.py, test_whatsapp_control.py.
    assert names == ({t["name"] for t in jarvis_mcp.TOOL_SPECS}
                     - {"home_propose", "google_propose", "whatsapp_propose"})
    # the full table is untouched: the registry tests compare against it
    assert "home_propose" in {t["name"] for t in jarvis_mcp.TOOL_SPECS}


def test_the_persona_in_spanish_documents_the_tool():
    text = (REPO / "jarvis_home" / "CLAUDE.es.md").read_text("utf-8")
    assert "home_propose" in text and "señor" in text


def test_only_the_users_decision_can_perform(env):
    """The one place that performs is `_home_decide`, and nothing the brain can
    call reaches it: the tool only proposes."""
    source = (REPO / "server.py").read_text("utf-8")
    assert source.count("home_actions.perform") == 1
    decide = inspect.getsource(env.server._home_decide)
    assert "home_actions.perform" in decide
    for fn in (env.server.tool_home_propose, env.server._bridge_announced_when_spoken):
        text = inspect.getsource(fn)
        assert "home_actions.perform" not in text and "_home_decide(" not in text
    callers = re.findall(r"_home_decide\(", source)
    assert len(callers) == 3                       # its definition, _bridge_decide, the panel route
    for name in ("bridge_speak", "bridge_ask", "bridge_notify", "bridge_remember"):
        assert "home_" not in inspect.getsource(getattr(env.server, name))


def test_the_bridge_cannot_reach_home_actions(env):
    assert "home_" not in (REPO / "jarvis_bridge_mcp.py").read_text("utf-8")
    assert "import home_actions" not in (REPO / "bridge.py").read_text("utf-8")


def test_the_new_proposals_are_memories_by_default():
    b = bridge.Bridge()
    p = b.propose("un hecho", "demo")
    assert p.kind == "memory" and p.payload is None and p.window == bridge.CONFIRM_WINDOW
    assert b.snapshot()["home"] == []


def test_strict_yes_is_narrower_than_a_memorys():
    for words in ("guárdalo", "save", "keep it", "guárdala", "ok", "okay",
                 "sure", "correcto", "si", "vale"):
        assert bridge.classify_decision(words) == "yes"
        assert bridge.classify_decision(words, strict=True) is None
    for words in ("sí", "adelante", "confirma", "confirmado", "hazlo", "yes",
                 "confirm"):
        assert bridge.classify_decision(words, strict=True) == "yes"
    # "vale" is never enough alone, memory flow or not; it counts for an
    # action only paired with another affirmative word.
    assert bridge.classify_decision("vale", strict=True) is None
    assert bridge.classify_decision("vale hazlo", strict=True) == "yes"
    assert bridge.classify_decision("no", strict=True) == "no"
    assert bridge.classify_decision("no, descártalo", strict=True) == "no"


def test_home_confirm_strict_requires_two_words(monkeypatch):
    monkeypatch.setenv("JARVIS_HOME_CONFIRM_STRICT", "1")
    assert home_actions.confirm_strict() is True
    assert bridge.classify_decision("sí", strict=True, require_two=True) is None
    assert bridge.classify_decision("sí, hazlo", strict=True, require_two=True) == "yes"
    assert bridge.classify_decision("yes, do it", strict=True, require_two=True) == "yes"
    # "no" is unaffected: one word is always enough to refuse.
    assert bridge.classify_decision("no", strict=True, require_two=True) == "no"


def test_home_confirm_strict_is_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_HOME_CONFIRM_STRICT", raising=False)
    assert home_actions.confirm_strict() is False


@pytest.mark.asyncio
async def test_home_confirm_strict_env_is_honoured_end_to_end(env, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME_CONFIRM_STRICT", "1")
    await propose(env)
    await announce(env)
    assert await say(env, "sí") is False               # one word is not enough now
    assert env.calls == [] and len(homes(env)) == 1
    assert await say(env, "sí, hazlo") is True
    assert len(env.calls) == 1


def test_note_other_speech_disarms_without_cancelling():
    b = bridge.Bridge()
    p = b.propose("Shall I turn it off, sir?", "demo", kind="home",
                  window=bridge.HOME_CONFIRM_WINDOW)
    b.mark_announced(p.id)
    assert b.awaiting_decision() is not None
    b.note_other_speech()
    assert b.awaiting_decision() is None                # disarmed...
    assert p.id in b.proposals                          # ...but still pending


@pytest.mark.asyncio
async def test_unrelated_bridge_speak_disarms_a_pending_home_proposal(env):
    """Finding #2 of the security audit: a `/internal/bridge/speak` from
    ANOTHER session, spoken between the question and the user's answer, must
    not leave the house proposal armed for a stray "yes" that was meant for
    that other sentence. The proposal itself is not lost (it stays pending,
    decidable on the panel) — it just stops listening for a spoken yes/no."""
    await propose(env)
    await announce(env)
    assert env.server.bridge_state.awaiting_decision() is not None
    token = env.server.data_paths.ensure_bridge_token()
    r = env.client.post("/internal/bridge/speak",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"session": "otra", "text": "Algo totalmente distinto."})
    assert r.status_code == 200
    assert env.server.bridge_state.awaiting_decision() is None
    assert await say(env, "sí") is False
    assert env.calls == [] and len(homes(env)) == 1


@pytest.mark.asyncio
async def test_a_run_failure_announcement_disarms_a_pending_home_proposal(env):
    await propose(env)
    await announce(env)
    assert env.server.bridge_state.awaiting_decision() is not None
    await env.server._announce_run_failure({"status": "failed", "id": "r1"})
    assert env.server.bridge_state.awaiting_decision() is None
    assert len(homes(env)) == 1                         # still pending


@pytest.mark.asyncio
async def test_an_ha_watch_alert_disarms_a_pending_home_proposal(env):
    import ha_watch
    await propose(env)
    await announce(env)
    assert env.server.bridge_state.awaiting_decision() is not None
    await env.server._home_alert_deliver(ha_watch.Alert("r1", "Alguien ha llegado a casa."))
    assert env.server.bridge_state.awaiting_decision() is None
    assert len(homes(env)) == 1


# --- spoken alerts ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_urgent_alert_interrupts_and_a_normal_one_waits_for_a_pause(env):
    import ha_watch
    await env.server._home_alert_deliver(ha_watch.Alert("alarma", ha_watch.S_ALARM, True))
    await env.server._home_alert_deliver(ha_watch.Alert(
        "ana", ha_watch.S_ARRIVE.format(name="Ana"), False))
    (urgent, calm) = env.speech.said
    assert urgent[0] == "Ha saltado la alarma, señor."
    assert urgent[1].name == "URGENT" and urgent[2] is True
    assert calm[0] == "Ana ha llegado a casa, señor."
    assert calm[1].name == "LOW" and calm[2] is None


@pytest.mark.asyncio
async def test_with_nobody_listening_an_alert_becomes_a_desktop_notification(env):
    import ha_watch
    env.server.voice_clients.clear()
    await env.server._home_alert_deliver(ha_watch.Alert("alarma", ha_watch.S_ALARM, True))
    assert env.speech.said == []
    assert env.notified == [("JARVIS", "Ha saltado la alarma, señor.", "JARVIS")]


@pytest.mark.asyncio
async def test_no_alerts_file_means_no_watch(env):
    await env.server.start_home_watch()
    assert env.server._home_watch_task is None


@pytest.mark.asyncio
async def test_an_enabled_file_starts_the_watch_and_stopping_ends_it(env):
    import ha_watch
    path = ha_watch.data_paths.home_alerts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": True, "rules": [
        {"id": "alarma", "type": "alarm_triggered",
         "entity": "alarm_control_panel.casa"}]}))
    started = []

    async def fake_run(watcher, deliver, **kw):
        started.append(watcher)
        await asyncio.Event().wait()

    env.monkeypatch.setattr(ha_watch, "run", fake_run)
    await env.server.start_home_watch()
    await asyncio.sleep(0)
    assert len(started) == 1 and env.server._home_watch_task is not None
    await env.server.stop_home_watch()
    assert env.server._home_watch_task is None


@pytest.mark.asyncio
async def test_a_broken_alerts_file_is_not_fatal(env):
    import ha_watch
    path = ha_watch.data_paths.home_alerts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken")
    await env.server.start_home_watch()
    assert env.server._home_watch_task is None
