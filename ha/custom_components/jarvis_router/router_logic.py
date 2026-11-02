"""Lógica pura del agente «JARVIS Router» para Assist.

Sin Home Assistant ni red: decide qué hacer con cada frase. Lo que toca el
mundo (llamar al router, a Gemini o a Home Assistant) vive en conversation.py.

Flujo de un turno:
1. Si hay una pregunta pendiente («¿a Gemini o a Claude?») y la frase es una
   elección, se reenvía la pregunta original a ese destino.
2. Si la frase es una valoración («bien» / «mal») y hay una última respuesta
   reciente, se guarda en la base de datos del router.
3. Si no, se pide destino al router de JARVIS y se actúa según la ruta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

HA_AGENT = "conversation.home_assistant"
GEMINI_AGENT = "conversation.google_ai_conversation"

ROUTE_HA = "ha"
ROUTE_GEMINI = "gemini"
ROUTE_CLAUDE = "claude"
ROUTE_ASK = "ask"

PENDING_TTL = 120.0  # segundos para contestar a «¿a quién lo pregunto?»
RATE_TTL = 600.0     # segundos para valorar la última respuesta

CLAUDE_NOTE = "Claude todavía no está conectado a Assist; te contesta Gemini."
ROUTER_DOWN_NOTE = "El router de JARVIS no responde; te contesta Gemini."
ASK_TEXT = "No tengo claro a quién preguntarlo. ¿Lo pregunto a Gemini o a Claude?"
RATED_TEXT = "Anotado, gracias."
RATE_FAILED_TEXT = "No he podido guardar la valoración."

_GOOD = {"bien", "muy bien", "perfecto", "genial", "correcto", "bien hecho",
         "buena", "muy buena", "vale bien"}
_BAD = {"mal", "muy mal", "fatal", "incorrecto", "mala", "no vale", "no sirve",
        "no era eso"}
_CLAUDE_WORDS = {"claude", "anthropic"}
_GEMINI_WORDS = {"gemini", "google"}
_MAX_CHOICE_WORDS = 6  # una elección es una frase corta; lo demás es una pregunta nueva


@dataclass(frozen=True)
class Forward:
    """Reenviar `text` al agente `agent` y devolver su respuesta."""

    agent: str
    text: str
    note: str = ""
    question_id: int | None = None


@dataclass(frozen=True)
class Ask:
    """Preguntar al usuario a qué destino va la pregunta."""

    question: str
    question_id: int | None


@dataclass(frozen=True)
class Rate:
    """Guardar si la última respuesta fue buena."""

    question_id: int
    good: bool


@dataclass
class Pending:
    question: str
    question_id: int | None
    at: float


@dataclass
class Last:
    question_id: int | None
    at: float


@dataclass
class DialogState:
    """Estado de una conversación de Assist. Vive solo en memoria."""

    pending: Pending | None = None
    last: Last | None = None


def normalise(text: str) -> str:
    """Minúsculas, sin tildes ni signos, con espacios simples."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", plain).split())


def parse_rating(text: str) -> bool | None:
    """True si la frase valora bien, False si mal, None si no es una valoración."""
    t = normalise(text)
    if t in _GOOD:
        return True
    if t in _BAD:
        return False
    return None


def parse_choice(text: str) -> str | None:
    """Destino elegido por el usuario («Gemini», «a Claude»), o None."""
    words = normalise(text).split()
    if not words or len(words) > _MAX_CHOICE_WORDS:
        return None
    wants_claude = bool(_CLAUDE_WORDS & set(words))
    wants_gemini = bool(_GEMINI_WORDS & set(words))
    if wants_claude == wants_gemini:  # ninguno o los dos: no es una elección clara
        return None
    return ROUTE_CLAUDE if wants_claude else ROUTE_GEMINI


def forward_for(route: str, text: str, question_id: int | None) -> Forward:
    """Agente real para una ruta. Claude no es agente de Assist: se avisa y responde Gemini."""
    if route == ROUTE_HA:
        return Forward(HA_AGENT, text, "", question_id)
    if route == ROUTE_GEMINI:
        return Forward(GEMINI_AGENT, text, "", question_id)
    if route == ROUTE_CLAUDE:
        return Forward(GEMINI_AGENT, text, CLAUDE_NOTE, question_id)
    raise ValueError(f"ruta sin reenvío: {route}")


def router_unavailable(text: str) -> Forward:
    """Si el router de JARVIS no responde, se contesta igualmente con Gemini."""
    return Forward(GEMINI_AGENT, text, ROUTER_DOWN_NOTE, None)


def plan_followup(text: str, state: DialogState, now: float) -> Forward | Rate | None:
    """Trata una frase que puede ser respuesta a una pregunta o una valoración.

    Devuelve None si la frase es una pregunta nueva (hay que pedir destino al
    router). Modifica `state`: limpia la pregunta pendiente o la última respuesta
    cuando ya se han usado o han caducado.
    """
    if state.pending is not None:
        pending = state.pending
        state.pending = None  # una pregunta pendiente se contesta una vez
        if now - pending.at <= PENDING_TTL:
            choice = parse_choice(text)
            if choice is not None:
                return forward_for(choice, pending.question, pending.question_id)
        # caducada o la frase no es una elección: se trata como pregunta nueva

    rating = parse_rating(text)
    if rating is not None and state.last is not None:
        last = state.last
        state.last = None
        if last.question_id is not None and now - last.at <= RATE_TTL:
            return Rate(last.question_id, rating)
    return None
