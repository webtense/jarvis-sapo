# JARVIS en el escritorio y pruebas de extremo a extremo

## Lanzador de escritorio (GNOME)

`scripts/install-launcher.sh` deja JARVIS en el menú de aplicaciones, con icono, como cualquier otro programa. No usa sudo y solo escribe bajo su propio home.

```bash
scripts/install-launcher.sh               # lanzador + icono
scripts/install-launcher.sh --autostart   # además, abre JARVIS al iniciar sesión
scripts/install-launcher.sh --dry-run     # muestra qué haría, sin escribir
scripts/install-launcher.sh --uninstall   # lo quita todo, autoinicio incluido
```

Crea:

| Fichero | Para qué |
|---|---|
| `~/.local/share/applications/jarvis.desktop` | La entrada «JARVIS» de Actividades, con la acción «Abrir JARVIS» (clic derecho sobre el icono). |
| `~/.local/share/icons/hicolor/scalable/apps/jarvis.svg` | El icono (`scripts/jarvis.svg`). |
| `~/.config/autostart/jarvis-app.desktop` | Solo con `--autostart`: abre JARVIS cinco segundos después de iniciar sesión. |

Es idempotente: repetirlo deja exactamente lo mismo. Si mueve el repositorio de sitio, vuelva a ejecutarlo para que el lanzador apunte a la ruta nueva.

### Qué hace `scripts/jarvis-app`

1. Si el puerto 5173 ya escucha, no toca nada.
2. Si existe `jarvis.service`, pide a systemd que lo arranque. Sin sudo, polkit puede pedirle la contraseña en una ventana o negarse; en ese caso pasa al punto 3.
3. Si no, lanza `scripts/start-ubuntu.sh` en segundo plano con su usuario (registro en `~/.cache/jarvis/app.log`).
4. Espera hasta 60 segundos a que la página responda y la abre: Chrome o Chromium con `--app=http://localhost:5173` (ventana propia, sin barra de direcciones) o, si no hay ninguno, Firefox en una ventana nueva.

Variables opcionales: `JARVIS_URL`, `JARVIS_BROWSER` (por ejemplo `firefox`) y `JARVIS_WAIT_SEC`. `scripts/jarvis-app --start-only` solo se asegura de que el servicio esté en marcha.

Chrome guarda el permiso del micrófono por origen: la primera vez que abra la ventana, acéptelo; después no se vuelve a pedir mientras la dirección sea `http://localhost:5173`.

## Atajo de teclado: lo que se puede y lo que no

**No existe un atajo global «pulsar para grabar».** En Wayland ninguna aplicación puede escuchar teclas cuando no tiene el foco, y JARVIS no funciona por pulsación de todos modos: con la página abierta, el micrófono está siempre escuchando.

Lo que sí se puede es un atajo de GNOME que **abra JARVIS** (o lo traiga al frente) sin buscarlo con el ratón. Estos comandos crean uno con `Super+J`. Copian lo que hubiera en la lista de atajos personalizados y añaden uno; no los ejecuta nada de este repositorio, quien decide es usted:

```bash
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
KEY=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/jarvis/
CURRENT=$(gsettings get $SCHEMA custom-keybindings)
case "$CURRENT" in
  *"$KEY"*) ;;                                              # ya está en la lista
  "@as []") gsettings set $SCHEMA custom-keybindings "['$KEY']" ;;
  *)        gsettings set $SCHEMA custom-keybindings "${CURRENT%]}, '$KEY']" ;;
esac
SLOT=$SCHEMA.custom-keybinding:$KEY
gsettings set $SLOT name 'JARVIS'
gsettings set $SLOT command '/ruta/al/repositorio/scripts/jarvis-app'
gsettings set $SLOT binding '<Super>j'
```

Sustituya `/ruta/al/repositorio` por la del repositorio. Para quitarlo: `gsettings reset-recursively $SLOT` y elimine esa ruta de la lista `custom-keybindings`. También se puede hacer desde Ajustes → Teclado → Atajos personalizados, con el mismo nombre, comando y combinación.

Sin comprobar: si, con la ventana ya abierta, el atajo abre una segunda ventana o trae la primera al frente. Depende de Chrome y de la versión de GNOME.

### Silenciar a JARVIS: la tecla Esc

Con la página en primer plano, **Esc** corta lo que JARVIS está diciendo en ese momento (también el botón «Parar» que aparece mientras habla). Es una tecla y no una palabra a propósito: su voz sale por el altavoz y vuelve por el micrófono deformada, y un «para» mal entendido le cortaría sin que usted lo pidiera. Esc solo actúa con la página enfocada y solo mientras JARVIS habla; no silencia el micrófono (para eso, el botón del micrófono de la página).

## Pruebas de extremo a extremo (E2E)

Están en `tests/e2e/` y llevan el marcador `e2e`. `pytest` a secas **no** las ejecuta (igual que las `browser`), porque abren navegadores de verdad y compilan el frontend.

```bash
pip install -r requirements.txt
python -m playwright install chromium firefox     # o con --with-deps en una máquina limpia
(cd frontend && npm ci && npm run build)
pytest tests/e2e -m e2e -v
```

La primera vez compilan además el frontend en castellano en `frontend/dist-es/` (ignorada por git); es la misma compilación que `scripts/start-ubuntu.sh` hace con `JARVIS_LANGUAGE=es`. Sin Playwright, `npm` o los navegadores, las pruebas se saltan; con `JARVIS_E2E_STRICT=1` (lo que pone la CI) fallan.

### Qué comprueban

1. **Chromium con micrófono falso** (`--use-fake-device-for-media-stream` y `--use-file-for-fake-audio-capture` con `tests/fixtures/e2e_phrase_es.wav`, una frase sintetizada de unos 3 s). Con `JARVIS_STT=local`: el AudioWorklet remuestrea de 48 a 16 kHz y lo envía por `/ws/mic`; el servidor pasa por `recording`, `transcribing` y `listening`; llega un `transcript` y se convierte en un turno. Se comprueba además que el transcriptor recibió audio de 16 kHz de verdad (unos 2,4 s medidos; si el remuestreo fallara llegaría el triple).
2. **Firefox** (`media.navigator.streams.fake` y `media.navigator.permission.disabled`) con `JARVIS_STT=auto`: no tiene `SpeechRecognition`, así que la página elige `/ws/mic` y recibe `ready`.
3. **Página en castellano**: `lang="es"`, menú («Ajustes», «Reiniciar el servidor») y panel de Ajustes con sus secciones en castellano.

### Qué NO comprueban

- **Whisper**: la CI no tiene modelos. El servidor de la prueba (`tests/e2e/server_runner.py`) es el `server.app` real, pero con un transcriptor falso que devuelve siempre «Jarvis, ¿está puesta la alarma?».
- **Voz real en Firefox**: Firefox no admite un fichero como micrófono falso, solo un tono. Solo se prueba que elige el micrófono local y que el servidor responde `ready`.
- **El cerebro (`claude`)**, la voz de salida (Piper o Fish) y Home Assistant: el servidor arranca con `JARVIS_BRAIN_AUTOSTART=0`, `JARVIS_TTS_PROVIDER=none`, un directorio de datos y un `HOME` temporales, y las claves de Fish vacías. Nada toca sus datos ni su `.env`.
- **El micrófono ni el altavoz del equipo**, ni los permisos de su navegador de uso diario.

El audio de la frase, su origen y su licencia (CC0) están en `tests/fixtures/README.md`; `tests/fixtures/make_e2e_phrase.py` lo regenera.
