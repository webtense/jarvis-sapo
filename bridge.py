"""The state and the rules of the MCP bridge: what another Claude Code
session may ask of JARVIS's voice, and how much.

No I/O in here. `server.py` owns the routes, the speech scheduler and the
notifier; this module owns the decisions that have to hold whatever the
caller does, so they can be tested without a server:

  * how long a text may be, and what it is cleaned of;
  * how often each kind of call may happen (a script in a loop must not
    be able to take over the voice);
  * the one question that may wait for an answer at a time;
  * the memory PROPOSALS, which are not memories until the user says so.

Everything that arrives here is UNTRUSTED. It comes from a model that may
have read a hostile web page or README, and it is spoken in JARVIS's own
voice — so it is always introduced by who said it ("La sesión X dice:"), and
the user's decisions (answer a question, confirm a memory) are read by plain
word matching in `server.py`, never by a model.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import lang

# --- length limits -------------------------------------------------------
# A text over its limit is REFUSED, not trimmed: a caller that sends a wall
# of text is either broken or hostile, and a half-spoken wall is no better.
SPEAK_MAX = 500
QUESTION_MAX = 400
NOTIFY_TITLE_MAX = 80
NOTIFY_MESSAGE_MAX = 300
FACT_MAX = 300
LABEL_MAX = 40
BODY_MAX_BYTES = 8 * 1024          # a whole request, before it is even parsed

# --- how long a question waits -------------------------------------------
ASK_TIMEOUT_DEFAULT = 45.0
ASK_TIMEOUT_MIN = 5.0
ASK_TIMEOUT_MAX = 120.0
# The most the handler waits for JARVIS to FINISH saying the question before
# the clock for the answer starts. jarvis_bridge_mcp.py's HTTP timeout is
# this plus the answer's own timeout plus margin: a handler that outlives
# its client's patience tells the session "unreachable" while the question
# is still live (the lie jarvis_mcp.py's TIMEOUT_SEC comment describes).
SPEAK_WAIT_MAX = 30.0

# --- frequency -----------------------------------------------------------
# (calls, per this many seconds). Per kind, across ALL callers: the token is
# one secret shared by every session on the machine, so one budget.
RATE_LIMITS = {
    "speak": (6, 60.0),
    "ask": (4, 60.0),
    "notify": (6, 60.0),
    "remember": (5, 300.0),
}
DEDUP_WINDOW = 30.0                # the same text twice in this long is ignored
MAX_QUEUED_SPEECH = 4              # bridge utterances waiting to be spoken
MAX_PROPOSALS = 20                 # pending memories; the oldest falls off
# A spoken "shall I keep it?" is open this long AFTER it was fully played, and
# only until the brain says something else (`note_brain_turn`). Short on
# purpose: a "yes" that late is more likely meant for somebody else.
CONFIRM_WINDOW = 15.0

# Newlines and tabs stay: the TTS cleaner turns them into pauses.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_LABEL_BAD_RE = re.compile(r"[^\w .\-]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

NO_ANSWER = "sin respuesta"


class Refused(Exception):
    """A call the bridge will not carry out; the message is for the caller."""


def clean_label(raw) -> str:
    """Who is speaking, made safe to read aloud and to put in a title.

    The label comes from the caller (a folder name by default), so it is as
    untrusted as the text: letters, digits, space, dot, dash and underscore
    only, short, never empty. It cannot smuggle a sentence into the
    "La sesión X dice:" frame.
    """
    text = _LABEL_BAD_RE.sub(" ", str(raw or ""))
    text = _SPACE_RE.sub(" ", text).strip(" .-_")[:LABEL_MAX].strip()
    return text or ("desconocida" if lang.language() == "es" else "unknown")


def clean_text(raw, limit: int, what: str,
               prepare: Optional[Callable[[str], str]] = None) -> str:
    """Untrusted text, reduced to something that can be spoken or shown.

    `prepare` is `server.strip_markdown_for_tts`, the same cleaner the
    brain's own words go through. Raises `Refused` if there is nothing left
    or the text is over its limit.
    """
    if not isinstance(raw, str):
        raise Refused(f"{what}: hace falta un texto.")
    if len(raw) > limit * 4:           # do not run regexes over a megabyte
        raise Refused(f"{what}: demasiado largo (máximo {limit} caracteres).")
    text = _CONTROL_RE.sub(" ", raw)
    if prepare is not None:
        text = prepare(text)
    text = _SPACE_RE.sub(" ", text).strip()
    if not text:
        raise Refused(f"{what}: no queda nada que decir.")
    if len(text) > limit:
        raise Refused(f"{what}: demasiado largo (máximo {limit} caracteres).")
    return text


def clamp_timeout(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return ASK_TIMEOUT_DEFAULT
    if value != value:                 # NaN
        return ASK_TIMEOUT_DEFAULT
    return max(ASK_TIMEOUT_MIN, min(ASK_TIMEOUT_MAX, value))


# --- what JARVIS says, always attributed ----------------------------------

def frame_speak(label: str, text: str) -> str:
    if lang.language() == "es":
        return f"La sesión {label} dice: {text}"
    return f"The session {label} says: {text}"


def frame_ask(label: str, text: str) -> str:
    if lang.language() == "es":
        return (f"La sesión {label} pregunta: {text} "
                f"Lo que contestes se le enviará a ella.")
    return (f"The session {label} asks: {text} "
            f"Whatever you answer will be sent to it.")


def frame_remember(label: str, text: str) -> str:
    if lang.language() == "es":
        return f"La sesión {label} propone que recuerde: {text} ¿La guardo?"
    return f"The session {label} suggests I remember: {text} Shall I keep it?"


# --- the user's words, matched as plain words -----------------------------
_YES = frozenset({"sí", "si", "vale", "guárdalo", "guardalo", "guárdala",
                  "guardala", "adelante", "confirma", "confirmo", "correcto",
                  "yes", "ok", "okay", "save", "keep", "sure", "confirm"})
_NO = frozenset({"no", "descarta", "descártalo", "descartalo", "descártala",
                 "olvídalo", "olvidalo", "rechaza", "rechazo", "nope",
                 "discard", "reject", "forget"})
_FILLER = frozenset({"por", "favor", "please", "it", "lo", "la", "que",
                     "gracias", "thanks", "thank", "you", "that", "esa",
                     "eso", "ésa", "ése"})
_CANCEL_STARTS = frozenset({"cancela", "cancelar", "cancel", "ignórala",
                            "ignorala", "ignore", "déjalo", "dejalo",
                            "never"})


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def classify_decision(text: str) -> Optional[str]:
    """'yes', 'no' or None for the user's reply to "shall I keep it?".

    Deliberately narrow: at most four words, all of them from a short list.
    A sentence about something else is NOT a decision and goes to the brain
    as usual. Plain matching on purpose — the thing deciding whether a
    proposal from an untrusted session becomes a memory is not a model.
    """
    words = _words(text)
    if not words or len(words) > 4:
        return None
    has_yes = any(w in _YES for w in words)
    has_no = any(w in _NO for w in words)
    if has_yes == has_no:              # neither, or both: ambiguous
        return None
    allowed = (_YES if has_yes else _NO) | _FILLER
    if not all(w in allowed for w in words):
        return None
    return "yes" if has_yes else "no"


def is_cancel_of_question(text: str) -> bool:
    """'Cancela', 'ignórala': the user does not want to answer it at all.
    A plain 'no' is NOT this — it is an answer to a yes/no question."""
    words = _words(text)
    return bool(words) and len(words) <= 3 and words[0] in _CANCEL_STARTS


# --- state ---------------------------------------------------------------

class RateLimiter:
    """A sliding window: at most `limit` calls in the last `window` seconds."""

    def __init__(self, limit: int, window: float,
                 clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        now = self._clock()
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True


@dataclass
class Question:
    text: str
    label: str
    future: "asyncio.Future[Optional[str]]"
    armed: bool = False                # true once JARVIS has FINISHED asking
    asked_at: float = 0.0


@dataclass
class Proposal:
    id: str
    fact: str
    label: str
    created: float
    announced_at: Optional[float] = None


class Bridge:
    """Everything the bridge remembers between calls. One per server."""

    def __init__(self, *, prepare: Optional[Callable[[str], str]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.prepare = prepare
        self._clock = clock
        self._limiters = {kind: RateLimiter(n, w, clock)
                          for kind, (n, w) in RATE_LIMITS.items()}
        self._seen: dict[tuple[str, str], float] = {}
        self._queued: list = []                    # utterances not yet spoken
        self.question: Optional[Question] = None
        self.proposals: dict[str, Proposal] = {}
        self._brain_epoch = 0          # bumped each time the brain starts talking

    # -- admission --------------------------------------------------------
    def admit(self, kind: str, text: str) -> None:
        """Raise `Refused` if this call is a repeat or over its budget.

        Repeats are checked first and do NOT spend budget: a loop sending
        the same line forever is silenced without also starving the
        well-behaved session next to it.
        """
        now = self._clock()
        self._seen = {k: t for k, t in self._seen.items()
                      if now - t < DEDUP_WINDOW}
        key = (kind, text.casefold())
        if key in self._seen:
            raise Refused("Ya lo he recibido hace un momento; lo ignoro.")
        if not self._limiters[kind].allow():
            raise Refused("Demasiado seguido: espera un poco antes de "
                          "volver a usar el puente.")
        self._seen[key] = now

    def room_to_speak(self) -> bool:
        self._queued = [u for u in self._queued if not u.done]
        return len(self._queued) < MAX_QUEUED_SPEECH

    def track(self, utterance) -> None:
        self._queued.append(utterance)

    # -- questions --------------------------------------------------------
    def open_question(self, text: str, label: str) -> Question:
        if self.question is not None:
            raise Refused("Ya hay otra pregunta esperando respuesta.")
        q = Question(text=text, label=label,
                     future=asyncio.get_running_loop().create_future(),
                     asked_at=self._clock())
        self.question = q
        return q

    def close_question(self, q: Question) -> None:
        if self.question is q:
            self.question = None

    async def wait_answer(self, q: Question, timeout: float) -> Optional[str]:
        """The user's answer, or None if it never came (or was cancelled)."""
        q.armed = True
        try:
            return await asyncio.wait_for(asyncio.shield(q.future), timeout)
        except asyncio.TimeoutError:
            return None

    def offer_answer(self, text: str, *, typed: bool = False) -> bool:
        """Hand the user's next words to the pending question, if one is
        waiting. True means the words were taken and must NOT go on to the
        brain. Spoken words only count once the question has been said
        (`armed`): before that, the microphone is hearing JARVIS himself.
        Typed words (the dashboard) have no echo and count at once."""
        q = self.question
        if q is None or q.future.done() or not (q.armed or typed):
            return False
        q.future.set_result(None if is_cancel_of_question(text) else text)
        return True

    # -- memory proposals -------------------------------------------------
    def propose(self, fact: str, label: str) -> Proposal:
        while len(self.proposals) >= MAX_PROPOSALS:
            self.proposals.pop(next(iter(self.proposals)))
        p = Proposal(id=secrets.token_hex(3), fact=fact, label=label,
                     created=self._clock())
        self.proposals[p.id] = p
        return p

    @property
    def brain_epoch(self) -> int:
        return self._brain_epoch

    def mark_announced(self, pid: str, epoch: Optional[int] = None) -> None:
        """The proposal has been SAID in full. Call it only then: queued is
        not heard, and a cancelled announcement was never asked. `epoch` is
        `brain_epoch` as read BEFORE the wait; if the brain spoke in between,
        the user's next "yes" is an answer to the brain, not to this."""
        if epoch is not None and epoch != self._brain_epoch:
            return
        p = self.proposals.get(pid)
        if p is not None:
            p.announced_at = self._clock()

    def note_brain_turn(self) -> None:
        """The brain started talking: whatever question the user may now be
        answering is the brain's. Every announced proposal stops listening for
        a yes/no (it stays pending, decidable on the dashboard)."""
        self._brain_epoch += 1
        for p in self.proposals.values():
            p.announced_at = None

    def awaiting_decision(self) -> Optional[Proposal]:
        """The proposal JARVIS last asked about, while that ask is fresh."""
        now = self._clock()
        asked = [p for p in self.proposals.values()
                 if p.announced_at is not None
                 and now - p.announced_at < CONFIRM_WINDOW]
        return max(asked, key=lambda p: p.announced_at) if asked else None

    def take(self, pid: str) -> Optional[Proposal]:
        """Remove and return a proposal: it is being decided, either way."""
        return self.proposals.pop(pid, None)

    def snapshot(self) -> dict:
        """What the dashboard shows. Texts are untrusted: it renders them
        with textContent. Carries no token and no path."""
        q = self.question
        now = self._clock()
        return {
            "question": None if q is None else {
                "text": q.text, "session": q.label, "armed": q.armed},
            "proposals": [{"id": p.id, "fact": p.fact, "session": p.label,
                           "age": int(now - p.created)}
                          for p in self.proposals.values()],
        }
