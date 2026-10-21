"""Local speech recognition (stt_local.py) and its /ws/mic socket.

faster-whisper and openWakeWord are faked at the two seams the module gives
(`_make_whisper`, `_make_wake`) or replaced by a fake `Transcriber`; the audio
is synthetic frames of constant amplitude. Nothing here needs a microphone, a
model, numpy, the network or a key, so it runs the same on macOS and Ubuntu.
"""
import asyncio
import sys
import time
from array import array

import pytest
from starlette.websockets import WebSocketDisconnect

import preflight
import stt_local
from stt_local import (END_SILENCE_FRAMES, FRAME_BYTES, MAX_PHRASE_FRAMES,
                       MIN_SPEECH_FRAMES, ONSET_FRAMES, WAKE_WINDOW_FRAMES,
                       EnergyVad, FrameChunker, MicSession, Phase,
                       PhraseListener)
from tests.test_voice_ws import client  # noqa: F401  (fixture)


def frame(level: int) -> bytes:
    """One 80 ms frame at a constant amplitude (RMS == |level|)."""
    samples = array("h", [level] * stt_local.FRAME_SAMPLES)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


QUIET = frame(0)
LOUD = frame(4000)
WAKE = frame(1)          # the marker the fake wake-word model fires on


def speak(listener: PhraseListener, voiced=8):
    """Feed one phrase's worth of audio, return what the listener returned."""
    out = None
    for _ in range(voiced):
        out = listener.feed(LOUD) or out
    for _ in range(END_SILENCE_FRAMES):
        out = listener.feed(QUIET) or out
    return out


@pytest.fixture(autouse=True)
def _stt_env(monkeypatch):
    for var in ("JARVIS_STT", "JARVIS_WAKE_WORD", "JARVIS_STT_MODEL",
                "JARVIS_LANGUAGE", "JARVIS_STT_IDLE_SEC"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def installed(monkeypatch):
    """Both optional packages 'importable' and their models 'present'."""
    monkeypatch.setattr(stt_local, "_has_module", lambda name: True)
    monkeypatch.setattr(stt_local, "whisper_model_cached", lambda name=None: True)
    monkeypatch.setattr(stt_local, "wakeword_model_present", lambda: True)


class FakeWhisper:
    def __init__(self, name, text="hola"):
        self.name = name
        self.text = text
        self.calls = []

    def transcribe(self, pcm, language):
        self.calls.append((len(pcm), language))
        return self.text


class FakeWake:
    def score(self, data):
        return 1.0 if data == WAKE else 0.0


class FakeTranscriber:
    def __init__(self, text="enciende la luz", fail=False):
        self.text, self.fail, self.calls = text, fail, []

    async def transcribe(self, pcm, language):
        self.calls.append((len(pcm), language))
        if self.fail:
            raise RuntimeError("model exploded")
        return self.text


# ── frames and level ───────────────────────────────────────────────────────

def test_chunker_reslices_arbitrary_message_sizes_into_whole_frames():
    chunker = FrameChunker(frame_bytes=10)
    assert chunker.feed(b"a" * 7) == []
    assert chunker.feed(b"b" * 7) == [b"a" * 7 + b"b" * 3]
    assert chunker.feed(b"c" * 22) == [b"b" * 4 + b"c" * 6, b"c" * 10]
    assert chunker.feed(b"") == []            # 6 bytes still held
    assert chunker.feed(b"d" * 4) == [b"c" * 6 + b"d" * 4]


def test_chunker_keeps_an_odd_byte_between_messages():
    chunker = FrameChunker()
    assert chunker.feed(LOUD[:-1]) == []
    assert chunker.feed(LOUD[-1:]) == [LOUD]


def test_chunker_never_holds_more_than_one_frame():
    chunker = FrameChunker()
    chunker.feed(b"\x01" * (FRAME_BYTES * 5 + 123))
    assert len(chunker._rest) < FRAME_BYTES


def test_chunker_reset_drops_the_remainder():
    chunker = FrameChunker()
    chunker.feed(b"\x01" * 10)
    chunker.reset()
    assert chunker.feed(b"\x02" * FRAME_BYTES) == [b"\x02" * FRAME_BYTES]


def test_rms_of_silence_and_of_a_constant():
    assert stt_local.frame_rms(QUIET) == 0.0
    assert stt_local.frame_rms(LOUD) == pytest.approx(4000)
    assert stt_local.frame_rms(frame(-4000)) == pytest.approx(4000)
    assert stt_local.frame_rms(b"") == 0.0


# ── VAD ────────────────────────────────────────────────────────────────────

def test_vad_tells_speech_from_silence():
    vad = EnergyVad()
    assert vad.is_speech(QUIET) is False
    assert vad.is_speech(LOUD) is True


def test_vad_threshold_rises_with_a_noisy_room_but_never_below_the_minimum():
    vad = EnergyVad()
    assert vad.threshold == stt_local.MIN_SPEECH_RMS
    hum = frame(300)                      # below the minimum: room tone
    for _ in range(100):
        assert vad.is_speech(hum) is False
    assert vad.floor == pytest.approx(300, rel=0.05)
    assert vad.threshold == pytest.approx(300 * stt_local.FLOOR_FACTOR, rel=0.05)
    assert vad.is_speech(frame(700)) is False     # louder than the hum, not speech
    assert vad.is_speech(LOUD) is True


def test_a_long_phrase_cannot_raise_its_own_bar():
    vad = EnergyVad()
    for _ in range(200):
        vad.is_speech(LOUD)
    assert vad.floor == 0.0


# ── text helpers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("heard, expected", [
    ("Jarvis, abre el terminal", "abre el terminal"),
    ("Hey Jarvis abre el terminal", "abre el terminal"),
    ("oye jarvis: qué hora es", "qué hora es"),
    ("Jarvis", ""),
    ("  Hey, Jarvis.  ", ""),
    ("abre el terminal", "abre el terminal"),
    ("dile a Jarvis que pare", "dile a Jarvis que pare"),   # only a LEADING name
    ("jarvisimo", "jarvisimo"),
    ("", ""),
])
def test_strip_wake_word(heard, expected):
    assert stt_local.strip_wake_word(heard) == expected


