"""Voice-to-text worker for Niklaus's voice app (v0).

Runs with Darkroom's venv Python INSIDE Niklaus's app/voz/sandbox/voz.sb (kernel: no network, no
indirect paths to it), started by voz.py with `python -I -B -c <this file's text>`: the sandbox cannot
read this file, only the recording at the fixed path passed as AUDIO.

Protocol (one line each, UTF-8):
  worker -> {"listo": true}                         once the model is loaded
  voz.py -> "T"                                     transcribe the recording at SOURCE
  worker -> {"texto": "...", "s": 1.23}             or {"error": "..."}
EOF on stdin ends the worker.
"""
import json
import os
import sys
import time


def responder(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    audio = os.environ["VOZ_AUDIO"]
    modelo = os.environ.get("VOZ_WHISPER", "small")
    from faster_whisper import WhisperModel   # imported here so a missing venv fails with a clear message

    model = WhisperModel(modelo, device="cpu", compute_type="int8", local_files_only=True)
    responder({"listo": True})
    for linea in sys.stdin:
        if linea.strip() != "T":
            continue
        t0 = time.monotonic()
        try:
            segs, _ = model.transcribe(audio, language="es", beam_size=5, vad_filter=True,
                                       condition_on_previous_text=False,
                                       hotwords=os.environ.get("VOZ_PALABRAS") or None)
            texto = " ".join(s.text.strip() for s in segs).strip()
            responder({"texto": texto, "s": round(time.monotonic() - t0, 2)})
        except Exception as exc:              # report and keep serving; voz.py decides what to do
            responder({"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
