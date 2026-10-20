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