def test_whisper_hallucinations_are_dropped_but_thanks_is_not():
    assert stt_local.is_hallucination("Subtítulos realizados por la comunidad de Amara.org")
    assert stt_local.is_hallucination("Thanks for watching!")
    assert not stt_local.is_hallucination("gracias")
    assert not stt_local.is_hallucination("thank you")


# ── the state machine ──────────────────────────────────────────────────────

def test_silence_does_nothing():
    listener = PhraseListener()
    for _ in range(50):
        assert listener.feed(QUIET) is None
    assert listener.phase is Phase.LISTENING


def test_a_phrase_without_a_wake_word_listening_recording_transcribing():
    listener = PhraseListener()
    for _ in range(ONSET_FRAMES - 1):
        listener.feed(LOUD)
    assert listener.phase is Phase.LISTENING          # one loud frame is a click
    listener.feed(LOUD)
    assert listener.phase is Phase.RECORDING
    for _ in range(6):
        assert listener.feed(LOUD) is None
    phrase = None
    for _ in range(END_SILENCE_FRAMES):
        phrase = listener.feed(QUIET) or phrase
    assert phrase is not None and len(phrase) % FRAME_BYTES == 0
    assert listener.phase is Phase.TRANSCRIBING
    listener.done()
    assert listener.phase is Phase.LISTENING


def test_the_phrase_includes_the_audio_before_the_onset():
    listener = PhraseListener()
    marker = frame(100)                  # quiet but distinctive
    listener.feed(marker)
    phrase = speak(listener)
    assert phrase.startswith(marker)


def test_frames_are_ignored_while_transcribing():
    listener = PhraseListener()
    assert speak(listener) is not None
    assert listener.phase is Phase.TRANSCRIBING
    for _ in range(20):
        assert listener.feed(LOUD) is None
    assert listener.phase is Phase.TRANSCRIBING


def test_a_cough_is_discarded():
    listener = PhraseListener()
    out = None
    for _ in range(ONSET_FRAMES):        # fewer voiced frames than a word needs
        out = listener.feed(LOUD) or out
    assert ONSET_FRAMES < MIN_SPEECH_FRAMES
    for _ in range(END_SILENCE_FRAMES):
        out = listener.feed(QUIET) or out
    assert out is None
    assert listener.phase is Phase.LISTENING


def test_a_phrase_that_never_ends_is_cut_at_the_maximum():
    listener = PhraseListener()
    out, fed = None, 0
    while out is None and fed < MAX_PHRASE_FRAMES + 50:
        out = listener.feed(LOUD)
        fed += 1
    assert out is not None
    assert len(out) // FRAME_BYTES <= MAX_PHRASE_FRAMES


