# Voz de salida: Piper, Kokoro, Edge y Fish

JARVIS habla con uno de cuatro motores. Por defecto no cambia nada: sin tocar
`.env`, habla Piper con `es_ES-davefx-medium` (o Fish si tienes una clave
configurada), como en la versión anterior.

| Motor | Dónde corre | Coste | Instalación |
|---|---|---|---|
| **Piper** | en tu equipo | gratis | viene con `install-ubuntu.sh` |
| **Kokoro** | en tu equipo | gratis | opcional (`JARVIS_INSTALL_KOKORO=1`) |
| **Edge** | **nube de Microsoft** | gratis, sin clave | opcional (`JARVIS_INSTALL_EDGE=1`) |
| **Fish Audio** | nube de Fish | de pago (crédito) | clave en Ajustes |

## Cómo elegir

En **Ajustes → Claves de API** hay un selector de motor y de voz con solo lo que
está instalado, un botón **Probar voz** (dice una frase corta con ese motor y esa
voz, sin guardar nada) y **Guardar voz**. El cambio se aplica desde la frase
siguiente, sin reiniciar. Arriba se ve el motor que habla de verdad ahora mismo.

A mano, en `.env`:

```
JARVIS_TTS_PROVIDER=auto      # auto | piper | kokoro | edge | fish | none
JARVIS_TTS_VOICE=es_MX-claude-high
```

- `auto` (por defecto): Fish si hay clave y funciona; si no, Piper; si Piper no
  está, Kokoro; si no hay ninguno, solo texto. **`auto` nunca elige Edge.**
- `JARVIS_TTS_VOICE` es un solo valor y cada motor lo toma solo si tiene la
  forma de sus nombres, así que elegir una voz de Kokoro no estropea el respaldo
  a Piper:
  - Piper: `es_ES-davefx-medium` (la voz debe estar instalada en `data/voices/`).
  - Kokoro: `em_alex`, `ef_dora`, `em_santa`.
  - Edge: `es-ES-AlvaroNeural`, `es-ES-ElviraNeural`.
- `PIPER_VOICE` y `PIPER_MODEL` siguen funcionando como antes. Orden para Piper:
  `JARVIS_TTS_VOICE` (si es una voz de Piper instalada), `PIPER_MODEL`,
  `PIPER_VOICE`, la voz por defecto del idioma.
- Kokoro busca sus ficheros en `data/voices/kokoro/` (se cambia con
  `KOKORO_DIR`): `kokoro-v1.0.int8.onnx` (o `kokoro-v1.0.onnx`) y
  `voices-v1.0.bin`.

## Si un motor falla, JARVIS no se queda mudo

Cada frase se intenta con el motor elegido y, si no sale, con Piper:

- Kokoro o Edge elegidos y fallan: esa misma frase se dice con Piper.
- Edge además queda en pausa 2 minutos (la siguiente frase va directa a Piper en
  vez de esperar a un servicio caído) y se reintenta después.
- Fish con `auto` y clave rechazada o sin crédito (`401`, `402`, `403`): se usa
  Piper 10 minutos; con un fallo de red, 1 minuto. Con `JARVIS_TTS_PROVIDER=fish`
  forzado no hay respaldo: es lo que pediste.
- Si pides Kokoro o Edge y no están instalados, habla Piper.

Ajustes lo dice con palabras cuando ocurre: por ejemplo, con Fish configurado y
sin crédito, "Fish rechaza la clave: sin crédito o clave inválida. Usando Piper."
Si tienes una clave de Fish sin crédito y no la vas a usar, quítala de `.env`
o elige Piper en Ajustes: así no se hace ni un intento por frase en cada reinicio.

## Privacidad

- **Piper y Kokoro**: todo en tu equipo. Tras descargar el modelo no hay red.
- **Edge**: **el texto de cada frase sale de tu equipo hacia el servicio en línea
  de Microsoft.** Eso incluye lo que JARVIS diga de tus proyectos, de la casa o de
  tu agenda. Por eso es estrictamente opt-in: solo con
  `JARVIS_TTS_PROVIDER=edge` explícito (a mano o en Ajustes, donde se avisa),
  nunca por `auto`. No es una API oficial: `edge-tts` imita al lector en voz alta
  de Microsoft Edge y puede dejar de funcionar sin aviso; entonces habla Piper.
  JARVIS escribe una vez en el registro que Edge está activo; nunca escribe el
  texto.
- **Fish**: también es nube; el texto sale hacia Fish Audio, como siempre.

## Comparativa medida

Medida en este equipo (8 núcleos, sin GPU, Python 3.14.4), con cuatro frases en
castellano de 1 a 5 s de audio, cada una dos veces; se da la segunda pasada. Las
cifras de tiempo son de una pasada con la máquina casi en reposo (carga 1-3); una
primera tanda con la máquina saturada (carga 25-35) daba tiempos 3 o 4 veces
peores en todas las voces, así que **compara ratios, no valores absolutos**.

**No se ha escuchado ninguna.** Esta tabla dice qué es objetivamente más pesado o
más lento, no cuál suena mejor: eso hay que decidirlo con el botón Probar voz.

