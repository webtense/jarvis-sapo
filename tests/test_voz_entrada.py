"""Voz de entrada de ubuntu-0.7.0: la palabra "Jarvis" en el texto
(JARVIS_WAKE_WORD=whisper) y la interrupción hablando (JARVIS_BARGE_IN=on).

Todo con fakes: audio sintético de amplitud constante, un `Transcriber`
falso y un reloj falso. Nada necesita micrófono, modelo, numpy ni red.
"""
import asyncio
import sys
from array import array

import pytest

import preflight
import stt_local
from stt_local import (BargeInDetector, END_SILENCE_FRAMES, MicSession,
                       PREROLL_FRAMES)
from tests.test_voice_ws import client  # noqa: F401  (fixture)


def frame(level: int) -> bytes:
    samples = array("h", [level] * stt_local.FRAME_SAMPLES)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


QUIET = frame(0)
ECHO = frame(300)          # su propia voz, ya recortada por la cancelación de eco
LOUD = frame(4000)         # el usuario hablando


def audio(*frames) -> bytes:
    return b"".join(frames)


def phrase(voiced=8):
    return audio(QUIET, QUIET, *([LOUD] * voiced), *([QUIET] * (END_SILENCE_FRAMES + 1)))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Stt:
    """Un transcriptor que devuelve lo que se le diga, y recuerda las llamadas."""

    def __init__(self, text="", then=()):
        self.text, self.calls = text, 0
        self._then = list(then)          # textos de las llamadas siguientes, en orden

    async def transcribe(self, pcm, language):
        self.calls += 1
        if self._then:
            self.text = self._then.pop(0)
        return self.text


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("JARVIS_STT", "JARVIS_WAKE_WORD", "JARVIS_STT_MODEL", "JARVIS_LANGUAGE",
                "JARVIS_WAKE_WINDOW_SEC", "JARVIS_BARGE_IN", "JARVIS_BARGE_IN_RMS",
                "JARVIS_BARGE_IN_MS", "JARVIS_MUTE_MIC_DURING_SPEECH"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(stt_local, "_has_module", lambda name: True)
    monkeypatch.setattr(stt_local, "whisper_model_cached", lambda name=None: True)
    monkeypatch.setattr(stt_local, "wakeword_model_present", lambda: True)


async def nothing(_=None):
    pass


# ── lo que no cambia por defecto ───────────────────────────────────────────

def test_everything_new_is_off_by_default(installed):
    assert stt_local.requested_mode() == "browser"
    assert stt_local.mode() == "browser" and stt_local.offers_local() is False
    assert stt_local.wake_mode() == "off" and stt_local.wake_text_enabled() is False
    assert stt_local.barge_in_requested() is False
    assert stt_local.wake_window_sec() == 20.0


def test_the_ready_frame_is_the_old_one_by_default():
    assert stt_local.ready_frame() == {"type": "ready", "sampleRate": 16000,
                                       "wakeWord": False, "model": "base"}


def test_a_default_session_has_no_gate_and_no_detector(monkeypatch):
    session = MicSession(emit=nothing, on_text=nothing, stt=Stt(), use_wake_word=False)
    assert session.wake_text is False and session.barge_in is False


@pytest.mark.asyncio
async def test_a_default_session_hands_over_every_phrase_even_while_he_speaks():
    heard = []

    async def on_text(t): heard.append(t)

    stt = Stt("enciende la luz")
    session = MicSession(emit=nothing, on_text=on_text, stt=stt, use_wake_word=False,
                         on_barge_in=nothing)
    await session.feed(phrase(), speaking=True)        # sin JARVIS_BARGE_IN: como siempre
    assert heard == ["enciende la luz"] and stt.calls == 1


# ── configuración ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, mode", [
    ("", "off"), ("off", "off"), ("on", "openwakeword"), ("1", "openwakeword"),
    ("TRUE", "openwakeword"), ("whisper", "whisper"), (" Whisper ", "whisper"),
    ("quizás", "off"),
])
def test_wake_mode(monkeypatch, raw, mode):
    monkeypatch.setenv("JARVIS_WAKE_WORD", raw)
    assert stt_local.wake_mode() == mode


