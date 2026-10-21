"""El catálogo en castellano cubre cada texto que el código pide traducir.

Sin navegador: lee los `.ts` y comprueba, en cualquier máquina, tres cosas que
si fallan solo se ven con el panel abierto en castellano (un texto que cae al
inglés sin avisar) o rompen la compilación de la CI (una clave repetida en el
objeto es un error de TypeScript).

  * toda llamada `t("…")`, `tp(n, "…", "…")`, `tc("ctx", "…")` o
    `t1(n, "…")` con texto literal tiene su entrada en
    `frontend/src/i18n-es.ts` (y `t1` además la de su singular, con contexto
    "one");
  * el texto fijo del HTML marcado con `data-i18n*` también;
  * una traducción conserva las {variables} de su original, y no hay claves
    repetidas.

Los textos que el código arma en tiempo de ejecución (tablas de estados,
etiquetas) se listan aquí a mano en DYNAMIC porque ninguna llamada los muestra
como literal.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONTEND = REPO / "frontend"
SRC = FRONTEND / "src"

STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
WS = " \t\r\n"


def _concat(src: str, i: int):
    """`"a" + "b"` desde la posición i: (texto, fin), o None si no es literal."""
    parts: list[str] = []
    while True:
        while i < len(src) and src[i] in WS:
            i += 1
        m = STRING.match(src, i)
        if not m:
            return None
        parts.append(json.loads(f'"{m.group(1)}"'))
        i = m.end()
        j = i
        while j < len(src) and src[j] in WS:
            j += 1
        if j < len(src) and src[j] == "+":
            k = j + 1
            while k < len(src) and src[k] in WS:
                k += 1
            if STRING.match(src, k):
                i = j + 1
                continue
            return None
        return "".join(parts), i


def _skip_arg(src: str, i: int) -> int:
    """Salta un argumento (hasta la coma de primer nivel) y devuelve lo que sigue."""
    depth = 0
    while src[i] != "," or depth:
        if src[i] in "([{":
            depth += 1
        elif src[i] in ")]}":
            depth -= 1
        i += 1
    return i + 1


def _call_site_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for path in SRC.rglob("*.ts"):
        if path.name in ("i18n.ts", "i18n-es.ts"):
            continue
        src = path.read_text()
        for m in re.finditer(r"(?<![\w.])(t1|tp|tc|t)\(", src):
            kind, i = m.group(1), m.end()
            if kind in ("tp", "tc", "t1"):
                i = _skip_arg(src, i)
            first = _concat(src, i)
            if first is None:
                continue              # argumento dinámico: va en DYNAMIC
            keys[first[0]] = path.name
            if kind == "t1":
                keys["one\u0004" + first[0]] = path.name
            if kind == "tp":
                j = first[1]
                while src[j] in WS:
                    j += 1
                second = _concat(src, j + 1)
                assert second is not None, f"{path.name}: plural sin segunda forma"
                keys[second[0]] = path.name
    return keys


def _html_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for name in ("index.html", "dashboard.html", "admin.html"):
        src = (FRONTEND / name).read_text()
        for m in re.finditer(
                r"<(\w+)([^>]*?)data-i18n(?:-(title|aria-label))?([^>]*)>", src):
            attrs = m.group(2) + m.group(4)
            if m.group(3):
                found = re.search(m.group(3) + r'="([^"]*)"', attrs)
                assert found, f"{name}: data-i18n-{m.group(3)} sin atributo"
                keys[found.group(1)] = name
                continue
            end = src.find(f"</{m.group(1)}>", m.end())
            inner = src[m.end():end]
            text = re.sub(r"\s+", " ", re.sub(r"<.*", "", inner, flags=re.S)).strip()
            if text:
                keys[text] = name
    return keys


# Lo que se traduce con una variable: tablas de estados y etiquetas.
DYNAMIC = [
    "queued", "running", "succeeded", "failed", "timed out", "cancelled",
    "needs you", "working", "idle", "shell", "fresh", "gone", "unknown",
    "Waiting on you", "Working", "Idle", "In a shell", "Never prompted",
    "Gone", "No status",
    "It is mid-turn. Nothing is asked of you.",
    "It finished and stopped. Nothing is asked of you.",
    "A command is running in that terminal — not a turn.",
    "Nobody has typed into this one. It has written no transcript, so there "
    "is nothing to read and nothing to answer.",
    "Every process on this conversation has exited.",
    "The process is alive, but its roster entry carries no status. What it "
    "is doing is not known.",
    "waiting for", "working for", "idle for", "in shell for", "alive for",
    "last seen", "unchanged for", "for",
    "memory", "project", "journal", "spec", "plan", "(unknown)",
    "planning", "building", "for review",
    "Waiting on your approval.", "Approved. JARVIS is writing the plan.",
    "Approved and under way.", "Finished. Waiting on your review.",
    "awaiting approval", "approved", "revised since approval",
    "Nobody has approved this yet. Read it, then tell JARVIS “approved” — "
    "or name a section to change.",
    "You approved these words. A revision will say so here.",
    "This has been rewritten since you approved it. Read it again before it "
    "is built.",
    "5-hour session", "7-day week", "7-day Opus",
    "JARVIS · Run Control",
    # El título de la pantalla de administración: lo traduce `i18n.init()`
    # desde `document.title`, no una llamada visible al escáner.
    "JARVIS · Admin",
]


def _catalogue() -> tuple[dict[str, str], list[str]]:
    body = (SRC / "i18n-es.ts").read_text()
    body = body[body.index("export const ES"):]
    pairs = re.findall(
        r'^\s*("(?:[^"\\]|\\.)*")\s*:\s*("(?:[^"\\]|\\.)*")\s*,\s*$',
        body, flags=re.M)
    entries = len(re.findall(r'^\s*"(?:[^"\\]|\\.)*"\s*:', body, flags=re.M))
    assert entries == len(pairs), "una entrada del catálogo no se lee como clave: valor"
    cat: dict[str, str] = {}
    dups: list[str] = []
    for k, v in pairs:
        key = json.loads(k)
        if key in cat:
            dups.append(key)
        cat[key] = json.loads(v)
    return cat, dups


def test_the_catalogue_has_no_repeated_key():
    _, dups = _catalogue()
    assert dups == [], f"claves repetidas (error de TypeScript): {dups}"


def test_every_text_the_code_translates_is_in_the_catalogue():
    cat, _ = _catalogue()
    wanted = {**_call_site_keys(), **_html_keys()}
    wanted.update({k: "tabla de estados" for k in DYNAMIC})
    missing = sorted(k for k in wanted if k not in cat)
    assert missing == [], "sin traducir al castellano:\n" + "\n".join(missing)


def test_a_translation_keeps_the_variables_of_its_original():
    cat, _ = _catalogue()
    broken = []
    for key, value in cat.items():
        original = set(re.findall(r"\{\w+\}", key))
        translated = set(re.findall(r"\{\w+\}", value))
        # Un singular puede prescindir de {n} ("otra ubicación"); lo que no
        # puede es inventarse una variable que el código no pasa.
        if not translated <= original:
            broken.append(key)
    assert broken == [], f"variables inventadas en la traducción: {broken}"


def test_the_english_default_never_reads_the_catalogue():
    """`t()` devuelve su argumento intacto en inglés: es lo que mantiene
    idéntica la salida que comprueban las pruebas del panel."""
    src = (SRC / "i18n.ts").read_text()
    assert 'CURRENT === "es" ? (ES[text] ?? text) : text' in src
    assert 'if (CURRENT !== "es") return;' in src


def test_the_catalogue_has_the_singular_of_every_t1_text():
    """`t1(n, text)` shows `one\\u0004text` when n is 1 in Castilian: "1 activo",
    not "1 activos". Every text passed to it needs that second entry."""
    cat, _ = _catalogue()
    singles = {k for k, f in _call_site_keys().items() if k.startswith("one\u0004")}
    assert singles, "no t1 call found: the scan is broken"
    assert sorted(k for k in singles if k not in cat) == []
    for key in singles:
        plain = key.split("\u0004", 1)[1]
        assert cat[key] != cat[plain], key


def test_a_singular_never_keeps_the_plural_ending():
    cat, _ = _catalogue()
    for key, value in cat.items():
        if key.startswith("one\u0004"):
            assert "{n} activos" not in value and "{n} lanzados" not in value, key


def test_t1_is_the_plain_text_in_english():
    src = (SRC / "i18n.ts").read_text()
    assert 'if (CURRENT !== "es" || n !== 1) return t(text, vars);' in src
