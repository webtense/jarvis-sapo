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

# Más voces de salida (opcional). Ver docs/voz.md para la comparativa.
#
#   JARVIS_INSTALL_VOICES=1  descarga voces Piper adicionales (~60-115 MB cada
#                            una): es_MX-claude-high y es_ES-sharvard-medium en
#                            castellano; en_GB-northern_english_male-medium en inglés.
#   JARVIS_INSTALL_KOKORO=1  instala kokoro-onnx y el modelo cuantizado de Kokoro
#                            (~120 MB, local). Se activa en Ajustes o con
#                            JARVIS_TTS_PROVIDER=kokoro.
#   JARVIS_INSTALL_EDGE=1    instala edge-tts. Es una voz en la NUBE: el texto de
#                            cada frase sale hacia Microsoft. Nunca la elige
#                            `auto`; solo con JARVIS_TTS_PROVIDER=edge (o en Ajustes).
#
# Ninguna cambia la voz que ya tienes: se eligen después.
if [[ "${JARVIS_INSTALL_VOICES:-0}" == "1" ]]; then
  echo "==> Voces Piper adicionales"
  case "$LANGUAGE" in
    es*) EXTRA_VOICES="es_MX-claude-high es_ES-sharvard-medium" ;;
    *)   EXTRA_VOICES="en_GB-northern_english_male-medium" ;;
  esac
  for extra in $EXTRA_VOICES; do
    [[ -f "data/voices/$extra.onnx" ]] || \
      .venv/bin/python -m piper.download_voices --download-dir data/voices "$extra"
  done
fi

if [[ "${JARVIS_INSTALL_KOKORO:-0}" == "1" ]]; then
  echo "==> Kokoro (voz local opcional)"
  .venv/bin/pip install --quiet kokoro-onnx
  KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
  mkdir -p data/voices/kokoro
  # Suma de comprobación fija: si el fichero descargado no es el que se midió, se borra.
  declare -A KOKORO_SHA=(
    [kokoro-v1.0.int8.onnx]=6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb
    [voices-v1.0.bin]=bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
  )
  for f in "${!KOKORO_SHA[@]}"; do
    if [[ ! -f "data/voices/kokoro/$f" ]]; then
      curl -fL --retry 3 -o "data/voices/kokoro/$f.part" "$KOKORO_URL/$f"
      if echo "${KOKORO_SHA[$f]}  data/voices/kokoro/$f.part" | sha256sum -c --quiet -; then
        mv "data/voices/kokoro/$f.part" "data/voices/kokoro/$f"
      else
        rm -f "data/voices/kokoro/$f.part"
        echo "ERROR: $f no coincide con la suma de comprobación esperada; descartado." >&2
        exit 1
      fi
    fi
  done
fi

if [[ "${JARVIS_INSTALL_EDGE:-0}" == "1" ]]; then
  echo "==> Edge TTS (voz en la NUBE, opcional)"
  .venv/bin/pip install --quiet edge-tts
  echo "AVISO: con esta voz el texto de cada frase sale hacia Microsoft. Solo se usa si la eliges." >&2
fi

# Reconocimiento de voz LOCAL y palabra de activación (opcional, desactivado).
# Uso: JARVIS_INSTALL_STT=1 scripts/install-ubuntu.sh
# Instala faster-whisper y openWakeWord y descarga los modelos (~150 MB el
# base multilingüe, más el modelo hey_jarvis). Después, en .env:
#   JARVIS_STT=local      JARVIS_WAKE_WORD=on      (JARVIS_STT_MODEL=base)
if [[ "${JARVIS_INSTALL_STT:-0}" == "1" ]]; then
  echo "==> Reconocimiento de voz local (faster-whisper + openWakeWord)"
  .venv/bin/pip install --quiet faster-whisper openwakeword onnxruntime
  STT_MODEL="${JARVIS_STT_MODEL:-base}"
  STT_MODEL="${STT_MODEL%.en}"   # en castellano, nunca el modelo solo-inglés
  .venv/bin/python - "$STT_MODEL" <<'PY'
import sys
import data_paths
from faster_whisper.utils import download_model
d = data_paths.data_dir() / "stt"
d.mkdir(parents=True, exist_ok=True)
download_model(sys.argv[1], cache_dir=str(d))
import openwakeword.utils
openwakeword.utils.download_models(["hey_jarvis"])
PY
fi

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
