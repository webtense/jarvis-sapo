"""Piezas comunes de las pruebas E2E (navegador real + servidor real).

Se ejecutan a propósito, no con `pytest` a secas:

    python -m playwright install chromium firefox
    pytest -m e2e

En la CI (`JARVIS_E2E_STRICT=1`) lo que falte es un FALLO, no un salto: un
job de pruebas que se salta todo en silencio es peor que no tenerlo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.dashboard_page import _free_port, build_bundle, why_unavailable

REPO = Path(__file__).resolve().parent.parent.parent
RUNNER = Path(__file__).with_name("server_runner.py")
PHRASE_WAV = REPO / "tests" / "fixtures" / "e2e_phrase_es.wav"
FIXED_TEXT = "Jarvis, ¿está puesta la alarma?"
STRICT = os.environ.get("JARVIS_E2E_STRICT") == "1"
START_TIMEOUT_SEC = 60


@pytest.fixture(autouse=True)
def _environment_ready():
    reason = why_unavailable()
    if reason is None and not PHRASE_WAV.is_file():
        reason = f"missing fixture {PHRASE_WAV}"
    if reason:
        if STRICT:
            pytest.fail(f"E2E environment incomplete: {reason}")
        pytest.skip(reason)


@pytest.fixture(scope="session")
def bundles() -> dict[str, Path]:
    """`en` es `frontend/dist` (el de siempre); `es` va a `dist-es`."""
    return {"en": build_bundle("en"), "es": build_bundle("es")}


@dataclass
class Jarvis:
    base: str
    port: int
    log_path: Path
    process: subprocess.Popen

    def events(self, kind: str | None = None) -> list[dict]:
        if not self.log_path.is_file():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kind is None or event.get("event") == kind:
                out.append(event)
        return out


@pytest.fixture
def start_jarvis(tmp_path, bundles):
    """Fábrica: `start_jarvis("es")` arranca un servidor aislado y devuelve su
    dirección. Datos y HOME temporales; sin `claude`; sin voz; sin `.env`
    heredado (las claves vacías ganan al `.env` del desarrollador, que solo
    rellena lo que falta)."""
    started: list[subprocess.Popen] = []

    def start(language: str, stt: str) -> Jarvis:
        port = _free_port()
        work = tmp_path / f"jarvis-{language}-{port}"
        (work / "home").mkdir(parents=True)
        (work / "data").mkdir()
        (work / "projects").mkdir()
        log_path = work / "e2e.log"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("JARVIS_", "FISH_", "HA_", "ANTHROPIC_"))}
        env.update({
            "HOME": str(work / "home"),
            "JARVIS_DATA_DIR": str(work / "data"),
            "JARVIS_PROJECTS_DIR": str(work / "projects"),
            "JARVIS_FRONTEND_DIST": str(bundles[language]),
            "JARVIS_LANGUAGE": language,
            "JARVIS_STT": stt,
            "JARVIS_WAKE_WORD": "off",
            "JARVIS_BRAIN_AUTOSTART": "0",
            "JARVIS_TTS_PROVIDER": "none",
            "FISH_API_KEY": "",
            "E2E_LOG": str(log_path),
            "E2E_FIXED_TEXT": FIXED_TEXT,
            "PYTHONUNBUFFERED": "1",
        })
        process = subprocess.Popen(
            [sys.executable, str(RUNNER), "--port", str(port)],
            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        started.append(process)
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + START_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if process.poll() is not None:
                out = process.stdout.read() if process.stdout else ""
                pytest.fail(f"the JARVIS server exited early:\n{out[-3000:]}")
            try:
                with urllib.request.urlopen(base + "/", timeout=2) as resp:
                    if resp.status == 200:
                        return Jarvis(base, port, log_path, process)
            except OSError:
                time.sleep(0.3)
        pytest.fail(f"the JARVIS server did not answer on {base} in {START_TIMEOUT_SEC}s")

    yield start

    for process in started:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
