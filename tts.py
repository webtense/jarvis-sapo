"""
tts.py — speech synthesis, one request per sentence chunk.

Five providers behind one seam (`SynthResult` or None, never raises):

* **Fish Audio** (upstream's only voice): streamed so time-to-first-byte can be
  measured; the chunk is returned whole because the browser decodes one
  complete MP3 per chunk.
* **Piper** (jarvis-ubuntu): a local neural voice, no network and no key. One
  complete WAV per chunk, which `decodeAudioData` takes just as well as MP3.
  Piper is an OPTIONAL dependency (`requirements-linux.txt`); everything here
  imports it lazily so the rest of JARVIS never needs it.

* **Kokoro** (jarvis-ubuntu, local, optional): `kokoro-onnx` plus a ~90 MB
  model. Never chosen by `auto` while Piper is there; opt in with
  `JARVIS_TTS_PROVIDER=kokoro`.
* **Edge** (jarvis-ubuntu, CLOUD, optional): Microsoft's online voices through
  the unofficial `edge-tts` package. The text of every sentence LEAVES THE
  MACHINE. Strictly opt-in (`JARVIS_TTS_PROVIDER=edge`): `auto` never picks it.
  It is not an official API and may stop working; a failure falls back to
  Piper.

`provider()` picks: `JARVIS_TTS_PROVIDER` (`auto` | `fish` | `piper` | `kokoro` |
`edge` | `none`) when set. `auto` (the default) is Fish if a real key is
configured, otherwise Piper if it is usable, otherwise Kokoro if it is usable,
otherwise `none` — text only, as before. `synthesize()` runs the cascade: the
chosen provider first, then the local voice if it fails.

The voice is `JARVIS_TTS_VOICE` (a Piper, Kokoro or Edge name: each provider
only takes the value if it has that shape), else `PIPER_VOICE` for Piper, else
the language's default.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
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
    provider: str = ""          # who actually made it ("fish", "piper"...)


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
    return SynthResult(bytes(buf), first if first is not None else 0.0, time.monotonic() - t0, "fish")


# ── provider choice ────────────────────────────────────────────────────────

PROVIDERS = ("fish", "piper", "kokoro", "edge")
CHOICES = ("auto",) + PROVIDERS + ("none",)
# Providers whose audio never leaves the machine, in the order `auto` tries them.
LOCAL_PROVIDERS = ("piper", "kokoro")
# Providers that send the TEXT of every sentence to someone else's server.
CLOUD_PROVIDERS = ("fish", "edge")


def wanted() -> str:
    """What `JARVIS_TTS_PROVIDER` asks for; anything unknown is `auto`."""
    raw = os.environ.get("JARVIS_TTS_PROVIDER", "auto").strip().lower()
    return raw if raw in CHOICES else "auto"


def fish_configured() -> bool:
    return os.environ.get("FISH_API_KEY", "").strip() not in _FISH_PLACEHOLDERS


def _has_package(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# One variable for the voice, three name shapes that cannot be confused:
#   Piper   es_ES-davefx-medium     (lang_REGION-name-quality)
#   Kokoro  em_alex                 (two letters, underscore, name)
#   Edge    es-ES-AlvaroNeural
# A provider only takes `JARVIS_TTS_VOICE` when it has ITS shape, so choosing a
# Kokoro voice never breaks the Piper fallback, and the value can never be a
# path (no slash is allowed by any of the three).
_PIPER_VOICE_RE = re.compile(r"[a-z]{2}_[A-Z]{2}-[A-Za-z0-9_]+-(?:x_low|low|medium|high)")
_KOKORO_VOICE_RE = re.compile(r"[a-z]{2}_[a-z]+")
_EDGE_VOICE_RE = re.compile(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural")
VOICE_SHAPES = {"piper": _PIPER_VOICE_RE, "kokoro": _KOKORO_VOICE_RE, "edge": _EDGE_VOICE_RE}

# Voices offered in Settings, per language. Piper's are the files on disk.
KOKORO_VOICES = {
    "es": ("em_alex", "ef_dora", "em_santa"),
    "en": ("bm_george", "bm_daniel", "bf_emma", "bf_isabella"),
}
KOKORO_DEFAULT = {"es": "em_alex", "en": "bm_george"}
EDGE_VOICES = {
    "es": ("es-ES-AlvaroNeural", "es-ES-ElviraNeural"),
    "en": ("en-GB-RyanNeural", "en-GB-SoniaNeural"),
}
EDGE_DEFAULT = {"es": "es-ES-AlvaroNeural", "en": "en-GB-RyanNeural"}


def _voice_setting(provider_name: str) -> str:
    """`JARVIS_TTS_VOICE` if it is shaped like a voice of `provider_name`."""
    value = os.environ.get("JARVIS_TTS_VOICE", "").strip()
    return value if value and VOICE_SHAPES[provider_name].fullmatch(value) else ""


def _voices_dir() -> Path:
    import data_paths
    return data_paths.data_dir() / "voices"


def piper_voices() -> list[str]:
    """Names of the Piper voices installed (model and its .json side by side)."""
    try:
        return sorted(p.name[:-len(".onnx")] for p in _voices_dir().glob("*.onnx")
                      if p.with_name(p.name + ".json").is_file()
                      and _PIPER_VOICE_RE.fullmatch(p.name[:-len(".onnx")]))
    except OSError:
        return []


def piper_voice_name(voice: Optional[str] = None) -> str:
    """The Piper voice that will speak, by name (for display)."""
    return piper_model_path(voice).name[:-len(".onnx")]


def piper_model_path(voice: Optional[str] = None) -> Path:
    """Where the Piper voice lives. `voice` (a name, for a trial) wins; then
    `JARVIS_TTS_VOICE` if it is a Piper voice that is installed; then
    `PIPER_MODEL` (a path to the `.onnx`); then `PIPER_VOICE`; then the
    language's default, all under `<data dir>/voices/`."""
    if voice is not None:
        name = voice if _PIPER_VOICE_RE.fullmatch(voice) else "invalid"
        return _voices_dir() / f"{name}.onnx"
    chosen = _voice_setting("piper")
    if chosen and (_voices_dir() / f"{chosen}.onnx").is_file():
        return _voices_dir() / f"{chosen}.onnx"
    explicit = os.environ.get("PIPER_MODEL", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    import lang
    name = os.environ.get("PIPER_VOICE", "").strip() or lang.default_piper_voice()
    return _voices_dir() / f"{name}.onnx"


def local_available(voice: Optional[str] = None) -> bool:
    """Is a Piper voice usable right now: the package and the model file."""
    if not _has_package("piper"):
        return False
    return piper_model_path(voice).is_file()


# ── Kokoro: where things are and whether they can run ─────────────────────

KOKORO_MODEL_FILES = ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx")
KOKORO_VOICES_FILE = "voices-v1.0.bin"


def kokoro_dir() -> Path:
    explicit = os.environ.get("KOKORO_DIR", "").strip()
    return Path(explicit).expanduser() if explicit else _voices_dir() / "kokoro"


def kokoro_model_path() -> Optional[Path]:
    """The quantised model if it is there (a third of the size), else the full one."""
    for name in KOKORO_MODEL_FILES:
        path = kokoro_dir() / name
        if path.is_file():
            return path
    return None


def kokoro_available() -> bool:
    return (_has_package("kokoro_onnx") and kokoro_model_path() is not None
            and (kokoro_dir() / KOKORO_VOICES_FILE).is_file())


def kokoro_voice_name() -> str:
    import lang
    return _voice_setting("kokoro") or KOKORO_DEFAULT[lang.language()]


# espeak language tag by the first letter of a Kokoro voice name.
_KOKORO_LANG = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
                "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}


# ── Edge: cloud, opt-in, unofficial ───────────────────────────────────────

def edge_available() -> bool:
    return _has_package("edge_tts")


def edge_voice_name() -> str:
    import lang
    return _voice_setting("edge") or EDGE_DEFAULT[lang.language()]


EDGE_DOWN_SEC = 120.0          # after a failure, go straight to Piper for a while
EDGE_TIMEOUT_SEC = 12.0
_edge_down_until = 0.0
_edge_warned = False


def edge_is_down() -> bool:
    return time.monotonic() < _edge_down_until


def _note_edge_failure() -> None:
    global _edge_down_until
    _edge_down_until = time.monotonic() + EDGE_DOWN_SEC


def _clear_edge_failure() -> None:
    global _edge_down_until
    _edge_down_until = 0.0


# Fish can be configured and still unusable: a key without credit answers
# `402 Payment Required` to every sentence, and JARVIS used to go mute because a
# configured key outranked the local voice. A failure now moves `auto` to Piper
# for a while (when Piper is there), and the next attempt on Fish comes after.
_ACCOUNT_STATUSES = (401, 402, 403)
FISH_DOWN_ACCOUNT_SEC = 600.0      # the account will not fix itself in a minute
FISH_DOWN_TRANSIENT_SEC = 60.0     # a network blip might
_fish_down_until = 0.0
_fish_down_account = False         # the last failure was the key/credit, not the network


def note_fish_failure(status: Optional[int] = None) -> None:
    """Remember that Fish just failed. `status` is the HTTP status, if any."""
    global _fish_down_until, _fish_down_account
    window = FISH_DOWN_ACCOUNT_SEC if status in _ACCOUNT_STATUSES else FISH_DOWN_TRANSIENT_SEC
    now = time.monotonic()
    if now >= _fish_down_until and _first_local() is not None:
        log.warning(
            f"Fish Audio failed ({status or 'network error'}"
            f"{': the key is rejected or the account has no credit' if status in _ACCOUNT_STATUSES else ''}); "
            f"speaking with the local voice for {int(window)} s")
    _fish_down_until = max(_fish_down_until, now + window)
    _fish_down_account = status in _ACCOUNT_STATUSES


def fish_is_down() -> bool:
    return time.monotonic() < _fish_down_until


def fish_down_is_account() -> bool:
    """While Fish is down: is it the key or the credit (True), or the network?"""
    return fish_is_down() and _fish_down_account


def _clear_fish_failure() -> None:
    global _fish_down_until
    _fish_down_until = 0.0


def _first_local() -> Optional[str]:
    """The local voice that can speak right now, Piper before Kokoro."""
    if local_available():
        return "piper"
    if kokoro_available():
        return "kokoro"
    return None


def provider() -> str:
    """`fish`, `piper`, `kokoro`, `edge` or `none`. Never raises.

    `edge` is returned only when `JARVIS_TTS_PROVIDER=edge` says so. A forced
    Kokoro or Edge that cannot run (package or model missing) answers with the
    local voice instead of going mute; forced Piper or Fish keep their old
    meaning (unavailable means `none`)."""
    asked = wanted()
    if asked == "fish":
        return "fish" if fish_configured() else "none"
    if asked == "piper":
        return "piper" if local_available() else "none"
    if asked == "none":
        return "none"
    if asked == "kokoro":
        return "kokoro" if kokoro_available() else (_first_local() or "none")
    if asked == "edge":
        return "edge" if edge_available() else (_first_local() or "none")
    local = _first_local()
    if fish_configured():
        if fish_is_down() and local:
            return local
        return "fish"
    return local or "none"


def effective_provider() -> str:
    """What the NEXT sentence will really be said with: `provider()` after the
    outage windows (a rejected Edge goes to Piper for a couple of minutes)."""
    chosen = provider()
    if chosen == "edge" and edge_is_down() and local_available():
        return "piper"
    return chosen


def voice_name(which: str) -> str:
    """The voice `which` would use, for display. Empty when it has none."""
    if which == "piper":
        return piper_voice_name() if local_available() else ""
    if which == "kokoro":
        return kokoro_voice_name()
    if which == "edge":
        return edge_voice_name()
    return ""


def voices_for(which: str) -> list[str]:
    """The voices Settings offers for `which`."""
    import lang
    if which == "piper":
        return piper_voices()
    if which == "kokoro":
        return list(KOKORO_VOICES[lang.language()])
    if which == "edge":
        return list(EDGE_VOICES[lang.language()])
    return []


def status() -> dict:
    """Everything Settings shows about the voice, with no wording of its own:
    the server turns the flags into sentences (and the page into labels)."""
    available = {
        "fish": fish_configured(),
        "piper": local_available() or bool(piper_voices()) and _has_package("piper"),
        "kokoro": kokoro_available(),
        "edge": edge_available(),
    }
    return {
        "wanted": wanted(),
        "provider": provider(),
        "in_use": effective_provider(),
        "last_used": _last_used,
        "fish_down": fish_configured() and fish_is_down(),
        "fish_down_account": fish_configured() and fish_down_is_account(),
        "edge_down": edge_is_down(),
        "providers": [
            {"id": name, "available": available[name], "cloud": name in CLOUD_PROVIDERS,
             "voices": voices_for(name), "voice": voice_name(name)}
            for name in PROVIDERS
        ],
    }


# ── Piper ──────────────────────────────────────────────────────────────────

_voice = None
_voice_path: Optional[Path] = None
_voice_lock = threading.Lock()
# Piper's session is not documented as safe to call from two threads at once;
# the scheduler speaks one chunk at a time anyway, so serialising costs nothing.
_synth_lock = threading.Lock()
_last_used = ""                # the provider that made the last audio


def _load_voice(path: Optional[Path] = None):
    """Load (once) and return the Piper voice. Blocking: runs in a thread."""
    global _voice, _voice_path
    path = path or piper_model_path()
    with _voice_lock:
        if _voice is None or _voice_path != path:
            from piper import PiperVoice          # optional dependency
            t0 = time.monotonic()
            _voice = PiperVoice.load(str(path))
            _voice_path = path
            log.info(f"piper voice {path.name} loaded in {time.monotonic() - t0:.1f}s")
        return _voice


def piper_length_scale() -> float:
    """How slowly Piper speaks: `PIPER_LENGTH_SCALE`, 1.0 (the voice's own pace)
    by default, clamped to 0.7-1.6 (above 1 is slower). Anything that is not a
    number is 1.0. Read every time, so the admin screen can change it live."""
    try:
        value = float(os.environ.get("PIPER_LENGTH_SCALE", "1.0"))
    except ValueError:
        return 1.0
    if value != value:                                  # NaN
        return 1.0
    return min(1.6, max(0.7, value))


def _piper_synthesis_config():
    scale = piper_length_scale()
    if scale == 1.0:
        return None                                      # the untouched, original path
    from piper import SynthesisConfig                    # optional dependency
    return SynthesisConfig(length_scale=scale)


def _piper_wav(text: str, voice: Optional[str] = None) -> bytes:
    loaded = _load_voice(piper_model_path(voice) if voice is not None else None)
    buf = io.BytesIO()
    config = _piper_synthesis_config()
    with _synth_lock, wave.open(buf, "wb") as out:
        if config is None:
            loaded.synthesize_wav(text, out)
        else:
            loaded.synthesize_wav(text, out, syn_config=config)
    return buf.getvalue()


def warm_up_local() -> None:
    """Load the local voice ahead of the first sentence (Piper ~1.5 s, Kokoro
    ~1 s). Blocking; call from a thread. Never raises: a missing voice is a log
    line, not a crash."""
    try:
        if provider() == "kokoro":
            _load_kokoro()
        elif local_available():
            _load_voice()
    except Exception as e:
        log.warning(f"local voice warm-up failed: {e}")


async def synthesize_local(text: str, voice: Optional[str] = None) -> Optional[SynthResult]:
    """One sentence through Piper, as a complete WAV. None on any failure,
    like `synthesize_chunk` — the caller falls back to text. `voice` names a
    Piper voice for a trial (Settings); omitted, the configured one."""
    text = (text or "").strip()
    if not text or not (local_available() if voice is None else local_available(voice)):
        return None
    t0 = time.monotonic()
    try:
        audio = await asyncio.to_thread(_piper_wav, text) if voice is None \
            else await asyncio.to_thread(_piper_wav, text, voice)
    except Exception as e:
        log.error(f"piper error: {e}")
        return None
    if not audio:
        return None
    took = time.monotonic() - t0
    return SynthResult(audio, took, took, "piper")


# ── Kokoro ─────────────────────────────────────────────────────────────────

_kokoro = None
_kokoro_path: Optional[Path] = None
_kokoro_lock = threading.Lock()
_kokoro_synth_lock = threading.Lock()


def _load_kokoro():
    """Load (once) and return the Kokoro engine. Blocking: runs in a thread."""
    global _kokoro, _kokoro_path
    path = kokoro_model_path()
    with _kokoro_lock:
        if _kokoro is None or _kokoro_path != path:
            from kokoro_onnx import Kokoro        # optional dependency
            t0 = time.monotonic()
            _kokoro = Kokoro(str(path), str(kokoro_dir() / KOKORO_VOICES_FILE))
            _kokoro_path = path
            log.info(f"kokoro model {path.name} loaded in {time.monotonic() - t0:.1f}s")
        return _kokoro


def _pcm16(samples) -> bytes:
    """Float samples in [-1, 1] as little-endian 16-bit PCM. numpy when the
    engine hands back an array (it always does), plain Python otherwise."""
    if hasattr(samples, "astype"):
        import numpy as np                        # a dependency of kokoro-onnx
        return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    import array
    import sys
    pcm = array.array("h", (int(max(-1.0, min(1.0, float(x))) * 32767) for x in samples))
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm.tobytes()


def _kokoro_wav(text: str, voice: str) -> bytes:
    engine = _load_kokoro()
    with _kokoro_synth_lock:
        samples, rate = engine.create(text, voice=voice, speed=1.0,
                                      lang=_KOKORO_LANG.get(voice[:1], "en-us"))
    if len(samples) == 0:
        return b""
    pcm = _pcm16(samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(pcm)
    return buf.getvalue()


async def synthesize_kokoro(text: str, voice: Optional[str] = None) -> Optional[SynthResult]:
    """One sentence through Kokoro, as a complete WAV. None on any failure."""
    text = (text or "").strip()
    if voice is not None and not _KOKORO_VOICE_RE.fullmatch(voice):
        return None
    if not text or not kokoro_available():
        return None
    t0 = time.monotonic()
    try:
        audio = await asyncio.to_thread(_kokoro_wav, text, voice or kokoro_voice_name())
    except Exception as e:
        log.error(f"kokoro error: {e}")
        return None
    if not audio:
        return None
    took = time.monotonic() - t0
    return SynthResult(audio, took, took, "kokoro")


# ── Edge (cloud) ───────────────────────────────────────────────────────────

async def _edge_mp3(text: str, voice: str) -> tuple[bytes, float]:
    import edge_tts                               # optional dependency
    t0 = time.monotonic()
    first = 0.0
    buf = bytearray()
    async for part in edge_tts.Communicate(text, voice).stream():
        if part.get("type") == "audio" and part.get("data"):
            if not buf:
                first = time.monotonic() - t0
            buf.extend(part["data"])
    return bytes(buf), first


async def synthesize_edge(text: str, voice: Optional[str] = None) -> Optional[SynthResult]:
    """One sentence through Microsoft's online voices, as an MP3. None on any
    failure (offline, the service changed, the package is missing): this is an
    unofficial API and it WILL break one day. The TEXT is sent to Microsoft."""
    global _edge_warned
    text = (text or "").strip()
    if voice is not None and not _EDGE_VOICE_RE.fullmatch(voice):
        return None
    if not text or not edge_available():
        return None
    if not _edge_warned:
        _edge_warned = True
        log.warning("Edge TTS is a cloud voice: the text of each sentence is sent to Microsoft")
    t0 = time.monotonic()
    try:
        audio, first = await asyncio.wait_for(
            _edge_mp3(text, voice or edge_voice_name()), EDGE_TIMEOUT_SEC)
    except Exception as e:                        # includes asyncio.TimeoutError
        log.error(f"edge error: {type(e).__name__}")
        _note_edge_failure()
        return None
    if not audio:
        _note_edge_failure()
        return None
    _clear_edge_failure()
    return SynthResult(audio, first, time.monotonic() - t0, "edge")


# ── the cascade ────────────────────────────────────────────────────────────

def _cascade() -> list[str]:
    """Providers to try for the next sentence, in order. The chosen one first,
    then the local voice, so that a provider failing never means silence:
    Fish falls back only when `auto` chose it (forcing Fish means Fish), and an
    Edge that failed a moment ago is skipped, not waited on, when Piper is there."""
    chosen = provider()
    if chosen in ("none", ""):
        return []
    order = [chosen]
    if chosen == "fish":
        if wanted() == "auto":
            order += [p for p in LOCAL_PROVIDERS if _usable(p)]
    elif chosen in ("kokoro", "edge") and local_available():
        order.append("piper")
    if chosen == "edge" and edge_is_down() and len(order) > 1:
        order.remove("edge")
    return order


def _usable(name: str) -> bool:
    return local_available() if name == "piper" else kokoro_available()


async def synthesize_with(name: str, text: str, *, voice: Optional[str] = None,
                          fish_key: str = "", fish_voice_id: str = "",
                          client: Optional[httpx.AsyncClient] = None) -> Optional[SynthResult]:
    """One sentence through exactly `name`, no fallback."""
    if name == "piper":
        return await synthesize_local(text, voice)
    if name == "kokoro":
        return await synthesize_kokoro(text, voice)
    if name == "edge":
        return await synthesize_edge(text, voice)
    if name == "fish":
        return await synthesize_chunk(text, api_key=fish_key, voice_id=fish_voice_id, client=client)
    return None


async def synthesize(text: str, *, fish_key: str = "", fish_voice_id: str = "",
                     client: Optional[httpx.AsyncClient] = None) -> Optional[SynthResult]:
    """One sentence through the chosen provider, falling back to the local voice
    if it fails (see `_cascade`). None when nothing could speak it."""
    global _last_used
    for name in _cascade():
        result = await synthesize_with(name, text, fish_key=fish_key,
                                       fish_voice_id=fish_voice_id, client=client)
        if result is not None:
            _last_used = result.provider or name
            return result
    return None