def test_wake_word_gates_the_phrase():
    listener = PhraseListener(wake_scorer=FakeWake().score)
    for _ in range(30):                          # talk without the name: ignored
        assert listener.feed(LOUD) is None
    assert listener.phase is Phase.LISTENING
    listener.feed(WAKE)
    assert listener.phase is Phase.WAKE
    phrase = speak(listener)
    assert phrase is not None
    assert listener.phase is Phase.TRANSCRIBING
    listener.done()
    assert listener.phase is Phase.LISTENING     # and it must be woken again


def test_wake_window_expires_back_to_listening():
    listener = PhraseListener(wake_scorer=FakeWake().score)
    listener.feed(WAKE)
    for _ in range(WAKE_WINDOW_FRAMES + 1):
        listener.feed(QUIET)
    assert listener.phase is Phase.LISTENING


def test_reset_drops_a_half_recorded_phrase():
    listener = PhraseListener()
    for _ in range(6):
        listener.feed(LOUD)
    assert listener.phase is Phase.RECORDING
    listener.reset()
    assert listener.phase is Phase.LISTENING
    assert listener.feed(QUIET) is None


# ── configuration ──────────────────────────────────────────────────────────

def test_browser_is_the_default_mode(installed):
    assert stt_local.requested_mode() == "browser"
    assert stt_local.mode() == "browser"
    assert stt_local.wake_enabled() is False


def test_unknown_values_fall_back_to_browser(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "cloud")
    assert stt_local.mode() == "browser"


def test_local_needs_whisper_to_be_importable(monkeypatch):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: False)
    assert stt_local.requested_mode() == "local"
    assert stt_local.mode() == "browser"
    monkeypatch.setattr(stt_local, "_has_module", lambda name: name == "faster_whisper")
    assert stt_local.mode() == "local"


def test_wake_word_is_off_by_default_and_needs_the_package(monkeypatch):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: True)
    assert stt_local.wake_enabled() is False
    monkeypatch.setenv("JARVIS_WAKE_WORD", "on")
    assert stt_local.wake_enabled() is True
    monkeypatch.setattr(stt_local, "_has_module", lambda name: name == "faster_whisper")
    assert stt_local.wake_enabled() is False      # asked for, missing: open mic, flagged by preflight


def test_wake_word_only_applies_with_local(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_WAKE_WORD", "on")
    assert stt_local.wake_enabled() is False      # JARVIS_STT is still browser


def test_model_defaults_to_base_and_spanish_never_gets_an_english_only_model(monkeypatch):
    assert stt_local.model_name() == "base"
    monkeypatch.setenv("JARVIS_STT_MODEL", "small.en")
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert stt_local.model_name() == "small"
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    assert stt_local.model_name() == "small.en"


def test_missing_pieces_are_named(monkeypatch):
    assert stt_local.problems() == []             # nothing asked for, nothing to say
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "on")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: False)
    said = " ".join(stt_local.problems())
    assert "faster-whisper" in said and "openwakeword" in said


def test_model_cache_check_looks_under_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert stt_local.whisper_model_cached("base") is False
    (tmp_path / "stt" / "models--Systran--faster-whisper-base" / "snapshots").mkdir(parents=True)
    assert stt_local.whisper_model_cached("base") is True


# ── preflight ──────────────────────────────────────────────────────────────

def test_preflight_is_quiet_in_the_default_mode():
    check = preflight._check_local_stt_sync()
    assert check.status == preflight.STATUS_OK


def test_preflight_says_what_is_missing_without_failing(monkeypatch):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: False)
    check = preflight._check_local_stt_sync()
    assert check.status == preflight.STATUS_WARN         # never FAIL: startup goes on
    assert "faster-whisper" in check.message
    assert "JARVIS_INSTALL_STT=1" in check.remedy


def test_preflight_is_ok_when_everything_is_in_place(monkeypatch, installed):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "on")
    check = preflight._check_local_stt_sync()
    assert check.status == preflight.STATUS_OK
    assert "wake word" in check.message


def test_a_model_not_yet_downloaded_is_a_warning(monkeypatch, installed):
    monkeypatch.setattr(stt_local, "whisper_model_cached", lambda name=None: False)
    monkeypatch.setenv("JARVIS_STT", "local")
    check = preflight._check_local_stt_sync()
    assert check.status == preflight.STATUS_WARN
    assert "not downloaded" in check.message


