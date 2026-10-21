"""Sending a WhatsApp through the server: propose, ask aloud, act only on the
user's "yes". Same pattern as `test_google_control.py`.

The speech scheduler and Evolution are fakes (the network seam is
`whatsapp_actions._open`); no audio, no network, no real credentials, no real
number. What is pinned here is the part `test_whatsapp_actions.py` cannot
see: that the brain's tool only STAGES a message, that the spoken yes is read
by plain word matching and only after the whole question was heard, that
nothing a model, a transcript, another session or an incoming message says can
send one, and that a turn which read other people's messages needs two words.
"""
import ast
import asyncio
import importlib
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
import whatsapp_actions  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
APIKEY = "-".join(["fake", "write", "apikey", "for", "tests"])  # gitleaks:allow
BASE = "https://evolution.example.test"
ORIGIN = {"Origin": "http://localhost:5173"}
MARIO = "34611111111@s.whatsapp.net"
ANA = "34622222222@s.whatsapp.net"
GROUP = "120363000000000001@g.us"
SECRET_TEXT = "llego en diez minutos"

POLICY = {
    "enabled": True,
    "contacts": {"Mario": MARIO, "Ana": ANA},
    "limits": {"max_per_minute": 2, "max_chars": 200},
}
MSG = {"contact": "Mario", "text": SECRET_TEXT}
READER = "mcp__whatsapp__whatsapp_read_chat"


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
    """Like the real brain for the two things the gate reads: the origin and
    the tools called this turn (a non-JARVIS MCP tool taints the turn, which
    is `brain.untrusted_tool_source`)."""
    ready = False

    def __init__(self, origin="user", tools=()):
        self.current_origin = origin
        self.turn_tool_names = list(tools)
        self.label = None

    @property
    def turn_untrusted_source(self):
        import brain
        if self.label:
            return self.label
        for name in self.turn_tool_names:
            source = brain.untrusted_tool_source(name)
            if source:
                return source
        return None

    @property
    def turn_is_tainted(self):
        return self.turn_untrusted_source is not None

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
    for name in ("EVOLUTION_TOKEN_FILE", "EVOLUTION_WRITE_TOKEN_FILE", "JARVIS_WHATSAPP_DRYRUN",
                 "JARVIS_WHATSAPP_CONFIRM_STRICT", "WHATSAPP_POLICY_FILE"):
        monkeypatch.delenv(name, raising=False)
    import server as server_module
    importlib.reload(server_module)

    now = [1000.0]
    monkeypatch.setattr(server_module, "bridge_state", bridge.Bridge(
        prepare=server_module.strip_markdown_for_tts, clock=lambda: now[0]))
    monkeypatch.setattr(server_module, "whatsapp_limits", whatsapp_actions.Limits(lambda: now[0]))

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

    def fake_open(req, timeout):
        calls.append(req)
        return _Resp(json.dumps({"key": {"id": "3EB0ABCDEF"}}).encode())

    monkeypatch.setattr(whatsapp_actions, "_open", fake_open)

    def write_policy(data=POLICY, mode=0o600):
        path = whatsapp_actions.data_paths.whatsapp_actions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        path.chmod(mode)

    creds = tmp_path / "evolution_personal.json"
    creds.write_text(json.dumps({"url": BASE, "instance": "INST", "apikey": APIKEY}), encoding="utf-8")
    creds.chmod(0o600)
    monkeypatch.setenv("EVOLUTION_TOKEN_FILE", str(creds))

    write_policy()
    server_module.brain_instance = None
    client = TestClient(server_module.app)
    return SimpleNamespace(server=server_module, speech=fake, calls=calls, now=now, client=client,
                           write_policy=write_policy, monkeypatch=monkeypatch, tmp=tmp_path)


def log(env):
    path = whatsapp_actions.data_paths.whatsapp_actions_log_path()
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text("utf-8").splitlines()]


