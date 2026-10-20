#!/usr/bin/env bash
# Arranca el backend (127.0.0.1) y Vite en el puerto fijo 5173.
# Con --strictPort, si 5173 está ocupado falla en vez de cambiar de puerto:
# Chrome guarda el permiso del micrófono por puerto y lo perdería sin avisar.
set -euo pipefail

cd "$(dirname "$0")/.."
trap 'kill 0' EXIT

.venv/bin/python server.py --host 127.0.0.1 &
(cd frontend && npm run dev -- --strictPort --port 5173) &

echo "JARVIS: http://localhost:5173  ·  panel: http://localhost:5173/dashboard.html"
wait
