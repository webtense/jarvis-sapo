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


DEAD_RECOGNISER = """
(() => {
  class FakeSR {
    constructor() { this.continuous = false; this.interimResults = false; this.lang = ''; }
    start() { setTimeout(() => { this.onstart && this.onstart();
                                 setTimeout(() => { this.onend && this.onend(); }, 5); }, 2); }
    stop() {} abort() {}
  }
  window.SpeechRecognition = FakeSR; window.webkitSpeechRecognition = FakeSR;
})();
"""


async def test_a_recogniser_that_dies_at_once_does_not_restart_in_a_loop(start_jarvis):
    """Si el micrófono no se puede abrir, cada reconocedor muere nada más
    nacer. Antes cada rotación y cada vigilancia añadían una cadena de
    reinicios que nunca paraba (en vivo: ~380 'engine ended' por segundo y el
    registro lleno). Ahora hay un solo reinicio pendiente, con espera creciente."""
    from playwright.async_api import async_playwright

    jarvis = start_jarvis("en", "browser")
    ended: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chromium")
        try:
            page = await browser.new_page()
            await page.add_init_script(DEAD_RECOGNISER)
            page.on("console", lambda m: ended.append(m.text) if "engine ended" in m.text else None)
            await page.goto(jarvis.base + "/")
            await page.mouse.click(500, 400)
            await asyncio.sleep(20)
        finally:
            await browser.close()
    # Sin la espera creciente salen ~50 en 20 s (y crecen con las rotaciones).
    assert 1 <= len(ended) <= 25, f"{len(ended)} reinicios en 20 s"


# --- el botón MUTE de la pantalla principal --------------------------------

async def dismiss_first_run_settings(page) -> None:
    """Sin clave de voz, la página abre sola el panel de Ajustes (a los ~2 s).
    Para los casos con el modal CERRADO se cierra como lo haría una persona,
    con su botón; el botón MUTE no depende de esto (ver `open_settings`)."""
    try:
        await page.wait_for_selector("#settings-container.open", timeout=8000)
    except Exception:
        return                      # no se abrió: nada que cerrar
    await page.click("#settings-close")
    await page.wait_for_selector("#settings-container", state="hidden", timeout=5000)


async def open_settings(page) -> None:
    """El panel de Ajustes ABIERTO y ya quieto (acabó su transición). Si la
    página no lo abre sola, se abre como lo haría una persona: menú, Ajustes."""
    try:
        await page.wait_for_selector("#settings-container.open", timeout=8000)
    except Exception:
        await page.click("#btn-menu")
        await page.click("#btn-settings")
        await page.wait_for_selector("#settings-container.open", timeout=5000)
    # El panel entra deslizándose (0,3 s): se espera a que llegue a su sitio.
    await page.wait_for_function(
        "() => document.getElementById('settings-panel-inner')"
        ".getBoundingClientRect().right <= window.innerWidth + 1",
        timeout=5000)


MUTE_HIT = """
() => {
  const b = document.getElementById('mute-toggle').getBoundingClientRect();
  const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
  return { onTop: !!hit && !!hit.closest('#mute-toggle'),
           got: hit ? (hit.id || hit.className || hit.tagName) : null };
}
"""


async def assert_mute_is_on_top(page) -> None:
    """El centro del botón MUTE devuelve el propio botón: nada lo tapa."""
    hit = await page.evaluate(MUTE_HIT)
    assert hit["onTop"], f"algo tapa el botón MUTE: {hit['got']}"


