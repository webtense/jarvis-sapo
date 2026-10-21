"""Spanish for the server's fixed phrases (i18n.t) and the guarantees around it.

Three things are load-bearing:

* With `JARVIS_LANGUAGE=en` nothing changes, not by a byte. `t()` hands back
  the very object it was given.
* With `es` a sentence the catalogue knows is translated in one pass, and one
  it does not know comes back untouched. It never raises.
* A sentence that is added without a Spanish pair fails HERE, in CI, instead of
  being heard in English by the one person who set the language to Spanish.
"""
import ast
import importlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import i18n
import i18n_es

REPO = Path(__file__).parent.parent
_PLACEHOLDER = re.compile(r"\{(\w+)(?::(?:t|opt|req))?\}")


@pytest.fixture
def es(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")


def _sample(template: str) -> str:
    """A concrete sentence for a template: each marker filled with a plain,
    distinct word (a `:t` / `:req` marker needs something translatable, so the
    ones that must translate get a phrase the catalogue knows)."""
    known = {"approval": "It's not approved yet", "head": "Two of three tasks done",
             "clause": "Two of three tasks done", "rest": "Nothing is running.",
             "said": "Return", "wrap": "<wrap>"}

    def fill(m):
        return known.get(m.group(1), "x" + m.group(1))
    return _PLACEHOLDER.sub(fill, template)


# --- the language switch ------------------------------------------------------

def test_english_is_the_identity_for_every_catalogue_sentence(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    for english, _spanish in i18n_es.PAIRS:
        sentence = _sample(english)
        assert i18n.t(sentence) is sentence


def test_an_unset_language_is_english_too(monkeypatch):
    monkeypatch.delenv("JARVIS_LANGUAGE", raising=False)
    assert i18n.t("Cancelled, sir.") == "Cancelled, sir."


def test_what_is_not_text_passes_through(es):
    assert i18n.t(None) is None
    assert i18n.t(42) == 42
    assert i18n.t("") == ""


# --- translating -------------------------------------------------------------

def test_an_exact_phrase_is_translated(es):
    assert i18n.t("Cancelled, sir.") == "Cancelado, señor."
    assert i18n.t("Terminal is open, sir.") == "La terminal está abierta, señor."


def test_a_template_keeps_its_dynamic_parts(es):
    assert (i18n.t("Opened index.html from chitauri, sir.")
            == "He abierto index.html de chitauri, señor.")
    assert (i18n.t("Created hammer in your Projects folder, sir. A fresh git "
                   "repository with a README, ready to start work in.")
            == "He creado hammer en su carpeta Projects, señor. Un repositorio "
               "git nuevo con un README, listo para trabajar.")
    assert (i18n.t("Pulled that up in Firefox, sir.")
            == "Lo he abierto en Firefox, señor.")


def test_a_sentence_of_ours_nested_in_another_is_translated_too(es):
    # The age arrives already Spanish: the helper that builds it (`_say_age`)
    # translates it, and the frame around it copies it.
    assert (i18n.t("It finished before I could stop it, sir. The work in "
                   "chitauri finished hace unos 5 minutos, sir, and it worked.")
            == "Terminó antes de que pudiera detenerlo, señor. El trabajo en "
               "chitauri terminó hace unos 5 minutos, señor, y funcionó.")
    assert i18n.t("your browser") == "su navegador"
    assert (i18n.t("Pulled that up in your browser, sir.")
            == "Lo he abierto en su navegador, señor.")


def test_a_progress_clause_is_translated_inside_its_frame(es):
    # The numbers arrive as Spanish words (`_say_number`), the frame in English.
    english = "Dos of nueve tasks done — it's on the memory tools now, sir."
    assert i18n.t(english) == "Dos de nueve tareas hechas; ahora está con the memory tools, señor."
    # The task name is the plan file's own words: copied, never translated.
    assert i18n.t("Cuatro of nueve tasks done in hammer, sir, though nothing of "
                  "mine is running it.").startswith("Cuatro de nueve tareas hechas en hammer")


def test_an_unknown_phrase_is_left_alone(es):
    for text in ("Something nobody wrote a pair for.",
                 "Foo bar, sir.",                      # the catch-all frame must not half-translate it
                 "Several unrelated words, sir — and more."):
        assert i18n.t(text) == text


def test_a_sentence_is_never_translated_twice(es):
    for english, spanish in i18n_es.PAIRS:
        sentence = _sample(english)
        once = i18n.t(sentence)
        assert i18n.t(once) == once, english
        assert i18n.t(_sample(spanish)) == _sample(spanish), spanish


def test_untrusted_text_around_our_sentence_is_copied_not_translated(es):
    hostile = "Cancelled, sir."
    out = i18n.t(f"Telling hammer: {hostile}")
    assert out == "Le digo a hammer: Cancelled, sir."
    block = '<session-output name="run">Cancelled, sir.\nGood morning, sir.</session-output>'
    out = i18n.t(f"Dos going in chitauri, sir.\n{block}")
    assert out.startswith("Dos en marcha en chitauri, señor.\n")
    assert out.endswith(block)                       # the block is byte for byte what it was


def test_a_translation_failure_returns_the_original(es, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("catalogue exploded")
    monkeypatch.setattr(i18n, "_translate", boom)
    assert i18n.t("Cancelled, sir.") == "Cancelled, sir."


# --- the catalogue itself -----------------------------------------------------

def test_every_pair_uses_the_same_markers_on_both_sides():
    for english, spanish in i18n_es.PAIRS:
        assert (sorted(m.group(1) for m in _PLACEHOLDER.finditer(english))
                == sorted(m.group(1) for m in _PLACEHOLDER.finditer(spanish))), english


def test_no_english_template_is_listed_twice():
    keys = [english for english, _ in i18n_es.PAIRS]
    assert len(keys) == len(set(keys))


def test_the_spanish_side_never_says_sir_and_always_says_senor():
    for english, spanish in i18n_es.PAIRS:
        assert not re.search(r"\bsir\b", spanish), spanish
        if ", sir" in english:
            assert "señor" in spanish, spanish


def test_every_template_compiles():
    for english, _spanish in i18n_es.PAIRS:
        i18n._compile_template(english)


# --- the completeness test ----------------------------------------------------

# Modules that never speak: tests, the frontend and the persona templates.
_SKIP_DIRS = {"tests", "frontend", "node_modules", ".venv", "venv", "data", "docs"}

# A literal with ", sir" that is deliberately NOT in the catalogue, with the
# reason. Keep this short: the point of the test is that it stays empty.
EXEMPT: set[str] = set()


def _normalise(text: str) -> str:
    return _PLACEHOLDER.sub("{}", text)


def _catalogue_shapes() -> list[str]:
    return [_normalise(english) for english, _ in i18n_es.PAIRS]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _literals_with_sir():
    """(file, line, shape) for every string literal with ", sir" in the
    server's own modules. An f-string is reduced to its shape, `{}` standing
    for each spliced value; docstrings are documentation, not output."""
    for path in sorted(REPO.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if id(node) in skip:
                continue
            if isinstance(node, ast.JoinedStr):
                shape = "".join(v.value if isinstance(v, ast.Constant) else "{}"
                                for v in node.values)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                shape = node.value
            else:
                continue
            shape = _normalise(shape)
            if ", sir" in shape:
                yield path.name, node.lineno, shape


def test_every_sentence_with_sir_has_a_spanish_pair():
    shapes = _catalogue_shapes()
    missing = []
    for filename, line, shape in _literals_with_sir():
        if shape in EXEMPT:
            continue
        # Whole sentence, or a piece of one that the code glues together
        # (" ran into a problem, sir." after an f-string value).
        if any(shape == s or shape in s for s in shapes):
            continue
        missing.append(f"{filename}:{line}: {shape!r}")
    assert not missing, (
        "These sentences say \"sir\" but have no pair in i18n_es.PAIRS "
        "(add one, or justify an exemption):\n  " + "\n  ".join(missing))


def test_the_completeness_check_would_catch_a_new_sentence(tmp_path):
    """The guard above is only worth having if it can fail."""
    shapes = _catalogue_shapes()
    invented = "I have invented a brand new sentence, sir."
    assert not any(invented == s or invented in s for s in shapes)


def test_the_exemption_list_stays_short():
    assert len(EXEMPT) <= 3


# --- the call sites ---------------------------------------------------------

@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import server as server_module
    importlib.reload(server_module)
    return server_module


class _FakeSpeech:
    def __init__(self):
        self.calls = []

    async def say(self, text, priority=None, **k):
        self.calls.append(text)


@pytest.mark.asyncio
async def test_a_run_failure_is_announced_in_spanish(wired, monkeypatch, es):
    speech = _FakeSpeech()
    monkeypatch.setattr(wired, "speech", speech)
    await wired._announce_run_failure(
        {"project_name": "chitauri", "status": wired.run_store.RunStatus.FAILED})
    assert speech.calls == ["El trabajo en chitauri ha fallado, señor."]


@pytest.mark.asyncio
async def test_the_same_announcement_is_english_by_default(wired, monkeypatch):
    speech = _FakeSpeech()
    monkeypatch.setattr(wired, "speech", speech)
    await wired._announce_run_failure(
        {"project_name": "chitauri", "status": wired.run_store.RunStatus.FAILED})
    assert speech.calls == ["The work in chitauri failed, sir."]


@pytest.mark.asyncio
async def test_a_needs_you_line_is_spanish_for_the_voice_and_the_notification(
        wired, monkeypatch, es):
    speech = _FakeSpeech()
    monkeypatch.setattr(wired, "speech", speech)
    sent = []

    async def notify(name, line):
        sent.append(line)

    monkeypatch.setattr(wired, "_notify_needs_you", notify)
    await wired._announce_needs_you({
        "kind": "needs_you", "at": 1.0,
        "session": {"session_id": "s", "voice_name": "chitauri", "project": "chitauri",
                    "state": "needs_you", "needs": "permission prompt",
                    "needs_a_human_hand": True, "steerable": True}})
    expected = ("chitauri está esperando un aviso de permiso, señor; "
                "ese necesita su propia pulsación.")
    assert speech.calls == [expected]
    assert sent == [expected]


def test_the_helpers_that_build_fragments_speak_spanish(wired, es):
    assert wired._say_number(3) == "tres"
    assert wired._list_join(["a", "b", "c"]) == "a, b y c"
    assert wired._join_natural(["Iván", "Isabel"]) == "Iván e Isabel"
    assert wired._say_age(10) == "ahora mismo"
    assert wired._say_age(300) == "hace unos 5 minutos"
    assert wired._say_age(200000) == "hace 2 días"
    assert wired._cap_listing(["a", "b", "c", "d", "e"]) == "a, b y c, y dos más"
    assert wired._phrase_needs("permission prompt") == "esperando un aviso de permiso"


def test_the_helpers_are_unchanged_in_english(wired):
    assert wired._say_number(3) == "three"
    assert wired._list_join(["a", "b", "c"]) == "a, b and c"
    assert wired._say_age(300) == "about 5 minutes ago"
    assert wired._cap_listing(["a", "b", "c", "d", "e"]) == "a, b and c, and two others"
    assert wired._phrase_needs("permission prompt") == "waiting on a permission prompt"


def test_a_reset_time_is_said_on_a_24_hour_clock(es):
    from datetime import datetime
    when = datetime(2026, 10, 21, 10, 30)
    assert i18n.reset_clock(when, 0) == "las 10:30"
    assert i18n.reset_clock(datetime(2026, 10, 21, 1, 0), 0) == "la 1"
    assert i18n.reset_clock(when, 1) == "mañana a las 10:30"
    assert i18n.reset_clock(when, 3) == "el miércoles a las 10:30"
    assert i18n.reset_clock(when, 10) == "el miércoles 21 de octubre a las 10:30"


def test_the_tool_funnel_translates_a_result_and_caps_it(wired, es):
    reply = wired._tool_reply(True, "Cancelled, sir.")
    assert reply == {"ok": True, "text": "Cancelado, señor."}
    # Whatever it did to the text, the brain's context budget still holds.
    long = wired._tool_reply(True, "x" * 5000)
    assert len(long["text"]) <= wired.TOOL_RESULT_CAP


def test_the_dispatcher_translates_what_a_tool_returns(monkeypatch, tmp_path, es):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import server as server_module
    importlib.reload(server_module)
    monkeypatch.setitem(server_module.TOOL_HANDLERS, "list_sessions",
                        lambda args: "Which project, sir?")
    with TestClient(server_module.app,
                    headers={"Origin": "http://localhost:5173"}) as c:
        token = server_module.data_paths.ensure_tool_token()
        r = c.post("/internal/tool", json={"tool": "list_sessions", "arguments": {}},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.json() == {"ok": True, "text": "¿Qué proyecto, señor?"}


def test_the_preflight_summary_is_spoken_in_spanish(es):
    import preflight
    checks = [preflight.Check(name="fish_api_key", status=preflight.STATUS_FAIL,
                              message="FISH_API_KEY is not set.", remedy="")]
    assert (preflight.spoken_summary(checks)
            == "Hay una cosa que requiere atención, señor: no tengo clave de Fish Audio.")
    checks.append(preflight.Check(name="linux_tools", status=preflight.STATUS_FAIL,
                                  message="missing", remedy=""))
    assert preflight.spoken_summary(checks).startswith(
        "Hay dos cosas que requieren atención, señor: no tengo clave de Fish Audio y ")


def test_the_preflight_summary_is_unchanged_in_english():
    import preflight
    checks = [preflight.Check(name="fish_api_key", status=preflight.STATUS_FAIL,
                              message="FISH_API_KEY is not set.", remedy="")]
    assert (preflight.spoken_summary(checks)
            == "One thing needs attention, sir: I have no Fish Audio key.")


# --- the echo filter ----------------------------------------------------------

@pytest.mark.asyncio
async def test_the_echo_filter_remembers_the_spanish_that_was_spoken(es):
    """What the microphone hears is compared with what was SAID. The line is
    translated before the scheduler records it, so the Spanish the speaker
    plays back is recognised as JARVIS's own voice."""
    import asyncio
    from speech import SpeechScheduler

    async def synth(text):
        return b"A:" + text.encode()

    async def emit(msg):
        pass

    sched = SpeechScheduler(synth, emit, pause_after=0.2, stale_after=0.6,
                            echo_window=0.5, batch_interval=0.3, batch_settle=0.05)
    await sched.start()
    try:
        await sched.say(i18n.t("The work in chitauri failed, sir."))
        await asyncio.sleep(0.1)
        assert sched.classify("el trabajo en chitauri ha fallado señor") == "echo"
        # The same ears against an English memory would have heard the user:
        # that is the bug translating before registering exists to prevent.
        other = SpeechScheduler(synth, emit, pause_after=0.2, stale_after=0.6,
                                echo_window=0.5, batch_interval=0.3, batch_settle=0.05)
        await other.start()
        try:
            await other.say("The work in chitauri failed, sir.")
            await asyncio.sleep(0.1)
            assert other.classify("el trabajo en chitauri ha fallado señor") == "speech"
        finally:
            await other.stop()
    finally:
        await sched.stop()


def test_spanish_words_are_tokenised_whole():
    import speech
    assert speech._tokens("Está cancelado, señor.") == ["está", "cancelado", "señor"]
    assert speech._tokens("Good evening, sir.") == ["good", "evening", "sir"]


def test_a_long_text_is_content_not_a_phrase_and_is_left_alone(monkeypatch):
    """Patterns are quadratic on long input; content is never translated."""
    import time
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    long_text = "Opened that file, sir. " + "x " * 40_000
    start = time.monotonic()
    assert i18n.t(long_text) is long_text
    assert time.monotonic() - start < 0.5


def test_the_limit_does_not_touch_ordinary_phrases(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert i18n.t("Terminal's open in jarvis, sir.") != "Terminal's open in jarvis, sir."