| Voz | Modelo en disco | Memoria (pico) | Carga | Frase corta (1,2 s de audio) | Frase larga (5,5 s) | Tiempo / audio |
|---|---|---|---|---|---|---|
| Piper `es_ES-davefx-medium` (por defecto) | 63 MB | 264 MB | 1,4 s | 0,06 s | 0,27 s | 0,05 |
| Piper `es_MX-claude-high` | 63 MB | 318 MB | 0,9 s | 0,06 s | 0,33 s | 0,04 |
| Piper `es_ES-sharvard-medium` (2 voces) | 77 MB | 339 MB | 1,3 s | 0,04 s | 0,22 s | 0,04 |
| Piper `es_AR-daniela-high` | 114 MB | 307 MB | 1,5 s | 0,41 s | 1,15 s | 0,24 |
| Kokoro `ef_dora` / `em_alex`, modelo `int8` | 92 MB + 28 MB de voces | 460 MB | 0,7 s | 0,55-0,62 s | 2,3-2,6 s | 0,37-0,42 |
| Kokoro `ef_dora` / `em_alex`, modelo completo | 326 MB + 28 MB | 688 MB | 0,7 s | 0,52-0,80 s | 2,1-2,8 s | 0,36-0,45 |
| Edge `es-ES-AlvaroNeural` | nada local | nada | — | no medido | no medido | no medido |

Qué dicen los números:

- **`es_MX-claude-high` y `es_ES-sharvard-medium` pesan lo mismo que davefx en
  tiempo** (la síntesis es 20-25 veces más rápida que hablar) y solo cuestan 55-75
  MB más de memoria. Si te suenan mejor, no hay razón técnica para no usarlas. La
  duración del audio de claude-high es mayor para el mismo texto (7,3 s frente a
  5,6 s en la frase larga): habla más despacio, no más cosas.
- **`es_AR-daniela-high` es 5 veces más lenta** que las demás de Piper y la
  de mayor tamaño en disco (114 MB). Sigue siendo más rápida que hablar, pero la
  primera frase tarda ~0,4 s en vez de ~0,06 s.
- **Kokoro es unas 8 veces más lento que Piper** en este equipo: una frase de 5 s
  tarda ~2,3 s en sintetizarse, y la primera de una respuesta (corta) ~0,6 s. JARVIS
  sintetiza frase a frase, así que entre dos frases largas puede haber un hueco
  perceptible si la CPU anda ocupada. Usa 1,7 veces más memoria que Piper.
- **El modelo `int8` de Kokoro no es más lento que el completo** en esta CPU, ocupa
  una cuarta parte en disco y 230 MB menos de memoria: es el que instala el script
  y el que se prefiere si están los dos.
- **Piper davefx se mantiene como valor por defecto**: ninguna otra voz mide mejor
  en latencia, y no hay forma de medir cuál suena mejor sin escucharlas.

### Kokoro en Python 3.14

Funciona. `kokoro-onnx 0.6.1` instala con ruedas ya publicadas (`onnxruntime 1.31`,
`numpy 2.5.4`) en un entorno con Python 3.14.4, y trae `espeak-ng` empaquetado
(`espeakng-loader`), así que no hace falta instalar nada con `apt`. El entorno
con todas sus dependencias ocupa 151 MB. Las voces en castellano de Kokoro 1.0 son
tres: `ef_dora` (femenina), `em_alex` y `em_santa` (masculinas).

### Lo que no se ha medido

- **Edge**: no se ha hecho ninguna llamada a Microsoft (sería sacar texto del
  equipo solo para medir). Está probado con un servicio simulado, y sus tiempos
  dependerán de tu conexión. `edge-tts 7.3.1` instala en 12 MB.
- Calidad percibida de todas las voces.
- Qué pasa con Kokoro o Piper en otros equipos con menos núcleos.

## Instalar las voces

```bash
JARVIS_INSTALL_VOICES=1 scripts/install-ubuntu.sh   # Piper: es_MX-claude-high y es_ES-sharvard-medium
JARVIS_INSTALL_KOKORO=1 scripts/install-ubuntu.sh   # Kokoro: paquete + modelo int8 + voces (~120 MB)
JARVIS_INSTALL_EDGE=1   scripts/install-ubuntu.sh   # Edge: solo el paquete (nube)
```

Se pueden combinar. El script no cambia la voz activa: se elige después en Ajustes.
Los ficheros de Kokoro se descargan de las publicaciones de `kokoro-onnx` en GitHub
y se comprueban contra una suma SHA-256 fija; si no coincide, se descartan.

Para instalarlas a mano, `requirements-linux.txt` lleva `kokoro-onnx` y `edge-tts`
comentadas, y las voces de Piper se bajan con
`python -m piper.download_voices --download-dir data/voices <voz>`.

## Costes

Piper y Kokoro: nada, salvo disco, memoria y algo de CPU. Edge: gratis y sin clave,
pero sin garantía ni contrato. Fish: crédito de pago; una clave sin crédito ya no
deja a JARVIS mudo (ver arriba), pero tampoco te da la voz de Fish.
