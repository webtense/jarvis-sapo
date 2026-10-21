"""The Piper provider in tts.py. Piper itself is faked: the suite must pass on a
runner that has never downloaded a voice."""
import io
import sys
import types
import wave

import pytest

import speech
import tts


def _wav(seconds=0.5, rate=22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    """A model file on disk and a `piper` module whose voice writes a WAV."""
    model = tmp_path / "voices" / "es_ES-test.onnx"
    model.parent.mkdir()
    model.write_bytes(b"x")
    monkeypatch.setenv("PIPER_MODEL", str(model))
    calls = []

    class Voice:
        def synthesize_wav(self, text, out):
            calls.append(text)
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(22050)
            out.writeframes(b"\x00\x00" * 11025)

    module = types.ModuleType("piper")
    module.PiperVoice = types.SimpleNamespace(load=lambda path: Voice())
    module.__spec__ = types.SimpleNamespace(name="piper")
    monkeypatch.setitem(sys.modules, "piper", module)
    monkeypatch.setattr(tts, "_voice", None)
    monkeypatch.setattr(tts, "_voice_path", None)
    return calls


def test_the_placeholder_in_env_example_is_not_a_key(monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "your-fish-audio-api-key-here")
    assert tts.fish_configured() is False
    monkeypatch.setenv("FISH_API_KEY", "")
    assert tts.fish_configured() is False
    monkeypatch.setenv("FISH_API_KEY", "a-real-looking-key")
    assert tts.fish_configured() is True


def test_provider_prefers_fish_then_piper_then_none(monkeypatch, fake_piper):
    monkeypatch.delenv("JARVIS_TTS_PROVIDER", raising=False)
    monkeypatch.setenv("FISH_API_KEY", "k")
    assert tts.provider() == "fish"
    monkeypatch.setenv("FISH_API_KEY", "")
    assert tts.provider() == "piper"
    monkeypatch.setenv("PIPER_MODEL", "/nonexistent/voice.onnx")
    assert tts.provider() == "none"


def test_provider_can_be_forced(monkeypatch, fake_piper):
    monkeypatch.setenv("FISH_API_KEY", "k")
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "piper")
    assert tts.provider() == "piper"
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "none")
    assert tts.provider() == "none"
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "fish")
    monkeypatch.setenv("FISH_API_KEY", "")
    assert tts.provider() == "none"       # forced, but there is no key: no voice


@pytest.mark.asyncio
async def test_local_synthesis_returns_a_playable_wav(fake_piper):
    r = await tts.synthesize_local("  Buenos días, señor.  ")
    assert r is not None
    assert fake_piper == ["Buenos días, señor."]
    assert r.audio[:4] == b"RIFF"
    assert speech.wav_seconds(r.audio) == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_local_synthesis_is_none_for_blank_text_or_missing_voice(fake_piper, monkeypatch):
    assert await tts.synthesize_local("   ") is None
    monkeypatch.setenv("PIPER_MODEL", "/nonexistent/voice.onnx")
    assert await tts.synthesize_local("hola") is None


@pytest.mark.asyncio
async def test_a_piper_crash_is_none_not_an_exception(fake_piper, monkeypatch):
    monkeypatch.setattr(tts, "_piper_wav", lambda text: (_ for _ in ()).throw(RuntimeError("onnx")))
    assert await tts.synthesize_local("hola") is None


def test_wav_length_feeds_the_ack_floor():
    assert speech.wav_seconds(_wav(2.0)) == pytest.approx(2.0, abs=0.01)
    assert speech.ack_floor_seconds(_wav(2.0)) == pytest.approx(2.0 * speech.ACK_FLOOR_FACTOR, abs=0.01)
    assert speech.wav_seconds(b"RIFFxxxxWAVEgarbage" * 4) == 0.0
    assert speech.wav_seconds(None) == 0.0
    assert speech.audio_seconds(b"\xff\xfb\x90\x00" * 10) == speech.mp3_seconds(b"\xff\xfb\x90\x00" * 10)


def test_preflight_accepts_the_local_voice(monkeypatch, fake_piper):
    import preflight
    monkeypatch.setenv("FISH_API_KEY", "")
    check = preflight._check_fish_api_key_sync()
    assert check.status == preflight.STATUS_OK
    assert "Piper" in check.message


# ── Fish configurado pero caído: JARVIS no se queda mudo ───────────────────

def test_a_fish_failure_moves_auto_to_piper_for_a_while(monkeypatch, fake_piper):
    monkeypatch.delenv("JARVIS_TTS_PROVIDER", raising=False)
    monkeypatch.setenv("FISH_API_KEY", "a-key-without-credit")
    assert tts.provider() == "fish"
    now = [1000.0]
    monkeypatch.setattr(tts.time, "monotonic", lambda: now[0])
    tts.note_fish_failure(402)
    assert tts.provider() == "piper" and tts.fish_is_down()
    now[0] += tts.FISH_DOWN_ACCOUNT_SEC - 1
    assert tts.provider() == "piper"
    now[0] += 2                                  # the window is over: try Fish again
    assert tts.provider() == "fish" and not tts.fish_is_down()


