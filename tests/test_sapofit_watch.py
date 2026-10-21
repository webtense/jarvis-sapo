"""sapofit_watch: Croac's messages spoken by JARVIS, quiet by construction.

Nothing here reaches SapoFit or a real speaker: the fetch is a fake coroutine,
the clock and the local time are injected, and the server's delivery is
checked with its speech and notification seams replaced.
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import sapofit_readonly_mcp as sr  # noqa: E402
import sapofit_watch as sw  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
NOON = datetime(2026, 10, 21, 12, 0)
NIGHT = datetime(2026, 10, 21, 23, 30)
MORNING = datetime(2026, 10, 22, 8, 30)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Local:
    def __init__(self, when):
        self.when = when

    def __call__(self):
        return self.when


def cfg(**over):
    return sw.parse_config({"enabled": True, **over})


def item(item_id, kind="COACH_NUDGE", text="Buen trabajo hoy."):
    return {"id": item_id, "kind": kind, "text": text, "createdAt": ""}


def make(config=None, state=None, clock=None, local=None, prepare=None):
    clock = clock or Clock()
    local = local or Local(NOON)
    watcher = sw.Watcher(config or cfg(), state or sw.State(cursor="1:a"), clock=clock,
                         local_now=local, prepare=prepare or (lambda text: text))
    return watcher, clock, local


# --- the settings file --------------------------------------------------------

def test_the_defaults_are_the_quiet_ones():
    config = cfg()
    assert config.kinds == sw.DEFAULT_KINDS
    assert config.poll_s == 60 and config.max_per_hour == 6 and config.max_chars == 400
    assert config.quiet == sw.Quiet(23 * 60, 8 * 60)


@pytest.mark.parametrize("data", [{}, {"enabled": "true"}, {"enabled": False}, [], "x"])
def test_it_is_off_unless_switched_on_literally(data):
    with pytest.raises(sw.ConfigError):
        sw.parse_config(data)


@pytest.mark.parametrize("kinds", [[], ["NOT_A_KIND"], ["COACH_NUDGE", 5], "COACH_NUDGE"])
def test_unknown_kinds_are_refused(kinds):
    with pytest.raises(sw.ConfigError):
        cfg(kinds=kinds)


def test_reminder_kinds_are_allowed_and_duplicates_are_dropped():
    config = cfg(kinds=["REMINDER_WATER", "COACH_NUDGE", "COACH_NUDGE"])
    assert config.kinds == ("REMINDER_WATER", "COACH_NUDGE")


@pytest.mark.parametrize("over", [
    {"poll_s": 10}, {"poll_s": True}, {"poll_s": "60"}, {"max_per_hour": 0},
    {"max_per_hour": 99}, {"max_chars_spoken": 5}, {"max_chars_spoken": 5000},
    {"quiet_hours": {"from": "25:00", "to": "08:00"}},
    {"quiet_hours": {"from": "23:00"}}, {"quiet_hours": "night"},
])
def test_out_of_range_settings_are_refused(over):
    with pytest.raises(sw.ConfigError):
        cfg(**over)


def test_no_quiet_hours_means_speak_at_any_hour():
    assert cfg(quiet_hours=None).quiet is None
    assert cfg(quiet_hours={"from": "08:00", "to": "08:00"}).quiet is None


def test_a_missing_or_broken_file_means_off(tmp_path):
    with pytest.raises(sw.ConfigError, match="off"):
        sw.load_config(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(sw.ConfigError):
        sw.load_config(bad)
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"enabled": True, "poll_s": 90}), encoding="utf-8")
    assert sw.load_config(good).poll_s == 90


def test_the_environment_sets_the_poll_but_never_below_thirty(monkeypatch):
    config = cfg(poll_s=120)
    monkeypatch.delenv(sw.ENV_POLL, raising=False)
    assert sw.effective_poll_s(config) == 120
    monkeypatch.setenv(sw.ENV_POLL, "10")
    assert sw.effective_poll_s(config) == 30
    monkeypatch.setenv(sw.ENV_POLL, "300")
    assert sw.effective_poll_s(config) == 300
    monkeypatch.setenv(sw.ENV_POLL, "soon")
    assert sw.effective_poll_s(config) == 120


@pytest.mark.parametrize("when, quiet", [
    (datetime(2026, 1, 1, 23, 0), True), (datetime(2026, 1, 1, 23, 30), True),
    (datetime(2026, 1, 1, 0, 30), True), (datetime(2026, 1, 1, 7, 59), True),
    (datetime(2026, 1, 1, 8, 0), False), (datetime(2026, 1, 1, 12, 0), False),
    (datetime(2026, 1, 1, 22, 59), False),
])
def test_quiet_hours_cross_midnight(when, quiet):
    assert sw.in_quiet(sw.Quiet(23 * 60, 8 * 60), when) is quiet


def test_quiet_hours_inside_one_day_and_none():
    assert sw.in_quiet(sw.Quiet(13 * 60, 14 * 60), datetime(2026, 1, 1, 13, 30))
    assert not sw.in_quiet(sw.Quiet(13 * 60, 14 * 60), datetime(2026, 1, 1, 14, 0))
    assert not sw.in_quiet(None, NIGHT)


# --- cutting long messages ----------------------------------------------------------

def test_a_short_message_is_left_alone():
    assert sw.cut_for_speech("Muy bien,  hoy.", 400) == ("Muy bien, hoy.", False)


def test_a_long_message_keeps_the_sentences_that_fit():
    text = "Primera frase corta. " + "Segunda frase " * 40 + "Última."
    out, cut = sw.cut_for_speech(text, 120)
    assert cut is True
    assert out == "Primera frase corta."
    assert len(out) <= 120


def test_one_endless_sentence_is_cut_hard_with_a_mark():
    out, cut = sw.cut_for_speech("x" * 900, 100)
    assert cut is True and out.endswith("…") and len(out) == 100


# --- the cursor file ----------------------------------------------------------------

def test_the_cursor_round_trips_owner_only_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "sapofit-cursor.json"
    state = sw.State(cursor="1729500000000:abc", seen=["a", "b"], pending=2, last_read_at=1.5)
    assert sw.save_state(state, path) is True
    assert (os.stat(path).st_mode & 0o777) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sapofit-cursor.json"]
    back = sw.load_state(path)
    assert back == state


def test_the_cursor_file_holds_no_message_text(tmp_path):
    path = tmp_path / "c.json"
    sw.save_state(sw.State(cursor="1:a", seen=["a"]), path)
    assert "text" not in json.loads(path.read_text(encoding="utf-8"))


def test_a_broken_cursor_file_is_a_first_run_not_a_crash(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("[1, 2", encoding="utf-8")
    assert sw.load_state(path) == sw.State()
    path.write_text(json.dumps({"cursor": "bad cursor", "seen": [1, "ok"], "pending": -3}),
                    encoding="utf-8")
    state = sw.load_state(path)
    assert state.cursor is None and state.seen == ["ok"] and state.pending == 0


def test_a_failed_save_is_logged_not_raised(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(sw.os, "replace", boom)
    assert sw.save_state(sw.State(cursor="1:a"), tmp_path / "c.json") is False


# --- what is said, and when --------------------------------------------------------

def test_a_message_is_said_in_the_fixed_frame():
    watcher, _, _ = make()
    assert watcher.observe([item("a", text="Buen trabajo hoy.")]) == ["Croac says: Buen trabajo hoy."]


def test_the_text_is_cleaned_before_it_is_said():
    watcher, _, _ = make(prepare=lambda t: t.replace("**", ""))
    assert watcher.observe([item("a", text="**Bien** hecho")]) == ["Croac says: Bien hecho"]


def test_a_long_message_is_cut_and_the_rest_is_said_to_be_in_the_app():
    watcher, _, _ = make(config=cfg(max_chars_spoken=120))
    out = watcher.observe([item("a", text="Frase uno. " * 60)])
    assert len(out) == 1
    assert out[0].endswith("The rest is in the app, sir.")
    assert len(out[0]) < 200


def test_a_kind_that_is_not_asked_for_is_never_said():
    watcher, _, _ = make()
    assert watcher.observe([item("a", kind="GENERIC")]) == []
    assert watcher.observe([item("b", kind="WORKOUT_ANALYSIS")]) == ["Croac says: Buen trabajo hoy."]


def test_the_same_message_is_never_said_twice():
    watcher, clock, _ = make()
    assert len(watcher.observe([item("a")])) == 1
    clock.t += 3600
    assert watcher.observe([item("a")]) == []


def test_quiet_hours_count_the_messages_and_say_one_line_in_the_morning():
    local = Local(NIGHT)
    watcher, clock, local = make(local=local)
    assert watcher.observe([item("a"), item("b")]) == []
    assert watcher.state.pending == 2
    local.when = MORNING
    clock.t += 600
    assert watcher.observe([]) == [sw.S_PENDING_MANY.format(n=2)]
    assert watcher.state.pending == 0
    assert watcher.observe([]) == []


def test_one_message_waiting_in_the_morning_is_said_in_the_singular():
    local = Local(NIGHT)
    watcher, clock, local = make(local=local)
    watcher.observe([item("a")])
    local.when = MORNING
    assert watcher.observe([]) == [sw.S_PENDING_ONE]


def test_no_more_than_max_per_hour_is_said_and_the_rest_waits_for_the_next_hour():
    watcher, clock, _ = make(config=cfg(max_per_hour=2))
    assert len(watcher.observe([item("a")])) == 1
    clock.t += 120
    assert len(watcher.observe([item("b")])) == 1
    clock.t += 120
    assert watcher.observe([item("c")]) == []
    assert watcher.state.pending == 1
    clock.t += 3700
    assert watcher.observe([]) == [sw.S_PENDING_ONE]


def test_two_lines_are_never_closer_than_two_minutes():
    watcher, clock, _ = make()
    assert len(watcher.observe([item("a")])) == 1
    clock.t += 10
    assert watcher.observe([item("b")]) == []
    clock.t += 130
    assert watcher.observe([]) == [sw.S_PENDING_ONE]


def test_a_burst_of_two_is_one_spoken_message_and_one_pending():
    watcher, clock, _ = make()
    assert watcher.observe([item("a"), item("b")]) == ["Croac says: Buen trabajo hoy."]
    assert watcher.state.pending == 1
    clock.t += 130
    assert watcher.observe([]) == [sw.S_PENDING_ONE]


def test_a_pending_batch_and_a_new_message_become_one_summary():
    watcher, clock, _ = make()
    watcher.state.pending = 2
    assert watcher.observe([item("c")]) == [sw.S_PENDING_MANY.format(n=3)]


def test_the_watcher_only_ever_returns_plain_sentences():
    watcher, _, _ = make()
    out = watcher.observe([item("a")])
    assert all(isinstance(s, str) for s in out)


def test_the_watcher_never_reaches_the_brain():
    source = (REPO / "sapofit_watch.py").read_text(encoding="utf-8")
    assert "import brain" not in source and "from brain" not in source
    assert "jarvis_mcp" not in source and "tool_call" not in source


# --- the loop: first run, cursor, errors -------------------------------------------

def fetch_script(*answers):
    """A fetch that answers in order; an exception instance is raised."""
    calls = []
    queue = list(answers)

    async def fetch(since, kinds):
        calls.append((since, kinds))
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer
    fetch.calls = calls
    return fetch


async def _no_deliver(sentence):
    raise AssertionError(f"nothing should be said: {sentence}")


@pytest.mark.asyncio
async def test_the_first_poll_records_the_cursor_and_says_nothing(tmp_path):
    path = tmp_path / "c.json"
    old = [item("old1", text="Mensaje antiguo"), item("old2")]
    fetch = fetch_script((old, "900:old2"), ([item("new")], "901:new"))
    spoken = []

    async def deliver(sentence):
        spoken.append(sentence)

    watcher = sw.Watcher(cfg(), sw.State(), clock=Clock(), local_now=Local(NOON))
    assert await sw.poll_once(watcher, fetch, deliver, state_path=path) is True
    assert spoken == []
    assert watcher.state.cursor == "900:old2"
    assert fetch.calls[0] == (None, sw.DEFAULT_KINDS)
    assert await sw.poll_once(watcher, fetch, deliver, state_path=path) is True
    assert spoken == ["Croac says: Buen trabajo hoy."]
    assert fetch.calls[1] == ("900:old2", sw.DEFAULT_KINDS)
    saved = sw.load_state(path)
    assert saved.cursor == "901:new" and "old1" in saved.seen


@pytest.mark.asyncio
async def test_a_poll_that_fails_raises_for_the_loop_to_back_off(tmp_path):
    fetch = fetch_script(sr.SapoFitError("SapoFit no responde."))
    watcher, _, _ = make()
    with pytest.raises(sr.SapoFitError):
        await sw.poll_once(watcher, fetch, _no_deliver, state_path=tmp_path / "c.json")


@pytest.mark.asyncio
async def test_a_failed_delivery_does_not_stop_the_watch(tmp_path):
    async def broken(sentence):
        raise RuntimeError("speaker gone")

    fetch = fetch_script(([item("a"), item("b")], "2:b"))
    watcher, _, _ = make(config=cfg(max_per_hour=30))
    watcher.state.cursor = "1:x"
    assert await sw.poll_once(watcher, fetch, broken, state_path=tmp_path / "c.json") is True


@pytest.mark.asyncio
async def test_the_loop_backs_off_on_errors_and_resets_on_success(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv(sw.ENV_POLL, raising=False)
    errors = sr.SapoFitError("SapoFit no responde.")
    answers = [errors, errors, errors, ([], "5:z"), ([], "5:z")]
    fetch = fetch_script(*answers)
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 5:
            raise asyncio.CancelledError()

    watcher = sw.Watcher(cfg(poll_s=60), sw.State(cursor="1:a"), clock=Clock(),
                         local_now=Local(NOON))
    with caplog.at_level("INFO", logger="jarvis.sapofit_watch"):
        with pytest.raises(asyncio.CancelledError):
            await sw.run(watcher, _no_deliver, fetch=fetch, sleep=sleep,
                         state_path=tmp_path / "c.json")
    assert waits[:3] == [120, 240, 480]
    assert waits[3] == 60                       # back to the normal poll after success
    assert all(w <= sw.BACKOFF_MAX_S for w in waits)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1                   # one line for the outage, not one per retry


@pytest.mark.asyncio
async def test_the_loop_polls_with_the_configured_kinds_only(tmp_path, monkeypatch):
    monkeypatch.delenv(sw.ENV_POLL, raising=False)
    fetch = fetch_script(([], "2:x"))
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        raise asyncio.CancelledError()

    watcher = sw.Watcher(cfg(kinds=["DAILY_SUMMARY"]), sw.State(cursor="1:a"),
                         clock=Clock(), local_now=Local(NOON))
    with pytest.raises(asyncio.CancelledError):
        await sw.run(watcher, _no_deliver, fetch=fetch, sleep=sleep,
                     state_path=tmp_path / "c.json")
    assert fetch.calls[0] == (
        "1:a", ("DAILY_SUMMARY",))


# --- the server's delivery: the voice when there is one, the desktop when not --------

@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    import server as server_module
    return server_module


class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, priority=None, **_k):
        self.said.append((text, priority))


@pytest.mark.asyncio
async def test_croac_is_said_at_low_priority_when_a_voice_is_connected(server, monkeypatch):
    speech = _Speech()
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "voice_clients", {object()})
    await server._sapofit_deliver("Croac says: Buen trabajo.")
    assert speech.said == [("Croac says: Buen trabajo.", server.Priority.LOW)]


@pytest.mark.asyncio
async def test_croac_without_a_tab_goes_out_as_a_desktop_notification(server, monkeypatch):
    sent = []

    async def fake_notify(label, text):
        sent.append((label, text))
        return True

    monkeypatch.setattr(server, "speech", None)
    monkeypatch.setattr(server, "voice_clients", set())
    monkeypatch.setattr(server, "_bridge_notify_fallback", fake_notify)
    await server._sapofit_deliver("Croac says: Buen trabajo.")
    assert sent == [("Croac", "Croac says: Buen trabajo.")]


@pytest.mark.asyncio
async def test_the_watch_stays_off_without_a_token_or_a_switch(server, monkeypatch):
    monkeypatch.delenv("SAPOFIT_TOKEN_FILE", raising=False)
    await server.start_sapofit_watch()
    assert server._sapofit_watch_task is None
    monkeypatch.setenv("SAPOFIT_TOKEN_FILE", "/nonexistent/sapofit.json")
    await server.start_sapofit_watch()             # no sapofit-alerts.json in the test data dir
    assert server._sapofit_watch_task is None
    await server.stop_sapofit_watch()              # idempotent when nothing runs


def test_the_frames_have_their_spanish_pairs():
    import i18n
    import i18n_es
    pairs = dict(i18n_es.PAIRS)
    assert pairs[sw.S_CROAC] == "Croac dice: {text}"
    assert pairs[sw.S_REST] == "El resto está en la app, señor."
    assert pairs[sw.S_PENDING_ONE].startswith("Croac tiene un mensaje nuevo")
    assert pairs[sw.S_PENDING_MANY].startswith("Croac tiene {n} mensajes")
    assert i18n is not None
