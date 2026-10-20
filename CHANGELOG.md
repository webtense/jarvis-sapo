# Changelog — jarvis-ubuntu

Fork de [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis) (© 2026 Ethan Rogers) orientado a Ubuntu/Linux.
Versionado semántico con prefijo `ubuntu-`. Uso personal y no comercial, igual que la licencia original.

## ubuntu-0.2.0 — F1 port Linux
- `host.py`: selección de plataforma en tiempo de llamada; el camino macOS queda intacto.
- Notificaciones con `notify-send`, terminal y navegador con gnome-terminal/Chrome/Firefox (URL validada, nunca opciones), editor con `xdg-open`.
- Capturas con gnome-screenshot/spectacle/grim/scrot + ImageMagick; lista de ventanas vía extensión Window Calls (GNOME), sway, Hyprland o wmctrl (X11); si el escritorio no lo permite, lo dice en vez de devolver una lista vacía.
- Responder permisos en otras sesiones mediante tmux, con la misma regla que macOS: si ningún panel es dueño del tty, no se pulsa nada. `scripts/jarvis-claude` lanza claude dentro de tmux.
- `preflight`: Accessibility y Screen Recording no aplican; nuevo aviso de herramientas Linux que faltan; caducidad del login OAuth leída de `~/.claude/.credentials.json`.
- Arreglo: un transcript reemplazado con mismo tamaño e inodo reutilizado (Linux) se detectaba como sin cambios; ahora se verifica el final de lo leído.
- Tests: dos tests de mayúsculas/minúsculas solo aplican a sistemas de archivos insensibles (macOS); 25 tests nuevos de las ramas Linux.
- `scripts/install-ubuntu.sh` y `scripts/start-ubuntu.sh` (puerto fijo 5173).

## ubuntu-0.1.0 — base del fork
- CI en Ubuntu (permitido fallar hasta completar la capa de plataforma) y escaneo de secretos con gitleaks.
- Aviso de licencia y atribución en el README.
- Hoja de ruta: F1 port Linux · F2 voz local (Piper + whisper.cpp) · F3 puente MCP · F4 casa/BTR · F5 calidad.
