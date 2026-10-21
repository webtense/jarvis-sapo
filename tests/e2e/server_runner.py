"""Arranca el servidor JARVIS REAL para las pruebas E2E, con dos piezas falsas.

    python tests/e2e/server_runner.py --port 5000

Es el mismo `server.app` de siempre, con uvicorn, sirviendo el frontend
compilado (`JARVIS_FRONTEND_DIST`). Solo se sustituyen dos cosas, ambas en
memoria y antes de importar nada más:

* el Transcriber de `stt_local`: en la CI no hay Whisper ni modelos. El falso
  devuelve siempre `E2E_FIXED_TEXT` y apunta cuántos bytes de PCM recibió;
* `_handle_utterance`: sin `claude`, el "turno" que llegaría al cerebro se
  apunta en vez de lanzarse.

Ambos apuntes van, como líneas JSON, al fichero `E2E_LOG`. El resto (el
remuestreo del AudioWorklet, `/ws/mic`, el detector de frases, la máquina de
estados, `_handle_final_transcript`) es el código de producción.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

LOG = os.environ.get("E2E_LOG", "")
FIXED_TEXT = os.environ.get("E2E_FIXED_TEXT", "Jarvis, ¿está puesta la alarma?")


def note(**fields) -> None:
    if not LOG:
        return
    fields["t"] = time.time()
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(fields, ensure_ascii=False) + "\n")


class FakeTranscriber:
    loaded = True

    async def transcribe(self, pcm: bytes, language: str) -> str:
        note(event="transcribe", bytes=len(pcm), language=language)
        return FIXED_TEXT

    def unload(self) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    import stt_local
    # Sin faster-whisper instalado `mode()` diría "browser": se afirma que está.
    stt_local.whisper_installed = lambda: True
    stt_local._transcriber = FakeTranscriber()

    import server

    async def fake_utterance(text: str) -> None:
        note(event="turn", text=text)

    server._handle_utterance = fake_utterance

    # Lo mismo que hace server.py al arrancar con `python server.py`: el
    # filtro de origen se construye con el puerto real.
    os.environ["JARVIS_PORT"] = str(args.port)
    os.environ["JARVIS_SCHEME"] = "http"
    os.environ["JARVIS_BIND_HOST"] = "127.0.0.1"

    import uvicorn
    uvicorn.run(server.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
