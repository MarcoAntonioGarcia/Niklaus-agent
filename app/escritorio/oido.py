"""Niklaus's ear: the microphone always open, cut into utterances by a simple energy detector (standard library).

ffmpeg (avfoundation) streams 16 kHz mono PCM through a pipe; nothing is written to disk until an utterance ends,
and then only to data/voz/turno.wav (overwritten every time, deleted on exit) for the sandboxed local Whisper.
Utterances that do not contain the wake word are discarded by the app without being shown or stored.
"""
import array
import math
import os
import re
import subprocess
import threading
import time
import unicodedata
import wave
from collections import deque

TASA, MUESTRA = 16000, 2
CUADRO = 480                      # 30 ms
INICIO, FIN = 4, 36               # frames above threshold to start (120 ms); quiet to end (~1.1 s). Measured
                                  # 2026-11-16: 0.75 s cut the owner off after the name; 1.5 s glued background
                                  # voices into one 72-word blob that buried the command.
PREVIO = 10                       # frames kept before the start (300 ms)
MIN_CUADROS, MAX_CUADROS = 10, 500   # 0.3 s … 15 s

# "Niklaus" as Whisper may write it: Niklaus, Niclaus, Nicklaus, Nikolaus. NOT "Nicolás" or "Niklas" (common names
# that a newscast or the TV would say), so only spellings with the "-aus" ending wake it.
# Also the short forms the owner uses: Nik, Nick, "Nikalus" (2026-11-16).
# Its full name is Marcus Niklaus (2026-11-16): Marcus/Markus also wake it, and so does the phrase "a trabajar".
# Short forms as Whisper really writes them when the owner says "Nick": nik, nick, nic, nique, niki, nicky, neck…
# Whisper sometimes glues the greeting to the name: "Hey Nick" -> «Eynick», "Oye Nik" -> «Oyenik» (owner, 2026-11-17).
_NOMBRE = re.compile(r"\b((h?e[iy]|oye?)?ni(c|k|ck)(o?laus|alus)?|(h?e[iy]|oye?)?ni(que|ki|kki|cky|cki|q)|neck|mar[ck]us)\b")
_FRASE = re.compile(r"^(hey|ey|oye|hola|bueno|ok|okay)?\s*a trabajar\b")


def normalizar(texto):
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def usar_nombres(nombres):
    """Replace the built-in wake names with the owner's own (single words, from local/ajustes.json)."""
    global _NOMBRE
    limpios = [normalizar(n) for n in nombres]
    limpios = [n for n in limpios if re.fullmatch(r"[a-z]{2,20}", n)]
    if limpios:
        _NOMBRE = re.compile(r"\b(" + "|".join(re.escape(n) for n in limpios) + r")\b")
    return bool(limpios)


def buscar_nombre(texto):
    """(heard the name?, the command around it). "Niklaus, ordena el escritorio" -> (True, "ordena el escritorio");
    "oye Niklaus" -> (True, ""); "ordena el escritorio, Niklaus" -> (True, "ordena el escritorio")."""
    original = str(texto or "")
    if _FRASE.search(normalizar(original)):             # "hey, a trabajar": a wake phrase, with or without an order after
        resto = re.split(r"(?i)a\s+trabajar", original, maxsplit=1)[-1].strip(" ,.;:!?¡¿")
        return True, resto
    palabras = original.split()
    for i, w in enumerate(palabras):
        if _NOMBRE.fullmatch(normalizar(w) or "-"):
            despues = " ".join(palabras[i + 1:]).strip(" ,.;:!?¡¿")
            if despues and _NOMBRE.fullmatch(normalizar(despues.split()[0]) or "-"):     # "Marcus Niklaus, …"
                despues = " ".join(despues.split()[1:]).strip(" ,.;:!?¡¿")
            antes = " ".join(palabras[:i]).strip(" ,.;:!?¡¿")
            if normalizar(antes) in ("", "oye", "hey", "ey", "hola", "oiga", "ok", "okay"):
                antes = ""
            return True, despues or antes
    return False, ""


def rms(cuadro):
    a = array.array("h")
    a.frombytes(cuadro)
    return math.sqrt(sum(x * x for x in a) / max(1, len(a)))


