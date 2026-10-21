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
JARVIS_WAKE_WORD=on        # opcional: solo escucha tras "Hey Jarvis" (openWakeWord, inglés)
JARVIS_WAKE_WORD=whisper   # opcional: solo atiende frases que empiezan por "Jarvis" (castellano)
JARVIS_STT_MODEL=base      # opcional; en español se usa siempre el multilingüe
JARVIS_BARGE_IN=on         # opcional: hablar encima de JARVIS lo calla
```

Con `JARVIS_STT=auto` (lo recomendado si usas Firefox y Chrome) el servidor ofrece
la voz local y **la página elige**: Chrome conserva su reconocimiento de siempre y
Firefox, que no tiene ninguno, usa el local. Con `local`, todos los navegadores
usan el local.

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

## Palabra de activación en castellano: `JARVIS_WAKE_WORD=whisper`

`on` usa el modelo `hey_jarvis` de openWakeWord, que exige decir "Hey Jarvis" en
inglés. `whisper` no necesita ningún modelo más: **cada frase que detecta el VAD
se transcribe y solo pasa al turno si empieza por la palabra**.

- Se acepta "Jarvis, ...", "Oye Jarvis ...", "Hey, Jarvis. ...", con o sin coma y
  con o sin acentos, y las variantes que Whisper suele escribir (yarvis, harvis,
  jarbis, arvis, "jar vis"...), más cualquier palabra de 5 letras o más a una
  letra de distancia de "jarvis". Solo cuenta al **principio** de la frase:
  "dile a Jarvis que pare" no activa nada.
- La palabra se quita del texto: "Jarvis, enciende la luz" llega como
  "enciende la luz". Una frase que es solo "Jarvis" no entrega nada pero abre la
  ventana, así que se puede decir "Jarvis." y, tras una pausa, la orden.
- **Ventana de conversación**: tras una frase aceptada se puede seguir hablando
  sin repetir la palabra `JARVIS_WAKE_WINDOW_SEC` segundos (20 por defecto; `0` la
  desactiva). Cada frase aceptada la renueva, y mientras JARVIS habla no
  corre: su respuesta no la gasta.
- Frases descartadas: se transcriben pero ni se entregan ni se registran, y no se
  envían a la página.
- Lógica en `wake_text.py` (Python puro: normalización, coincidencia, recorte y
  `WakeGate`). Funciona con `local` y con `auto` (en las páginas que usan la voz
  local). Si activas `JARVIS_WAKE_WORD=on`, manda el modelo de audio.

**Coste.** Con esta opción JARVIS transcribe todo lo que oye, no solo lo que va
dirigido a él. Con openWakeWord el modelo de voz solo trabaja tras la palabra.
Mide la latencia (abajo) y elige el modelo con eso: recomendado `base`; `small`
acierta más pero tarda casi 3 veces más por frase, y `tiny` falla el nombre
(3 de 5 en la prueba). Con `medium` o más grande el preflight avisa.

## Interrumpir a JARVIS hablando: `JARVIS_BARGE_IN=on`

Si hablas **encima** de JARVIS, se calla, igual que con la tecla Esc o el botón
(`hush`: nada se guarda para decirlo luego). Solo en voz local (`local` o `auto`
en la página que la use) y desactivado por defecto.

Mientras JARVIS habla, el micrófono también lo oye a él. Para no confundir su
voz con la tuya:

1. El navegador cancela el eco al abrir el micrófono (`echoCancellation`,
   `localmic.ts`): es la primera defensa.
2. Una frase de audio de 80 ms solo cuenta si supera `JARVIS_BARGE_IN_RMS` (1200,
   muy por encima del 350 del VAD de frases) **y** 3 veces el nivel de eco que el
   detector ha medido en esa misma intervención (media lenta de lo que está por
   debajo del umbral: hablar más alto no sube tu propio listón).
3. Tiene que mantenerse así, sin cortes, `JARVIS_BARGE_IN_MS` (400 ms = 5
   frames). El eco de una voz tiene pausas entre palabras; una persona que quiere
   interrumpir sigue hablando.
4. Los primeros 6 frames (~0,5 s) de cada intervención solo aprenden el eco.

Mientras JARVIS habla, el audio **solo se mide**: no se transcribe, así que no se
gasta Whisper en su propia voz. Al dispararse se corta su voz, se descarta
lo que estuviera a medio grabar y se reenvían a la frase los últimos frames
oídos, de modo que lo que dijiste al interrumpir (por ejemplo "para, mejor
enciende la luz") se transcribe entero y entra como turno normal. Si hay
palabra de activación `whisper`, la ventana está abierta (hablabas con él).

Medido con frases de Piper mezcladas como "eco" de JARVIS y otra voz encima como
"usuario" (20 mezclas por caso, RMS medio de los frames con voz): con el valor
por defecto, JARVIS solo no se cortó nunca con eco de 200, 600, 1200 y 2500; con
eco 200 y voz del usuario a 3000 se cortó 20 de 20 veces (la primera vez, unos
0,9 s después de empezar a hablar), con eco 600 y voz 3000 18 de 20, con eco 600 y
voz 2000 6 de 20. Con un listón de 2,5 veces el eco en vez de 3 acierta más (16 de
20 en el último caso) pero con eco muy alto (1200 o más) se cortó solo 3 de 20
veces, y con 2 veces 14 de 20: por eso se queda en 3. Es audio sintético sin
altavoz ni cancelación real: confírmalo con tu equipo.

Qué no cubre: una voz baja por debajo del umbral, o un eco enorme con mala
cancelación, no se distingue de una interrupción. Si se corta con su propio eco,
sube `JARVIS_BARGE_IN_RMS` (2000-3000); si no te hace caso, bájalo o acorta
`JARVIS_BARGE_IN_MS`. Esc sigue funcionando siempre. Con
`JARVIS_MUTE_MIC_DURING_SPEECH` activado no hay interrupción por voz (el
micrófono está cerrado mientras habla) y el preflight lo avisa.

## Latencia medida

Equipo: portátil de 8 núcleos, sin GPU, con otras cargas en marcha (los números
son orientativos). faster-whisper 1.x, CPU, `int8`, `beam_size=1`, 4 hilos, la
guía de vocabulario de siempre. Frases en castellano generadas con Piper
(`es_ES-davefx-medium`, de 3,1 a 5,9 s con 0,3 s antes y 0,9 s de silencio
después, como las entrega el servidor), 9 frases x 5 repeticiones, tras calentar:

| Modelo | por frase (mediana) | p90 | "Jarvis" bien escrito |
|--------|--------------------:|----:|:---------------------:|
| tiny   | 0,30 s | 0,33 s | 3 de 5 |
| base   | 0,53 s | 0,57 s | 5 de 5 |
| small  | 1,42 s | 1,58 s | 5 de 5 |

`vad_filter` de faster-whisper (el VAD de Silero que filtra el silencio dentro
de la frase) no cambia nada de forma apreciable (0,30/0,52/1,43 s con él frente a
0,30/0,53/1,42 s sin él): se deja activado, que protege de alucinaciones con
ruido. Tampoco hay ganancia clara que justifique cambiar `JARVIS_STT_MODEL`
(sigue siendo `base`). Un caso real: la frase "Oye Jarvis, ¿está puesta la
alarma?" sale bien con `small`, como "Oye, JARVIS está apuesta la alarma" con
`base` y como "Hoy dejarlices esta puesta..." con `tiny`. Pruebas con voz
sintética: con tu voz y tu micrófono los números de acierto cambiarán.

Para esta opción el equilibrio es `base` (medio segundo) si quieres respuesta
ágil y `small` (cerca de 1,5 s) si dices órdenes largas y quieres que acierte;
`tiny` no, porque falla el nombre.

## Eco y silencio

Con `JARVIS_MUTE_MIC_DURING_SPEECH` activado, el servidor descarta el audio
mientras JARVIS habla. Sin ello decide el rechazo de eco de `speech.py`, igual
que con el navegador. El navegador además aplica su cancelación de eco al abrir
el micrófono. Esc sigue siendo la forma fiable de callarlo.

## Recursos

El modelo no se carga al arrancar: se carga con la primera frase y se descarga
tras `JARVIS_STT_IDLE_SEC` (300 s por defecto) sin uso. Modelos en `data/stt/`.

## Límites conocidos

- No hay texto provisional: sin `JARVIS_BARGE_IN=on`, hablar encima de JARVIS
  solo se tiene en cuenta cuando termina la frase.
- El modelo preentrenado de openWakeWord es en inglés ("hey jarvis"); con
  acento o en castellano puede fallar más. Para decir solo "Jarvis" en castellano
  está `JARVIS_WAKE_WORD=whisper`.
- La palabra por texto depende de que Whisper escriba el nombre: si lo oye mal
  del todo (p. ej. "dejarlis"), la frase se pierde y hay que repetirla.
- Mientras se transcribe, el audio se encola en el socket y se procesa después.
