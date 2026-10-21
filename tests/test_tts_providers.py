"""Voz de salida: Piper, Kokoro y Edge detrás de la misma interfaz, el selector
de Ajustes y el respaldo en cascada.

Todo con falsos: ni Piper, ni Kokoro, ni edge-tts, ni modelos, ni red. Lo que
sí se comprueba es la lógica que decide quién habla (y que Edge, que es NUBE,
no lo elige nunca `auto`), qué pasa cuando un proveedor falla y qué dice la API
de Ajustes.
"""

from __future__ import annotations

import base64
import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import tts

# conftest las deja en False para todos los tests; aquí se recuperan las reales
# (las que mira un equipo con los paquetes instalados) solo donde hace falta.
_REAL_KOKORO_AVAILABLE = tts.kokoro_available
_REAL_EDGE_AVAILABLE = tts.edge_available
_REAL_LOCAL_AVAILABLE = tts.local_available


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__spec__ = types.SimpleNamespace(name=name)
    return module


@pytest.fixture
def piper_voices(monkeypatch, tmp_path):
    """Dos voces de Piper "instaladas" y un `piper` falso que las lee."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    voices = tmp_path / "voices"
    voices.mkdir()
    for name in ("es_ES-davefx-medium", "es_MX-claude-high"):
        (voices / f"{name}.onnx").write_bytes(b"x")
        (voices / f"{name}.onnx.json").write_text("{}")
    monkeypatch.delenv("PIPER_MODEL", raising=False)
    # La voz por defecto de Piper en inglés no está en la carpeta: se fija.
    monkeypatch.setenv("PIPER_VOICE", "es_ES-davefx-medium")
    spoken: list[str] = []
    loaded: list[str] = []

    class Voice:
        def synthesize_wav(self, text, out):
            spoken.append(text)
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(22050)
            out.writeframes(b"\x00\x00" * 2205)

    module = _module("piper")
    module.PiperVoice = types.SimpleNamespace(
        load=lambda path: (loaded.append(Path(path).name), Voice())[1])
    monkeypatch.setitem(sys.modules, "piper", module)
    monkeypatch.setattr(tts, "_voice", None)
    monkeypatch.setattr(tts, "_voice_path", None)
    monkeypatch.setattr(tts, "local_available", _REAL_LOCAL_AVAILABLE)
    return types.SimpleNamespace(spoken=spoken, loaded=loaded, dir=voices)


@pytest.fixture
def kokoro(monkeypatch, tmp_path):
    """Un `kokoro_onnx` falso con su modelo y sus voces en disco."""
    kdir = tmp_path / "kokoro"
    kdir.mkdir()
    (kdir / "kokoro-v1.0.int8.onnx").write_bytes(b"x")
    (kdir / "voices-v1.0.bin").write_bytes(b"x")
    monkeypatch.setenv("KOKORO_DIR", str(kdir))
    calls: list[dict] = []

    class Kokoro:
        def __init__(self, model_path, voices_path):
            calls.append({"model": Path(model_path).name})

        def create(self, text, voice, speed, lang):
            calls.append({"text": text, "voice": voice, "lang": lang})
            return [0.0, 0.5, -0.5, 2.0] * 600, 24000

    module = _module("kokoro_onnx")
    module.Kokoro = Kokoro
    monkeypatch.setitem(sys.modules, "kokoro_onnx", module)
    monkeypatch.setattr(tts, "kokoro_available", _REAL_KOKORO_AVAILABLE)
    monkeypatch.setattr(tts, "_kokoro", None)
    monkeypatch.setattr(tts, "_kokoro_path", None)
    return calls


@pytest.fixture
def edge(monkeypatch):
    """Un `edge_tts` falso: lo que "llega" de la nube y lo que se le envía."""
    sent: list[tuple[str, str]] = []
    state = {"fail": False}

    class Communicate:
        def __init__(self, text, voice):
            sent.append((text, voice))

        async def stream(self):
            if state["fail"]:
                raise OSError("sin red")
            yield {"type": "WordBoundary"}
            yield {"type": "audio", "data": b"ID3"}
            yield {"type": "audio", "data": b"\x00" * 50}

    module = _module("edge_tts")
    module.Communicate = Communicate
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    monkeypatch.setattr(tts, "edge_available", _REAL_EDGE_AVAILABLE)
    return types.SimpleNamespace(sent=sent, state=state)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("JARVIS_TTS_PROVIDER", "JARVIS_TTS_VOICE", "FISH_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "auto")


# ── quién habla ────────────────────────────────────────────────────────────

def test_auto_prefers_fish_then_piper_then_kokoro_then_nothing(monkeypatch, piper_voices, kokoro):
    monkeypatch.setenv("FISH_API_KEY", "una-clave")
    assert tts.provider() == "fish"
    monkeypatch.setenv("FISH_API_KEY", "")
    assert tts.provider() == "piper"
    monkeypatch.setattr(tts, "local_available", lambda voice=None: False)
    assert tts.provider() == "kokoro"
    monkeypatch.setattr(tts, "kokoro_available", lambda: False)
    assert tts.provider() == "none"


def test_auto_never_chooses_edge_even_when_it_is_all_there_is(monkeypatch, edge):
    assert tts.edge_available() is True
    assert tts.provider() == "none"
    monkeypatch.setenv("FISH_API_KEY", "una-clave")
    assert tts.provider() == "fish"
    tts.note_fish_failure(402)
    assert tts.provider() == "fish"              # nada local: sigue intentando Fish, no salta a Edge


def test_edge_and_kokoro_are_chosen_only_when_asked(monkeypatch, piper_voices, kokoro, edge):
    assert tts.provider() == "piper"
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "kokoro")
    assert tts.provider() == "kokoro"
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "edge")
    assert tts.provider() == "edge"


def test_a_forced_provider_that_cannot_run_answers_with_the_local_voice(monkeypatch, piper_voices):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "edge")          # edge_tts no está instalado
    assert tts.provider() == "piper"
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "kokoro")        # ni Kokoro
    assert tts.provider() == "piper"
    monkeypatch.setattr(tts, "local_available", lambda voice=None: False)
    assert tts.provider() == "none"


def test_an_unknown_provider_name_is_auto(monkeypatch, piper_voices):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "elevenlabs")
    assert tts.wanted() == "auto" and tts.provider() == "piper"


# ── qué voz ────────────────────────────────────────────────────────────────

def test_the_voice_setting_picks_an_installed_piper_voice(monkeypatch, piper_voices):
    assert tts.piper_model_path().name == "es_ES-davefx-medium.onnx"
    monkeypatch.setenv("JARVIS_TTS_VOICE", "es_MX-claude-high")
    assert tts.piper_model_path().name == "es_MX-claude-high.onnx"
    monkeypatch.setenv("JARVIS_TTS_VOICE", "es_AR-daniela-high")      # no instalada: se ignora
    assert tts.piper_model_path().name == "es_ES-davefx-medium.onnx"


def test_each_provider_only_takes_a_voice_with_its_own_shape(monkeypatch, piper_voices):
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    monkeypatch.setenv("JARVIS_TTS_VOICE", "ef_dora")
    assert tts.kokoro_voice_name() == "ef_dora"
    assert tts.piper_model_path().name == "es_ES-davefx-medium.onnx"   # Piper no se rompe
    assert tts.edge_voice_name() == "es-ES-AlvaroNeural"
    monkeypatch.setenv("JARVIS_TTS_VOICE", "es-ES-ElviraNeural")
    assert tts.edge_voice_name() == "es-ES-ElviraNeural"
    assert tts.kokoro_voice_name() == "em_alex"


def test_a_voice_name_can_never_be_a_path(monkeypatch, piper_voices):
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    for evil in ("../../etc/passwd", "es_ES-x-medium/../../y", "/tmp/x.onnx"):
        monkeypatch.setenv("JARVIS_TTS_VOICE", evil)
        assert tts.piper_model_path().name == "es_ES-davefx-medium.onnx"
        assert tts.kokoro_voice_name() == "em_alex"
        assert tts.edge_voice_name() == "es-ES-AlvaroNeural"
    assert tts.piper_model_path("../../etc/passwd").name == "invalid.onnx"


def test_the_default_voices_follow_the_language(monkeypatch, piper_voices):
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    assert tts.kokoro_voice_name() == "bm_george"
    assert tts.edge_voice_name() == "en-GB-RyanNeural"
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert tts.kokoro_voice_name() == "em_alex"
    assert tts.edge_voice_name() == "es-ES-AlvaroNeural"


# ── síntesis ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kokoro_makes_a_wav_with_the_voice_and_language_of_its_name(monkeypatch, kokoro):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "kokoro")
    monkeypatch.setenv("JARVIS_TTS_VOICE", "ef_dora")
    r = await tts.synthesize("Buenos días, señor.")
    assert r is not None and r.provider == "kokoro" and r.audio[:4] == b"RIFF"
    assert {"text": "Buenos días, señor.", "voice": "ef_dora", "lang": "es"} in kokoro
    import io
    import wave
    with wave.open(io.BytesIO(r.audio)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2400
        samples = w.readframes(4)
    import struct
    assert samples == struct.pack("<4h", 0, 16383, -16383, 32767)      # recortado a [-1, 1]


@pytest.mark.asyncio
async def test_kokoro_rejects_a_voice_name_that_is_not_one(kokoro):
    assert await tts.synthesize_kokoro("hola", "../x") is None
    assert await tts.synthesize_kokoro("   ") is None


@pytest.mark.asyncio
async def test_edge_returns_the_mp3_and_sends_the_configured_voice(monkeypatch, edge):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "edge")
    monkeypatch.setenv("JARVIS_TTS_VOICE", "es-ES-ElviraNeural")
    r = await tts.synthesize("Buenas noches, señor.")
    assert r is not None and r.provider == "edge" and r.audio.startswith(b"ID3") and len(r.audio) == 53
    assert edge.sent == [("Buenas noches, señor.", "es-ES-ElviraNeural")]
    assert not tts.edge_is_down()


# ── respaldo en cascada ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_kokoro_crash_falls_back_to_piper(monkeypatch, piper_voices, kokoro):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "kokoro")
    monkeypatch.setattr(tts, "_kokoro_wav", lambda text, voice: (_ for _ in ()).throw(RuntimeError("onnx")))
    assert tts._cascade() == ["kokoro", "piper"]
    r = await tts.synthesize("Hola.")
    assert r is not None and r.provider == "piper" and piper_voices.spoken == ["Hola."]
    assert tts.status()["last_used"] == "piper"


@pytest.mark.asyncio
async def test_edge_failing_falls_back_to_piper_and_is_skipped_for_a_while(monkeypatch, piper_voices, edge):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "edge")
    edge.state["fail"] = True
    r = await tts.synthesize("Primera.")
    assert r is not None and r.provider == "piper"       # la frase que choca con el fallo se dice igual
    assert tts.edge_is_down()
    assert tts.effective_provider() == "piper" and tts.provider() == "edge"
    assert tts._cascade() == ["piper"]                   # sin esperar a un servicio caído
    sent_before = len(edge.sent)
    r = await tts.synthesize("Segunda.")
    assert r.provider == "piper" and len(edge.sent) == sent_before
    edge.state["fail"] = False
    monkeypatch.setattr(tts, "_edge_down_until", 0.0)    # pasada la ventana, vuelve a intentarlo
    r = await tts.synthesize("Tercera.")
    assert r.provider == "edge"


@pytest.mark.asyncio
async def test_edge_with_nothing_local_keeps_trying_and_returns_none(monkeypatch, edge):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "edge")
    edge.state["fail"] = True
    assert await tts.synthesize("Hola.") is None
    assert tts._cascade() == ["edge"]


@pytest.mark.asyncio
async def test_the_edge_timeout_is_a_failure_not_a_hang(monkeypatch, piper_voices, edge):
    import asyncio
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "edge")
    monkeypatch.setattr(tts, "EDGE_TIMEOUT_SEC", 0.05)

    async def never(text, voice):
        await asyncio.sleep(5)

    monkeypatch.setattr(tts, "_edge_mp3", never)
    r = await tts.synthesize("Hola.")
    assert r is not None and r.provider == "piper" and tts.edge_is_down()


def test_fish_falls_back_only_when_auto_chose_it(monkeypatch, piper_voices):
    monkeypatch.setenv("FISH_API_KEY", "k")
    assert tts._cascade() == ["fish", "piper"]
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "fish")
    assert tts._cascade() == ["fish"]


@pytest.mark.asyncio
async def test_nothing_available_is_none_and_never_an_exception():
    assert tts.provider() == "none"
    assert await tts.synthesize("Hola.") is None


@pytest.mark.asyncio
async def test_a_trial_uses_exactly_the_provider_asked_for(monkeypatch, piper_voices):
    r = await tts.synthesize_with("piper", "Prueba.", voice="es_MX-claude-high")
    assert r is not None and r.provider == "piper"
    assert piper_voices.loaded[-1] == "es_MX-claude-high.onnx"
    assert await tts.synthesize_with("edge", "Prueba.") is None      # no está instalado: sin respaldo
    assert await tts.synthesize_with("piper", "Prueba.", voice="es_AR-daniela-high") is None


# ── la API de Ajustes ──────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path, piper_voices):
    import data_paths
    importlib.reload(data_paths)
    import run_store
    importlib.reload(run_store)
    import server
    importlib.reload(server)
    run_store.init_db()
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    monkeypatch.setattr(server, "_env_file_path", lambda: env_file)
    monkeypatch.setattr(server, "_append_usage_entry", lambda *a, **k: None)
    with TestClient(server.app, headers={"Origin": "http://localhost:5173"}) as c:
        yield c, server, env_file


def _provider(body, name):
    return next(p for p in body["voice"]["providers"] if p["id"] == name)


def test_the_status_lists_what_is_installed_and_what_is_in_use(client, kokoro):
    c, _, _ = client
    body = c.get("/api/settings/status").json()
    assert body["voice_provider"] == "piper" and body["voice"]["in_use"] == "piper"
    assert body["voice"]["wanted"] == "auto"
    piper = _provider(body, "piper")
    assert piper["available"] and piper["cloud"] is False
    assert piper["voices"] == ["es_ES-davefx-medium", "es_MX-claude-high"]
    assert piper["voice"] == "es_ES-davefx-medium"
    assert _provider(body, "kokoro")["available"] is True
    assert _provider(body, "edge")["available"] is False         # no está instalado
    assert _provider(body, "edge")["cloud"] is True and _provider(body, "fish")["cloud"] is True
    assert body["voice_notice"] == ""


def test_a_rejected_fish_key_is_explained_and_names_the_voice_that_speaks(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    monkeypatch.setenv("FISH_API_KEY", "una-clave-sin-credito")
    assert c.get("/api/settings/status").json()["voice_notice"] == ""
    tts.note_fish_failure(402)
    body = c.get("/api/settings/status").json()
    assert body["voice"]["fish_down"] and body["voice"]["fish_down_account"]
    assert body["voice"]["in_use"] == "piper"
    assert body["voice_notice"] == "Fish rejects the key: no credit or the key is invalid. Using Piper."
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert c.get("/api/settings/status").json()["voice_notice"] == \
        "Fish rechaza la clave: sin crédito o clave inválida. Usando Piper."


def test_a_network_blip_is_not_called_a_rejected_key(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    monkeypatch.setenv("FISH_API_KEY", "k")
    tts.note_fish_failure(None)
    notice = c.get("/api/settings/status").json()["voice_notice"]
    assert notice == "Fish is not answering. Using Piper for now."


def test_without_a_spare_voice_the_notice_says_so(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    monkeypatch.setenv("FISH_API_KEY", "k")
    monkeypatch.setattr(tts, "local_available", lambda voice=None: False)
    tts.note_fish_failure(402)
    notice = c.get("/api/settings/status").json()["voice_notice"]
    assert notice.endswith("and there is no other voice to fall back on.")


def test_forcing_another_provider_hides_the_fish_notice(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setenv("FISH_API_KEY", "k")
    tts.note_fish_failure(402)
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "piper")
    assert c.get("/api/settings/status").json()["voice_notice"] == ""


def test_choosing_a_voice_writes_the_env_and_takes_effect_at_once(client, kokoro):
    c, server, env_file = client
    r = c.post("/api/settings/voice", json={"provider": "piper", "voice": "es_MX-claude-high"})
    assert r.status_code == 200 and r.json()["success"] is True
    _, parsed = server._read_env()
    assert parsed["JARVIS_TTS_PROVIDER"] == "piper"
    assert parsed["JARVIS_TTS_VOICE"] == "es_MX-claude-high"
    assert r.json()["voice"]["wanted"] == "piper"
    assert _provider(r.json(), "piper")["voice"] == "es_MX-claude-high"
    r = c.post("/api/settings/voice", json={"provider": "kokoro", "voice": "ef_dora"})
    assert r.status_code == 200 and tts.provider() == "kokoro"


def test_a_voice_that_is_not_installed_cannot_be_chosen(client):
    c, server, _ = client
    for body in ({"provider": "edge", "voice": ""},                       # paquete no instalado
                 {"provider": "kokoro", "voice": ""},                     # ídem
                 {"provider": "fish", "voice": ""},                       # sin clave
                 {"provider": "piper", "voice": "es_AR-daniela-high"},    # voz no instalada
                 {"provider": "piper", "voice": "../../x"},
                 {"provider": "elevenlabs", "voice": ""},
                 {"provider": "auto", "voice": "es_ES-davefx-medium"}):
        r = c.post("/api/settings/voice", json=body)
        assert r.status_code == 400 and r.json()["success"] is False, body
    _, parsed = server._read_env()
    assert "JARVIS_TTS_PROVIDER" not in parsed and "JARVIS_TTS_VOICE" not in parsed


def test_auto_and_none_can_be_chosen_and_edge_is_offered_only_when_installed(client, edge):
    c, _, _ = client
    assert c.post("/api/settings/voice", json={"provider": "none"}).status_code == 200
    assert tts.provider() == "none"
    assert c.post("/api/settings/voice", json={"provider": "auto"}).status_code == 200
    assert tts.provider() == "piper"
    r = c.post("/api/settings/voice", json={"provider": "edge", "voice": "es-ES-ElviraNeural"})
    assert r.status_code == 200 and tts.provider() == "edge"
    assert _provider(r.json(), "edge")["voice"] == "es-ES-ElviraNeural"


def test_the_generic_key_route_cannot_write_nonsense_into_the_voice_keys(client):
    c, _, _ = client
    r = c.post("/api/settings/keys", json={"key_name": "JARVIS_TTS_VOICE", "key_value": "a\nFISH_API_KEY=x"})
    assert r.status_code == 400


def test_the_voice_test_speaks_with_that_provider_and_does_not_save_it(client, kokoro):
    c, server, _ = client
    r = c.post("/api/settings/voice-test", json={"provider": "kokoro", "voice": "em_alex"})
    body = r.json()
    assert r.status_code == 200 and body["provider"] == "kokoro" and body["mime"] == "audio/wav"
    assert base64.b64decode(body["audio"])[:4] == b"RIFF"
    assert {"text": "Probando el audio, señor.", "voice": "em_alex", "lang": "es"} in kokoro
    _, parsed = server._read_env()
    assert "JARVIS_TTS_PROVIDER" not in parsed


def test_the_voice_test_is_spoken_in_the_language_of_the_butler(client, piper_voices):
    c, _, _ = client
    r = c.post("/api/settings/voice-test", json={"provider": "piper", "voice": "es_MX-claude-high"})
    assert r.json()["provider"] == "piper"
    assert piper_voices.spoken == ["Probando el audio, señor."]


def test_the_edge_test_labels_mp3_and_a_dead_voice_says_so(client, edge, monkeypatch):
    c, _, _ = client
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    r = c.post("/api/settings/voice-test", json={"provider": "edge", "voice": ""}).json()
    assert r["mime"] == "audio/mpeg" and r["provider"] == "edge"
    edge.state["fail"] = True
    r = c.post("/api/settings/voice-test", json={"provider": "edge", "voice": ""}).json()
    assert r["audio"] is None and r["error"] == "That voice did not answer"


def test_the_voice_test_refuses_what_is_not_installed(client):
    c, _, _ = client
    assert c.post("/api/settings/voice-test", json={"provider": "edge"}).status_code == 400
    assert c.post("/api/settings/voice-test", json={"provider": "piper", "voice": "x"}).status_code == 400
