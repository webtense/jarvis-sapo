#!/usr/bin/env bash
# Registra el puente de JARVIS como servidor MCP de Claude Code (alcance user),
# para que cualquier sesión (terminal, VS Code, Cowork) pueda hablarte.
#
#   scripts/install-bridge.sh              instala (o reinstala)
#   scripts/install-bridge.sh --dry-run    muestra el comando sin ejecutarlo
#   scripts/install-bridge.sh --uninstall  lo quita
#
# El token del puente NO va en la línea de comandos ni en ~/.claude.json: solo
# la RUTA del fichero (0600, en el directorio de datos de JARVIS). Este script
# crea el fichero si falta, pero nunca lo lee ni lo imprime.
set -euo pipefail

NAME="jarvis-bridge"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$REPO/jarvis_bridge_mcp.py"
MODE="install"
case "${1:-}" in
  "") ;;
  --dry-run) MODE="dry" ;;
  --uninstall) MODE="uninstall" ;;
  *) echo "Uso: $0 [--dry-run|--uninstall]" >&2; exit 2 ;;
esac

command -v claude >/dev/null || { echo "ERROR: falta 'claude' (Claude Code) en el PATH." >&2; exit 1; }

if [[ "$MODE" == "uninstall" ]]; then
  claude mcp remove "$NAME" --scope user
  echo "Quitado. El fichero del token sigue en el directorio de datos de JARVIS."
  exit 0
fi

[[ -f "$SERVER" ]] || { echo "ERROR: no encuentro $SERVER" >&2; exit 1; }

# Python: el venv del repo si existe (es el que JARVIS usa), si no el del sistema.
PY="${JARVIS_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$REPO/.venv/bin/python3" ]]; then PY="$REPO/.venv/bin/python3"; else PY="$(command -v python3)"; fi
fi

# Directorio de datos: el del entorno, o el que dice .env (solo esa clave, sin
# 'source'), o el de por defecto del repo.
DATA_DIR="${JARVIS_DATA_DIR:-}"
if [[ -z "$DATA_DIR" && -f "$REPO/.env" ]]; then
  DATA_DIR="$(sed -n 's/^JARVIS_DATA_DIR=//p' "$REPO/.env" | tail -n1 | tr -d "\"'")"
fi

# Crea el token si falta y devuelve SOLO su ruta.
TOKEN_FILE="$(JARVIS_DATA_DIR="$DATA_DIR" "$PY" -c '
import sys
sys.path.insert(0, sys.argv[1])
import data_paths
data_paths.ensure_bridge_token()
print(data_paths.bridge_token_path())
' "$REPO")"

# URL del servidor: https si hay certificados junto a server.py (así arranca).
PORT="${JARVIS_PORT:-8340}"
SCHEME="http"
[[ -f "$REPO/cert.pem" && -f "$REPO/key.pem" ]] && SCHEME="https"
URL="$SCHEME://127.0.0.1:$PORT/internal/bridge"

CMD=(claude mcp add "$NAME" --scope user
     --env "JARVIS_BRIDGE_URL=$URL"
     --env "JARVIS_BRIDGE_TOKEN_FILE=$TOKEN_FILE"
     -- "$PY" "$SERVER")

if [[ "$MODE" == "dry" ]]; then
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi

# Idempotente: si ya estaba, se sustituye.
claude mcp remove "$NAME" --scope user >/dev/null 2>&1 || true
"${CMD[@]}"

echo "Registrado '$NAME' (alcance user) -> $SERVER"
echo "Token: $TOKEN_FILE (0600, no se muestra)"
echo "Sirve en las sesiones NUEVAS de Claude Code. Guía: docs/bridge.md y docs/bridge-skill.md"