async def propose(env, args=None):
    return await env.server.tool_whatsapp_propose(dict(MSG if args is None else args))


async def announce(env):
    b = env.server.bridge_state
    for pid in list(b.proposals):
        await env.server._bridge_announced_when_spoken(pid, FakeUtt(), b.brain_epoch)


def pending(env):
    return [p for p in env.server.bridge_state.proposals.values() if p.kind == "whatsapp"]


async def say(env, words):
    return await env.server._bridge_take_utterance(words)


def bodies(env):
    return [json.loads(r.data) for r in env.calls]


# --- off by default ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_without_a_file_nothing_is_proposed_said_or_sent(env):
    whatsapp_actions.data_paths.whatsapp_actions_path().unlink()
    text = await propose(env)
    assert "not available" in text and "switched off" in text
    assert env.speech.said == [] and pending(env) == []
    assert env.calls == [] and log(env) == []


@pytest.mark.asyncio
async def test_a_switched_off_file_is_off(env):
    env.write_policy({**POLICY, "enabled": False})
    assert "not available" in await propose(env)
    assert env.calls == [] and env.speech.said == []


@pytest.mark.asyncio
async def test_a_file_anyone_can_write_is_off(env):
    env.write_policy(mode=0o666)
    assert "not available" in await propose(env)
    assert env.calls == []


# --- the brain's tool only stages ------------------------------------------------------

@pytest.mark.asyncio
async def test_proposing_says_who_and_the_whole_message_and_sends_nothing(env):
    text = await propose(env)
    assert "NOTHING has been sent" in text and "end your turn" in text
    assert env.calls == []
    assert [s[0] for s in env.speech.said] == [
        f"¿Envío a Mario este WhatsApp: «{SECRET_TEXT}», señor?"]
    (p,) = pending(env)
    assert p.kind == "whatsapp" and p.payload == {"contact": "Mario", "text": SECRET_TEXT, "tainted": False}
    assert [e["result"] for e in log(env)] == ["proposed"]
    assert log(env)[0]["confirmed_by"] == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"contact": "Pedro", "text": "hola"},                       # not on the list
    {"contact": "Mario", "text": ""},
    {"contact": "Mario", "text": "x" * 201},                    # over the user's limit
    {"contact": "Mario", "text": "hola", "number": "34600000000"},
    {"contact": "Mario", "text": "hola", "jid": GROUP},
    {"contact": "Mario", "text": "hola", "linkPreview": True},
    {"contact": "Mario", "text": "hola", "media": "x.jpg"},
    {"contact": GROUP, "text": "hola"},
    {"contact": "Familia", "text": "hola"},
    {"contact": MARIO, "text": "hola"},                         # a JID is not a name
    {"text": "hola"},
    {"contact": "Mario"},
    {},
])
async def test_a_refused_proposal_stages_nothing_and_is_logged_without_its_text(env, args):
    text = await propose(env, args)
    assert "Refused" in text
    assert env.speech.said == [] and pending(env) == []
    assert env.calls == []
    (entry,) = log(env)
    assert entry["result"].startswith("refused") and entry["confirmed_by"] == "none"
    assert "hola" not in json.dumps(entry)


@pytest.mark.asyncio
async def test_a_group_listed_as_a_contact_is_never_a_destination(env):
    env.write_policy({**POLICY, "contacts": {"Familia": GROUP, "Mario": MARIO}})
    assert "Refused" in await propose(env, {"contact": "Familia", "text": "hola"})
    assert env.speech.said == [] and env.calls == []


@pytest.mark.asyncio
async def test_only_one_message_waits_at_a_time(env):
    await propose(env)
    await propose(env, {"contact": "Ana", "text": "otra cosa"})
    (p,) = pending(env)
    assert p.payload["contact"] == "Ana"


