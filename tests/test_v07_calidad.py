"""Cabos sueltos de la 0.6.0 que no caben en otra prueba.

* El bundle inglés de las pruebas del panel no se puede reutilizar si se
  compiló para otro idioma (`tests/dashboard_page.py`).
* "Probar" la clave de Fish con el campo vacío no pone el punto en rojo si hay
  voz (`frontend/src/settings.ts`; sin navegador, se lee el fuente como hacen
  las demás pruebas del catálogo).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests import dashboard_page as dp

REPO = Path(__file__).resolve().parent.parent


# --- el bundle de las pruebas del panel ---------------------------------------

class _FakeBuild:
    """Sustituye a `subprocess.run`: anota la orden y el entorno y deja una
    carpeta de salida con un dashboard.html, como haría `vite build`."""

    def __init__(self, frontend: Path):
        self.frontend = frontend
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, cwd=None, env=None, **_kw):
        self.calls.append((list(cmd), dict(env or {})))
        out = self.frontend / cmd[cmd.index("--outDir") + 1]
        out.mkdir(parents=True, exist_ok=True)
        (out / "dashboard.html").write_text("<html></html>")


def _sandbox(monkeypatch, tmp_path):
    frontend = tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "src" / "a.ts").write_text("x")
    monkeypatch.setattr(dp, "FRONTEND", frontend)
    fake = _FakeBuild(frontend)
    monkeypatch.setattr(dp.subprocess, "run", fake)
    return frontend, fake


def test_a_bundle_is_built_once_and_then_reused(monkeypatch, tmp_path):
    frontend, fake = _sandbox(monkeypatch, tmp_path)
    assert dp.build_bundle("en") == frontend / "dist-en"
    assert dp.build_bundle("en") == frontend / "dist-en"
    assert len(fake.calls) == 1


def test_english_never_reuses_or_writes_the_shipped_dist(monkeypatch, tmp_path):
    frontend, fake = _sandbox(monkeypatch, tmp_path)
    # A `dist` compiled for Spanish by scripts/start-ubuntu.sh, newer than the sources.
    shipped = frontend / "dist"
    shipped.mkdir()
    (shipped / "dashboard.html").write_text("<html lang='es'></html>")
    out = dp.build_bundle("en")
    assert out != shipped
    assert (shipped / "dashboard.html").read_text() == "<html lang='es'></html>"
    assert "--outDir" in fake.calls[0][0] and "dist-en" in fake.calls[0][0]


def test_a_bundle_with_the_wrong_stamp_is_rebuilt(monkeypatch, tmp_path):
    frontend, fake = _sandbox(monkeypatch, tmp_path)
    out = frontend / "dist-en"
    out.mkdir()
    (out / "dashboard.html").write_text("<html></html>")
    (out / dp.BUNDLE_STAMP).write_text("lang=es-ES\n")      # compiled for Spanish
    dp.build_bundle("en")
    assert len(fake.calls) == 1
    assert (out / dp.BUNDLE_STAMP).read_text().strip() == "lang=default"


def test_a_bundle_nobody_stamped_is_rebuilt(monkeypatch, tmp_path):
    frontend, fake = _sandbox(monkeypatch, tmp_path)
    out = frontend / "dist-en"
    out.mkdir()
    (out / "dashboard.html").write_text("<html></html>")
    dp.build_bundle("en")
    assert len(fake.calls) == 1


def test_the_english_build_ignores_a_language_in_the_environment(monkeypatch, tmp_path):
    _frontend, fake = _sandbox(monkeypatch, tmp_path)
    monkeypatch.setenv("VITE_JARVIS_LANG", "es-ES")
    dp.build_bundle("en")
    assert "VITE_JARVIS_LANG" not in fake.calls[0][1]


def test_the_spanish_build_sets_its_language_and_has_its_own_folder(monkeypatch, tmp_path):
    frontend, fake = _sandbox(monkeypatch, tmp_path)
    monkeypatch.setenv("VITE_JARVIS_LANG", "en-US")
    assert dp.build_bundle("es") == frontend / "dist-es"
    assert fake.calls[0][1]["VITE_JARVIS_LANG"] == "es-ES"
    assert (frontend / "dist-es" / dp.BUNDLE_STAMP).read_text().strip() == "lang=es-ES"
    # and the English one is a different folder with a different stamp
    dp.build_bundle("en")
    assert (frontend / "dist-en" / dp.BUNDLE_STAMP).read_text().strip() == "lang=default"


def test_a_newer_source_rebuilds_even_a_stamped_bundle(monkeypatch, tmp_path):
    import os
    frontend, fake = _sandbox(monkeypatch, tmp_path)
    dp.build_bundle("en")
    html = frontend / "dist-en" / "dashboard.html"
    os.utime(html, (1, 1))
    dp.build_bundle("en")
    assert len(fake.calls) == 2


# --- Ajustes: la clave de Fish es opcional ------------------------------------

def test_testing_an_empty_fish_key_does_not_turn_the_dot_red_when_there_is_a_voice():
    src = (REPO / "frontend" / "src" / "settings.ts").read_text(encoding="utf-8")
    handler = src[src.index('getElementById("btn-test-fish")'):]
    handler = handler[:handler.index("// Save preferences")]
    guard = handler.index("lastStatus.env_keys_set.fish_audio")
    # The guard runs BEFORE the request, and it falls back to the status.
    assert guard < handler.index("/api/settings/test-fish")
    assert "voiceMissing(lastStatus)" in handler
    assert re.search(r'voiceMissing\(lastStatus\) \? "red" : "off"', handler)
    # Whatever the server answers for a key that really is typed is unchanged.
    assert 'result.valid ? "green" : "red"' in handler
