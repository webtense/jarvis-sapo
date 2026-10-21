# Changelog — jarvis-ubuntu

Fork de [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis) (© 2026 Ethan Rogers) orientado a Ubuntu/Linux.
Versionado semántico con prefijo `ubuntu-`. Uso personal y no comercial, igual que la licencia original.

## ubuntu-0.6.0 — todo en castellano, Firefox y Chrome
- **Traducción completa**: textos del servidor (`i18n.py`, `i18n_es.py`, 277 plantillas, tratamiento "señor"; se traduce en las fronteras y nunca el contenido no confiable), interfaz web (361 textos, fechas y plurales es-ES, `frontend/src/i18n.ts`) y documentación (`README.es.md`, `CONTRIBUTING.es.md`, `.env.example` y scripts). Con `JARVIS_LANGUAGE=en` la salida es idéntica a la original.
- **Ajustes**: la clave de Fish Audio es opcional; el panel muestra el proveedor de voz activo (Piper / Fish / ninguno) y enlaza a https://fish.audio/app/api-keys/.
- **Firefox y Chrome**: `JARVIS_STT=auto` ofrece la voz local y la página elige (Chrome conserva el reconocimiento del navegador, Firefox usa el local). Verificado con navegadores reales: Firefox abre el micrófono local; Chromium con una frase real de voz llega como turno de usuario.
- Whisper recibe una guía para escribir "Jarvis" (antes "Harbys").
- `i18n.t()` no mira textos de más de 1500 caracteres (los patrones son cuadráticos y 60 KB bloqueaban ~2 s el bucle de eventos).
- `scripts/start-ubuntu.sh` arranca Vite directamente, sin `npm run`.
- **Respaldo de voz**: si Fish Audio falla (402 por clave sin crédito, 401/403, red), JARVIS habla con Piper en vez de quedarse mudo: en modo `auto` la frase que encuentra el fallo y las siguientes (10 min si la cuenta rechaza la clave, 1 min si es la red) salen por Piper, y un éxito de Fish borra el aviso. El panel de Ajustes muestra el proveedor realmente en uso.
- Los mensajes de micrófono del navegador (permiso bloqueado, otra aplicación lo usa, reconocimiento perdido) salen en castellano.
- Conocido y pendiente: frases con nombres de fichero/proyecto idénticos a una entrada del catálogo ("later", "nothing") se traducen; las palabras de cancelar/repetir en castellano ("para", "repite") aún no se reconocen; voz real en Firefox sin probar.

## ubuntu-0.5.0 — voz local, puente MCP, casa en solo lectura
- **F2c** voz de entrada local opcional (`JARVIS_STT=local`, faster-whisper) y palabra de activación "Hey Jarvis" (openWakeWord, `JARVIS_WAKE_WORD=on`). Desactivado por defecto; sin los paquetes opcionales todo sigue igual. Endpoint `/ws/mic` tras la misma puerta Origin/Host; el texto final entra por `_handle_final_transcript`.
- **F3** puente MCP para sesiones de Claude Code (`jarvis_speak`, `jarvis_ask_user`, `jarvis_notify`, `jarvis_remember`): token propio y limitado, límites de frecuencia y longitud, texto siempre atribuido y tratado como no confiable. `jarvis_remember` solo propone: el usuario confirma por voz o en el panel, y por voz nunca sustituye una memoria existente.
- **F4** Home Assistant en solo lectura (`ha_readonly_mcp.py`: solo GET, rutas permitidas, sin seguir redirecciones, token por fichero 600) y ejemplo para el MCP de BTR; los runs sin supervisión no heredan estas credenciales.
- **F5** limpieza: eliminado `work_mode.py`, `JARVIS_SKIP_APT=1` en el instalador, comprobación de Python/Node, sección Ubuntu del README y CI sin ejecuciones duplicadas (push solo en `main`).
- Conocido y pendiente: reconexión en bucle de `/ws/mic` con dos pestañas abiertas y carga bloqueante del modelo de la palabra de activación (ver auditoría de F2c); voz local sin probar con micrófono real.

## ubuntu-0.4.1 — JARVIS residente
- `scripts/install-service.sh` + `scripts/jarvis.service`: servicio de sistema (arranca con el equipo, se reinicia si cae). La unidad vive en /etc para que un rsync `--delete` del perfil no pueda borrarla.

## ubuntu-0.4.0 — F2b castellano (`JARVIS_LANGUAGE=es`)
- `lang.py`: `JARVIS_LANGUAGE` (`en` por defecto, `es`) elige persona, voz Piper y locale del reconocimiento.
- `jarvis_home/CLAUDE.es.md`: persona traducida (mismas reglas y herramientas); cambiar de idioma sustituye la copia del cerebro solo si el usuario no la ha editado.
- Voz por idioma: `es_ES-davefx-medium` / `en_GB-alan-medium` (mayordomo británico).
- El navegador escucha en `es-ES` (`VITE_JARVIS_LANG`, lo fija `start-ubuntu.sh`).
- Las frases fijas del código siguen en inglés a propósito: casi todas son resultados de herramientas que el cerebro dice con sus palabras.

## ubuntu-0.3.0 — F2a voz local (Piper)
- `tts.py` con dos proveedores: Fish Audio (original) y Piper local, sin clave ni red. Elección automática (`JARVIS_TTS_PROVIDER=auto|fish|piper|none`): Fish si hay clave real, si no Piper, si no solo texto.
- Piper es dependencia opcional (`requirements-linux.txt`), cargada en diferido; la voz se precarga al arrancar (~1,5 s una vez, luego ~0,05–0,2 s por frase).
- `speech.py` calcula la duración de audio WAV para el mismo suelo de ack que el MP3.
- Preflight: el valor de relleno de `.env.example` ya no cuenta como clave; acepta la voz local.
- El instalador descarga la voz `es_ES-davefx-medium` (cambiable con `PIPER_VOICE`).

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