@pytest.mark.asyncio
async def test_proposals_are_rate_limited(env):
    results = [await propose(env) for _ in range(whatsapp_actions.PROPOSALS_PER_5_MIN + 2)]
    assert sum("Too many WhatsApp requests" in r for r in results) == 2
    assert env.calls == []


@pytest.mark.asyncio
async def test_with_nobody_to_hear_it_nothing_waits(env):
    env.server.voice_clients.clear()
    text = await propose(env)
    assert "Nobody can hear" in text and pending(env) == []


@pytest.mark.asyncio
async def test_with_nobody_to_hear_it_the_panel_may_hold_it_if_the_file_says_so(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    env.server.voice_clients.clear()
    text = await propose(env)
    assert "Pending" in text and len(pending(env)) == 1
    assert "asking him aloud" not in text


@pytest.mark.asyncio
async def test_no_audit_log_no_proposal(env):
    path = whatsapp_actions.data_paths.whatsapp_actions_log_path()
    path.mkdir(parents=True, exist_ok=True)
    text = await propose(env)
    assert "audit log" in text and pending(env) == [] and env.speech.said == []


@pytest.mark.asyncio
async def test_a_dry_run_is_announced_to_the_brain(env):
    env.monkeypatch.setenv("JARVIS_WHATSAPP_DRYRUN", "1")
    assert "dry run" in (await propose(env)).lower()


# --- the user's yes -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_spoken_yes_after_hearing_the_whole_question_sends_it(env):
    await propose(env)
    await announce(env)
    assert env.calls == []
    assert await say(env, "sí") is True
    (req,) = env.calls
    assert req.full_url == f"{BASE}/message/sendText/INST"
    assert bodies(env) == [{"number": "34611111111", "text": SECRET_TEXT, "linkPreview": False}]
    assert env.speech.said[-1][0] == "Hecho, señor."
    assert pending(env) == []
    assert [(e["result"], e["confirmed_by"]) for e in log(env)] == [("proposed", "none"), ("ok", "voice")]


@pytest.mark.asyncio
async def test_a_yes_before_the_question_was_heard_goes_to_the_brain(env):
    await propose(env)
    assert await say(env, "sí") is False
    assert env.calls == [] and len(pending(env)) == 1


@pytest.mark.asyncio
async def test_a_yes_meant_for_the_brain_is_not_a_yes_to_a_message(env):
    """The brain asks "¿quieres que lo busque?" and the user says "sí": that
    must not send a WhatsApp that was proposed earlier."""
    await propose(env)
    await announce(env)
    env.server.bridge_state.note_brain_turn()              # the brain started talking again
    assert await say(env, "sí") is False
    assert env.calls == [] and len(pending(env)) == 1


@pytest.mark.asyncio
async def test_anything_else_JARVIS_says_after_the_question_disarms_it(env):
    await propose(env)
    await announce(env)
    env.server.bridge_state.note_other_speech()            # a session, an alert, a run announcement
    assert await say(env, "sí") is False
    assert env.calls == []


@pytest.mark.asyncio
async def test_a_yes_is_not_listened_for_while_the_brain_is_still_talking(env, monkeypatch):
    monkeypatch.setattr(bridge, "SPEAK_WAIT_MAX", 0.2)
    lock = asyncio.Lock()
    await lock.acquire()
    env.server.brain_instance = SimpleNamespace(_turn_lock=lock)
    await propose(env)
    pid = pending(env)[0].id
    await env.server._bridge_announced_when_spoken(pid, FakeUtt(), env.server.bridge_state.brain_epoch)
    assert await say(env, "sí") is False and env.calls == []
    lock.release()
    await env.server._bridge_announced_when_spoken(pid, FakeUtt(), env.server.bridge_state.brain_epoch)
    assert await say(env, "sí") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_a_question_that_was_never_played_arms_nothing(env):
    env.speech.played = False
    await propose(env)
    await announce(env)
    assert await say(env, "sí") is False and env.calls == []


@pytest.mark.asyncio
async def test_only_the_strict_action_words_confirm_a_message(env):
    await propose(env)
    await announce(env)
    assert await say(env, "ok") is False                   # too weak for an action
    assert env.calls == [] and len(pending(env)) == 1
    assert await say(env, "sí") is True
    assert len(env.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["qué hora es", "sí, pero a las nueve", "vale", "ok", "si", "claro",
                                   "yes no", "hazlo no", "envíalo", "mándalo", "sí, díselo a Ana"])
async def test_what_is_not_a_plain_yes_does_not_send(env, words):
    await propose(env)
    await announce(env)
    assert await say(env, words) is False
    assert env.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["sí", "hazlo", "adelante", "confirma", "vale, hazlo", "yes", "do it"])