# ── the transcriber ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transcriber_loads_lazily_and_once(monkeypatch):
    made = []

    def make(name):
        engine = FakeWhisper(name, text="  hola  ".strip())
        made.append(engine)
        return engine

    monkeypatch.setattr(stt_local, "_make_whisper", make)
    stt = stt_local.Transcriber()
    assert made == [] and not stt.loaded
    assert await stt.transcribe(b"\x00" * 64, "es") == "hola"
    assert await stt.transcribe(b"\x00" * 64, "es") == "hola"
    assert len(made) == 1
    assert made[0].calls == [(64, "es"), (64, "es")]
    stt.unload()


@pytest.mark.asyncio
async def test_transcriber_uses_the_multilingual_model_for_spanish(monkeypatch):
    names = []
    monkeypatch.setenv("JARVIS_STT_MODEL", "base.en")
    monkeypatch.setattr(stt_local, "_make_whisper", lambda n: names.append(n) or FakeWhisper(n))
    stt = stt_local.Transcriber()
    await stt.transcribe(b"\x00\x00", "es")
    assert names == ["base"]
    stt.unload()


@pytest.mark.asyncio
async def test_transcriber_unloads_after_inactivity_and_reloads_on_demand(monkeypatch):
    made = []
    monkeypatch.setattr(stt_local, "_make_whisper", lambda n: made.append(n) or FakeWhisper(n))
    stt = stt_local.Transcriber()
    await stt.transcribe(b"\x00\x00", "en")
    assert stt.loaded and stt._unload_handle is not None
    stt.unload()                                   # what the timer calls
    assert not stt.loaded and stt._unload_handle is None
    await stt.transcribe(b"\x00\x00", "en")
    assert len(made) == 2
    stt.unload()


# ── a whole session, no sockets ────────────────────────────────────────────

def _audio(*frames) -> bytes:
    return b"".join(frames)


def _phrase_audio(voiced=8):
    return _audio(QUIET, QUIET, *([LOUD] * voiced), *([QUIET] * (END_SILENCE_FRAMES + 1)))


@pytest.mark.asyncio
async def test_session_turns_audio_into_text_and_reports_its_states():
    sent, heard = [], []

    async def emit(m): sent.append(m)
    async def on_text(t): heard.append(t)

    stt = FakeTranscriber("enciende la luz")
    session = MicSession(emit=emit, on_text=on_text, stt=stt, use_wake_word=False)
    await session.feed(_phrase_audio())
    assert heard == ["enciende la luz"]
    assert [m["state"] for m in sent] == ["recording", "transcribing", "listening"]
    assert stt.calls and stt.calls[0][1] == "en"          # lang.language(), here the default


