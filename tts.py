"""
tts.py — speech synthesis, one request per sentence chunk.

Two providers behind one seam:

* **Fish Audio** (upstream's only voice): streamed so time-to-first-byte can be
  measured; the chunk is returned whole because the browser decodes one
  complete MP3 per chunk.
* **Piper** (jarvis-ubuntu): a local neural voice, no network and no key. One
  complete WAV per chunk, which `decodeAudioData` takes just as well as MP3.
  Piper is an OPTIONAL dependency (`requirements-linux.txt`); everything here
  imports it lazily so the rest of JARVIS never needs it.

`provider()` picks: `JARVIS_TTS_PROVIDER` (`fish` | `piper` | `none`) when set,
otherwise Fish if a real key is configured, otherwise Piper if it is usable,
otherwise `none` — text only, as before.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("jarvis.tts")

FISH_TTS_URL = "https://api.fish.audio/v1/tts"

# `.env.example` ships a placeholder so the line exists; it is not a key.
_FISH_PLACEHOLDERS = {"", "your-fish-audio-api-key-here"}



@dataclass
class SynthResult:
    audio: bytes
    first_byte_sec: float
    total_sec: float


async def synthesize_chunk(text: str, *, api_key: str, voice_id: str,
                           client: Optional[httpx.AsyncClient] = None,
                           latency: str = "balanced", timeout: float = 15.0) -> Optional[SynthResult]:
    text = (text or "").strip()
    if not text or not api_key:
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    t0 = time.monotonic()
    first: Optional[float] = None
    buf = bytearray()
    try:
        async with client.stream(
            "POST", FISH_TTS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"text": text, "reference_id": voice_id, "format": "mp3", "mp3_bitrate": 128, "latency": latency},
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                log.error(f"TTS {resp.status_code} for {text[:40]!r}")
                note_fish_failure(resp.status_code)
                return None
            async for part in resp.aiter_bytes():
                if first is None:
                    first = time.monotonic() - t0
                buf.extend(part)
    except (httpx.HTTPError, OSError) as e:
        log.error(f"TTS error: {e}")
        note_fish_failure(None)
        return None
    finally:
        if own:
            try:
                await client.aclose()
            except Exception as e:      # never turn a clean None into an exception
                log.debug(f"TTS client close failed: {e}")
    if not buf:
        return None
    _clear_fish_failure()
    return SynthResult(bytes(buf), first if first is not None else 0.0, time.monotonic() - t0)


# ── provider choice ────────────────────────────────────────────────────────

def fish_configured() -> bool:
    return os.environ.get("FISH_API_KEY", "").strip() not in _FISH_PLACEHOLDERS


def piper_model_path() -> Path:
    """Where the Piper voice lives: `PIPER_MODEL` (a path to the `.onnx`), else
    `<data dir>/voices/<PIPER_VOICE>.onnx`."""
    explicit = os.environ.get("PIPER_MODEL", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    import data_paths
    import lang
    voice = os.environ.get("PIPER_VOICE", "").strip() or lang.default_piper_voice()
    return data_paths.data_dir() / "voices" / f"{voice}.onnx"


def local_available() -> bool:
    """Is a Piper voice usable right now: the package and the model file."""
    try:
        import importlib.util
        if importlib.util.find_spec("piper") is None:
            return False
    except (ImportError, ValueError):
        return False
    return piper_model_path().is_file()


# Fish can be configured and still unusable: a key without credit answers
# `402 Payment Required` to every sentence, and JARVIS used to go mute because a
# configured key outranked the local voice. A failure now moves `auto` to Piper
# for a while (when Piper is there), and the next attempt on Fish comes after.
_ACCOUNT_STATUSES = (401, 402, 403)
FISH_DOWN_ACCOUNT_SEC = 600.0      # the account will not fix itself in a minute
FISH_DOWN_TRANSIENT_SEC = 60.0     # a network blip might
_fish_down_until = 0.0


def note_fish_failure(status: Optional[int] = None) -> None:
    """Remember that Fish just failed. `status` is the HTTP status, if any."""
    global _fish_down_until
    window = FISH_DOWN_ACCOUNT_SEC if status in _ACCOUNT_STATUSES else FISH_DOWN_TRANSIENT_SEC
    now = time.monotonic()
    if now >= _fish_down_until and local_available():
        log.warning(
            f"Fish Audio failed ({status or 'network error'}"
            f"{': the key is rejected or the account has no credit' if status in _ACCOUNT_STATUSES else ''}); "
            f"speaking with the local Piper voice for {int(window)} s")
    _fish_down_until = max(_fish_down_until, now + window)


def fish_is_down() -> bool:
    return time.monotonic() < _fish_down_until


def _clear_fish_failure() -> None:
    global _fish_down_until
    _fish_down_until = 0.0


def provider() -> str:
    """`fish`, `piper` or `none`. Never raises."""
    wanted = os.environ.get("JARVIS_TTS_PROVIDER", "auto").strip().lower()
    if wanted == "fish":
        return "fish" if fish_configured() else "none"
    if wanted == "piper":
        return "piper" if local_available() else "none"
    if wanted == "none":
        return "none"
    if fish_configured():
        if fish_is_down() and local_available():
            return "piper"
        return "fish"
    return "piper" if local_available() else "none"


# ── Piper ──────────────────────────────────────────────────────────────────

_voice = None
_voice_path: Optional[Path] = None
_voice_lock = threading.Lock()
# Piper's session is not documented as safe to call from two threads at once;
# the scheduler speaks one chunk at a time anyway, so serialising costs nothing.
_synth_lock = threading.Lock()


def _load_voice():
    """Load (once) and return the Piper voice. Blocking: runs in a thread."""
    global _voice, _voice_path
    path = piper_model_path()
    with _voice_lock:
        if _voice is None or _voice_path != path:
            from piper import PiperVoice          # optional dependency
            t0 = time.monotonic()
            _voice = PiperVoice.load(str(path))
            _voice_path = path
            log.info(f"piper voice {path.name} loaded in {time.monotonic() - t0:.1f}s")
        return _voice


def _piper_wav(text: str) -> bytes:
    voice = _load_voice()
    buf = io.BytesIO()
    with _synth_lock, wave.open(buf, "wb") as out:
        voice.synthesize_wav(text, out)
    return buf.getvalue()


def warm_up_local() -> None:
    """Load the voice ahead of the first sentence (loading is ~1.5 s). Blocking;
    call from a thread. Never raises: a missing voice is a log line, not a crash."""
    try:
        if local_available():
            _load_voice()
    except Exception as e:
        log.warning(f"piper warm-up failed: {e}")


async def synthesize_local(text: str) -> Optional[SynthResult]:
    """One sentence through Piper, as a complete WAV. None on any failure,
    like `synthesize_chunk` — the caller falls back to text."""
    text = (text or "").strip()
    if not text or not local_available():
        return None
    t0 = time.monotonic()
    try:
        audio = await asyncio.to_thread(_piper_wav, text)
    except Exception as e:
        log.error(f"piper error: {e}")
        return None
    if not audio:
        return None
    took = time.monotonic() - t0
    return SynthResult(audio, took, took)
