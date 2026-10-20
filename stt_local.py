"""
stt_local.py — speech recognition on this machine, with a wake word.

Upstream listens through Chrome's Web Speech API, which streams the
microphone to Google and has no wake word: whatever is said near the laptop
becomes a turn. This module is the opt-in alternative:

* `JARVIS_STT=browser` (default) — nothing here runs; behaviour is unchanged.
* `JARVIS_STT=local` — the page streams 16 kHz mono PCM16 to `/ws/mic`, the
  server finds the phrases and transcribes them with faster-whisper.
* `JARVIS_WAKE_WORD=on` (default off, local only) — a phrase is only taken
  after the openWakeWord model `hey_jarvis` fires.
* `JARVIS_STT_MODEL` (default `base`) — the faster-whisper model. Spanish
  never uses a `.en` model: it would transcribe Spanish as bad English.

faster-whisper and openWakeWord are OPTIONAL and imported lazily, inside the
two small engine classes at the bottom. Without them JARVIS starts as it
always did; `mode()` quietly says `browser` and preflight explains why.

Everything between the sockets and those two engines is plain Python with no
dependency — frame slicing, an energy VAD, the phrase state machine — so the
test-suite exercises it with fakes and never needs audio, a model or numpy.

The transcript leaves this module as a string and enters server.py through
the SAME function the browser's final results use, so turn handling, echo
rejection and interruptions are not duplicated here.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import sys
import time
from array import array
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger("jarvis.stt")

# ── audio format ───────────────────────────────────────────────────────────
# Fixed by both models: 16 kHz, mono, signed 16-bit little-endian.
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
# 80 ms: openWakeWord's native chunk, and a fine step for an energy VAD.
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE
FRAME_SEC = FRAME_SAMPLES / SAMPLE_RATE

# One websocket message may carry this much audio (2 s). Anything larger is
# not our client, and is dropped rather than buffered.
MAX_MESSAGE_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * 2

# ── phrase detection ───────────────────────────────────────────────────────
ONSET_FRAMES = 2            # consecutive loud frames that start a phrase (160 ms)
PREROLL_FRAMES = 4          # audio kept from before the onset, so a word's first
                            # consonant is not clipped (320 ms)
END_SILENCE_FRAMES = 11     # quiet that ends a phrase (~0.9 s)
MIN_SPEECH_FRAMES = 4       # a phrase with less voiced audio than this is a cough
MAX_PHRASE_FRAMES = 190     # ~15 s: a hard stop on one recording
WAKE_WINDOW_FRAMES = 75     # ~6 s to start speaking after the wake word
WAKE_THRESHOLD = 0.5        # openWakeWord's own suggested activation score

# Energy VAD. RMS of PCM16 (0..32768). The floor tracks the room and the
# threshold rides above it, with an absolute minimum so digital silence cannot
# make the threshold zero and every dither bit "speech".
MIN_SPEECH_RMS = 350.0
FLOOR_FACTOR = 3.0
FLOOR_ALPHA = 0.05          # how fast the floor follows quiet frames


# ── configuration (read at call time, so tests and .env edits just work) ──

_PROMPTS = {
    "es": "Conversación con JARVIS, el asistente de la casa. Jarvis, señor.",
    "en": "A conversation with JARVIS, the house assistant. Jarvis, sir.",
}


def requested_mode() -> str:
    """`browser` (default), `local`, or `auto`.

    `auto` offers the local recogniser to pages that have no recogniser of
    their own: Chrome keeps using its Web Speech API, Firefox (which has none)
    gets the local one. The page decides; the server only says it is available.
    """
    raw = os.environ.get("JARVIS_STT", "browser").strip().lower()
    return raw if raw in ("browser", "local", "auto") else "browser"


def wake_requested() -> bool:
    return os.environ.get("JARVIS_WAKE_WORD", "off").strip().lower() in ("1", "on", "true", "yes")


def model_name(language: Optional[str] = None) -> str:
    """The faster-whisper model to load. An English-only `.en` model is
    replaced by its multilingual twin for any other language."""
    import lang
    language = language or lang.language()
    name = os.environ.get("JARVIS_STT_MODEL", "").strip() or "base"
    if name.endswith(".en") and language != "en":
        return name[: -len(".en")]
    return name


def idle_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("JARVIS_STT_IDLE_SEC", "300")))
    except ValueError:
        return 300.0


def stt_dir() -> Path:
    import data_paths
    return data_paths.data_dir() / "stt"


# ── what is installed ──────────────────────────────────────────────────────

def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def whisper_installed() -> bool:
    return _has_module("faster_whisper")


def wakeword_installed() -> bool:
    return _has_module("openwakeword")


def whisper_model_cached(name: Optional[str] = None) -> bool:
    """Is the model already on disk (so a first phrase does not wait on a
    download)? Looks for the Hugging Face cache folder under `data/stt/`."""
    folder = stt_dir() / f"models--Systran--faster-whisper-{name or model_name()}"
    return (folder / "snapshots").is_dir()


def wakeword_model_present() -> bool:
    """openWakeWord keeps its pretrained models inside its own package."""
    try:
        spec = importlib.util.find_spec("openwakeword")
    except (ImportError, ValueError):
        return False
    for root in (spec.submodule_search_locations or []) if spec else []:
        if any((Path(root) / "resources" / "models").glob("hey_jarvis*.onnx")):
            return True
    return False


def wake_enabled() -> bool:
    """The wake word is on only if asked for AND available. When it is asked
    for and missing, local recognition runs without it (an open microphone,
    as the browser's always is) and preflight says so."""
    return requested_mode() == "local" and wake_requested() and wakeword_installed()


def mode() -> str:
    """`local` or `auto` only when asked for and faster-whisper is importable;
    otherwise `browser`, so a half-installed setup still has a microphone."""
    wanted = requested_mode()
    if wanted in ("local", "auto") and whisper_installed():
        return wanted
    return "browser"


def offers_local() -> bool:
    """Will `/ws/mic` accept a microphone: `local` and `auto` both do."""
    return mode() in ("local", "auto")


def problems() -> list[str]:
    """What stops the requested configuration from working, in words.
    Empty when nothing was asked for or everything is in place."""
    if requested_mode() not in ("local", "auto"):
        return []
    out = []
    if not whisper_installed():
        out.append("faster-whisper is not installed")
    elif not whisper_model_cached():
        out.append(f"the Whisper model '{model_name()}' is not downloaded yet")
    if wake_requested():
        if not wakeword_installed():
            out.append("openwakeword is not installed (no wake word: the microphone stays open)")
        elif not wakeword_model_present():
            out.append("the openWakeWord model hey_jarvis is not downloaded")
    return out


# ── pure audio helpers ─────────────────────────────────────────────────────

class FrameChunker:
    """Re-slice arbitrary byte chunks from the socket into whole frames.

    The browser's worklet sends whatever size suits it and a message may end
    mid-sample, so the remainder is kept for the next call. It never holds
    more than one frame, which is also the bound on what a client can make
    the server buffer.
    """

    def __init__(self, frame_bytes: int = FRAME_BYTES):
        self._size = frame_bytes
        self._rest = b""

    def feed(self, data: bytes) -> list[bytes]:
        buf = self._rest + data
        whole = len(buf) - len(buf) % self._size
        self._rest = buf[whole:]
        return [buf[i:i + self._size] for i in range(0, whole, self._size)]

    def reset(self) -> None:
        self._rest = b""


def frame_rms(frame: bytes) -> float:
    """Root-mean-square level of one PCM16 little-endian frame."""
    if len(frame) < BYTES_PER_SAMPLE:
        return 0.0
    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % BYTES_PER_SAMPLE])
    if sys.byteorder == "big":
        samples.byteswap()
    return (sum(s * s for s in samples) / len(samples)) ** 0.5


