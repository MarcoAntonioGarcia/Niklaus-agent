"""Serena text-to-speech worker for Niklaus's voice app (Qwen3-TTS 0.6B CustomVoice, MLX 8-bit).

Runs with Niklaus's venv/qwen-mlx Python INSIDE app/voz/sandbox/voz.sb (kernel: no network), started by
voz.py with `python -I -B -c <this file's text>`. One thread generates audio (streaming chunks), another
plays it, so playback never waits for generation.

Protocol (JSON lines):
  voz.py -> {"decir": "<frase>"}      queue a sentence
  voz.py -> {"callar": true}          drop the queue and stop the current sentence now
  worker -> {"listo": true}           model loaded and warmed up
  worker -> {"sonando": true}         first audio of an utterance reached the speaker
  worker -> {"libre": true}           nothing queued, nothing playing
  worker -> {"error": "..."}
EOF on stdin ends the worker. VOZ_SILENCIO=1 generates without playing (tests).
"""
import glob
import json
import os
import queue
import sys
import threading

import numpy as np

SILENCIO = os.environ.get("VOZ_SILENCIO") == "1"
ESTILO = os.environ.get("VOZ_ESTILO", "")
VOZ = os.environ.get("VOZ_HABLANTE", "serena")
try:
    RAPIDEZ = max(0.5, min(2.0, float(os.environ.get("VOZ_RAPIDEZ", "1.0"))))
except ValueError:
    RAPIDEZ = 1.0
try:
    TONO = max(0.6, min(1.3, float(os.environ.get("VOZ_TONO", "1.0"))))     # <1 = deeper voice
except ValueError:
    TONO = 1.0
salida_lock = threading.Lock()


class Tono:
    """Lowers (or raises) the pitch by resampling, keeping phase across streamed chunks; the Acelerador then
    brings the duration back. An original timbre from the engine's own speaker, not a copy of anyone's voice."""

    def __init__(self, factor):
        self.f, self.pos, self.ultimo = factor, 0.0, None

    def alimentar(self, x):
        if abs(self.f - 1.0) < 0.005 or not len(x):
            return x
        base = x if self.ultimo is None else np.concatenate([[self.ultimo], x])
        desfase = 0 if self.ultimo is None else 1
        idx = np.arange(self.pos + desfase, len(base) - 1, self.f, dtype=np.float64)
        self.pos = (idx[-1] + self.f - (len(base) - 1)) if len(idx) else self.pos - (len(base) - 1 - desfase)
        self.ultimo = float(x[-1])
        return np.interp(idx, np.arange(len(base)), base).astype(np.float32) if len(idx) else np.zeros(0, np.float32)


class Acelerador:
    """WSOLA time compression: speaks `factor`x faster keeping the pitch (no chipmunk). For each synthesis frame it
    searches, within ±tol of the ideal analysis position, the input offset whose waveform best continues the previous
    frame (cross-correlation), so the voice stays smooth and full-volume. Streaming and stateful; one per sentence."""

    def __init__(self, factor, N=1024, tol=400):
        self.factor = factor
        self.N, self.Hs, self.tol = N, N // 2, tol      # Hann at 50% overlap sums to 1.0 (amplitude preserved)
        self.Ha = max(1, int(round(self.Hs * factor)))
        self.win = np.hanning(N).astype(np.float32)
        self.buf = np.zeros(0, np.float32)              # input not yet trimmed; buf[0] is absolute index `base`
        self.base = 0
        self.ideal = 0                                  # ideal absolute analysis position of the next frame
        self.tail = None                                # windowed 2nd half of the previous synthesis frame

    def _seg(self, abs_idx, n):
        i = abs_idx - self.base
        return self.buf[i:i + n]

    def alimentar(self, x):
        if self.factor <= 1.001:
            return x.astype(np.float32)
        self.buf = np.concatenate([self.buf, x.astype(np.float32)])
        salida = []
        while self.ideal + self.tol + self.N <= self.base + len(self.buf):
            delta = 0
            if self.tail is not None:                   # pick the offset that best continues the last frame
                mejor = -1e30
                for d in range(-self.tol, self.tol + 1, 8):
                    seg = self._seg(self.ideal + d, self.Hs)
                    c = float(np.dot(seg, self.tail)) / (float(np.linalg.norm(seg)) + 1e-6)
                    if c > mejor:
                        mejor, delta = c, d
            frame = self._seg(self.ideal + delta, self.N) * self.win
            cabeza = frame[:self.Hs] + (self.tail if self.tail is not None else 0.0)
            salida.append(np.clip(cabeza, -1.0, 1.0).astype(np.float32))
            self.tail = frame[self.Hs:].copy()
            self.ideal += self.Ha
            recorte = max(0, self.ideal - self.tol - self.base)   # drop input we will not search again
            if recorte > 0:
                self.buf = self.buf[recorte:]
                self.base += recorte
        return np.concatenate(salida) if salida else np.zeros(0, np.float32)

    def terminar(self):
        if self.factor <= 1.001 or self.tail is None:
            return np.zeros(0, np.float32)
        cola, self.tail = self.tail, None
        return np.clip(cola, -1.0, 1.0).astype(np.float32)


