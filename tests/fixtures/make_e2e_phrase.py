"""Regenera tests/fixtures/e2e_phrase_es.wav (ver README.md de esta carpeta).

Uso, con piper-tts y numpy instalados y una voz Piper en castellano:

    python tests/fixtures/make_e2e_phrase.py ruta/es_ES-davefx-medium.onnx

Sintetiza la frase con Piper, la lleva a 48 kHz mono de 16 bits (lo que Chrome
espera de un micrófono falso) y le pone un silencio delante y otro detrás: el
fichero se repite en bucle como micrófono, y el silencio final separa una
repetición de la siguiente para que el servidor las vea como frases distintas.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

PHRASE = "Jarvis, ¿está puesta la alarma?"
OUT_RATE = 48000
LEAD_SEC = 0.3
TAIL_SEC = 1.2
OUT = Path(__file__).with_name("e2e_phrase_es.wav")


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Remuestreo por FFT (relleno de ceros del espectro): sin dependencias."""
    n_out = int(round(len(x) * dst / src))
    spectrum = np.fft.rfft(x)
    out = np.zeros(n_out // 2 + 1, dtype=complex)
    k = min(len(spectrum), len(out))
    out[:k] = spectrum[:k]
    return np.fft.irfft(out, n_out) * (n_out / len(x))


def main(model: str) -> None:
    import io
    from piper import PiperVoice
    voice = PiperVoice.load(model)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        voice.synthesize_wav(PHRASE, w)
    buf.seek(0)
    with wave.open(buf, "rb") as r:
        src_rate = r.getframerate()
        pcm = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2").astype(np.float64)
    audio = resample(pcm, src_rate, OUT_RATE)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio * (0.8 * 32767 / peak)             # normaliza al 80 %
    audio = np.concatenate([np.zeros(int(LEAD_SEC * OUT_RATE)), audio,
                            np.zeros(int(TAIL_SEC * OUT_RATE))])
    data = np.clip(audio, -32768, 32767).astype("<i2")
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_RATE)
        w.writeframes(data.tobytes())
    print(f"{OUT} {len(data) / OUT_RATE:.2f} s, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
