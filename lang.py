"""Which language JARVIS speaks and listens in.

`JARVIS_LANGUAGE` is `en` (upstream, the default) or `es`. It selects four
things and nothing else: the persona the brain reads, the Piper voice, the
locale the browser's speech recogniser listens for, and the unit system left
alone. Anything not listed here is not translated.
"""
import os

SUPPORTED = ("en", "es")


def language() -> str:
    """`en` or `es`. Anything unrecognised falls back to English."""
    raw = os.environ.get("JARVIS_LANGUAGE", "en").strip().lower()[:2]
    return raw if raw in SUPPORTED else "en"


def recognition_locale() -> str:
    """The BCP-47 tag the browser's SpeechRecognition is told to expect."""
    return {"es": "es-ES"}.get(language(), "en-US")


# A voice per language. Piper voice names: <lang>_<REGION>-<name>-<quality>.
PIPER_VOICES = {
    "es": "es_ES-davefx-medium",
    "en": "en_GB-alan-medium",           # a British butler, as the persona says
}


def default_piper_voice() -> str:
    return PIPER_VOICES[language()]