class EnergyVad:
    """Is this frame speech? Level against a floor that follows the room.

    Stateful only in the floor; it learns from frames judged to be quiet, so
    a long phrase cannot raise its own bar.
    """

    def __init__(self, min_rms: float = MIN_SPEECH_RMS):
        self._min = min_rms
        self.floor = 0.0

    @property
    def threshold(self) -> float:
        return max(self._min, self.floor * FLOOR_FACTOR)

    def is_speech(self, frame: bytes) -> bool:
        level = frame_rms(frame)
        speech = level >= self.threshold
        if not speech:
            self.floor = level if self.floor == 0.0 else \
                self.floor + FLOOR_ALPHA * (level - self.floor)
        return speech


_WAKE_PREFIX = re.compile(
    r"^\s*(?:(?:hey|hei|ey|oye|hola|ok|okay|vale)[\s,.!¡¿?-]+)?jarvis\b[\s,.:;!¡¿?-]*",
    re.IGNORECASE)


def strip_wake_word(text: str) -> str:
    """Drop a leading "(hey) Jarvis," the recording picked up from the wake
    word itself. A phrase that was only the name becomes empty."""
    return _WAKE_PREFIX.sub("", text or "", count=1).strip()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


# Whisper invents these on near-silence. Matched whole, after normalising.
# "gracias" / "thank you" are deliberately NOT here: they are what a person
# says back to JARVIS.
_HALLUCINATIONS = frozenset(_norm(t) for t in (
    "Gracias por ver el vídeo", "Gracias por ver el video",
    "Subtítulos realizados por la comunidad de Amara.org",
    "Thanks for watching", "Thank you for watching",
))


def is_hallucination(text: str) -> bool:
    return _norm(text) in _HALLUCINATIONS


