#!/usr/bin/env bash
# Instala JARVIS como servicio de sistema (arranca con el equipo y se reinicia
# si cae). Pide sudo. Idempotente.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
sed -e "s#__USER__#$(id -un)#" -e "s#__DIR__#$DIR#g" -e "s#__HOME__#$HOME#" \
  "$DIR/scripts/jarvis.service" | sudo tee /etc/systemd/system/jarvis.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now jarvis.service
systemctl --no-pager --lines=0 status jarvis.service || true
