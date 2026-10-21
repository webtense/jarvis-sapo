# Changelog — jarvis-ubuntu

Fork de [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis) (© 2026 Ethan Rogers) orientado a Ubuntu/Linux.
Versionado semántico con prefijo `ubuntu-`. Uso personal y no comercial, igual que la licencia original.

## ubuntu-0.8.0 — Google con escritura, pantalla de administración, voz y trato más naturales
- **Google con escritura y confirmación** (`google_actions.py`, desactivado por defecto): crear y modificar eventos de Calendar, crear/modificar/completar tareas y crear o ampliar notas como documentos de Google Docs en la carpeta 'JARVIS Notas' (Keep no tiene API en cuentas personales). Sin borrar, sin enviar correo, sin invitados ni compartir. El cerebro solo propone (`google_propose`), JARVIS lo dice entero y solo ejecuta tras tu "sí" oído después de la pregunta; token de escritura aparte (`google_write.json`, `scripts/google-auth.py --write`), política en `data/jarvis/google-actions.json`, simulacro `JARVIS_GOOGLE_DRYRUN=1`, auditoría y límite por minuto. Ver `docs/google-escritura.md`.
- **Pantalla de administración** `/admin.html`: Perfil, Voz, Escucha, Conexiones, Casa y Sistema, con un esquema cerrado de ajustes (nunca claves ni tokens; la lista blanca de la casa solo se edita a mano). Ver `docs/admin.md`.
- **Botón MUTE grande** en la pantalla principal (atajo `M`); con el mute, el micrófono local deja de enviar audio al servidor.
- **Casa**: opción `HA_ACTIONS_ALLOW_READ_TOKEN=1` para usar el token de lectura también en las acciones (decisión del usuario, no recomendada, con aviso en el arranque); filtro de exclusión de entidades del resort (`ha_exclude.py`).
- **Trato por tu nombre** (`HONORIFIC=name`): el nombre solo sale al saludar; el resto, sin tratamiento y con un "Dígame." al cerrar. Piper con velocidad ajustable (`PIPER_LENGTH_SCALE`, más de 1 = más lenta).
- **Google en solo lectura** (`google_readonly_mcp.py`, Calendar, Tasks, Gmail, Drive y Hojas), `docs/google.md`.
- Conocido y pendiente: el panel no tiene pantalla para las propuestas de Google (solo la ruta `POST /api/google/<id>/confirm|reject`); el modal de primer uso tapa el botón MUTE; nada de esto está probado con la API real de Google ni con voz real; el resort (Odoo, gestión, cámaras) queda fuera por licencia; `server.py` sin partir.

