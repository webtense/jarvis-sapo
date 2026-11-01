"""Router de preguntas: decide a quién se pregunta (Home Assistant, Gemini,
Claude o preguntar al usuario). Solo reglas: no llama a ningún modelo, así
que decidir es gratis y siempre se puede explicar el motivo.

Principios:
- Las órdenes de casa van a Home Assistant (local, sin modelo).
- Nunca se gasta dinero en una pregunta que no encaja: se pregunta antes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

HA = "ha"            # Home Assistant: intents locales, sin modelo
GEMINI = "gemini"    # imágenes, novedades y búsquedas recientes
CLAUDE = "claude"    # razonamiento, código y documentos
ASK = "ask"          # no encaja: preguntar al usuario antes de gastar

# Verbos imperativos de casa. Deben ir acompañados de un objeto de casa.
_HOME_VERBS = ("enciende", "apaga", "sube", "baja", "pon", "abre", "cierra", "activa", "desactiva")
_HOME_OBJECTS = ("luz", "luces", "persiana", "persianas", "enchufe", "calefaccion", "temperatura",
                 "alarma", "aire", "bomba", "wallbox", "coche", "puerta", "ventilador")
_VISION = ("camara", "foto", "que ves", "que hay en", "esta pasando", "tejado", "piscina", "parking")
_RECENT = ("hoy", "ultim", "noticia", "precio", "ahora mismo", "tiempo que hace", "cuanto cuesta")
_REASON = ("explica", "analiza", "codigo", "programa", "resume", "redacta", "por que", "compara")


@dataclass(frozen=True)
class Decision:
    route: str
    reason: str


def _normalise(text: str) -> str:
    """Minúsculas y sin tildes, para que «cámara» y «camara» cuenten igual."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(r"\b" + re.escape(w), text) for w in words)


def route(text: str) -> Decision:
    """Elige destino para una pregunta. Orden de reglas: casa, imagen, novedades, razonamiento."""
    t = _normalise(text)
    if not t:
        return Decision(ASK, "pregunta vacía")
    if _has_any(t, _HOME_VERBS) and _has_any(t, _HOME_OBJECTS):
        return Decision(HA, "orden de casa")
    if _has_any(t, _VISION):
        return Decision(GEMINI, "imagen o cámara")
    if _has_any(t, _RECENT):
        return Decision(GEMINI, "información reciente")
    if _has_any(t, _REASON) or len(t.split()) > 25:
        return Decision(CLAUDE, "razonamiento o texto largo")
    return Decision(ASK, "no encaja en ninguna regla")
