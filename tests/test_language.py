"""JARVIS_LANGUAGE: persona, Piper voice and recognition locale follow it."""
import pytest

import data_paths
import lang
import tts


def test_english_is_the_default(monkeypatch):
    monkeypatch.delenv("JARVIS_LANGUAGE", raising=False)
    assert lang.language() == "en"
    assert lang.recognition_locale() == "en-US"
    assert lang.default_piper_voice() == "en_GB-alan-medium"


@pytest.mark.parametrize("raw", ["es", "ES", "es-ES", " es_MX "])
def test_spanish_is_recognised_in_any_spelling(monkeypatch, raw):
    monkeypatch.setenv("JARVIS_LANGUAGE", raw)
    assert lang.language() == "es"
    assert lang.recognition_locale() == "es-ES"
    assert lang.default_piper_voice() == "es_ES-davefx-medium"


@pytest.mark.parametrize("raw", ["", "fr", "klingon", "123"])
def test_anything_else_is_english(monkeypatch, raw):
    monkeypatch.setenv("JARVIS_LANGUAGE", raw)
    assert lang.language() == "en"


def test_the_persona_follows_the_language(monkeypatch):
    spanish = data_paths._TEMPLATE_DIR / "CLAUDE.es.md"
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    assert data_paths.persona_template_path().name == "CLAUDE.md"
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    expected = "CLAUDE.es.md" if spanish.is_file() else "CLAUDE.md"
    assert data_paths.persona_template_path().name == expected


def test_the_spanish_persona_keeps_every_tool_name():
    spanish = data_paths._TEMPLATE_DIR / "CLAUDE.es.md"
    if not spanish.is_file():
        pytest.skip("no Spanish persona in this checkout")
    english = (data_paths._TEMPLATE_DIR / "CLAUDE.md").read_text("utf-8")
    translated = spanish.read_text("utf-8")
    import re
    tools = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", english))
    missing = sorted(t for t in tools if t not in translated)
    assert not missing, f"tool names lost in translation: {missing}"


def test_the_piper_voice_follows_the_language(monkeypatch, tmp_path):
    monkeypatch.delenv("PIPER_MODEL", raising=False)
    monkeypatch.delenv("PIPER_VOICE", raising=False)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert tts.piper_model_path().name == "es_ES-davefx-medium.onnx"
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    assert tts.piper_model_path().name == "en_GB-alan-medium.onnx"
    monkeypatch.setenv("PIPER_VOICE", "es_MX-claude-high")
    assert tts.piper_model_path().name == "es_MX-claude-high.onnx"


# ── palabras de cancelar y de repetir ──────────────────────────────────────

async def _nothing(*args, **kwargs):
    return None


def _scheduler():
    import speech
    return speech.SpeechScheduler(_nothing, _nothing)


def test_spanish_adds_its_cancel_words_to_the_english_ones(monkeypatch):
    import speech
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    words = _scheduler().cancel_words
    assert set(speech.CANCEL_WORDS) <= set(words)
    assert {"para", "cancela", "espera", "basta", "un momento"} <= set(words)


def test_english_keeps_exactly_its_original_cancel_words(monkeypatch):
    import speech
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    assert _scheduler().cancel_words == speech.CANCEL_WORDS


def test_an_explicit_cancel_set_is_never_extended(monkeypatch):
    import speech
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    sched = speech.SpeechScheduler(_nothing, _nothing, cancel_words=("halt",))
    assert sched.cancel_words == ("halt",)


@pytest.mark.parametrize("said", [
    "repite", "Repite eso", "repítelo", "perdona, repite eso por favor",
    "Jarvis, repite", "¿qué has dicho?", "dilo otra vez", "otra vez"])
def test_spanish_asks_to_hear_it_again(monkeypatch, said):
    import speech
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert speech._is_replay_request(speech._tokens(said)) is True


@pytest.mark.parametrize("said", [
    "¿qué quieres decir con eso?", "repite el nombre del proyecto en inglés",
    "explícamelo otra vez con más detalle", "no", "perdón"])
def test_spanish_real_questions_still_reach_the_brain(monkeypatch, said):
    import speech
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert speech._is_replay_request(speech._tokens(said)) is False


def test_the_spanish_replay_words_do_nothing_in_english(monkeypatch):
    import speech
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    assert speech._is_replay_request(speech._tokens("repite")) is False
    assert speech._is_replay_request(speech._tokens("repeat that")) is True