async def test_the_strict_yes_words_send(env, words):
    await propose(env)
    await announce(env)
    assert await say(env, words) is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_the_opt_in_strict_mode_asks_for_two_words(env, monkeypatch):
    monkeypatch.setenv("JARVIS_WHATSAPP_CONFIRM_STRICT", "1")
    await propose(env)
    await announce(env)
    assert await say(env, "sí") is False and env.calls == []
    assert await say(env, "sí, hazlo") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_a_plain_no_rejects_and_sends_nothing(env):
    await propose(env)
    await announce(env)
    assert await say(env, "no") is True
    assert env.calls == [] and pending(env) == []
    assert log(env)[-1]["result"] == "rejected"


@pytest.mark.asyncio
async def test_a_window_that_has_passed_does_not_hear_the_yes(env):
    await propose(env)
    await announce(env)
    env.now[0] += bridge.HOME_CONFIRM_WINDOW + 1
    assert await say(env, "sí") is False and env.calls == []


@pytest.mark.asyncio
async def test_an_old_request_expires_even_if_it_is_decided_late(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    await propose(env)
    pid = pending(env)[0].id
    env.now[0] += env.server.WHATSAPP_PROPOSAL_TTL + 1
    r = env.client.post(f"/api/whatsapp/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 200 and "caducado" in r.json()["text"]
    assert env.calls == [] and log(env)[-1]["result"] == "expired"


@pytest.mark.asyncio
async def test_no_audit_log_at_the_moment_of_sending_means_no_message(env):
    await propose(env)
    await announce(env)
    path = whatsapp_actions.data_paths.whatsapp_actions_log_path()
    path.unlink()
    path.mkdir()
    await say(env, "sí")
    assert env.calls == []


@pytest.mark.asyncio
async def test_a_policy_that_changed_since_the_question_refuses_the_send(env):
    await propose(env)
    await announce(env)
    env.write_policy({**POLICY, "contacts": {"Ana": ANA}})            # Mario was taken off the list
    assert await say(env, "sí") is True
    assert env.calls == []
    assert "ha cambiado" in env.speech.said[-1][0]
    assert log(env)[-1]["result"] == "refused: the policy changed"


@pytest.mark.asyncio
async def test_a_policy_switched_off_since_the_question_sends_nothing(env):
    await propose(env)
    await announce(env)
    env.write_policy({**POLICY, "enabled": False})
    await say(env, "sí")
    assert env.calls == []


@pytest.mark.asyncio
async def test_the_per_minute_limit_stops_a_third_message(env):
    said = []
    for _ in range(3):
        await propose(env)
        await announce(env)
        await say(env, "sí")
        said.append(env.speech.said[-1][0])
    assert len(env.calls) == 2
    assert said[:2] == ["Hecho, señor."] * 2 and "minuto" in said[2]
    assert log(env)[-1]["result"] == "refused: too many per minute"


@pytest.mark.asyncio
async def test_a_dry_run_sends_nothing_and_says_so(env):
    env.monkeypatch.setenv("JARVIS_WHATSAPP_DRYRUN", "1")
    await propose(env)
    await announce(env)
    await say(env, "sí")
    assert env.calls == []
    assert "Simulacro" in env.speech.said[-1][0]
    assert log(env)[-1]["dryrun"] is True


@pytest.mark.asyncio
async def test_the_outcome_sentences(env, monkeypatch):
    for code, text in (("http_403", "WhatsApp lo ha rechazado, señor."),
                       ("redirect", "WhatsApp lo ha rechazado, señor."),
                       ("unreachable", "No he podido contactar con WhatsApp, señor."),
                       ("credentials", "no está bien configurada"),
                       ("not_permitted", "no está bien configurada")):
        monkeypatch.setattr(whatsapp_actions, "perform",
                            lambda a, code=code: whatsapp_actions.Outcome(False, code))
        await propose(env)
        await announce(env)
        pid = pending(env)[0].id
        said = await env.server._whatsapp_decide(pid, True, via="voice")
        assert text in said, (code, said)
        env.now[0] += 120
        env.server.whatsapp_limits = whatsapp_actions.Limits(lambda: env.now[0])


# --- after reading other people's messages ----------------------------------------------------

@pytest.mark.parametrize("names,expected", [
    ([READER], True),
    (["mcp__whatsapp__whatsapp_search"], True),
    (["mcp__whatsapp__whatsapp_list_chats"], True),
    (["mcp__mi_whatsapp__whatsapp_read_chat"], True),
    (["mcp__jarvis__recall", READER], True),
    (["mcp__jarvis__recall"], False),
    (["mcp__google__google_gmail_read"], False),
    (["mcp__whatsapp__whatsapp_propose"], False),
    (["mcp__jarvis__whatsapp_read_chat_x"], False),
    (["WebFetch"], False),
    ([], False),
])
def test_the_turn_knows_whether_a_whatsapp_reader_was_called(env, names, expected):
    env.server.brain_instance = StubBrain("user", names)
    assert env.server._turn_read_whatsapp() is expected


def test_a_stand_in_brain_without_the_tool_list_reads_as_nothing_read(env):
    env.server.brain_instance = SimpleNamespace(current_origin="user")
    assert env.server._turn_read_whatsapp() is False
    env.server.brain_instance = None
    assert env.server._turn_read_whatsapp() is False


@pytest.mark.asyncio
async def test_after_reading_messages_the_proposal_is_marked_and_says_so(env):
    env.server.brain_instance = StubBrain("user", [READER])
    await propose(env)
    (p,) = pending(env)
    assert p.payload["tainted"] is True
    said = env.speech.said[-1][0]
    assert said.startswith(f"¿Envío a Mario este WhatsApp: «{SECRET_TEXT}», señor?")
    assert "Acabo de leer mensajes de otras personas" in said and "sí, hazlo" in said
    assert log(env)[0]["after_reading"] is True


@pytest.mark.asyncio
async def test_after_reading_messages_one_word_is_not_enough_even_without_the_strict_option(env):
    env.server.brain_instance = StubBrain("user", [READER])
    await propose(env)
    await announce(env)
    for one_word in ("sí", "hazlo", "adelante", "confirma", "yes", "vale", "ok"):
        assert await say(env, one_word) is False, one_word
    assert env.calls == [] and len(pending(env)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["sí, hazlo", "sí hazlo", "yes, do it", "vale, hazlo", "hazlo, adelante"])
async def test_after_reading_messages_two_deliberate_words_send(env, words):
    env.server.brain_instance = StubBrain("user", [READER])
    await propose(env)
    await announce(env)
    assert await say(env, words) is True and len(env.calls) == 1
    assert (log(env)[-1]["result"], log(env)[-1]["after_reading"]) == ("ok", True)


@pytest.mark.asyncio
async def test_after_reading_messages_a_no_still_rejects_and_foreign_speech_disarms(env):
    env.server.brain_instance = StubBrain("user", [READER])
    await propose(env)
    await announce(env)
    assert await say(env, "no") is True and env.calls == []
    await propose(env)
    await announce(env)
    env.server.bridge_state.note_brain_turn()
    assert await say(env, "sí, hazlo") is False and env.calls == []


@pytest.mark.asyncio
async def test_a_turn_that_did_not_read_messages_keeps_the_single_word(env):
    env.server.brain_instance = StubBrain("user", ["mcp__jarvis__recall"])
    await propose(env)
    await announce(env)
    assert await say(env, "sí") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_a_message_proposed_after_reading_cannot_be_confirmed_from_the_panel(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    env.server.brain_instance = StubBrain("user", [READER])
    await propose(env)
    pid = pending(env)[0].id
    r = env.client.post(f"/api/whatsapp/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 200 and "de viva voz" in r.json()["text"]
    assert env.calls == [] and len(pending(env)) == 1


@pytest.mark.asyncio
async def test_with_nobody_to_hear_it_a_message_after_reading_is_not_held_for_the_panel(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    env.server.brain_instance = StubBrain("user", [READER])
    env.server.voice_clients.clear()
    text = await propose(env)
    assert "Nobody can hear" in text and pending(env) == []


# --- the panel -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_panel_cannot_confirm_unless_the_users_file_allows_it(env):
    await propose(env)
    pid = pending(env)[0].id
    r = env.client.post(f"/api/whatsapp/{pid}/confirm", headers=ORIGIN)
    assert r.status_code == 200 and "de viva voz" in r.json()["text"]
    assert env.calls == [] and len(pending(env)) == 1
    await announce(env)
    assert await say(env, "sí") is True and len(env.calls) == 1


@pytest.mark.asyncio
async def test_the_panel_can_always_reject(env):
    await propose(env)
    pid = pending(env)[0].id
    r = env.client.post(f"/api/whatsapp/{pid}/reject", headers=ORIGIN)
    assert r.status_code == 200 and pending(env) == [] and env.calls == []
    assert (log(env)[-1]["result"], log(env)[-1]["confirmed_by"]) == ("rejected", "panel")


@pytest.mark.asyncio
async def test_with_panel_confirm_on_the_panel_sends_and_is_logged_as_the_panel(env):
    env.write_policy({**POLICY, "panel_confirm": True})
    await propose(env)
    pid = pending(env)[0].id
    r = env.client.post(f"/api/whatsapp/{pid}/confirm", headers=ORIGIN)
    assert r.json()["text"] == "Hecho, señor."
    assert len(env.calls) == 1
    assert (log(env)[-1]["result"], log(env)[-1]["confirmed_by"]) == ("ok", "panel")


@pytest.mark.asyncio
async def test_the_panel_routes_need_the_dashboards_origin_and_a_real_decision(env):
    await propose(env)
    pid = pending(env)[0].id
    assert env.client.post(f"/api/whatsapp/{pid}/confirm").status_code in (401, 403)
    assert env.client.post(f"/api/whatsapp/{pid}/maybe", headers=ORIGIN).status_code == 404
    assert env.client.post("/api/whatsapp/zzzzzz/confirm", headers=ORIGIN).status_code == 404
    assert env.calls == []


@pytest.mark.asyncio
async def test_a_google_or_home_pid_is_not_a_whatsapp_message_and_vice_versa(env):
    env.server.bridge_state.propose("¿Enciendo la luz?", "home", kind="home", payload={})
    env.server.bridge_state.propose("¿Creo una tarea?", "google", kind="google", payload={})
    await propose(env)
    (wapp,) = pending(env)
    others = [p for p in env.server.bridge_state.proposals.values() if p.kind != "whatsapp"]
    for other in others:
        assert env.client.post(f"/api/whatsapp/{other.id}/confirm", headers=ORIGIN).status_code == 404
    assert env.client.post(f"/api/google/{wapp.id}/confirm", headers=ORIGIN).status_code == 404
    assert env.client.post(f"/api/home/{wapp.id}/confirm", headers=ORIGIN).status_code == 404
    assert env.calls == []


@pytest.mark.asyncio
async def test_a_message_is_not_a_memory_the_dashboard_could_save(env):
    await propose(env)
    snap = env.client.get("/api/bridge/pending").json()
    assert snap["proposals"] == []
    (item,) = snap["whatsapp"]
    assert item["text"] == f"¿Envío a Mario este WhatsApp: «{SECRET_TEXT}», señor?"
    assert set(item) == {"id", "text", "age"}
    assert APIKEY not in json.dumps(snap) and "34611111111" not in json.dumps(snap)
    pid = pending(env)[0].id
    assert env.client.post(f"/api/bridge/memory/{pid}/confirm", headers=ORIGIN).status_code == 404


# --- the audit never holds the text or a key -----------------------------------------------------

@pytest.mark.asyncio
async def test_a_whole_exchange_leaves_no_text_and_no_key_in_the_log(env):
    await propose(env, {"contact": "Ana", "text": "el código es 987654 ¡no lo digas!"})
    await announce(env)
    await say(env, "sí")
    await propose(env, {"contact": "Pedro", "text": "contraseña hunter2"})
    raw = whatsapp_actions.data_paths.whatsapp_actions_log_path().read_text("utf-8")
    for forbidden in ("987654", "código", "hunter2", "contraseña", APIKEY, "34622222222", BASE):
        assert forbidden not in raw
    entries = [json.loads(x) for x in raw.splitlines()]
    assert [e["result"].split(":")[0] for e in entries] == ["proposed", "ok", "refused"]
    assert entries[0]["to"] == "Ana" and entries[0]["jid"] == "346…22" and entries[0]["chars"] == 33


# --- the gate: only the user, only a clean turn -----------------------------------------------------

def _tool(env, brain, **arguments):
    env.server.brain_instance = brain
    token = env.server.data_paths.ensure_tool_token()
    r = env.client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                        json={"tool": "whatsapp_propose", "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_turn_the_user_drove_may_propose(env):
    out = _tool(env, StubBrain("user"), **MSG)
    assert out["ok"] is True and "Pending" in out["text"] and len(pending(env)) == 1
    assert env.calls == []


@pytest.mark.parametrize("origin", ["watcher", "event", "bridge", "", None])
def test_nothing_proposes_off_the_users_own_turn(env, origin):
    out = _tool(env, StubBrain(origin), **MSG)
    assert out["ok"] is False and "not_allowed_from_event" in out["text"]
    assert pending(env) == [] and env.speech.said == [] and env.calls == []


@pytest.mark.parametrize("source", ["a web page", "another session's transcript",
                                    "a file in one of your projects", "the output of a run"])
def test_a_turn_that_read_something_foreign_cannot_propose(env, source):
    brain = StubBrain("user")
    brain.mark_untrusted_content(source)
    out = _tool(env, brain, **MSG)
    assert out["ok"] is False and "untrusted_content_in_this_turn" in out["text"]
    assert pending(env) == [] and env.speech.said == [] and env.calls == []


@pytest.mark.parametrize("reader", ["whatsapp_read_chat", "whatsapp_search", "whatsapp_list_chats"])
def test_a_turn_in_which_a_message_was_read_is_refused_by_the_general_gate_first(env, reader):
    """An incoming message can say anything; reading it taints the turn like
    reading a web page, so the tool refuses before the proposal even exists."""
    out = _tool(env, StubBrain("user", [f"mcp__whatsapp__{reader}"]), **MSG)
    assert out["ok"] is False and "untrusted_content_in_this_turn" in out["text"]
    assert pending(env) == [] and env.speech.said == [] and env.calls == []


def test_the_tool_needs_the_tool_token(env):
    r = env.client.post("/internal/tool", json={"tool": "whatsapp_propose", "arguments": MSG})
    assert r.status_code in (401, 403)
    assert pending(env) == []


def test_the_bridge_token_does_not_open_the_whatsapp_tool(env):
    token = env.server.data_paths.ensure_bridge_token()
    r = env.client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                        json={"tool": "whatsapp_propose", "arguments": MSG})
    assert r.status_code in (401, 403)
    assert pending(env) == []


# --- structure: nothing but the user's yes reaches the send ---------------------------------------------

def _functions(tree):
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_only_call_that_sends_is_inside_the_decision_function():
    tree = ast.parse((REPO / "server.py").read_text("utf-8"))
    funcs = _functions(tree)
    users = [name for name, fn in funcs.items()
             if any(isinstance(n, ast.Attribute) and n.attr == "perform"
                    and isinstance(n.value, ast.Name) and n.value.id == "whatsapp_actions"
                    for n in ast.walk(fn))]
    assert users == ["_whatsapp_decide"]
    proposer = funcs["tool_whatsapp_propose"]
    names = {n.attr for n in ast.walk(proposer) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(proposer) if isinstance(n, ast.Name)}
    assert "perform" not in names and "_whatsapp_decide" not in names and "_request" not in names


def test_the_reader_cannot_send_and_the_sender_cannot_read():
    reader = (REPO / "whatsapp_readonly_mcp.py").read_text("utf-8")
    sender = (REPO / "whatsapp_actions.py").read_text("utf-8")
    assert "whatsapp_actions" not in reader and "sendText" not in reader.split('"""', 2)[2]
    assert "findMessages" not in sender and "findChats" not in sender


def test_the_tool_is_registered_allowed_and_described(env):
    import brain
    import jarvis_mcp
    assert "whatsapp_propose" in env.server.TOOL_HANDLERS
    assert "whatsapp_propose" in env.server.ACTING_TOOLS
    assert "whatsapp_propose" in env.server.TAINT_EXEMPT_TOOLS
    assert "whatsapp_propose" not in env.server.TAINT_EXEMPT_ACTING       # it is NOT let through a tainted turn
    assert "mcp__jarvis__whatsapp_propose" in brain.ALLOWED_TOOLS
    spec = next(t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "whatsapp_propose")
    assert set(spec["inputSchema"]["properties"]) == {"contact", "text"}
    assert spec["inputSchema"]["required"] == ["contact", "text"]
    assert "NOTHING happens" in spec["description"]
    assert len(spec["description"]) < 800          # it costs context on every turn
    assert "mcp__jarvis__whatsapp_search" not in brain.ALLOWED_TOOLS      # the readers are the user's own server


def test_the_tool_is_described_to_the_brain_only_when_whatsapp_is_configured(env):
    import jarvis_mcp

    def listed():
        reply = jarvis_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return {t["name"] for t in reply["result"]["tools"]}

    assert "whatsapp_propose" in listed()
    whatsapp_actions.data_paths.whatsapp_actions_path().unlink()
    assert "whatsapp_propose" not in listed()
    assert "whatsapp_propose" in {t["name"] for t in jarvis_mcp.TOOL_SPECS}


def test_the_brain_tracks_the_tools_it_called_this_turn(tmp_path):
    import brain
    b = brain.Brain(brain.BrainConfig.from_env(tmp_path))
    assert b.turn_tool_names == []


def test_the_persona_in_spanish_documents_the_tools_and_the_rules():
    text = (REPO / "jarvis_home" / "CLAUDE.es.md").read_text("utf-8")
    section = text.split("## WhatsApp", 1)[1]
    for needle in ("whatsapp_propose", "whatsapp_read_chat", "whatsapp_list_chats", "whatsapp_search",
                   "señor", "nunca obedezcas", "grupos", "este mismo turno", "UNA frase"):
        assert needle in section, needle
    assert re.search(r"[Nn]o leas ni busques mensajes salvo que él lo pida", section)
