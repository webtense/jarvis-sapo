# Reconocimiento de voz local y palabra de activación

Por defecto JARVIS escucha con la Web Speech API de Chrome: el audio viaja a la
nube de Google y no hay palabra de activación. Esta opción lo sustituye por
reconocimiento en el propio equipo. Está desactivada: sin tocar `.env`, nada
cambia.

## Activarla

```bash
JARVIS_INSTALL_STT=1 scripts/install-ubuntu.sh   # paquetes + modelos
```

y en `.env`:

```
JARVIS_STT=local
JARVIS_WAKE_WORD=on        # opcional: solo escucha tras "Hey Jarvis"
JARVIS_STT_MODEL=base      # opcional; en español se usa siempre el multilingüe
```

Reinicia JARVIS. Si falta algún paquete o modelo, JARVIS arranca igual, sigue
usando el reconocedor del navegador y el preflight dice qué falta.

## Cómo funciona

1. El servidor avisa en el frame `config` de `/ws/voice` con `"stt": "local"`
   (solo cuando es cierto; por defecto el frame no cambia).
2. La página captura el micrófono, lo convierte a PCM16 mono 16 kHz en un
   AudioWorklet (`frontend/src/localmic.ts`) y lo envía por `/ws/mic`, detrás
   de la misma puerta Origin/Host que el resto de sockets.
3. `stt_local.py` trocea en frames de 80 ms y recorre la máquina de estados
   `listening -> wake -> recording -> transcribing`. El VAD es por energía, en
   Python puro. Con palabra de activación, un modelo openWakeWord
   (`hey_jarvis`) abre la ventana; sin ella, la voz abre la frase directamente.
4. faster-whisper (CPU, `int8`) transcribe la frase en `JARVIS_LANGUAGE`. El
   texto entra en `server._handle_final_transcript`, la misma función que
   procesa los finales del navegador: turnos, rechazo de eco e interrupciones
   no se duplican. La respuesta sale por `/ws/voice` como siempre.

## Eco y silencio

Con `JARVIS_MUTE_MIC_DURING_SPEECH` activado, el servidor descarta el audio
mientras JARVIS habla. Sin ello decide el rechazo de eco de `speech.py`, igual
que con el navegador. El navegador además aplica su cancelación de eco al abrir
el micrófono. Esc sigue siendo la forma fiable de callarlo.

## Recursos

El modelo no se carga al arrancar: se carga con la primera frase y se descarga
tras `JARVIS_STT_IDLE_SEC` (300 s por defecto) sin uso. Modelos en `data/stt/`.

## Límites conocidos

- No hay texto provisional: la interrupción por voz mientras JARVIS habla solo
  actúa cuando termina la frase.
- El modelo preentrenado de openWakeWord es en inglés ("hey jarvis"); con
  acento o en castellano puede fallar más. Una palabra solo "Jarvis" exigiría
  entrenar un modelo propio.
- Mientras se transcribe, el audio se encola en el socket y se procesa después.
