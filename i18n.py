"""Translating JARVIS's fixed phrases, at the boundary they leave through.

The server's own sentences are written in English, with "sir", and a few
hundred tests pin them byte for byte. Rewriting them in place would break all
of that and fork the upstream code on every line. So the sentences stay as they
are and `t()` translates them where they LEAVE:

  * the result of every tool, on its way back to the brain (`_tool_reply`),
  * everything handed to `speech.say` / the announcers, BEFORE the scheduler
    records it. The echo filter compares what the microphone hears with what
    was said, so the text it remembers has to be in the language of the audio.
    Translating after registering would make it compare Spanish ears against
    English memory and every echo would be heard as the user,
  * the preflight summary and the settings API's error messages.

With `JARVIS_LANGUAGE=en` (the default) `t()` returns its argument untouched:
the same object, no regex run. With `es` it looks the sentence up in
`i18n_es.PAIRS` and returns the translation, or the original when there is no
entry. It never raises: a missing phrase is an English sentence, not a crash.

What is NOT translated, on purpose: anything the brain, a transcript, a repo,
a web page, Home Assistant or another session wrote. Those are untrusted data.
A template placeholder only ever COPIES what it captured, so a sentence that
carries someone else's text around it is translated and the text is not.

Placeholders (the kind is written in the English half; the Spanish half only
says `{name}`):
  `{name}`      one or more characters, copied verbatim.
  `{name:t}`    like `{name}`, but the capture is itself run through `t()`; for
                a part that is a whole sentence of ours (a run description).
  `{name:opt}`  zero or more characters, copied verbatim; for a trailing block
                that may be absent (an untrusted wrapper after the sentence).
  `{name:req}`  like `:t`, but the pair only applies when the capture really
                was translated; it is what lets a catch-all frame such as
                "{clause}, sir." exist without half-translating strangers.

A few things that are not sentences on their own (ages, numbers, a list's
"and") are translated by the small helpers that build them, so that every
frame around them can still be matched whole.
"""
from __future__ import annotations

import logging
import os
import re

import lang

log = logging.getLogger("jarvis.i18n")

_PLACEHOLDER = re.compile(r"\{(\w+)(?::(t|opt|req))?\}")
_MAX_DEPTH = 3

# (regex, spanish template, kinds) once compiled; None until first use. The
# kind of each placeholder (`t`, `opt`, `req`) is written once, in the English
# half, and read from there: the Spanish half just says `{name}`.
_compiled: list | None = None
_exact: dict[str, str] | None = None


def _compile_template(english: str) -> tuple[re.Pattern, int, dict[str, str]]:
    """The anchored regex for one English template, how much literal text it
    carries (the more, the more specific, the earlier it is tried) and the kind
    of each of its placeholders."""
    kinds: dict[str, str] = {}
    parts: list[str] = []
    literal = 0
    pos = 0
    for m in _PLACEHOLDER.finditer(english):
        chunk = english[pos:m.start()]
        literal += len(chunk)
        parts.append(re.escape(chunk))
        kinds[m.group(1)] = m.group(2) or ""
        quantifier = ".*?" if m.group(2) == "opt" else ".+?"
        parts.append(f"(?P<{m.group(1)}>{quantifier})")
        pos = m.end()
    tail = english[pos:]
    literal += len(tail)
    parts.append(re.escape(tail))
    return re.compile("".join(parts), re.DOTALL), literal, kinds


def _load() -> None:
    global _compiled, _exact
    import i18n_es
    exact: dict[str, str] = {}
    templated = []
    for english, spanish in i18n_es.PAIRS:
        if _PLACEHOLDER.search(english):
            regex, literal, kinds = _compile_template(english)
            templated.append((literal, regex, spanish, kinds))
        else:
            exact.setdefault(english, spanish)
    templated.sort(key=lambda item: -item[0])        # stable: ties keep catalogue order
    _exact = exact
    _compiled = [(regex, spanish, kinds) for _literal, regex, spanish, kinds in templated]


