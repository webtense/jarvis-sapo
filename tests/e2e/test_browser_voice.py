"""JARVIS entero en navegadores reales: servidor real, página compilada real.

Qué se prueba aquí y qué no:

* SÍ: el AudioWorklet de la página captura el micrófono (falso, un WAV de voz
  sintetizada), remuestrea de 48 a 16 kHz y lo manda por `/ws/mic`; el
  servidor lo trocea, detecta la frase, pasa por recording -> transcribing ->
  listening y entrega el texto por el mismo camino que un reconocimiento del
  navegador, hasta convertirse en un turno.
* NO: Whisper (la CI no tiene modelos: un Transcriber falso devuelve un texto
  fijo), el cerebro (`claude`) ni ninguna voz.

Todo con `-m e2e`; ver tests/e2e/conftest.py.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from tests.e2e.conftest import FIXED_TEXT, PHRASE_WAV

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

WAIT_SEC = 30
FRAME_BYTES_PER_SEC = 16000 * 2      # PCM16 mono 16 kHz

CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    f"--use-file-for-fake-audio-capture={PHRASE_WAV}",
    # La página abre su AudioContext sin un clic: sin esto Chromium lo deja en
    # "suspended" y el worklet no corre.
    "--autoplay-policy=no-user-gesture-required",
]
FIREFOX_PREFS = {
    "media.navigator.streams.fake": True,
    "media.navigator.permission.disabled": True,
    # La página dibuja el orbe con WebGL (three.js) y, si no puede, su script
    # se detiene antes de abrir ningún websocket. El Firefox sin GPU de la CI
    # lo tiene en la lista de bloqueo: se fuerza el WebGL por software.
    "webgl.force-enabled": True,
    "webgl.disabled": False,
    "webgl.enable-webgl2": True,
    "webgl.forbid-software": False,
    "gfx.webrender.software": True,
    "layers.acceleration.disabled": True,
}


# Se anota desde la propia página en vez de con page.on("websocket"): ese evento
# de Playwright no llegó a ver los marcos en Firefox en la CI, y un script de
# inicio que envuelve WebSocket funciona igual en los dos navegadores.
TAP_SCRIPT = """
(() => {
  const seen = (window.__jarvisFrames = []);
  const Native = window.WebSocket;
  window.WebSocket = function (url, protocols) {
    const ws = protocols === undefined ? new Native(url) : new Native(url, protocols);
    const path = String(url).replace(/^wss?:\\/\\/[^/]+\\//, "");
    ws.addEventListener("message", (event) => {
      if (typeof event.data === "string") seen.push([path, event.data]);
    });
    return ws;
  };
  window.WebSocket.prototype = Native.prototype;
  for (const k of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) window.WebSocket[k] = Native[k];
})();
"""


class SocketTap:
    """Los textos JSON que el servidor envía por cada websocket de la página."""

    def __init__(self, page) -> None:
        self.page = page
        self.frames: list[tuple[str, dict]] = []     # (ruta, mensaje)
        self.problems: list[str] = []                # consola y errores de la página
        page.on("pageerror", lambda e: self.problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: self.problems.append(f"console.{m.type}: {m.text}")
                if m.type in ("error", "warning") else None)

    async def install(self) -> None:
        await self.page.add_init_script(TAP_SCRIPT)

    async def refresh(self) -> None:
        raw = await self.page.evaluate("window.__jarvisFrames || []")
        frames = []
        for path, payload in raw:
            try:
                msg = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if isinstance(msg, dict):
                frames.append((path, msg))
        self.frames = frames

    def mic(self, kind: str) -> list[dict]:
        return [m for p, m in self.frames if p == "ws/mic" and m.get("type") == kind]

    def mic_states(self) -> list[str]:
        return [m.get("state") for m in self.mic("state")]


async def wait_for(predicate, what: str, tap: "SocketTap | None" = None,
                   timeout: float = WAIT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tap is not None:
            await tap.refresh()
        if predicate():
            return
        await asyncio.sleep(0.2)
    seen = "" if tap is None else (
        f"\nframes: {[(p, m.get('type')) for p, m in tap.frames]}"
        f"\npage problems: {tap.problems[-15:]}")
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}{seen}")


def is_subsequence(wanted: list[str], seen: list[str]) -> bool:
    it = iter(seen)
    return all(item in it for item in wanted)


async def test_chromium_streams_the_fake_microphone_to_a_turn(start_jarvis):
    """El camino completo de la voz local con audio de verdad."""
    from playwright.async_api import async_playwright

    jarvis = start_jarvis("es", "local")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chromium",
                                           args=CHROMIUM_ARGS)
        try:
            context = await browser.new_context(permissions=["microphone"])
            page = await context.new_page()
            tap = SocketTap(page)
            await tap.install()
            await page.goto(jarvis.base + "/")

            await wait_for(lambda: tap.mic("ready"), "the server's 'ready' on /ws/mic", tap)
            ready = tap.mic("ready")[0]
            assert ready["sampleRate"] == 16000

            await wait_for(lambda: tap.mic("transcript"), "a 'transcript' frame", tap)
            assert tap.mic("transcript")[0]["text"] == FIXED_TEXT

            # La máquina de estados pasó por las tres fases, en orden.
            await wait_for(lambda: is_subsequence(
                ["recording", "transcribing", "listening"], tap.mic_states()),
                "recording -> transcribing -> listening", tap)

            # Y el texto llegó a ser un turno (`_handle_utterance`).
            await wait_for(lambda: jarvis.events("turn"), "a turn")
            assert "alarma" in jarvis.events("turn")[0]["text"].lower()
        finally:
            await browser.close()

    # El PCM que recibió el transcriptor es de 16 kHz de verdad: si el worklet
    # mandara las muestras a 48 kHz, la misma voz duraría el triple y una frase
    # de ~2,5 s llegaría como ~8 s.
    heard = jarvis.events("transcribe")
    assert heard, "the fake transcriber never received audio"
    seconds = heard[0]["bytes"] / FRAME_BYTES_PER_SEC
    assert 1.0 <= seconds <= 5.5, f"phrase reached the server as {seconds:.1f}s of 16 kHz audio"
    assert heard[0]["language"] == "es"


async def test_firefox_in_auto_mode_picks_the_local_microphone(start_jarvis):
    """Firefox no tiene reconocimiento propio: con JARVIS_STT=auto la página
    debe elegir /ws/mic. (La voz real en Firefox no se prueba: no admite un
    fichero de audio como micrófono falso, solo un tono.)"""
    from playwright.async_api import async_playwright

    jarvis = start_jarvis("en", "auto")
    async with async_playwright() as pw:
        browser = await pw.firefox.launch(headless=True,
                                          firefox_user_prefs=FIREFOX_PREFS)
        try:
            page = await browser.new_page()
            tap = SocketTap(page)
            await tap.install()
            await page.goto(jarvis.base + "/")

            has_recogniser = await page.evaluate(
                "'SpeechRecognition' in window || 'webkitSpeechRecognition' in window")
            assert has_recogniser is False, "Firefox grew a SpeechRecognition: the premise changed"

            await wait_for(lambda: any(p == "ws/voice" and m.get("stt") == "auto"
                                       for p, m in tap.frames),
                           "the config frame announcing stt=auto", tap)
            await wait_for(lambda: tap.mic("ready"), "'ready' on /ws/mic", tap)
        finally:
            await browser.close()


async def test_the_page_is_in_spanish_when_the_server_is(start_jarvis):
    from playwright.async_api import async_playwright

    jarvis = start_jarvis("es", "browser")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chromium")
        try:
            page = await browser.new_page()
            await page.goto(jarvis.base + "/")

            assert await page.get_attribute("html", "lang") == "es"

            await page.click("#btn-menu")
            await page.wait_for_selector("#btn-settings", state="visible", timeout=5000)
            assert (await page.text_content("#btn-settings")).strip() == "Ajustes"
            assert (await page.text_content("#btn-restart")).strip() == "Reiniciar el servidor"

            await page.click("#btn-settings")
            heading = page.locator("#settings-panel-inner h2")
            await heading.wait_for(state="visible", timeout=5000)
            assert (await heading.text_content()).strip() == "Ajustes"
            sections = await page.locator("#settings-panel-inner h3").all_text_contents()
            assert "Claves de API" in sections
            assert "Estado de la conexión" in sections
            assert "Preferencias del usuario" in sections
        finally:
            await browser.close()