# ── the phrase state machine ───────────────────────────────────────────────

class Phase(str, Enum):
    LISTENING = "listening"          # waiting: for the wake word, or for a voice
    WAKE = "wake"                    # wake word heard; waiting for the phrase
    RECORDING = "recording"          # inside a phrase
    TRANSCRIBING = "transcribing"    # phrase handed off; frames are ignored


WakeScorer = Callable[[bytes], float]


class PhraseListener:
    """listening -> wake word heard -> recording -> transcribing -> listening.

    Fed one frame at a time and driven by frame COUNTS, never a clock, so it
    behaves identically at any speed and a test can script it exactly. It
    returns the finished phrase (PCM16 bytes) from the frame that ends it and
    then holds in TRANSCRIBING until `done()`.

    Without a `wake_scorer` the wake word is off: a voice starts a phrase
    directly from LISTENING.
    """

    def __init__(self, *, vad: Optional[EnergyVad] = None,
                 wake_scorer: Optional[WakeScorer] = None,
                 wake_threshold: float = WAKE_THRESHOLD):
        self.vad = vad or EnergyVad()
        self._wake = wake_scorer
        self._threshold = wake_threshold
        self.phase = Phase.LISTENING
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._loud_run = 0
        self._wake_left = 0
        self._phrase: list[bytes] = []
        self._voiced = 0
        self._quiet = 0

    def reset(self) -> None:
        """Back to LISTENING, dropping any half-recorded phrase."""
        self.phase = Phase.LISTENING
        self._preroll.clear()
        self._loud_run = 0
        self._wake_left = 0
        self._phrase = []
        self._voiced = 0
        self._quiet = 0

    def done(self) -> None:
        """The hand-off finished (transcribed, or failed): listen again."""
        self.reset()

    def feed(self, frame: bytes) -> Optional[bytes]:
        if self.phase is Phase.TRANSCRIBING:
            return None
        if self.phase is Phase.RECORDING:
            return self._record(frame)

        # LISTENING or WAKE: looking for the start of a phrase.
        if self.phase is Phase.LISTENING and self._wake is not None:
            self._preroll.append(frame)
            if self._wake(frame) >= self._threshold:
                self.phase = Phase.WAKE
                self._wake_left = WAKE_WINDOW_FRAMES
                self._loud_run = 0
            return None

        # The VAD runs on every frame in these phases, so its floor keeps
        # learning the room.
        loud = self.vad.is_speech(frame)
        self._preroll.append(frame)
        if self.phase is Phase.WAKE:
            self._wake_left -= 1
            if self._wake_left <= 0 and not loud:
                self.reset()
                return None
        self._loud_run = self._loud_run + 1 if loud else 0
        if self._loud_run >= ONSET_FRAMES:
            self.phase = Phase.RECORDING
            self._phrase = list(self._preroll)
            self._voiced = self._loud_run
            self._quiet = 0
        return None

    def _record(self, frame: bytes) -> Optional[bytes]:
        self._phrase.append(frame)
        if self.vad.is_speech(frame):
            self._voiced += 1
            self._quiet = 0
        else:
            self._quiet += 1
        if self._quiet < END_SILENCE_FRAMES and len(self._phrase) < MAX_PHRASE_FRAMES:
            return None
        phrase, voiced = b"".join(self._phrase), self._voiced
        if voiced < MIN_SPEECH_FRAMES:
            self.reset()
            return None
        self.phase = Phase.TRANSCRIBING
        self._phrase = []
        self._preroll.clear()
        return phrase


# ── engines (the only code that imports an optional package) ──────────────

class _WhisperEngine:
    """faster-whisper on the CPU, int8. The only place numpy is touched."""

    def __init__(self, name: str):
        from faster_whisper import WhisperModel          # optional, lazy
        d = stt_dir()
        d.mkdir(parents=True, exist_ok=True)
        self._model = WhisperModel(name, device="cpu", compute_type="int8",
                                   cpu_threads=4, download_root=str(d))

    def transcribe(self, pcm: bytes, language: str) -> str:
        import numpy as np                                # a faster-whisper dependency
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        # Without a hint Whisper writes "Harbys" for "Jarvis": a proper noun it
        # has no reason to expect. The prompt and hotword fix that (measured).
        segments, _info = self._model.transcribe(
            audio, language=language, beam_size=1,
            condition_on_previous_text=False, vad_filter=True,
            initial_prompt=_PROMPTS.get(language, _PROMPTS["en"]),
            hotwords="Jarvis")
        return " ".join(s.text.strip() for s in segments).strip()