def _fill(spanish: str, match: re.Match, kinds: dict[str, str], depth: int) -> str | None:
    """The Spanish template with each placeholder replaced by what the English
    one captured (translated again only when marked `:t` or `:req`). None when
    a `:req` capture had no translation, so the caller tries the next pair."""
    failed = False

    def one(m: re.Match) -> str:
        nonlocal failed
        captured = match.group(m.group(1)) or ""
        kind = kinds.get(m.group(1), "")
        if kind in ("t", "req"):
            done = _translate(captured, depth + 1)
            if kind == "req" and done == captured:
                failed = True
            return done
        return captured

    filled = _PLACEHOLDER.sub(one, spanish)
    return None if failed else filled


def _translate(text: str, depth: int) -> str:
    if depth > _MAX_DEPTH or not text:
        return text
    assert _exact is not None and _compiled is not None
    hit = _exact.get(text)
    if hit is not None:
        return _honorific_es(hit)
    for regex, spanish, kinds in _compiled:
        m = regex.fullmatch(text)
        if m:
            filled = _fill(_honorific_es(spanish), m, kinds, depth)
            if filled is not None:
                return filled
    return text


# ---------------------------------------------------------------------------
# The form of address ("señor" / "señora" / none)
# ---------------------------------------------------------------------------
#
# The Settings panel stores `HONORIFIC` as `sir` (the default), `ma'am`,
# `none` or `name`. The catalogue is written with "señor"; the preference is
# applied to the SPANISH TEMPLATE, before any captured text is spliced in, so
# a project or a file that happens to be called "señor" is never rewritten.
# English is never touched: with `JARVIS_LANGUAGE=en` this code does not run
# at all.
#
# `name` replaces "señor" with the user's own name (`USER_NAME`), read and
# sanitised here — the same shape of check `brain.plain_phrase` applies to the
# same value before it reaches the brain's system prompt, kept local rather
# than imported so this module stays free of a dependency on `brain.py` for
# three lines of regex. `USER_NAME` is configuration, not something a turn's
# own untrusted content could rewrite, but it is still somebody's typed text
# and gets no special treatment for being short: a blank or unusable name
# makes `name` behave exactly like `none`.

_SENOR = re.compile(r"\bseñor\b")
_SENOR_CI = re.compile(r"\bseñor\b", re.IGNORECASE)
_COMMA_SENOR = re.compile(r",\s*señor\b")
_MADAM = frozenset({"ma'am", "ma’am", "maam", "madam", "señora", "senora", "female"})
_NONE = frozenset({"none", "ninguno", "ninguna", "no", "off"})
_NAME_MODE = frozenset({"name", "nombre"})

# A name is a short, ordinary phrase: letters (with accents), spaces, and a
# handful of punctuation marks a real name can contain ("O'Brien", "Jean-Luc").
# No control character, no line break, nothing `str.splitlines()` would split
# on — the same shape `brain._PLAIN_PHRASE_RE` holds `USER_NAME` to before it
# reaches the brain's own system prompt.
_NAME_RE = re.compile(r"\w([\w ,.\-/+']{0,62}\w)?")


def honorific() -> str:
    """`sir`, `madam`, `none` or `name`, read from `HONORIFIC` each time (the
    settings endpoint updates the environment live). Anything unrecognised is
    `sir`."""
    raw = os.environ.get("HONORIFIC", "sir").strip().lower()
    if raw in _MADAM:
        return "madam"
    if raw in _NONE:
        return "none"
    if raw in _NAME_MODE:
        return "name"
    return "sir"


def _clean_user_name() -> str:
    """`USER_NAME`, sanitised: an ordinary short phrase, or "" for anything
    else (empty, a line break, a control character, too long). "" is not a
    placeholder here — the caller treats it exactly as having no name at
    all, never as a literal empty word spliced into a sentence."""
    raw = os.environ.get("USER_NAME", "").strip()
    if not raw:
        return ""
    m = _NAME_RE.fullmatch(raw)
    return m.group(0) if m else ""


def _honorific_es(spanish: str) -> str:
    """The Spanish template with "señor" turned into "señora", replaced by
    the user's own name, or dropped together with the comma that introduces
    it ("Hecho, señor: listo" -> "Hecho: listo"), according to the
    preference."""
    if "señor" not in spanish:
        return spanish
    mode = honorific()
    if mode == "sir":
        return spanish
    if mode == "madam":
        return _SENOR.sub("señora", spanish)
    if mode == "name":
        name = _clean_user_name()
        if name:
            return _SENOR_CI.sub(name, spanish)
        mode = "none"
    return re.sub(r" {2,}", " ", _SENOR.sub("", _COMMA_SENOR.sub("", spanish)))