@pytest.mark.parametrize("modal", ["open", "closed"])
async def test_the_mute_button_toggles_and_shows_its_state(start_jarvis, modal):
    """El botón grande existe, es accesible, alterna con el ratón y con la
    tecla M, y su estado se lee en la etiqueta y en `aria-pressed`. Se prueba
    con el modal de Ajustes ABIERTO (el de primer uso se abre solo a los 2 s y
    antes tapaba el botón) y con el modal cerrado."""
    from playwright.async_api import async_playwright

    jarvis = start_jarvis("es", "browser")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chromium")
        try:
            page = await browser.new_page()
            await page.goto(jarvis.base + "/")
            button = page.locator("#mute-toggle")
            # `data-ready` lo pone la página cuando ya atiende el botón.
            await page.wait_for_selector("#mute-toggle[data-ready='1']", timeout=10000)
            if modal == "open":
                await open_settings(page)
                assert await page.locator("#settings-container.open").count() == 1
            else:
                await dismiss_first_run_settings(page)
                assert not await page.locator("#settings-container.open").count()
            await assert_mute_is_on_top(page)

            # Visible y grande, no escondido en el menú ☰.
            assert await button.is_visible()
            box = await button.bounding_box()
            assert box is not None and box["width"] >= 120 and box["height"] >= 44
            assert await page.locator("#menu-dropdown #mute-toggle").count() == 0
            assert await button.evaluate("el => el.tagName") == "BUTTON"

            # Escuchando: etiqueta en castellano y sin pulsar.
            assert await button.get_attribute("aria-pressed") == "false"
            assert (await page.text_content("#mute-label")).strip() == "ESCUCHANDO"

            # Con el ratón (Playwright comprueba que el clic llega al botón).
            await button.click()
            assert await button.get_attribute("aria-pressed") == "true"
            assert (await page.text_content("#mute-label")).strip() == "MUTE"
            assert "is-muted" in (await button.get_attribute("class") or "")
            # El botón pequeño de siempre comparte el mismo estado.
            assert await page.get_attribute("#btn-mute", "aria-pressed") == "true"
            await button.click()
            assert await button.get_attribute("aria-pressed") == "false"
            assert (await page.text_content("#mute-label")).strip() == "ESCUCHANDO"

            # Con el teclado: M alterna; con el foco en un campo de texto, no.
            await page.locator("body").press("m")
            assert await button.get_attribute("aria-pressed") == "true"
            await page.locator("body").press("M")
            assert await button.get_attribute("aria-pressed") == "false"
            await page.evaluate(
                "() => { const i = document.createElement('input'); i.id = 'probe';"
                " document.body.appendChild(i); i.focus(); }")
            await page.keyboard.press("m")
            assert await button.get_attribute("aria-pressed") == "false"
            assert await page.input_value("#probe") == "m"

            # Se llega con el teclado: el foco cae en el botón y Espacio lo activa.
            await button.focus()
            assert await page.evaluate(
                "() => document.activeElement.id") == "mute-toggle"
            await page.keyboard.press("Space")
            assert await button.get_attribute("aria-pressed") == "true"
        finally:
            await browser.close()


async def test_the_mute_button_does_not_cover_the_settings_on_a_narrow_screen(start_jarvis):
    """A ~375 px el panel ocupa toda la anchura y el botón MUTE queda ENCIMA de
    él. Ningún control del panel puede quedar debajo: con el panel desplazado
    hasta el final, ninguno se solapa con el botón."""
    from playwright.async_api import async_playwright

    jarvis = start_jarvis("es", "browser")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chromium")
        try:
            page = await browser.new_page(viewport={"width": 375, "height": 667})
            await page.goto(jarvis.base + "/")
            await page.wait_for_selector("#mute-toggle[data-ready='1']", timeout=10000)
            await open_settings(page)
            await assert_mute_is_on_top(page)

            overlaps = await page.evaluate("""
            () => {
              const panel = document.getElementById('settings-panel-inner');
              panel.scrollTop = panel.scrollHeight;
              const m = document.getElementById('mute-toggle').getBoundingClientRect();
              const bad = [];
              const sel = 'button, input, select, textarea, a[href], [tabindex]';
              for (const el of panel.querySelectorAll(sel)) {
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                if (getComputedStyle(el).visibility === 'hidden') continue;
                const hit = !(r.right <= m.left || r.left >= m.right
                              || r.bottom <= m.top || r.top >= m.bottom);
                if (hit) bad.push(el.id || el.className || el.tagName);
              }
              return bad;
            }
            """)
            assert overlaps == [], f"el botón MUTE tapa controles de Ajustes: {overlaps}"
            # Arriba del todo, el botón de cerrar sigue a la vista y nada lo tapa.
            reachable = await page.evaluate("""
            () => {
              document.getElementById('settings-panel-inner').scrollTop = 0;
              const r = document.getElementById('settings-close').getBoundingClientRect();
              const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              return !!hit && !!hit.closest('#settings-close');
            }
            """)
            assert reachable, "el botón de cerrar Ajustes no se alcanza"
        finally:
            await browser.close()