class _WakeEngine:
    """openWakeWord with the pretrained `hey_jarvis` model."""

    def __init__(self):
        from openwakeword.model import Model              # optional, lazy
        self._model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    def score(self, frame: bytes) -> float:
        import numpy as np
        scores = self._model.predict(np.frombuffer(frame, dtype="<i2"))
        # Key names carry a version suffix in some releases ("hey_jarvis_v0.1").
        value = max((v for k, v in scores.items() if "jarvis" in k), default=0.0)
        if value >= WAKE_THRESHOLD:
            # Clear the model's rolling window or one "Jarvis" fires for the
            # next second of frames.
            reset = getattr(self._model, "reset", None)
            if callable(reset):
                reset()
        return float(value)


# Seams: tests replace these with fakes; production uses the engines above.
def _make_whisper(name: str):
    return _WhisperEngine(name)


def _make_wake():
    return _WakeEngine()


class Transcriber:
    """Lazy, serialised, self-unloading speech-to-text.

    The target laptop has 3.7 GB, so the model is not loaded at start-up: it
    loads on the first phrase and is dropped `idle_seconds()` after the last
    one (the next phrase then pays the load again).
    """

    def __init__(self):
        self._engine = None
        self._name = ""
        self._lock = asyncio.Lock()
        self._unload_handle: Optional[asyncio.TimerHandle] = None

    @property
    def loaded(self) -> bool:
        return self._engine is not None

    def unload(self) -> None:
        if self._unload_handle is not None:
            self._unload_handle.cancel()
            self._unload_handle = None
        if self._engine is not None:
            log.info("stt: model unloaded after inactivity")
        self._engine = None

    def _transcribe_blocking(self, pcm: bytes, language: str, name: str) -> str:
        if self._engine is None or self._name != name:
            t0 = time.monotonic()
            self._engine = _make_whisper(name)
            self._name = name
            log.info("stt: model %s loaded in %.1fs", name, time.monotonic() - t0)
        return self._engine.transcribe(pcm, language)

    async def transcribe(self, pcm: bytes, language: str) -> str:
        name = model_name(language)
        async with self._lock:
            try:
                return await asyncio.to_thread(self._transcribe_blocking, pcm, language, name)
            finally:
                if self._unload_handle is not None:
                    self._unload_handle.cancel()
                self._unload_handle = asyncio.get_running_loop().call_later(
                    idle_seconds(), self.unload)


_transcriber: Optional[Transcriber] = None


def transcriber() -> Transcriber:
    """The one shared model: a second tab must not load a second copy."""
    global _transcriber
    if _transcriber is None:
        _transcriber = Transcriber()
    return _transcriber


# ── one microphone connection ──────────────────────────────────────────────

Emit = Callable[[dict], Awaitable[None]]


class MicSession:
    """What one `/ws/mic` connection owns: the chunker, the state machine and
    (if enabled) the wake-word model. Audio goes in through `feed`; state
    changes and finished phrases come out through the two callbacks."""

    def __init__(self, *, emit: Emit,
                 on_text: Callable[[str], Awaitable[None]],
                 stt: Optional[Transcriber] = None,
                 use_wake_word: Optional[bool] = None):
        self._emit = emit
        self._on_text = on_text
        self._stt = stt or transcriber()
        self.wake_word = wake_enabled() if use_wake_word is None else use_wake_word
        self._wake_engine = _make_wake() if self.wake_word else None
        self.listener = PhraseListener(
            wake_scorer=self._wake_engine.score if self._wake_engine else None)
        self._chunker = FrameChunker()
        self._last_phase = self.listener.phase

    def reset(self) -> None:
        """Drop a half-heard phrase: the microphone was paused, or JARVIS is
        talking and `JARVIS_MUTE_MIC_DURING_SPEECH` is on."""
        self._chunker.reset()
        self.listener.reset()

    async def _announce(self) -> None:
        if self.listener.phase is not self._last_phase:
            self._last_phase = self.listener.phase
            await self._emit({"type": "state", "state": self.listener.phase.value})

    async def feed(self, data: bytes) -> None:
        import lang
        if len(data) > MAX_MESSAGE_BYTES:
            return
        for frame in self._chunker.feed(data):
            phrase = self.listener.feed(frame)
            await self._announce()
            if phrase is None:
                continue
            try:
                text = await self._stt.transcribe(phrase, lang.language())
            except Exception as e:                      # model missing, OOM, ...
                log.error("stt: transcription failed: %s", e)
                await self._emit({"type": "error",
                                  "text": "Local speech recognition failed; see the server log."})
                text = ""
            self.listener.done()
            await self._announce()
            if self.wake_word:
                text = strip_wake_word(text)
            if text and not is_hallucination(text):
                await self._on_text(text)

    def close(self) -> None:
        self._wake_engine = None


def ready_frame() -> dict:
    """First frame of a connection: what the page needs to know."""
    return {"type": "ready", "sampleRate": SAMPLE_RATE,
            "wakeWord": wake_enabled(), "model": model_name()}