# Longest text `t()` will look at; real phrases are far shorter.
MAX_TRANSLATED_CHARS = 1500


def t(text):
    """`text` in the spoken language: identity unless `JARVIS_LANGUAGE=es`."""
    if not isinstance(text, str) or lang.language() != "es":
        return text
    if len(text) > MAX_TRANSLATED_CHARS:
        # A sentence JARVIS says is short. Anything longer is content (a file,
        # a page, a listing) that only happens to pass through a tool result,
        # and the patterns are quadratic on it: 60 KB blocked the event loop
        # for ~2 s when measured. Data is never translated anyway.
        return text
    try:
        if _compiled is None:
            _load()
        return _translate(text, 0)
    except Exception:                                # never fail a sentence over its language
        log.warning("i18n: could not translate a phrase", exc_info=True)
        return text


# ---------------------------------------------------------------------------
# Small helpers for the fragments the server builds itself
# ---------------------------------------------------------------------------

def is_es() -> bool:
    return lang.language() == "es"


def pick(english: str, spanish: str) -> str:
    """One of two words for a fallback that has no single good translation
    ("that" is "ese nombre" in one sentence and "ese navegador" in another).
    English, the default, gets its own text back untouched."""
    return spanish if is_es() else english


# Feminine forms ("una") because what gets counted aloud is almost always
# tareas, conversaciones or ejecuciones. Only "one" changes with the gender of
# what is counted: "un proyecto", "una tarea", and "uno" when nothing follows
# it ("y uno más").
NUMBER_WORDS_ES = {
    1: "una", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco",
    6: "seis", 7: "siete", 8: "ocho", 9: "nueve",
}
NUMBER_WORDS_ES_MASC = {**NUMBER_WORDS_ES, 1: "un"}
NUMBER_WORDS_ES_ALONE = {**NUMBER_WORDS_ES, 1: "uno"}


def number_word(n: int, form: str = "f") -> str:
    """A small count in words: `f` (feminine, the default), `m` (before a
    masculine noun, "un") or `alone` ("uno"). Ten and over stay numerals."""
    table = {"m": NUMBER_WORDS_ES_MASC, "alone": NUMBER_WORDS_ES_ALONE}.get(
        form, NUMBER_WORDS_ES)
    return table.get(n, str(n))


DAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _starts_with_i_sound(word: str) -> bool:
    """Spanish turns "y" into "e" before an "i" sound ("Iván e Isabel")."""
    w = word.strip().lower()
    return w.startswith("i") or (w.startswith("hi") and not w.startswith(("hie", "hia")))


def join_list(items: list[str], conjunction: str = "y") -> str:
    """'a', 'a y b', 'a, b y c' — the Spanish counterpart of `_list_join`."""
    if len(items) <= 1:
        return ", ".join(items)
    last = items[-1]
    word = "e" if conjunction == "y" and _starts_with_i_sound(last) else conjunction
    return ", ".join(items[:-1]) + f" {word} {last}"


def reset_clock(when, days_ahead: int) -> str:
    """A reset time as a Spanish speaker says it after "hasta":
    "las 10:30", "mañana a las 10", "el lunes a las 10", "el lunes 8 de
    septiembre a las 10". 24-hour clock, as is usual in Spain."""
    hour = when.hour
    if hour == 0 and when.minute == 0:
        at = "medianoche"                       # not "las 0"
    else:
        clock = f"{hour}" if when.minute == 0 else f"{hour}:{when.minute:02d}"
        article = "la" if hour == 1 else "las"
        at = f"{article} {clock}"
    if days_ahead <= 0:
        return at
    if days_ahead == 1:
        return f"mañana a {at}"
    day = DAYS_ES[when.weekday()]
    if days_ahead < 7:
        return f"el {day} a {at}"
    return f"el {day} {when.day} de {MONTHS_ES[when.month - 1]} a {at}"
