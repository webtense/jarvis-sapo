#!/usr/bin/env bash
# Arranca el backend (127.0.0.1) y Vite en el puerto fijo 5173.
# Con --strictPort, si 5173 está ocupado falla en vez de cambiar de puerto:
# Chrome guarda el permiso del micrófono por puerto y lo perdería sin avisar.
set -euo pipefail

cd "$(dirname "$0")/.."
trap 'kill 0' EXIT

# JARVIS_LANGUAGE (en|es) from the environment or .env decides what the browser
# listens for. Read the one line; never source .env (it holds secrets).
LANGUAGE="${JARVIS_LANGUAGE:-$(sed -n 's/^JARVIS_LANGUAGE=//p' .env 2>/dev/null | tail -1 | tr -d '"'"'"' ')}"
case "${LANGUAGE:-en}" in
  es*) export VITE_JARVIS_LANG=es-ES ;;
  *)   export VITE_JARVIS_LANG=en-US ;;
esac

.venv/bin/python server.py --host 127.0.0.1 &
# Vite directamente, sin pasar por `npm run`: es lo mismo que hace el script
# "dev" y no depende de qué `npm` haya primero en el PATH (un envoltorio puede
# bloquear `npm run dev`).
(cd frontend && ./node_modules/.bin/vite --strictPort --port 5173) &

echo "JARVIS: http://localhost:5173  ·  panel: http://localhost:5173/dashboard.html"
wait