class Oido:
    """Calls al_empezar() when speech starts and al_terminar(pcm_bytes, t_inicio) when an utterance ends."""

    def __init__(self, ffmpeg, mic, al_empezar, al_terminar, al_fallar):
        self.ffmpeg, self.mic = ffmpeg, mic
        self.al_empezar, self.al_terminar, self.al_fallar = al_empezar, al_terminar, al_fallar
        self.p, self.silenciado, self._parar = None, False, threading.Event()
        self.dictando = False       # the owner is dictating to it: tolerate longer pauses and longer utterances

    def iniciar(self):
        self.p = subprocess.Popen([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-f", "avfoundation",
                                   "-i", f":{self.mic}", "-ac", "1", "-ar", str(TASA), "-f", "s16le", "-"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._escuchar, daemon=True).start()

    def _escuchar(self):
        # piso = the room's noise level, learned continuously. The threshold is RELATIVE to it (a fixed minimum
        # made the ear deaf with a low input volume: speech at ~230 never crossed 350; measured 2026-11-15).
        piso, voz_seguida, silencio, hablando = 30.0, 0, 0, False
        previo, actual, t_inicio = deque(maxlen=PREVIO), [], 0.0
        recientes = deque(maxlen=80)                  # quiet/loud flags of the last ~2.4 s
        try:
            while not self._parar.is_set():
                cuadro = self.p.stdout.read(CUADRO * MUESTRA)
                if not cuadro or len(cuadro) < CUADRO * MUESTRA:
                    break
                if self.silenciado:
                    hablando, actual, voz_seguida = False, [], 0
                    continue
                nivel = rms(cuadro)
                umbral = max(piso * 4.0, 45.0)
                if not hablando:
                    previo.append(cuadro)
                    if nivel > umbral:
                        voz_seguida += 1
                        if voz_seguida >= INICIO:
                            hablando, silencio, actual, t_inicio = True, 0, list(previo), time.monotonic()
                            recientes.clear()
                            self.al_empezar()
                    else:
                        voz_seguida = 0
                        piso = 0.95 * piso + 0.05 * min(nivel, piso * 2 + 5)   # adapts to the room, not to spikes
                    continue
                actual.append(cuadro)
                # the end of speech tolerates stray noises: most of the last ~0.8 s quiet, not a perfect silence
                recientes.append(nivel < umbral * 0.75)
                fin = 62 if self.dictando else FIN       # ~1.9 s of pause while dictating, ~1.1 s otherwise
                ventana = list(recientes)[-(fin + 4):]
                silencio = sum(ventana)
                tope = 1500 if self.dictando else MAX_CUADROS   # 45 s while dictating
                if (len(ventana) == fin + 4 and silencio >= fin) or len(actual) >= tope:
                    hablando, voz_seguida = False, 0
                    previo.clear()
                    if len(actual) - silencio >= MIN_CUADROS:
                        self.al_terminar(b"".join(actual), t_inicio)
                    actual = []
        except Exception as exc:
            self.al_fallar(f"{type(exc).__name__}: {exc}")
            return
        if not self._parar.is_set():
            err = b""
            try:
                err = self.p.stderr.read() or b""
            except Exception:
                pass
            motivo = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["el micrófono se cerró"]
            self.al_fallar(motivo[0][:200])

    def cerrar(self):
        self._parar.set()
        if self.p and self.p.poll() is None:
            self.p.kill()
            self.p.wait()


def guardar_wav(ruta, pcm):
    """Writes the utterance for the sandboxed transcriber; never through a link."""
    if os.path.islink(ruta):
        raise RuntimeError(f"{ruta} es un enlace")
    a = array.array("h")
    a.frombytes(pcm)
    pico = max(1, max(abs(x) for x in a))
    if pico < 20000:                              # quiet mic: bring the peak up so Whisper hears it well
        g = min(20000 / pico, 40.0)
        pcm = array.array("h", (max(-32767, min(32767, int(x * g))) for x in a)).tobytes()
    tmp = f"{ruta}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as fh, wave.open(fh, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(MUESTRA)
        w.setframerate(TASA)
        w.writeframes(pcm)
    os.replace(tmp, ruta)