def emitir(obj):
    with salida_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def main():
    from mlx_audio.tts.utils import load_model
    ruta = glob.glob(os.environ["HF_HOME"] + "/hub/models--mlx-community--Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit/snapshots/*")
    if len(ruta) != 1:
        emitir({"error": "modelo de Serena no encontrado"})
        return
    modelo = load_model(ruta[0])
    sr = modelo.sample_rate
    for _ in modelo.generate_custom_voice(text="Hola.", speaker=VOZ, language="Spanish", instruct=ESTILO):
        pass                                     # warm-up: compiles the graph before the first real turn
    frases, audio = queue.Queue(), queue.Queue(maxsize=64)
    estado = {"epoca": 0, "pendientes": 0}
    candado = threading.Lock()

    def generar():
        while True:
            item = frases.get()
            if item is None:
                audio.put(None)
                return
            epoca, texto = item
            tono, acel = Tono(TONO), Acelerador(RAPIDEZ / TONO)     # stretched by 1/TONO, so speed up that much more
            try:
                for trozo in modelo.generate_custom_voice(text=texto, speaker=VOZ, language="Spanish",
                                                          instruct=ESTILO, stream=True, streaming_interval=0.6):
                    if estado["epoca"] != epoca:
                        break
                    r = acel.alimentar(tono.alimentar(np.asarray(trozo.audio, dtype=np.float32).reshape(-1)))
                    if len(r):
                        audio.put((epoca, r))
                cola = acel.terminar()
                if len(cola) and estado["epoca"] == epoca:
                    audio.put((epoca, cola))
            except Exception as exc:             # report, keep serving
                emitir({"error": f"{type(exc).__name__}: {exc}"})
            audio.put((epoca, "fin"))

    def reproducir():
        import sounddevice as sd
        flujo = None if SILENCIO else sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
        if flujo:
            flujo.start()
        nuevo = True
        while True:
            item = audio.get()
            if item is None:
                if flujo:
                    flujo.stop()
                    flujo.close()
                return
            epoca, datos = item
            if estado["epoca"] != epoca:
                continue
            if isinstance(datos, str):           # end of one sentence
                with candado:
                    estado["pendientes"] = max(0, estado["pendientes"] - 1)
                    libre = estado["pendientes"] == 0
                if libre:
                    nuevo = True
                    emitir({"libre": True})
                continue
            if nuevo:
                nuevo = False
                emitir({"sonando": True})
            if flujo:
                flujo.write(datos)

    hilos = [threading.Thread(target=generar, daemon=True), threading.Thread(target=reproducir, daemon=True)]
    for h in hilos:
        h.start()
    emitir({"listo": True})
    for linea in sys.stdin:
        try:
            orden = json.loads(linea)
        except ValueError:
            continue
        if not isinstance(orden, dict):
            continue
        if orden.get("callar"):
            with candado:
                estado["epoca"] += 1
                estado["pendientes"] = 0
            while not frases.empty():
                try:
                    frases.get_nowait()
                except queue.Empty:
                    break
            emitir({"libre": True})
        elif isinstance(orden.get("decir"), str) and orden["decir"].strip():
            with candado:
                estado["pendientes"] += 1
                epoca = estado["epoca"]
            frases.put((epoca, orden["decir"][:600]))
    frases.put(None)
    hilos[1].join(timeout=30)


if __name__ == "__main__":
    main()