@pytest.mark.asyncio
async def test_session_takes_the_language_from_jarvis_language(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    stt = FakeTranscriber()

    async def nothing(_): pass

    session = MicSession(emit=nothing, on_text=nothing, stt=stt, use_wake_word=False)
    await session.feed(_phrase_audio())
    assert stt.calls[0][1] == "es"


@pytest.mark.asyncio
async def test_session_accepts_audio_in_awkward_message_sizes():
    heard = []

    async def nothing(_): pass
    async def on_text(t): heard.append(t)

    session = MicSession(emit=nothing, on_text=on_text, stt=FakeTranscriber("hola"),
                         use_wake_word=False)
    data = _phrase_audio()
    for i in range(0, len(data), 1001):                    # odd size, splits samples
        await session.feed(data[i:i + 1001])
    assert heard == ["hola"]


@pytest.mark.asyncio
async def test_wake_word_gates_and_is_stripped_from_the_text(monkeypatch):
    monkeypatch.setattr(stt_local, "_make_wake", FakeWake)
    heard = []

    async def nothing(_): pass
    async def on_text(t): heard.append(t)

    stt = FakeTranscriber("Jarvis, abre el terminal")
    session = MicSession(emit=nothing, on_text=on_text, stt=stt, use_wake_word=True)
    await session.feed(_phrase_audio())                    # no wake word: nothing
    assert heard == [] and stt.calls == []
    await session.feed(_audio(WAKE) + _phrase_audio())
    assert heard == ["abre el terminal"]


@pytest.mark.asyncio
async def test_a_phrase_that_was_only_the_name_is_dropped(monkeypatch):
    monkeypatch.setattr(stt_local, "_make_wake", FakeWake)
    heard = []

    async def nothing(_): pass
    async def on_text(t): heard.append(t)

    session = MicSession(emit=nothing, on_text=on_text, stt=FakeTranscriber("Jarvis."),
                         use_wake_word=True)
    await session.feed(_audio(WAKE) + _phrase_audio())
    assert heard == []


@pytest.mark.asyncio
async def test_a_failing_model_reports_an_error_and_keeps_listening():
    sent, heard = [], []

    async def emit(m): sent.append(m)
    async def on_text(t): heard.append(t)

    session = MicSession(emit=emit, on_text=on_text, stt=FakeTranscriber(fail=True),
                         use_wake_word=False)
    await session.feed(_phrase_audio())
    assert heard == []
    errors = [m for m in sent if m["type"] == "error"]
    assert errors and "exploded" not in errors[0]["text"]  # the log has the detail
    assert session.listener.phase is Phase.LISTENING


@pytest.mark.asyncio
async def test_hallucinated_text_never_reaches_a_turn():
    heard = []

    async def nothing(_): pass
    async def on_text(t): heard.append(t)

    session = MicSession(emit=nothing, on_text=on_text,
                         stt=FakeTranscriber("Thanks for watching"), use_wake_word=False)
    await session.feed(_phrase_audio())
    assert heard == []


@pytest.mark.asyncio
async def test_an_oversized_message_is_ignored():
    stt = FakeTranscriber()

    async def nothing(_): pass

    session = MicSession(emit=nothing, on_text=nothing, stt=stt, use_wake_word=False)
    await session.feed(LOUD * 200)
    assert stt.calls == [] and session.listener.phase is Phase.LISTENING


@pytest.mark.asyncio
async def test_session_reset_drops_a_half_heard_phrase():
    heard = []

    async def nothing(_): pass
    async def on_text(t): heard.append(t)

    session = MicSession(emit=nothing, on_text=on_text, stt=FakeTranscriber("hola"),
                         use_wake_word=False)
    await session.feed(_audio(*([LOUD] * 6)))
    assert session.listener.phase is Phase.RECORDING
    session.reset()
    await session.feed(_audio(*([QUIET] * (END_SILENCE_FRAMES + 2))))
    assert heard == []


@pytest.mark.asyncio
async def test_importing_and_using_the_module_needs_no_optional_package(monkeypatch):
    """The pure half works with neither package importable (CI has neither)."""
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    assert stt_local.mode() == "browser"
    assert stt_local.problems() == []
    session = MicSession(emit=lambda m: asyncio.sleep(0), on_text=lambda t: asyncio.sleep(0),
                         stt=FakeTranscriber(), use_wake_word=False)
    await session.feed(QUIET)


# ── the sockets ────────────────────────────────────────────────────────────

def _drain(ws, predicate, limit=20):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if predicate(msg):
            return seen
    raise AssertionError(f"never saw the expected frame; got {seen}")


def _local_server(monkeypatch, text="enciende la luz"):
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: name == "faster_whisper")
    fake = FakeTranscriber(text)
    monkeypatch.setattr(stt_local, "transcriber", lambda: fake)
    return fake


def _record_finals(server, monkeypatch) -> list:
    """Stand in for the turn handling where a test is about the socket, not
    about what JARVIS answers."""
    texts: list = []

    async def record(text):
        texts.append(text)

    monkeypatch.setattr(server, "_handle_final_transcript", record)
    return texts


def test_the_config_frame_is_unchanged_by_default(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        assert ws.receive_json() == {"type": "config", "muteMicDuringSpeech": False}


def test_the_config_frame_announces_local_stt(client, monkeypatch):
    c, server = client
    _local_server(monkeypatch)
    with c.websocket_connect("/ws/voice") as ws:
        assert ws.receive_json() == {"type": "config", "muteMicDuringSpeech": False, "stt": "local"}


def test_local_requested_without_whisper_keeps_the_browser_engine(client, monkeypatch):
    c, server = client
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: False)
    with c.websocket_connect("/ws/voice") as ws:
        assert "stt" not in ws.receive_json()


