#!/usr/bin/env bash
# Instala el lanzador de escritorio de JARVIS (GNOME y cualquier escritorio que
# siga la especificación freedesktop). Solo escribe bajo el home de quien lo
# ejecuta; no usa sudo. Idempotente: se puede repetir y deja el mismo resultado.
#
#   scripts/install-launcher.sh               instala el lanzador y el icono
#   scripts/install-launcher.sh --autostart   además, abre JARVIS al iniciar sesión
#   scripts/install-launcher.sh --uninstall   quita lo que instaló (también el autoinicio)
#   scripts/install-launcher.sh --dry-run     muestra qué haría y no escribe nada
#
# Crea:
#   ~/.local/share/applications/jarvis.desktop
#   ~/.local/share/icons/hicolor/scalable/apps/jarvis.svg
#   ~/.config/autostart/jarvis-app.desktop        (solo con --autostart)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$REPO/scripts/jarvis-app"
ICON_SRC="$REPO/scripts/jarvis.svg"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DESKTOP_FILE="$DATA_HOME/applications/jarvis.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/scalable/apps/jarvis.svg"
AUTOSTART_FILE="$CONFIG_HOME/autostart/jarvis-app.desktop"

AUTOSTART=0
UNINSTALL=0
DRY=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Uso: $0 [--autostart] [--uninstall] [--dry-run]" >&2; exit 2 ;;
  esac
done

# Nada de lo que se escribe puede salirse del home del usuario.
for target in "$DESKTOP_FILE" "$ICON_FILE" "$AUTOSTART_FILE"; do
  case "$target" in
    "$HOME"/*) ;;
    *) echo "ERROR: $target no está bajo $HOME; no escribo fuera del home." >&2; exit 1 ;;
  esac
done

if (( UNINSTALL )); then
  for f in "$DESKTOP_FILE" "$ICON_FILE" "$AUTOSTART_FILE"; do
    if [[ -e "$f" ]]; then
      if (( DRY )); then echo "quitaría $f"; else rm -f -- "$f"; echo "quitado $f"; fi
    fi
  done
  command -v update-desktop-database >/dev/null 2>&1 && (( ! DRY )) &&
    update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
  exit 0
fi

[[ -x "$APP" ]] || { echo "ERROR: $APP no existe o no es ejecutable." >&2; exit 1; }
[[ -f "$ICON_SRC" ]] || { echo "ERROR: falta $ICON_SRC." >&2; exit 1; }

# La ruta va entre comillas en Exec=; estos caracteres tendrían que escaparse
# de una forma que no merece la pena soportar.
case "$APP" in
  *'"'*|*'$'*|*'`'*|*'\'*|*'%'*)
    echo "ERROR: la ruta del repositorio tiene caracteres que un .desktop no admite sin escapar: $APP" >&2
    exit 1 ;;
esac

desktop_entry() {
  # $1: "main" para el lanzador del menú, "autostart" para el de inicio de sesión.
  cat <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=JARVIS
Name[es]=JARVIS
GenericName=Voice assistant
GenericName[es]=Asistente de voz
Comment=Voice assistant on top of Claude Code
Comment[es]=Asistente de voz sobre Claude Code
Exec="$APP"
Icon=jarvis
Terminal=false
Categories=Utility;
Keywords=jarvis;voz;asistente;
StartupNotify=false
EOF
  if [[ "$1" == "main" ]]; then
    cat <<EOF
Actions=open;

[Desktop Action open]
Name=Open JARVIS
Name[es]=Abrir JARVIS
Exec="$APP"
EOF
  else
    cat <<EOF
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
EOF
  fi
}

# Escribe a un temporal en la misma carpeta y renombra: nunca queda a medias.
install_file() {
  local dest="$1" tmp
  mkdir -p "$(dirname "$dest")"
  tmp="$(mktemp "$dest.XXXXXX")"
  cat >"$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$dest"
}

if (( DRY )); then
  echo "escribiría $ICON_FILE (copia de $ICON_SRC)"
  echo "escribiría $DESKTOP_FILE:"
  desktop_entry main | sed 's/^/    /'
  (( AUTOSTART )) && echo "escribiría $AUTOSTART_FILE"
  exit 0
fi

install_file "$ICON_FILE" <"$ICON_SRC"
desktop_entry main | install_file "$DESKTOP_FILE"
echo "instalado $DESKTOP_FILE"

if (( AUTOSTART )); then
  desktop_entry autostart | install_file "$AUTOSTART_FILE"
  echo "instalado $AUTOSTART_FILE (JARVIS se abrirá al iniciar sesión)"
elif [[ -e "$AUTOSTART_FILE" ]]; then
  echo "nota: $AUTOSTART_FILE sigue en su sitio de una instalación anterior; --uninstall lo quita."
fi

command -v update-desktop-database >/dev/null 2>&1 &&
  update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 &&
  gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true

echo "Busque «JARVIS» en las Actividades de GNOME. Atajo de teclado: docs/escritorio.md"
