#!/usr/bin/env bash
# Prepara JARVIS en Ubuntu/Debian: paquetes del sistema, venv, Chromium de
# Playwright, dependencias del frontend y certificados locales.
# No pide ni escribe ninguna clave. Idempotente: se puede repetir.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Paquetes del sistema (pide sudo)"
sudo apt-get install -y --no-install-recommends \
  tmux libnotify-bin imagemagick gnome-screenshot openssl python3-venv

echo "==> Entorno virtual de Python"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt -r requirements-linux.txt
.venv/bin/python -m playwright install chromium

echo "==> Voz local (Piper, ~60 MB, sin clave ni red después de descargarla)"
VOICE="${PIPER_VOICE:-es_ES-davefx-medium}"
[[ -f "data/voices/$VOICE.onnx" ]] || \
  .venv/bin/python -m piper.download_voices --download-dir data/voices "$VOICE"

echo "==> Frontend"
(cd frontend && npm ci)

echo "==> Certificados locales (el proxy de Vite apunta a https://localhost:8340)"
if [[ ! -f cert.pem || ! -f key.pem ]]; then
  openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
    -days 365 -nodes -subj '/CN=localhost' 2>/dev/null
  chmod 600 key.pem
fi

[[ -f .env ]] || cp .env.example .env

command -v claude >/dev/null || echo "AVISO: falta Claude Code (npm install -g @anthropic-ai/claude-code) y 'claude' para iniciar sesión."
echo "Listo. Arranca con: scripts/start-ubuntu.sh"
