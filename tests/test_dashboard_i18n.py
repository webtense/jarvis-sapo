"""El panel en castellano, y en inglés exactamente como estaba.

`VITE_JARVIS_LANG` se fija al COMPILAR el bundle (es lo que hace
scripts/start-ubuntu.sh con `JARVIS_LANGUAGE`), así que cada idioma es un
bundle distinto: `dist` (inglés, el de siempre) y `dist-es`. El mecanismo es el
de `tests/dashboard_page.py`: bundle real, API simulada en un puerto local y el
Chromium de Playwright. Sin red ni modelos; se omiten solos sin `npm` o sin
Playwright.

Se lee el texto del DOM con `text_content()` y no con `inner_text()`: este
último aplica el `text-transform` del CSS (las pestañas van en mayúsculas) y
estas pruebas hablan de lo que el programa escribe, no de cómo lo pinta el CSS.
"""

from __future__ import annotations

import time

import pytest

from tests.dashboard_page import (
    dashboard, quiet_machine, run_row, why_unavailable,
)
from tests.test_dashboard_page import _roster, _session_row

UNAVAILABLE = why_unavailable()
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(UNAVAILABLE is not None, reason=UNAVAILABLE or ""),
]

WAIT_MS = 5_000


async def _text(page, selector: str) -> str:
    return (await page.locator(selector).first.text_content() or "").strip()


# ── el marco de la página ──────────────────────────────────────────────────

async def test_the_spanish_dashboard_frame_is_in_castilian():
    async with dashboard(quiet_machine(), lang="es") as page:
        await page.wait_for_selector("#history-list .empty", timeout=WAIT_MS)

        assert await page.evaluate("document.documentElement.lang") == "es"
        assert await page.title() == "JARVIS · Control de ejecuciones"
        assert await _text(page, ".brand-sub") == "Control de ejecuciones"
        assert await _text(page, "#tab-runs") == "Ejecuciones"
        assert "Sesiones" in await _text(page, "#tab-sessions")
        assert await _text(page, "#tab-memory") == "Memoria"
        assert await _text(page, "#tab-usage") == "Consumo"
        assert await _text(page, "#history-section .panel-title") == "Historial"
        assert "Aún no hay ejecuciones" in await _text(page, "#history-list")
        assert await _text(page, "#active-list") == "No hay nada en marcha."


async def test_the_english_dashboard_frame_is_what_it_always_was():
    async with dashboard(quiet_machine()) as page:
        await page.wait_for_selector("#history-list .empty", timeout=WAIT_MS)

        assert await page.evaluate("document.documentElement.lang") == "en"
        assert await page.title() == "JARVIS · Run Control"
        assert await _text(page, ".brand-sub") == "Run Control"
        assert await _text(page, "#tab-runs") == "Runs"
        assert "Sessions" in await _text(page, "#tab-sessions")
        assert await _text(page, "#tab-usage") == "Usage"
        assert await _text(page, "#history-section .panel-title") == "History"
        assert "No runs yet. Ask JARVIS to build something." in \
            await _text(page, "#history-list")
        assert await _text(page, "#active-list") == "Nothing running."


async def test_the_link_state_follows_the_language():
    async with dashboard(quiet_machine(), lang="es") as page:
        await page.wait_for_function(
            "document.getElementById('link-state-text')"
            "?.textContent === 'Enlace correcto'", timeout=WAIT_MS)


# ── fechas, duraciones y números ───────────────────────────────────────────

def _runs_machine():
    api = quiet_machine()
    api.json("/api/runs", {"runs": [run_row()]})
    return api


async def test_a_duration_is_spoken_the_spanish_way():
    """99 segundos: "1 min 39 s" con unidad separada, no "1m 39s"."""
    async with dashboard(_runs_machine(), lang="es") as page:
        await page.wait_for_selector("#history-list .row", timeout=WAIT_MS)

        assert await _text(page, ".run-dur") == "1 min 39 s"
        assert await _text(page, ".run-tokens") == "30 tok"


async def test_a_duration_in_english_is_unchanged():
    async with dashboard(_runs_machine()) as page:
        await page.wait_for_selector("#history-list .row", timeout=WAIT_MS)

        assert await _text(page, ".run-dur") == "1m 39s"
        assert await _text(page, ".run-tokens") == "30 tok"


def _stats_machine():
    api = quiet_machine()
    api.json("/api/runs/stats", {
        "period": "day", "by_status": {}, "total_runs": 0,
        "total_cost_usd": 0.0, "total_input_tokens": 1000,
        "total_output_tokens": 234})
    return api


async def test_the_decimal_separator_follows_the_language():
    """1234 tokens: "1,2k" con coma en castellano, "1.2k" con punto en inglés."""
    async with dashboard(_stats_machine(), lang="es") as page:
        await page.wait_for_function(
            "document.getElementById('stats')?.textContent.includes('1,2k')",
            timeout=WAIT_MS)
    async with dashboard(_stats_machine()) as page:
        await page.wait_for_function(
            "document.getElementById('stats')?.textContent.includes('1.2k')",
            timeout=WAIT_MS)


async def test_the_masthead_says_what_it_does_not_know_in_castilian():
    async with dashboard(quiet_machine(), lang="es") as page:
        await page.wait_for_selector(".usage-gauge", timeout=WAIT_MS)

        text = await _text(page, "#stats")
        assert "Suscripción" in text
        assert "se reinicia" in text
        assert "Hoy" in text
        assert "aún sin ejecuciones" in text


# ── las sesiones ───────────────────────────────────────────────────────────

def _sessions(**over):
    api = quiet_machine()
    api.json("/api/sessions", _roster([_session_row(**over)]))
    return api


async def test_sessions_pluralise_and_age_in_castilian():
    api = _sessions(since=time.time() - 7200)
    async with dashboard(api, lang="es") as page:
        await page.locator("#tab-sessions").click()
        await page.wait_for_selector("#sessions-view .row", timeout=WAIT_MS)

        text = (await page.locator("#sessions-view").inner_text()).lower()
        assert "12+ agentes" in text
        assert "hace 2 h" in text, text


async def test_sessions_in_english_are_unchanged():
    api = _sessions(since=time.time() - 7200)
    async with dashboard(api) as page:
        await page.locator("#tab-sessions").click()
        await page.wait_for_selector("#sessions-view .row", timeout=WAIT_MS)

        text = (await page.locator("#sessions-view").inner_text()).lower()
        assert "12+ agents" in text
        assert "2h ago" in text, text


async def test_what_a_session_said_is_not_translated():
    """El título y el último mensaje son datos de una transcripción: llegan al
    panel tal cual, en el idioma que sean."""
    api = _sessions(title="Doing the thing", last_text="working")
    async with dashboard(api, lang="es") as page:
        await page.locator("#tab-sessions").click()
        await page.wait_for_selector("#sessions-view .row", timeout=WAIT_MS)
        await page.locator("#sessions-view .row").first.click()
        await page.wait_for_selector(".sd-quote", timeout=WAIT_MS)

        quotes = await page.locator(".sd-quote").all_text_contents()
        assert "Doing the thing" in quotes
        assert "working" in quotes