def test_mic_socket_refuses_to_run_in_browser_mode(client):
    c, server = client
    with c.websocket_connect("/ws/mic") as ws:
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_mic_socket_is_behind_the_origin_gate(client, monkeypatch):
    c, server = client
    _local_server(monkeypatch)
    from fastapi.testclient import TestClient
    for headers in ({}, {"Origin": "http://evil.example"}):
        stranger = TestClient(server.app, headers=headers)
        with pytest.raises(WebSocketDisconnect):
            with stranger.websocket_connect("/ws/mic"):
                pass


def test_audio_in_turn_out_over_the_same_path_as_a_browser_final(client, monkeypatch):
    c, server = client
    fake = _local_server(monkeypatch, "enciende la luz")
    with c.websocket_connect("/ws/voice") as voice:
        voice.receive_json(); voice.receive_json()
        with c.websocket_connect("/ws/mic") as mic:
            ready = mic.receive_json()
            assert ready == {"type": "ready", "sampleRate": 16000, "wakeWord": False, "model": "base"}
            mic.send_bytes(_phrase_audio())
            seen = _drain(mic, lambda m: m["type"] == "transcript")
            assert seen[-1] == {"type": "transcript", "text": "enciende la luz"}
            assert [m["state"] for m in seen if m["type"] == "state"] == \
                ["recording", "transcribing", "listening"]
            # The reply arrives on /ws/voice, from the unchanged turn handling.
            reply = _drain(voice, lambda m: m["type"] == "audio")[-1]
            assert reply["text"] == "Echo: enciende la luz."
    assert server.brain_instance.turns == [("enciende la luz", "user")]
    assert len(fake.calls) == 1


def test_echo_of_jarvis_is_rejected_by_the_existing_policy(client, monkeypatch):
    c, server = client
    fake = _local_server(monkeypatch, "hello there")
    with c.websocket_connect("/ws/voice") as voice:
        voice.receive_json(); voice.receive_json()
        with c.websocket_connect("/ws/mic") as mic:
            mic.receive_json()
            mic.send_bytes(_phrase_audio())
            _drain(mic, lambda m: m["type"] == "transcript")
            audio = _drain(voice, lambda m: m["type"] == "audio")[-1]
            assert audio["text"] == "Echo: hello there."
            # The microphone now hears JARVIS himself; speech.user_final calls
            # it echo, exactly as it does for the browser's results.
            fake.text = "echo hello there"
            mic.send_bytes(_phrase_audio())
            _drain(mic, lambda m: m["type"] == "transcript")
            fake.text = "what is the weather like"
            mic.send_bytes(_phrase_audio())
            _drain(mic, lambda m: m["type"] == "transcript")
            _drain(voice, lambda m: m["type"] == "audio" and m["text"] != "Echo: hello there.")
    assert [t for t, _ in server.brain_instance.turns] == ["hello there", "what is the weather like"]


def test_nothing_is_transcribed_while_he_speaks_when_the_mute_option_is_on(client, monkeypatch):
    c, server = client
    fake = _local_server(monkeypatch, "enciende la luz")
    texts = _record_finals(server, monkeypatch)
    monkeypatch.setattr(server, "MUTE_MIC_DURING_SPEECH", True)
    speaking = {"on": True}
    monkeypatch.setattr(type(server.speech), "is_speaking", property(lambda self: speaking["on"]))
    with c.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_bytes(_phrase_audio())                  # dropped: he is talking
        mic.send_json({"type": "pause"})
        speaking["on"] = False
        mic.send_bytes(_phrase_audio())
        _drain(mic, lambda m: m["type"] == "transcript")
    assert len(fake.calls) == 1
    assert texts == ["enciende la luz"]


def test_a_second_mic_connection_replaces_the_first(client, monkeypatch):
    c, server = client
    _local_server(monkeypatch)
    with c.websocket_connect("/ws/mic") as first:
        first.receive_json()
        with c.websocket_connect("/ws/mic") as second:
            assert second.receive_json()["type"] == "ready"
            with pytest.raises(WebSocketDisconnect):
                for _ in range(5):
                    first.receive_json()


def test_garbage_text_frames_do_not_break_the_socket(client, monkeypatch):
    c, server = client
    _local_server(monkeypatch)
    _record_finals(server, monkeypatch)
    with c.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_text("not json")
        mic.send_text("[1, 2]")
        mic.send_json({"type": "nonsense"})
        mic.send_bytes(_phrase_audio())
        assert _drain(mic, lambda m: m["type"] == "transcript")