async def test_a_muted_microphone_sends_no_audio_to_the_server(start_jarvis):
    """Silenciado, la página no manda ni un fotograma de audio a `/ws/mic`
    (aunque el micrófono falso esté repitiendo una frase entera); al activarlo
    otra vez, la frase llega y se convierte en un turno."""
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
            await page.wait_for_selector("#mute-toggle[data-ready='1']", timeout=10000)
            await page.click("#mute-toggle")
            assert await page.get_attribute("#mute-toggle", "aria-pressed") == "true"

            # Tiempo de sobra para que la frase (~2,5 s) hubiera dado un turno.
            await asyncio.sleep(9)
            await dismiss_first_run_settings(page)
            await tap.refresh()
            assert not tap.mic("transcript"), "a muted page still got a transcript"
            assert not jarvis.events("transcribe"), "audio reached the server while muted"
            assert not jarvis.events("turn")

            await page.click("#mute-toggle")
            assert await page.get_attribute("#mute-toggle", "aria-pressed") == "false"
            await wait_for(lambda: tap.mic("transcript"), "a transcript after unmuting", tap)
            await wait_for(lambda: jarvis.events("turn"), "a turn after unmuting")
        finally:
            await browser.close()


async def test_a_page_without_a_microphone_does_not_take_the_socket_from_one_with(start_jarvis):
    """Una página cuyo micrófono está denegado abría /ws/mic antes de pedirlo y
    le quitaba el único hueco a la página que sí lo tenía: la buena quedaba
    sorda y la mala no oía nada. Ahora el socket se abre solo con micrófono."""
    from playwright.async_api import async_playwright

    jarvis = start_jarvis("es", "local")
    closed_good: list[int] = []
    bad_sockets: list[str] = []
    async with async_playwright() as pw:
        good_browser = await pw.chromium.launch(headless=True, channel="chromium", args=CHROMIUM_ARGS)
        # Sin --use-fake-ui-for-media-stream: nadie concede el permiso.
        bad_browser = await pw.chromium.launch(headless=True, channel="chromium",
                                               args=["--autoplay-policy=no-user-gesture-required"])
        try:
            good = await (await good_browser.new_context(permissions=["microphone"])).new_page()
            tap = SocketTap(good)
            await tap.install()
            good.on("websocket", lambda ws: ws.url.endswith("/ws/mic") and
                    ws.on("close", lambda *_: closed_good.append(1)))
            await good.goto(jarvis.base + "/")
            await wait_for(lambda: tap.mic("ready"), "the good page's 'ready'", tap)

            bad = await (await bad_browser.new_context(permissions=[])).new_page()
            bad.on("websocket", lambda ws: bad_sockets.append(ws.url) if ws.url.endswith("/ws/mic") else None)
            await bad.goto(jarvis.base + "/")
            await asyncio.sleep(8)

            assert bad_sockets == [], "a page with no microphone opened /ws/mic"
            assert closed_good == [], "the page with the microphone lost its socket"
        finally:
            await good_browser.close()
            await bad_browser.close()