def test_a_blip_is_forgiven_sooner_than_a_rejected_key(monkeypatch, fake_piper):
    monkeypatch.setenv("FISH_API_KEY", "k")
    now = [50.0]
    monkeypatch.setattr(tts.time, "monotonic", lambda: now[0])
    tts.note_fish_failure(None)
    now[0] += tts.FISH_DOWN_TRANSIENT_SEC + 1
    assert tts.fish_is_down() is False
    tts.note_fish_failure(401)
    now[0] += tts.FISH_DOWN_TRANSIENT_SEC + 1
    assert tts.fish_is_down() is True


def test_without_a_local_voice_fish_stays_the_choice_after_a_failure(monkeypatch):
    monkeypatch.delenv("JARVIS_TTS_PROVIDER", raising=False)
    monkeypatch.setenv("FISH_API_KEY", "k")
    monkeypatch.setattr(tts, "local_available", lambda: False)
    tts.note_fish_failure(402)
    assert tts.provider() == "fish"              # nothing to fall back to: keep trying


def test_forcing_fish_is_never_overridden(monkeypatch, fake_piper):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "fish")
    monkeypatch.setenv("FISH_API_KEY", "k")
    tts.note_fish_failure(402)
    assert tts.provider() == "fish"


class _Resp:
    def __init__(self, status, body=b""):
        self.status_code, self._body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        yield self._body


class _Client:
    def __init__(self, status, body=b""):
        self._status, self._body = status, body

    def stream(self, *args, **kwargs):
        return _Resp(self._status, self._body)


@pytest.mark.asyncio
async def test_a_402_from_fish_is_remembered_and_a_success_forgives_it(monkeypatch, fake_piper):
    assert await tts.synthesize_chunk("hola", api_key="k", voice_id="v", client=_Client(402)) is None
    assert tts.fish_is_down()
    ok = await tts.synthesize_chunk("hola", api_key="k", voice_id="v", client=_Client(200, b"mp3"))
    assert ok is not None and not tts.fish_is_down()


@pytest.mark.asyncio
async def test_the_sentence_that_meets_the_failure_is_still_spoken(monkeypatch, fake_piper):
    """The first sentence after Fish dies is not dropped: it is said with Piper."""
    import server
    monkeypatch.delenv("JARVIS_TTS_PROVIDER", raising=False)
    monkeypatch.setenv("FISH_API_KEY", "k")
    monkeypatch.setattr(server, "_tts_client", _Client(402))
    monkeypatch.setattr(server, "FISH_API_KEY", "k")
    monkeypatch.setattr(server, "_append_usage_entry", lambda *a, **k: None)   # no file under data/
    audio = await server._synth_for_speech("Buenos días, señor.")
    assert audio is not None and audio[:4] == b"RIFF"        # Piper's WAV
    assert fake_piper == ["Buenos días, señor."]
    assert tts.provider() == "piper"                          # and the next ones go straight there


# ── velocidad de Piper ─────────────────────────────────────────────────────

def test_piper_pace_defaults_to_the_voices_own_and_is_clamped(monkeypatch):
    monkeypatch.delenv("PIPER_LENGTH_SCALE", raising=False)
    assert tts.piper_length_scale() == 1.0 and tts._piper_synthesis_config() is None
    for raw, expected in (("1.15", 1.15), ("0.1", 0.7), ("9", 1.6), ("rápido", 1.0), ("nan", 1.0), ("", 1.0)):
        monkeypatch.setenv("PIPER_LENGTH_SCALE", raw)
        assert tts.piper_length_scale() == expected, raw


@pytest.mark.asyncio
async def test_a_slower_pace_reaches_the_voice(fake_piper, monkeypatch):
    seen = []

    class Voice:
        def synthesize_wav(self, text, out, syn_config=None):
            seen.append(getattr(syn_config, "length_scale", None))
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(22050)
            out.writeframes(b"\x00\x00" * 100)

    monkeypatch.setattr(tts, "_load_voice", lambda path=None: Voice())
    # `fake_piper` already put a proper module (with a __spec__, which
    # `local_available()` looks for) in sys.modules: give it the config class.
    monkeypatch.setattr(sys.modules["piper"], "SynthesisConfig",
                        lambda length_scale: types.SimpleNamespace(length_scale=length_scale),
                        raising=False)
    monkeypatch.delenv("PIPER_LENGTH_SCALE", raising=False)
    await tts.synthesize_local("hola")
    monkeypatch.setenv("PIPER_LENGTH_SCALE", "1.2")
    await tts.synthesize_local("hola")
    assert seen == [None, 1.2]
