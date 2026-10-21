"""
wake_text.py — la palabra de activación "Jarvis" reconocida en el TEXTO.

`JARVIS_WAKE_WORD=whisper` no usa ningún modelo de audio extra: cada frase que
el VAD detecta se transcribe con Whisper y solo pasa al turno si EMPIEZA por
la palabra de activación. Este módulo es Python puro, sin dependencias, y lo
único que hace es decidir sobre una cadena:

* `match_wake(text)`: ¿empieza por "Jarvis" (o una variante de transcripción)?
  Devuelve lo que sigue, o `None` si no empieza por ella.
* `WakeGate`: añade la ventana de conversación: tras una frase aceptada se
  puede seguir hablando sin repetir la palabra durante unos segundos.

El texto que llega aquí es la transcripción de una voz: dato no confiable. Solo
se compara y se recorta; nunca se interpreta ni se ejecuta.
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Callable, Optional

NAME = "jarvis"

# Lo que Whisper escribe a menudo para "Jarvis" en castellano. Lo que queda a
# distancia de edición 1 del nombre se acepta además sin estar aquí.
VARIANTS = frozenset({
    "jarvis", "yarvis", "harvis", "jarbis", "jarvi", "arvis", "yarbis",
    "harbis", "harbys", "jarvys", "jarvish", "charvis", "garvis", "jervis",
    "yarvi", "jarvins", "harvey", "harveys",
})

# Lo que se dice antes del nombre: "oye Jarvis", "hey, Jarvis", "vale Jarvis".
FILLERS = frozenset({"oye", "oiga", "hey", "hei", "ei", "ey", "eh", "hola", "ok", "okay", "vale", "buenas",
                      "hoy"})   # "hoy" es lo que Whisper (tiny/base) oye a veces por "oye"

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# Signos que separan el nombre de la orden: "Jarvis, enciende" -> "enciende".
_LEAD_PUNCT = " \t\r\n,.:;!?-–—…\"'«»"


def fold(word: str) -> str:
    """Minúsculas y sin acentos: "Jarvís" -> "jarvis"."""
    decomposed = unicodedata.normalize("NFD", (word or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def edit_distance(a: str, b: str, limit: int = 2) -> int:
    """Levenshtein, cortado en `limit + 1` (no hace falta saber cuánto de lejos)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def is_wake_token(word: str) -> bool:
    """¿Esta palabra suelta es el nombre, o una transcripción cercana?

    Las palabras muy cortas no se comparan por distancia (con 4 letras casi
    todo está a una letra de algo)."""
    w = fold(word)
    if w in VARIANTS:
        return True
    return len(w) >= 5 and edit_distance(w, NAME, 1) <= 1


def match_wake(text: str) -> Optional[str]:
    """Si `text` empieza por la palabra de activación, lo que viene después
    (con su mayúscula y puntuación originales, sin signos al inicio); si no,
    `None`. Una frase que es solo el nombre da `""`.

    Se aceptan: "Jarvis, ...", "Oye Jarvis ...", "Hey, Jarvis. ...", el nombre
    partido por Whisper ("Jar vis") y las variantes de `VARIANTS`.
    """
    text = text or ""
    words = list(_WORD.finditer(text))
    i = 0
    if words and fold(words[0].group()) in FILLERS:
        i = 1
    if i >= len(words):
        return None
    first = words[i]
    end = None
    if is_wake_token(first.group()):
        end = first.end()
    elif i + 1 < len(words):
        # "Jar vis": dos palabras que juntas son el nombre. Solo si entre ellas
        # no hay puntuación fuerte (una frase "Jar. Vis" no es el nombre).
        second = words[i + 1]
        between = text[first.end():second.start()]
        if between.strip(" -") == "" and is_wake_token(first.group() + second.group()):
            end = second.end()
    if end is None:
        return None
    return text[end:].lstrip(_LEAD_PUNCT)


class WakeGate:
    """Deja pasar una frase si empieza por la palabra o si hay conversación.

    `process(text)` devuelve el texto a entregar al turno (sin la palabra) o
    `None` si la frase se descarta. Tras una frase aceptada la ventana queda
    abierta `window_sec` segundos: las siguientes pasan sin la palabra. Una
    frase que es solo "Jarvis" no entrega nada pero abre la ventana ("Jarvis."
    ... pausa ... "enciende la luz").

    El reloj se inyecta para que las pruebas no esperen. Mientras JARVIS habla
    hay que llamar a `keep_open()`: su respuesta no debe gastar la ventana.
    """

    def __init__(self, window_sec: float, clock: Callable[[], float] = time.monotonic):
        self.window_sec = max(0.0, window_sec)
        self._clock = clock
        self._deadline = 0.0

    @property
    def is_open(self) -> bool:
        return self._clock() < self._deadline

    def _open(self) -> None:
        self._deadline = self._clock() + self.window_sec if self.window_sec > 0 else 0.0

    def keep_open(self) -> None:
        """Alarga la ventana si estaba abierta (JARVIS está hablando)."""
        if self.is_open:
            self._open()

    def close(self) -> None:
        self._deadline = 0.0

    def process(self, text: str) -> Optional[str]:
        text = (text or "").strip()
        if not text:
            return None
        rest = match_wake(text)
        if rest is not None:
            self._open()
            return rest
        if self.is_open:
            self._open()
            return text
        return None