## ubuntu-0.7.0 — palabra "Jarvis", voces, casa con confirmación, escritorio y E2E
- **Palabra "Jarvis" sin modelo extra** (`JARVIS_WAKE_WORD=whisper`, `wake_text.py`): cada frase se transcribe y solo pasa al turno si empieza por "Jarvis" (variantes de transcripción y distancia de edición 1; se quita del texto), con una ventana de conversación (`JARVIS_WAKE_WINDOW_SEC`, 20 s). Coste: transcribe todo lo que oye. La opción `on` sigue siendo openWakeWord ("Hey Jarvis").
- **Interrumpir hablando** (`JARVIS_BARGE_IN=on`, opt-in): si hablas encima, JARVIS se calla (como `hush`). Umbrales calibrados con eco simulado, no con el equipo real; la tecla Esc sigue siendo la vía fiable.
- **Voz de salida**: proveedores Kokoro (local) y Edge TTS (nube de Microsoft, solo si lo eliges explícitamente; el texto sale del equipo) junto a Piper y Fish; selector de voz y botón "Probar voz" en Ajustes; aviso cuando Fish está configurado pero falla. Ver `docs/voz.md`.
- **Casa con confirmación** (desactivada por defecto): `home_actions.py`, lista blanca explícita en `data/jarvis/home-actions.json`, dominios prohibidos por código (alarma, cerraduras, cámaras, scripts, automatizaciones...), token propio distinto del de lectura, confirmación hablada solo tras oír la pregunta entera (palabras de confirmación estrictas, `JARVIS_HOME_CONFIRM_STRICT=1` exige "sí, hazlo"; cualquier otra frase de JARVIS desarma la propuesta), auditoría JSONL, límite de frecuencia y simulacro (`JARVIS_HOME_DRYRUN=1`). **Avisos proactivos** (`ha_watch.py`, `home-alerts.json`, opt-in): alarma, llegadas y salidas, puerta abierta, consumo; tope por hora para la alarma. Ver `docs/casa-control.md` (riesgos residuales incluidos).
- **Escritorio**: lanzador de GNOME (`scripts/install-launcher.sh`, `scripts/jarvis-app`, autoarranque opcional). Ver `docs/escritorio.md`.
- **Pruebas E2E en CI** (job `e2e`, Ubuntu): Chromium con audio de fichero a través de AudioWorklet y `/ws/mic`, Firefox eligiendo el micrófono local, y la interfaz en castellano.
- **Calidad de la traducción**: nombres de ficheros y proyectos ya no se traducen por error, tratamiento señor/señora según preferencia, plurales y concordancia, frases de errores web y del puente, punto de Fish neutro en Ajustes, `dist` del frontend robusto en los tests.
- **Arreglo del micrófono del navegador**: si el reconocimiento de Chrome no podía abrir el micrófono, entraba en un bucle de reinicios (medido en vivo: ~380 por segundo, 441.706 líneas de registro en una hora). Ahora hay a lo sumo un motor arrancando o escuchando, un único reinicio pendiente con espera creciente (hasta 15 s) y aviso tras 3 muertes seguidas; el servidor resume los eventos repetidos del micrófono en vez de registrarlos todos. Prueba E2E en la CI.
- Conocido y pendiente: la interfaz del panel para confirmar acciones de la casa (hoy solo por voz o API); voz real en Firefox, barge-in y palabra "Jarvis" sin probar con micrófono real; BTR/ACI sin conectar; `server.py` sin partir.

## ubuntu-0.6.0 — todo en castellano, Firefox y Chrome
- **Traducción completa**: textos del servidor (`i18n.py`, `i18n_es.py`, 277 plantillas, tratamiento "señor"; se traduce en las fronteras y nunca el contenido no confiable), interfaz web (361 textos, fechas y plurales es-ES, `frontend/src/i18n.ts`) y documentación (`README.es.md`, `CONTRIBUTING.es.md`, `.env.example` y scripts). Con `JARVIS_LANGUAGE=en` la salida es idéntica a la original.
- **Ajustes**: la clave de Fish Audio es opcional; el panel muestra el proveedor de voz activo (Piper / Fish / ninguno) y enlaza a https://fish.audio/app/api-keys/.
- **Firefox y Chrome**: `JARVIS_STT=auto` ofrece la voz local y la página elige (Chrome conserva el reconocimiento del navegador, Firefox usa el local). Verificado con navegadores reales: Firefox abre el micrófono local; Chromium con una frase real de voz llega como turno de usuario.
- Whisper recibe una guía para escribir "Jarvis" (antes "Harbys").
- `i18n.t()` no mira textos de más de 1500 caracteres (los patrones son cuadráticos y 60 KB bloqueaban ~2 s el bucle de eventos).
- `scripts/start-ubuntu.sh` arranca Vite directamente, sin `npm run`.
- **Respaldo de voz**: si Fish Audio falla (402 por clave sin crédito, 401/403, red), JARVIS habla con Piper en vez de quedarse mudo: en modo `auto` la frase que encuentra el fallo y las siguientes (10 min si la cuenta rechaza la clave, 1 min si es la red) salen por Piper, y un éxito de Fish borra el aviso. El panel de Ajustes muestra el proveedor realmente en uso.
- Los mensajes de micrófono del navegador (permiso bloqueado, otra aplicación lo usa, reconocimiento perdido) salen en castellano.
- **Cancelar y repetir en castellano**: con `JARVIS_LANGUAGE=es`, "para", "cancela", "espera", "basta"... interrumpen a JARVIS y "repite", "repítelo", "¿qué has dicho?"... le piden repetir (se suman a las inglesas; una pregunta real como "¿qué quieres decir con eso?" sigue yendo al cerebro).
- Conocido y pendiente: frases con nombres de fichero/proyecto idénticos a una entrada del catálogo ("later", "nothing") se traducen; voz real en Firefox sin probar.

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
