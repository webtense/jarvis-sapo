#!/usr/bin/env bash
# Prepara JARVIS en Ubuntu/Debian: paquetes del sistema, venv, Chromium de
# Playwright, dependencias del frontend y certificados locales.
# No pide ni escribe ninguna clave. Idempotente: se puede repetir.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Comprobando versiones"
# Se comprueba antes de tocar nada: el código usa `X | None` en tiempo de
# ejecución (Python 3.9 falla al importar) y Vite necesita Node moderno.
command -v python3 >/dev/null || { echo "ERROR: falta python3 (se necesita 3.11 o superior)." >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "ERROR: se necesita Python 3.11 o superior; tienes $(python3 -V 2>&1)." >&2; exit 1; }
if command -v node >/dev/null; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  [[ "$NODE_MAJOR" -ge 18 ]] || {
    echo "ERROR: se necesita Node 18 o superior; tienes $(node -v)." >&2; exit 1; }
elif [[ "${JARVIS_SKIP_APT:-0}" == "1" ]]; then
  echo "ERROR: falta Node (se necesita 18 o superior) y JARVIS_SKIP_APT=1 impide instalarlo." >&2; exit 1
else
  echo "AVISO: Node aún no está instalado; se comprobará la versión después de apt." >&2
fi

if [[ "${JARVIS_SKIP_APT:-0}" == "1" ]]; then
  echo "==> Paquetes del sistema: omitidos (JARVIS_SKIP_APT=1; deben estar ya instalados)"
else
  echo "==> Paquetes del sistema (pide sudo)"
  sudo apt-get install -y --no-install-recommends \
    tmux libnotify-bin imagemagick gnome-screenshot openssl python3-venv
fi

echo "==> Entorno virtual de Python"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt -r requirements-linux.txt
.venv/bin/python -m playwright install chromium

echo "==> Voz local (Piper, ~60 MB, sin clave ni red después de descargarla)"
LANGUAGE="${JARVIS_LANGUAGE:-es}"
case "$LANGUAGE" in
  es*) DEFAULT_VOICE=es_ES-davefx-medium ;;
  *)   DEFAULT_VOICE=en_GB-alan-medium ;;
esac
VOICE="${PIPER_VOICE:-$DEFAULT_VOICE}"
[[ -f "data/voices/$VOICE.onnx" ]] || \
  .venv/bin/python -m piper.download_voices --download-dir data/voices "$VOICE"

echo "==> Frontend"
command -v npm >/dev/null || { echo "ERROR: falta npm (instala nodejs y npm, Node 18 o superior)." >&2; exit 1; }
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 18 ]] || { echo "ERROR: se necesita Node 18 o superior; tienes $(node -v)." >&2; exit 1; }
(cd frontend && npm ci)

echo "==> Certificados locales (el proxy de Vite apunta a https://localhost:8340)"
if [[ ! -f cert.pem || ! -f key.pem ]]; then
  openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
    -days 365 -nodes -subj '/CN=localhost' 2>/dev/null
  chmod 600 key.pem
fi

[[ -f .env ]] || cp .env.example .env
grep -q '^JARVIS_LANGUAGE=' .env || printf '\n# Idioma de JARVIS: es | en\nJARVIS_LANGUAGE=%s\n' "$LANGUAGE" >> .env

command -v claude >/dev/null || echo "AVISO: falta Claude Code (npm install -g @anthropic-ai/claude-code) y 'claude' para iniciar sesión."
echo "Listo. Arranca con: scripts/start-ubuntu.sh"