def test_a_second_page_displaces_the_first_with_its_own_close_code(client, monkeypatch):
    """Two tabs used to trade the microphone forever: the displaced page was
    closed with a normal 1000, reconnected, and displaced the other in turn.
    4001 is how the page knows to stay out of it."""
    c, server = client
    _local_server(monkeypatch)
    with c.websocket_connect("/ws/mic") as first:
        assert first.receive_json()["type"] == "ready"
        with c.websocket_connect("/ws/mic") as second:
            assert second.receive_json()["type"] == "ready"
            with pytest.raises(WebSocketDisconnect) as gone:
                first.receive_json()
            assert gone.value.code == 4001


# ── modo auto (Chrome conserva el suyo, Firefox usa el local) ──────────────

def test_auto_is_offered_only_when_whisper_is_importable(monkeypatch):
    monkeypatch.setenv("JARVIS_STT", "auto")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: False)
    assert stt_local.requested_mode() == "auto"
    assert stt_local.mode() == "browser" and stt_local.offers_local() is False
    monkeypatch.setattr(stt_local, "_has_module", lambda name: name == "faster_whisper")
    assert stt_local.mode() == "auto" and stt_local.offers_local() is True


def test_the_default_does_not_offer_the_local_recogniser(monkeypatch):
    monkeypatch.delenv("JARVIS_STT", raising=False)
    monkeypatch.setattr(stt_local, "_has_module", lambda name: True)
    assert stt_local.offers_local() is False


def test_auto_tells_the_page_to_choose(client, monkeypatch):
    c, server = client
    monkeypatch.setenv("JARVIS_STT", "auto")
    monkeypatch.setattr(stt_local, "_has_module", lambda name: name == "faster_whisper")
    with c.websocket_connect("/ws/voice") as ws:
        assert ws.receive_json() == {"type": "config", "muteMicDuringSpeech": False, "stt": "auto"}


def test_the_mic_socket_accepts_a_page_in_auto_mode(client, monkeypatch):
    c, server = client
    _local_server(monkeypatch)
    monkeypatch.setenv("JARVIS_STT", "auto")
    with c.websocket_connect("/ws/mic") as mic:
        assert mic.receive_json()["type"] == "ready"


def test_the_recogniser_is_told_what_a_proper_noun_it_would_miss_looks_like():
    assert "Jarvis" in stt_local._PROMPTS["es"] and "Jarvis" in stt_local._PROMPTS["en"]


# ── diagnóstico por frase: dice qué etapa la descartó, sin sus palabras ────

@pytest.mark.asyncio
@pytest.mark.parametrize("text, expected", [
    ("enciende la luz", "accepted"),
    ("", "nothing recognised"),
])
async def test_each_phrase_leaves_one_line_saying_what_became_of_it(caplog, text, expected):
    import logging

    async def nothing(_): pass

    session = MicSession(emit=nothing, on_text=nothing, stt=FakeTranscriber(text),
                         use_wake_word=False)
    with caplog.at_level(logging.INFO):
        await session.feed(_phrase_audio())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("stt: phrase")]
    assert len(lines) == 1 and expected in lines[0]
    assert "enciende" not in lines[0]               # the words are not logged by default


@pytest.mark.asyncio
async def test_the_words_are_logged_only_when_debugging_is_asked_for(caplog, monkeypatch):
    import logging
    monkeypatch.setenv("JARVIS_STT_DEBUG", "1")

    async def nothing(_): pass

    session = MicSession(emit=nothing, on_text=nothing, stt=FakeTranscriber("enciende la luz " * 10),
                         use_wake_word=False)
    with caplog.at_level(logging.INFO):
        await session.feed(_phrase_audio())
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("stt: phrase"))
    assert "enciende la luz" in line and len(line) < 140      # cut to 60 characters


@pytest.mark.asyncio
async def test_a_phrase_without_the_wake_word_is_reported_as_such(caplog, monkeypatch):
    import logging
    monkeypatch.setenv("JARVIS_STT", "local")
    monkeypatch.setenv("JARVIS_WAKE_WORD", "whisper")

    async def nothing(_): pass

    session = MicSession(emit=nothing, on_text=nothing, stt=FakeTranscriber("enciende la luz"),
                         use_wake_word=False)
    with caplog.at_level(logging.INFO):
        await session.feed(_phrase_audio())
    assert any("no wake word" in r.getMessage() for r in caplog.records)
