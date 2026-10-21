# Fixtures de audio para las pruebas E2E

## e2e_phrase_es.wav

Frase corta en castellano para el micrófono falso de Chromium
(`--use-file-for-fake-audio-capture`) en `tests/e2e/`.

- **Texto:** "Jarvis, ¿está puesta la alarma?"
- **Formato:** WAV, 48 kHz, mono, 16 bits, 3,08 s (0,3 s de silencio, 1,4 s de voz, 1,2 s de silencio), unos 290 KB.
- **Origen:** sintetizada con [Piper](https://github.com/OHF-Voice/piper1-gpl) y la voz `es_ES-davefx-medium`, remuestreada de 22,05 a 48 kHz y normalizada al 80 %. No es una grabación de ninguna persona.
- **Licencia:** el conjunto de datos de la voz es CC0 (ver su
  [ficha](https://huggingface.co/rhasspy/piper-voices/blob/main/es/es_ES/davefx/medium/MODEL_CARD)),
  así que el audio generado se puede redistribuir sin condiciones. Piper y la voz no se incluyen en el repositorio.
- **Regenerar:** `python tests/fixtures/make_e2e_phrase.py ruta/es_ES-davefx-medium.onnx` (necesita `piper-tts` y `numpy`).
- **Comprobado a mano:** el 81 % de la energía está entre 300 y 3400 Hz y Whisper (`base`) la transcribe como "Farviste, está puesta la alarma?". Las pruebas no dependen de que Whisper la entienda: usan un transcriptor falso.

El silencio final es deliberado: Chromium reproduce el fichero en bucle y ese hueco (más de 0,9 s) separa una repetición de la siguiente para que el servidor las vea como frases distintas.