def test_whisper_is_not_the_audio_wake_word(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    assert stt_local.wake_requested() is False and stt_local.wake_enabled() is False
    assert stt_local.wake_text_enabled() is True


def test_the_text_wake_word_also_applies_in_auto_mode(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "auto")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    assert stt_local.wake_text_enabled() is True


def test_the_text_wake_word_needs_the_local_recogniser(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")           # JARVIS_STT=browser
    assert stt_local.wake_text_enabled() is False


@pytest.mark.parametrize("raw, seconds", [
    ("20", 20.0), ("45.5", 45.5), ("0", 0.0), ("-3", 0.0), ("100000", 600.0),
    ("mucho", 20.0), ("nan", 20.0), ("", 20.0),
])
def test_window_seconds(monkeypatch, raw, seconds):
    monkeypatch.setenv("JARVIS_WAKE_WINDOW_SEC", raw)
    assert stt_local.wake_window_sec() == seconds


def test_barge_in_settings(monkeypatch):
    assert stt_local.barge_in_rms() == stt_local.BARGE_MIN_RMS
    assert stt_local.barge_in_frames() == 5                      # 400 ms de 80
    monkeypatch.setenv("JARVIS_BARGE_IN_MS", "800")
    assert stt_local.barge_in_frames() == 10
    monkeypatch.setenv("JARVIS_BARGE_IN_MS", "1")                # nunca menos de 2 frames
    assert stt_local.barge_in_frames() == 2
    monkeypatch.setenv("JARVIS_BARGE_IN_MS", "basura")
    assert stt_local.barge_in_frames() == 5
    monkeypatch.setenv("JARVIS_BARGE_IN_RMS", "5")               # nunca bajo el doble del VAD
    assert stt_local.barge_in_rms() == 2 * stt_local.MIN_SPEECH_RMS
    monkeypatch.setenv("JARVIS_BARGE_IN_RMS", "2500")
    assert stt_local.barge_in_rms() == 2500.0
    monkeypatch.setenv("JARVIS_BARGE_IN_RMS", "nan")
    assert stt_local.barge_in_rms() == stt_local.BARGE_MIN_RMS


def test_the_ready_frame_says_what_is_on(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    monkeypatch.setenv("JARVIS_BARGE_IN", "on")
    assert stt_local.ready_frame() == {"type": "ready", "sampleRate": 16000, "wakeWord": False,
                                       "model": "base", "wake": "whisper", "bargeIn": True}


# ── la palabra en el texto, dentro de la sesión ────────────────────────────

def _gated(stt, clock=None, window=20.0, heard=None):
    heard = heard if heard is not None else []

    async def on_text(t): heard.append(t)

    session = MicSession(emit=nothing, on_text=on_text, stt=stt, use_wake_word=False,
                         wake_text_gate=True, window_sec=window, clock=clock or Clock())
    return session, heard


@pytest.mark.asyncio
async def test_a_phrase_without_the_name_never_reaches_a_turn():
    stt = Stt("enciende la luz")
    session, heard = _gated(stt)
    await session.feed(phrase())
    assert stt.calls == 1            # se transcribe todo lo que se oye
    assert heard == []               # pero no se entrega


@pytest.mark.asyncio
async def test_a_phrase_with_the_name_is_delivered_without_it():
    stt = Stt("Jarvis, enciende la luz")
    session, heard = _gated(stt)
    await session.feed(phrase())
    assert heard == ["enciende la luz"]


@pytest.mark.asyncio
async def test_the_conversation_window_lets_him_go_on_and_then_closes():
    clock = Clock()
    stt = Stt("Oye Jarvis, ¿está puesta la alarma?")
    session, heard = _gated(stt, clock)
    await session.feed(phrase())
    stt.text = "y la del garaje"
    clock.t += 12
    await session.feed(phrase())
    clock.t += 30
    stt.text = "otra cosa cualquiera"
    await session.feed(phrase())
    assert heard == ["¿está puesta la alarma?", "y la del garaje"]


@pytest.mark.asyncio
async def test_only_the_name_opens_the_window_and_delivers_nothing():
    stt = Stt("Jarvis.")
    session, heard = _gated(stt)
    await session.feed(phrase())
    assert heard == []
    stt.text = "enciende la luz"
    await session.feed(phrase())
    assert heard == ["enciende la luz"]


@pytest.mark.asyncio
async def test_while_he_speaks_the_window_does_not_run_out():
    clock = Clock()
    stt = Stt("Jarvis, cuéntame algo largo")
    session, heard = _gated(stt, clock)
    await session.feed(phrase())
    for _ in range(3):
        clock.t += 15
        await session.feed(QUIET, speaking=True)     # habla: la ventana se alarga
    stt.text = "gracias"
    await session.feed(phrase())
    assert heard == ["cuéntame algo largo", "gracias"]


@pytest.mark.asyncio
async def test_whisper_hallucinations_do_not_open_the_window():
    stt = Stt("Gracias por ver el vídeo")
    session, heard = _gated(stt)
    await session.feed(phrase())
    stt.text = "enciende la luz"
    await session.feed(phrase())
    assert heard == []


@pytest.mark.asyncio
async def test_the_audio_wake_word_wins_over_the_text_one(monkeypatch):
    class FakeWake:
        def score(self, data): return 0.0

    monkeypatch.setattr(stt_local, "_make_wake", FakeWake)
    session = MicSession(emit=nothing, on_text=nothing, stt=Stt(), use_wake_word=True,
                         wake_text_gate=True)
    assert session.wake_text is False


def test_the_gate_follows_the_environment(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    monkeypatch.setenv("JARVIS_WAKE_WINDOW_SEC", "7")
    session = MicSession(emit=nothing, on_text=nothing, stt=Stt(), use_wake_word=False)
    assert session.wake_text is True and session._gate.window_sec == 7.0


# ── el detector de interrupción ────────────────────────────────────────────

def _detector(**kw):
    return BargeInDetector(min_rms=1200, frames=5, warmup=6, **kw)


def _fires(det, frames):
    return [i for i, f in enumerate(frames) if det.feed(f)]


def test_his_own_quiet_echo_never_fires():
    det = _detector()
    assert _fires(det, [ECHO] * 200) == []
    assert 250 < det.echo < 350                    # y la ha medido


def test_a_loud_but_steady_echo_is_learned_and_does_not_fire():
    """Altavoz alto y mala cancelación: 2000 constante, por encima del mínimo."""
    det = _detector()
    assert _fires(det, [frame(2000)] * 200) == []
    assert det.threshold == pytest.approx(6000, rel=0.05)      # 3 x el eco


def test_an_echo_with_the_gaps_between_words_does_not_fire():
    det = _detector()
    burst = [frame(2500)] * 3 + [QUIET] * 2        # palabras de 240 ms con pausas
    assert _fires(det, burst * 40) == []


def test_the_user_over_a_quiet_echo_fires_after_the_required_duration():
    det = _detector()
    heard = [ECHO] * 8 + [LOUD] * 10
    assert _fires(det, heard) == [8 + 4]           # el 5.º frame fuerte seguido


def test_the_user_over_a_loud_echo_must_be_well_above_it():
    det = _detector()
    echo = frame(2000)
    assert _fires(det, [echo] * 10 + [frame(4500)] * 8) == []      # < 3 x 2000
    det = _detector()
    assert _fires(det, [echo] * 10 + [frame(8000)] * 8) == [14]


def test_a_short_burst_is_not_enough():
    det = _detector()
    assert _fires(det, [ECHO] * 8 + [LOUD] * 4 + [ECHO] * 3 + [LOUD] * 4 + [ECHO] * 3) == []


def test_a_pause_resets_the_count():
    det = _detector()
    assert _fires(det, [ECHO] * 8 + [LOUD] * 3 + [QUIET] + [LOUD] * 4) == []


def test_nothing_fires_while_it_is_still_learning():
    det = _detector()
    assert _fires(det, [LOUD] * 6) == []           # el usuario ya hablaba al empezar: se aprende como eco
    assert det.echo > 3000


def test_it_fires_once_per_utterance_and_reset_rearms_it():
    det = _detector()
    hits = _fires(det, [ECHO] * 8 + [LOUD] * 30)
    assert len(hits) == 1 and det.fired
    det.reset()
    assert det.echo == 0.0 and not det.fired
    assert len(_fires(det, [ECHO] * 8 + [LOUD] * 30)) == 1


def test_the_user_speaking_up_cannot_raise_his_own_bar():
    det = _detector()
    _fires(det, [ECHO] * 8)
    before = det.echo
    _fires(det, [LOUD] * 3)                        # fuertes, sin llegar a disparar
    assert det.echo == pytest.approx(before)


# ── el detector dentro de la sesión ────────────────────────────────────────

def _barging(stt, heard=None, calls=None):
    heard = heard if heard is not None else []
    calls = calls if calls is not None else []

    async def on_text(t): heard.append(t)
    async def on_barge(): calls.append(1)

    session = MicSession(emit=nothing, on_text=on_text, stt=stt, use_wake_word=False,
                         wake_text_gate=False, barge_in=True, on_barge_in=on_barge)
    return session, heard, calls


@pytest.mark.asyncio
async def test_his_own_echo_is_neither_transcribed_nor_a_barge_in():
    stt = Stt("eco de jarvis")
    session, heard, calls = _barging(stt)
    await session.feed(audio(*([ECHO] * 40)), speaking=True)
    assert calls == [] and stt.calls == 0 and heard == []
    assert session.listener.phase is stt_local.Phase.LISTENING


@pytest.mark.asyncio
async def test_the_user_talking_over_him_cuts_him_and_the_phrase_is_kept():
    stt = Stt("para, enciende la luz")
    session, heard, calls = _barging(stt)
    await session.feed(audio(*([ECHO] * 10), *([LOUD] * 6)), speaking=True)
    assert calls == [1]
    # ya ha dejado de hablar (el servidor lo cortó): el resto de la frase
    await session.feed(audio(*([LOUD] * 3), *([QUIET] * (END_SILENCE_FRAMES + 1))), speaking=False)
    assert heard == ["para, enciende la luz"] and stt.calls == 1


@pytest.mark.asyncio
async def test_the_first_frames_of_the_interruption_are_replayed_into_the_phrase():
    seen = {}

    class Measure(Stt):
        async def transcribe(self, pcm, language):
            seen["frames"] = len(pcm) // stt_local.FRAME_BYTES
            return await super().transcribe(pcm, language)

    session, heard, calls = _barging(Measure("hola"))
    await session.feed(audio(*([ECHO] * 10), *([LOUD] * 5)), speaking=True)
    await session.feed(audio(*([LOUD] * 2), *([QUIET] * (END_SILENCE_FRAMES + 1))), speaking=False)
    # las 5 fuertes que lo dispararon + las 2 de después + el silencio + el
    # eco que cabe en el pre-roll: la frase empieza donde empezó el usuario.
    assert seen["frames"] >= 5 + 2 + END_SILENCE_FRAMES
    assert heard == ["hola"]


@pytest.mark.asyncio
async def test_a_half_recorded_phrase_is_dropped_when_he_starts_speaking():
    stt = Stt("viejo")
    session, heard, calls = _barging(stt)
    await session.feed(audio(QUIET, QUIET, LOUD, LOUD, LOUD), speaking=False)
    assert session.listener.phase is stt_local.Phase.RECORDING
    await session.feed(ECHO, speaking=True)
    assert session.listener.phase is stt_local.Phase.LISTENING


@pytest.mark.asyncio
async def test_it_rearms_for_the_next_utterance():
    stt = Stt("uno")
    session, heard, calls = _barging(stt)
    for _ in range(2):
        await session.feed(audio(*([ECHO] * 10), *([LOUD] * 6)), speaking=True)
        await session.feed(audio(*([QUIET] * (END_SILENCE_FRAMES + 1))), speaking=False)
    assert calls == [1, 1]


@pytest.mark.asyncio
async def test_a_failing_handler_does_not_kill_the_session():
    async def boom(): raise RuntimeError("no se pudo")

    stt = Stt("hola")
    session = MicSession(emit=nothing, on_text=nothing, stt=stt, use_wake_word=False,
                         wake_text_gate=False, barge_in=True, on_barge_in=boom)
    await session.feed(audio(*([ECHO] * 10), *([LOUD] * 6)), speaking=True)
    await session.feed(phrase(), speaking=False)
    assert stt.calls >= 1


def test_barge_in_needs_somewhere_to_send_the_cut():
    session = MicSession(emit=nothing, on_text=nothing, stt=Stt(), use_wake_word=False,
                         wake_text_gate=False, barge_in=True, on_barge_in=None)
    assert session.barge_in is False


@pytest.mark.asyncio
async def test_barge_in_and_the_text_wake_word_work_together():
    clock = Clock()
    heard = []

    async def on_text(t): heard.append(t)

    stt = Stt("Jarvis, cuéntame un chiste")
    session = MicSession(emit=nothing, on_text=on_text, stt=stt, use_wake_word=False,
                         wake_text_gate=True, window_sec=20, clock=clock,
                         barge_in=True, on_barge_in=nothing)
    await session.feed(phrase())
    stt.text = "vale, para"
    await session.feed(audio(*([ECHO] * 10), *([LOUD] * 6)), speaking=True)
    await session.feed(audio(*([LOUD] * 2), *([QUIET] * (END_SILENCE_FRAMES + 1))))
    assert heard == ["cuéntame un chiste", "vale, para"]     # sin repetir "Jarvis": ventana abierta


# ── preflight ──────────────────────────────────────────────────────────────

def test_preflight_names_the_new_modes(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    monkeypatch.setenv("JARVIS_STT_MODEL", "base")
    monkeypatch.setenv("JARVIS_BARGE_IN", "on")
    check = preflight._check_local_stt_sync()
    assert check.status == preflight.STATUS_OK
    assert "text wake word" in check.message and "20 s" in check.message
    assert "barge-in" in check.message


def test_preflight_warns_that_a_big_model_is_slow_for_the_text_wake_word(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    monkeypatch.setenv("JARVIS_STT_MODEL", "medium")
    check = preflight._check_local_stt_sync()
    assert check.status == preflight.STATUS_WARN
    assert "transcribes everything" in check.message and "JARVIS_STT_MODEL=base or small" in check.message


def test_a_light_model_is_fine_for_the_text_wake_word(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    for model in ("tiny", "base", "small", "tiny.en"):
        monkeypatch.setenv("JARVIS_STT_MODEL", model)
        assert not any("transcribes everything" in p for p in stt_local.problems())


def test_preflight_says_barge_in_clashes_with_muting_the_microphone(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_BARGE_IN", "on")
    monkeypatch.setenv("JARVIS_MUTE_MIC_DURING_SPEECH", "true")
    check = preflight._check_local_stt_sync()
    assert check.status == preflight.STATUS_WARN
    assert "JARVIS_BARGE_IN has no effect" in check.message


def test_the_default_preflight_message_is_unchanged(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    check = preflight._check_local_stt_sync()
    assert check.message == "Local speech recognition ready (base)."


# ── el socket ──────────────────────────────────────────────────────────────

def _drain(ws, predicate, limit=20):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if predicate(msg):
            return seen
    raise AssertionError(f"never saw the expected frame; got {seen}")


def _server_with(monkeypatch, text, then=()):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: name == "faster_whisper")
    fake = Stt(text, then)
    monkeypatch.setattr(stt_local, "transcriber", lambda: fake)
    return fake


def _record_finals(server, monkeypatch) -> list:
    texts: list = []

    async def record(text):
        texts.append(text)

    monkeypatch.setattr(server, "_handle_final_transcript", record)
    return texts


def test_the_socket_gates_by_the_name_and_cuts_it_from_the_turn(client, monkeypatch):
    c, server = client
    fake = _server_with(monkeypatch, "charla de la tele", then=["charla de la tele",
                                                               "Jarvis, enciende la luz"])
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")
    texts = _record_finals(server, monkeypatch)
    with c.websocket_connect("/ws/mic") as mic:
        ready = mic.receive_json()
        assert ready["wake"] == "whisper"
        mic.send_bytes(phrase())             # no es para él: ni turno ni transcript
        mic.send_bytes(phrase())             # "Jarvis, ..."
        seen = _drain(mic, lambda m: m["type"] == "transcript")
        assert seen[-1] == {"type": "transcript", "text": "enciende la luz"}
        assert [m for m in seen if m["type"] == "transcript"] == [seen[-1]]   # solo esa
    assert texts == ["enciende la luz"] and fake.calls == 2


def test_the_socket_cuts_his_speech_when_the_user_talks_over_him(client, monkeypatch):
    c, server = client
    _server_with(monkeypatch, "para")
    monkeypatch.setenv("JARVIS_BARGE_IN", "on")
    texts = _record_finals(server, monkeypatch)
    speaking = {"on": True}
    cuts = []

    async def fake_barge_in(self, keep_unread=True, reason=""):
        cuts.append((keep_unread, reason))
        speaking["on"] = False                  # callado, como el de verdad

    monkeypatch.setattr(type(server.speech), "is_speaking", property(lambda self: speaking["on"]))
    monkeypatch.setattr(type(server.speech), "barge_in", fake_barge_in)
    with c.websocket_connect("/ws/mic") as mic:
        assert mic.receive_json()["bargeIn"] is True
        # un solo mensaje: su eco y luego el usuario, mientras habla
        mic.send_bytes(audio(*([ECHO] * 10), *([LOUD] * 6)))
        mic.send_bytes(audio(*([LOUD] * 2), *([QUIET] * (END_SILENCE_FRAMES + 1))))
        _drain(mic, lambda m: m["type"] == "transcript")
    assert cuts == [(False, "hush (voice)")]
    assert texts == ["para"]


def _speaking_only_first_message(monkeypatch, server, cuts):
    """`is_speaking` is True for the first read and False after. The socket
    reads it once per audio message, so the first message is heard while he
    talks and the second after; the second is also the test's sync point (its
    transcript comes back)."""
    reads = {"n": 0}

    def speaking(self):
        reads["n"] += 1
        return reads["n"] <= 1

    async def fake_barge_in(self, keep_unread=True, reason=""):
        cuts.append(reason)

    monkeypatch.setattr(type(server.speech), "is_speaking", property(speaking))
    monkeypatch.setattr(type(server.speech), "barge_in", fake_barge_in)


def test_the_socket_does_not_cut_him_on_his_own_echo(client, monkeypatch):
    c, server = client
    fake = _server_with(monkeypatch, "eco")
    monkeypatch.setenv("JARVIS_BARGE_IN", "on")
    texts = _record_finals(server, monkeypatch)
    cuts = []
    _speaking_only_first_message(monkeypatch, server, cuts)
    with c.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_bytes(audio(*([ECHO] * 60)))           # su voz, mientras habla
        mic.send_bytes(phrase())                         # ya callado: el usuario
        _drain(mic, lambda m: m["type"] == "transcript")
    assert cuts == []
    assert texts == ["eco"] and fake.calls == 1          # solo la del usuario


def test_with_the_mute_option_on_barge_in_stays_out_of_it(client, monkeypatch):
    c, server = client
    fake = _server_with(monkeypatch, "hola")
    monkeypatch.setenv("JARVIS_BARGE_IN", "on")
    monkeypatch.setattr(server, "MUTE_MIC_DURING_SPEECH", True)
    texts = _record_finals(server, monkeypatch)
    cuts = []
    _speaking_only_first_message(monkeypatch, server, cuts)
    with c.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_bytes(audio(*([ECHO] * 10), *([LOUD] * 10)))    # descartado: habla él
        mic.send_bytes(phrase())
        _drain(mic, lambda m: m["type"] == "transcript")
    assert cuts == []
    assert texts == ["hola"] and fake.calls == 1
