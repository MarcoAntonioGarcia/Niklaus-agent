"""Niklaus voice app, v0: push-to-talk by keyboard, spoken answers.

    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 -I app/voz/voz.py [opciones]

Enter to talk, Enter to stop. Enter while it speaks interrupts it. "salir" (or Ctrl-C) quits.

- Recording: ffmpeg (avfoundation) -> data/voz/turno.wav, local.
- Voice -> text: app/voz/stt.py, a faster-whisper worker (Darkroom's venv and models; Darkroom's folder
  comes from herramientas.toml) inside app/voz/sandbox/voz.sb: no network or indirect paths to it,
  writes only to data/voz/tmp and models/cache, reads only the recording of data/voz.
- Text -> Claude: `claude -p` (Claude Code, the owner's plan, no API keys) in the agent's folder,
  prompt on stdin (never argv: a transcript starting with "-" must not parse as a flag), streaming,
  same session resumed every turn. Only Read, Glob and Grep exist in the session (--tools) and no
  MCP server (--strict-mcp-config): no writing, no commands, no web, whatever settings.json allows.
- Answer -> voice: Serena (app/voz/tts.py: Qwen3-TTS 0.6B, MLX 8-bit, streaming) in the same sandbox,
  sentence by sentence; `--motor say` falls back to macOS `say`. Whatever comes after ---PANTALLA---
  is only printed (English coaching, links, long data).
- Workers run in their own process group and are killed as a group; logs are opened with O_NOFOLLOW.
- turno.wav is overwritten every turn and deleted on exit (owner's authorization, CLAUDE.md).
Standard library only.
"""
import argparse
import contextlib
import json
import os
import pwd
import queue
import re
import select
import signal
import shutil
import subprocess
import sys
import termios
import threading
import time
import tomllib
import unicodedata
import urllib.parse
from pathlib import Path

NIKLAUS = Path(__file__).resolve().parents[2]
DATA = NIKLAUS / "data" / "voz"
AUDIO = DATA / "turno.wav"
TIEMPOS = DATA / "tiempos.tsv"
PROMPT_VOZ = Path(__file__).with_name("prompt-voz.md")
STT_SRC = Path(__file__).with_name("stt.py")
FFMPEG = "/opt/homebrew/bin/ffmpeg"
PYTHON_SISTEMA = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
MARCA = "---PANTALLA---"
TOOLS = "Read,Glob,Grep"
PERFIL = Path(__file__).with_name("sandbox") / "voz.sb"
TTS_SRC = Path(__file__).with_name("tts.py")
TMP = DATA / "tmp"
CACHE = NIKLAUS / "models" / "cache"
VENV_TTS = NIKLAUS / "venv" / "qwen-mlx" / "bin" / "python"
ESTILO_SERENA = "Habla en español latinoamericano, relajado y natural, como en una plática entre amigos."
HABLANTE_VOZ, TONO_VOZ = "ryan", "0.88"   # owner, 2026-11-16: a man's voice, deep and calm (engine's own speaker)
RAPIDEZ_VOZ = "1.25"                   # owner, 2026-11-16: "métele por 1.5" (WSOLA in tts.py keeps the pitch)
MAX_TURNOS, MAX_SEGUNDOS = "8", 120   # M1: cap every voice turn (steps and wall time)
# With hands (owner's decision, 2026-11-14): reversible MCP "manos" auto-allowed; Bash and the web need the owner's
# OK through --permission-prompt-tool stdio; no native Write/Edit (every write goes through manos, with backup).
TOOLS_MANOS, MAX_TURNOS_MANOS = "Read,Glob,Grep,Bash,WebFetch,WebSearch,Agent,Skill", "80"
# With hands a turn can be real work (e.g. reviewing hundreds of tickets): the cap counts INACTIVITY (MAX_SEGUNDOS with no
# event from Claude) up to an absolute MAX_SEGUNDOS_TRABAJO. Without hands it stays a plain 120 s.
MAX_SEGUNDOS_TRABAJO = 30 * 60          # long agentic work (a panel of analyst subagents) fits
VENV_APP = NIKLAUS / "venv" / "app" / "bin" / "python"
MANOS_SERVIDOR = NIKLAUS / "app" / "manos" / "servidor.py"
PROMPT_MANOS = Path(__file__).with_name("prompt-manos.md")
AGENDA_SERVIDOR = NIKLAUS / "app" / "agenda" / "servidor.py"
# Calendar and reminders: reading, creating (never invites anyone) and moving (refuses events with attendees) run
# without asking. The two delete tools are deliberately NOT here: the owner confirms each one on a card.
AGENDA_SIN_PREGUNTAR = ("calendarios", "eventos", "crear_evento", "mover_evento", "recordatorios",
                        "crear_recordatorio", "completar_recordatorio")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
FIN_FRASE = re.compile(r"(?<=[.!?…;:])\s+|\n+")
URL = re.compile(r"https?://\S+")
MARKDOWN = re.compile(r"(\*\*|__|`+|^#+\s*|^\s*[-*•]\s+|\|)", re.M)


# ---------- configuration ----------

def home_real():
    """HOME from the user database, not from $HOME (a forged HOME would widen the sandbox)."""
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def ruta_darkroom():
    with open(NIKLAUS / "herramientas.toml", "rb") as fh:
        ruta = tomllib.load(fh)["darkroom"]["ruta"]
    d = Path(os.path.expanduser(ruta)).resolve()
    for need in ("scripts/sandbox/sin-red.sb", "venv/bin/python", "models/huggingface"):
        if not (d / need).exists():
            sys.exit(f"[voz] Darkroom incompleto: falta {d / need}")
    return d


# ---------- text handling ----------

def mostrar(s):
    """No terminal escape sequences from model or tool text."""
    return CONTROL.sub("", str(s))


def limpiar_para_voz(s):
    s = URL.sub("enlace", s)
    s = MARKDOWN.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


class Trocear:
    """Splits streamed text into speakable sentences; everything after MARCA goes to the screen."""

    def __init__(self):
        self.buf, self.pantalla, self.en_pantalla = "", "", False

    def alimentar(self, texto):
        if self.en_pantalla:
            self.pantalla += texto
            return []
        self.buf += texto
        i = self.buf.find(MARCA)
        if i >= 0:
            hablar, self.pantalla, self.en_pantalla = self.buf[:i], self.buf[i + len(MARCA):], True
            self.buf = ""
            return [f for f in (limpiar_para_voz(p) for p in FIN_FRASE.split(hablar)) if f]
        partes = FIN_FRASE.split(self.buf)
        self.buf = partes.pop()               # the last piece may be an unfinished sentence (or MARCA)
        return [f for f in (limpiar_para_voz(p) for p in partes) if f]

    def terminar(self):
        resto = [] if self.en_pantalla else [f for f in [limpiar_para_voz(self.buf)] if f]
        self.buf = ""
        return resto, self.pantalla.strip()


def evento_de(d):
    """One claude stream-json object -> ("sid", id) | ("texto", chunk) | ("herramienta", name) | ("fin", is_error)."""
    t = d.get("type")
    if t == "system" and d.get("subtype") == "init" and d.get("session_id"):
        yield "sid", d["session_id"]
        servidores = d.get("mcp_servers")
        if isinstance(servidores, list):
            yield "mcp", [(str(x.get("name")), str(x.get("status"))) for x in servidores if isinstance(x, dict)]
    elif t == "stream_event":
        ev = d.get("event") or {}
        delta = ev.get("delta") or {}
        if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            yield "texto", delta.get("text", "")
        elif ev.get("type") == "content_block_start" and (ev.get("content_block") or {}).get("type") == "tool_use":
            bloque = ev.get("content_block") or {}
            yield "herramienta", bloque.get("name", "")
            yield "herramienta_id", (str(bloque.get("id", "")), str(bloque.get("name", "")))
    elif t == "control_request" and (d.get("request") or {}).get("subtype") == "can_use_tool":
        r = d["request"]                        # Claude Code asks the host (--permission-prompt-tool stdio)
        yield "permiso", (d.get("request_id"), str(r.get("tool_name", "")), r.get("input") or {})
    elif t == "user":
        for bloque in ((d.get("message") or {}).get("content") or []):
            if isinstance(bloque, dict) and bloque.get("type") == "tool_result":
                c = bloque.get("content")
                texto = " ".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else str(c)
                corto = texto[:2000]
                lote = re.search(r"\[lote [0-9a-f]{12}\]", texto)
                if lote and lote.group(0) not in corto:      # the undo button needs the tag of long messages
                    corto += " … " + lote.group(0)
                yield "resultado", (corto, bool(bloque.get("is_error")), str(bloque.get("tool_use_id", "")))
    elif t == "result":
        if d.get("session_id"):
            yield "sid", d["session_id"]
        yield "fin", bool(d.get("is_error")) or d.get("subtype") not in (None, "success")


def eventos(lineas):
    """claude stream-json lines -> the events of evento_de; malformed or non-object lines are skipped."""
    for linea in lineas:
        try:
            d = json.loads(linea)
        except ValueError:
            print(f"  ⚠️ línea ilegible de Claude ignorada: {mostrar(linea)[:120]}")
            continue
        if not isinstance(d, dict):
            continue
        try:
            hubo = False
            for e in evento_de(d):
                hubo = True
                yield e
            if not hubo:
                yield "actividad", None         # e.g. a subagent working: keeps the inactivity cap from firing
        except Exception as exc:
            print(f"  ⚠️ evento de Claude ignorado ({type(exc).__name__}): {mostrar(str(d))[:120]}")


# ---------- voice -> text (sandboxed, no network) ----------

def comando_sandbox(darkroom, args):
    """Both voice workers run in app/voz/sandbox/voz.sb: no network, HOME closed except their venvs/models."""
    return [SANDBOX_EXEC, "-f", str(PERFIL), "-D", f"NIKLAUS={NIKLAUS}", "-D", f"DARKROOM={darkroom}",
            "-D", f"HOME={home_real()}", "-D", f"AUDIO={AUDIO}", *args]


def abrir_log(nombre):
    """Append-only, never through a link (B2): data/voz is written by trusted code only."""
    fd = os.open(DATA / nombre, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def lanzar_trabajador(cmd, env, log):
    """Own process group (M3): closing kills the worker and anything it could have left behind."""
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                            encoding="utf-8", cwd=str(CACHE), env=env, start_new_session=True)


def matar_grupo(p, espera=5):
    try:
        p.stdin.close()
    except OSError:
        pass
    try:
        p.wait(espera)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(p.pid, signal.SIGKILL)       # the group, even if the worker itself already exited
    except (ProcessLookupError, PermissionError):
        pass
    p.wait()
    try:
        p.stdout.close()
    except OSError:
        pass


def _entorno_base():
    return {"HOME": str(CACHE / "home"), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": str(TMP),
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1", "ORT_DISABLE_TELEMETRY": "1", "GRADIO_ANALYTICS_ENABLED": "False",
            "XDG_CACHE_HOME": str(CACHE), "MPLCONFIGDIR": str(CACHE / "matplotlib"),
            "NUMBA_CACHE_DIR": str(CACHE / "numba")}


def entorno_sandbox(darkroom, whisper):
    return dict(_entorno_base(), HF_HOME=str(darkroom / "models/huggingface"), VOZ_AUDIO=str(AUDIO),
                VOZ_WHISPER=whisper, VOZ_PALABRAS=PALABRAS_CLAVE)      # the wake word, so Whisper spells it right


def entorno_tts(hablante, silencio=False):
    e = dict(_entorno_base(), HF_HOME=str(NIKLAUS / "models/huggingface"), VOZ_ESTILO=ESTILO_SERENA,
             VOZ_HABLANTE=HABLANTE_VOZ if hablante == "serena" else hablante, VOZ_RAPIDEZ=RAPIDEZ_VOZ,
             VOZ_TONO=TONO_VOZ)
    if silencio:
        e["VOZ_SILENCIO"] = "1"
    return e


def preparar_carpetas():
    for d in (DATA, TMP):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        if d.is_symlink() or not d.is_dir():
            sys.exit(f"[voz] {d} no es una carpeta real; no sigo.")
        os.chmod(d, 0o700, follow_symlinks=False)
    for d in (CACHE / "home", CACHE / "matplotlib", CACHE / "numba"):
        d.mkdir(parents=True, exist_ok=True)


class Transcriptor:
    def __init__(self, darkroom, whisper="small"):
        cmd = comando_sandbox(darkroom, [str(darkroom / "venv/bin/python"), "-I", "-B", "-c",
                                         STT_SRC.read_text(encoding="utf-8")])
        self.log = abrir_log("stt.log")
        self.p = lanzar_trabajador(cmd, entorno_sandbox(darkroom, whisper), self.log)
        try:
            r = self._leer(120)
        except Exception:
            self.cerrar()
            raise
        if not r.get("listo"):
            raise RuntimeError(f"el transcriptor no arrancó: {r} (ver {DATA / 'stt.log'})")

    def _leer(self, timeout):
        listo, _, _ = select.select([self.p.stdout], [], [], timeout)
        if not listo:
            raise RuntimeError("el transcriptor no respondió a tiempo")
        linea = self.p.stdout.readline()
        if not linea:
            raise RuntimeError(f"el transcriptor terminó (ver {DATA / 'stt.log'})")
        return json.loads(linea)

    def transcribir(self):
        self.p.stdin.write("T\n")
        self.p.stdin.flush()
        r = self._leer(120)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["texto"], r["s"]

    def cerrar(self):
        matar_grupo(self.p)
        self.log.close()


# ---------- recording ----------

def grabar(mic):
    if AUDIO.is_symlink():
        sys.exit(f"[voz] {AUDIO} es un enlace; no grabo ahí.")
    if AUDIO.exists():
        AUDIO.unlink()                        # the app's own file (authorized); ffmpeg then creates a new inode
    p = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats", "-f", "avfoundation",
                          "-i", f":{mic}", "-t", "300", "-ac", "1", "-ar", "16000", "-y", str(AUDIO)],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    input("  ● grabando… (⏎ para terminar) ")
    t_fin = time.monotonic()
    try:
        _, err = p.communicate(b"q", timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
        _, err = p.communicate()
    if p.returncode not in (0, 255) or not AUDIO.exists() or AUDIO.stat().st_size < 8000:
        print("  ⚠️ no se grabó audio. ¿Diste permiso de micrófono a esta terminal? "
              "(Configuración → Privacidad y seguridad → Micrófono)", (err or b"").decode(errors="replace")[-200:])
        return None
    return t_fin


# ---------- answer -> voice ----------

class Voz:
    def __init__(self, voz, velocidad):
        self.voz, self.velocidad = voz, str(velocidad)
        self.cola, self.actual, self.primera, self.muerta = queue.Queue(), None, None, False
        self.hilo = threading.Thread(target=self._bucle, daemon=True)
        self.hilo.start()

    def _bucle(self):
        try:
            while True:
                frase = self.cola.get()
                if frase is None:
                    return
                self.actual = subprocess.Popen(["/usr/bin/say", "-v", self.voz, "-r", self.velocidad, "--", frase])
                self.primera = self.primera or time.monotonic()
                self.actual.wait()
                self.actual = None
                self.cola.task_done()
        except Exception as exc:                # never leave ocupada() stuck at True (validator A2)
            print(f"  ⚠️ la voz de macOS falló: {type(exc).__name__}: {exc}")
        finally:
            self.muerta, self.actual = True, None

    def decir(self, frase):
        self.cola.put(frase)

    def ocupada(self):
        return not self.muerta and (self.actual is not None or not self.cola.empty())

    def callar(self):
        while not self.cola.empty():
            try:
                self.cola.get_nowait()
                self.cola.task_done()
            except queue.Empty:
                break
        if self.actual:
            self.actual.terminate()


class Serena:
    """Client of the sandboxed Serena worker (app/voz/tts.py): same interface as Voz."""

    def __init__(self, darkroom, hablante="serena", silencio=False):
        cmd = comando_sandbox(darkroom, [str(VENV_TTS), "-I", "-B", "-c", TTS_SRC.read_text(encoding="utf-8")])
        self.log = abrir_log("tts.log")
        self.p = lanzar_trabajador(cmd, entorno_tts(hablante, silencio), self.log)
        self.primera, self._hablando, self._enviadas, self._listo = None, False, 0, threading.Event()
        self.errores, self.muerta, self._libres_viejos, self._candado = [], False, 0, threading.Lock()
        self._cerrando = False
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        try:
            for linea in self.p.stdout:
                try:
                    ev = json.loads(linea)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("listo"):
                    self._listo.set()
                elif ev.get("sonando"):
                    self.primera = self.primera or time.monotonic()
                    self._hablando = True
                elif ev.get("libre"):
                    with self._candado:
                        if self._libres_viejos:        # the ack of a callar(): not about new sentences (M9)
                            self._libres_viejos -= 1
                        else:
                            self._hablando, self._enviadas = False, 0
                elif ev.get("error"):
                    self.errores.append(str(ev["error"])[:200])
                    print(f"  ⚠️ Serena: {mostrar(ev['error'])[:160]}")
        except Exception as exc:                # a dead reader must never leave ocupada() stuck (validator A2)
            print(f"  ⚠️ Serena dejó de responder: {type(exc).__name__}")
        finally:
            self.muerta = True
            self._listo.set()                   # worker gone: never wait forever
            if not self._cerrando:              # a silent voice must say why (validator N2)
                print(f"\n  ⚠️ Serena se detuvo: {mostrar(ultima_linea(DATA / 'tts.log'))[:160]}")

    def esperar(self, timeout=180):
        if not self._listo.wait(timeout) or self.p.poll() is not None:
            self.cerrar()
            raise RuntimeError(f"Serena no arrancó (ver {DATA / 'tts.log'})")

    def _enviar(self, obj):
        try:
            # ensure_ascii: a lone surrogate in the model's text is escaped, never a crash (L2)
            self.p.stdin.write(json.dumps(obj, ensure_ascii=True) + "\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def decir(self, frase):
        self._enviadas += 1
        self._enviar({"decir": frase})

    def ocupada(self):
        if self.muerta or self.p.poll() is not None:     # worker or its reader gone: never wait for it
            return False
        return self._hablando or self._enviadas > 0

    def callar(self):
        with self._candado:
            self._enviadas, self._hablando = 0, False
            self._libres_viejos += 1            # the worker acks every callar with exactly one "libre"
        self._enviar({"callar": True})

    def cerrar(self):
        self._cerrando = True
        matar_grupo(self.p, espera=10)
        self.log.close()


# ---------- one turn with Claude ----------

class Cerebro:
    """Persistent Claude Code session: one `claude -p` process for the whole conversation (stream-json in and
    out), so every turn after the first skips the ~2-3 s start-up. Same restrictions as before: --tools
    (only Read/Glob/Grep exist, whatever settings.json auto-allows), --strict-mcp-config (no MCP),
    --max-turns per message, user text as JSON on stdin (never argv).
    - Interrupt (Enter or the 120 s cap): Claude Code's own interrupt control request; the session stays.
    - Renewed from scratch after MAX_INACTIVO idle seconds or MAX_TURNOS_SESION turns (context and quota),
      and the owner hears it.
    - A process that dies before answering: one retry (resuming if it can, else a new session, said aloud);
      a second failure is said aloud and shown with the last line of data/voz/claude.log. Never a loop.
    - Every wait is bounded: the turn (MAX_SEGUNDOS), the interrupt drain, and the voice after the answer.
    """
    # ESPERA_VOZ: base seconds the voice may keep talking after the answer, plus 1 s per CARACTERES_POR_SEGUNDO
    # characters queued (so long answers are never cut: validator N1).
    MAX_INACTIVO, MAX_TURNOS_SESION, ESPERA_INTERRUPCION, ESPERA_VOZ, CARACTERES_POR_SEGUNDO = 15 * 60, 30, 10, 20, 10

    ESPERA_PERMISO = 60                         # no answer from the owner in time = deny

    def __init__(self, claude, modelo, manos=False, permisos=None, aviso=None, politica=True):
        """manos: the session gets the hands (MCP "manos", reversible, auto-allowed) plus Bash/WebFetch/WebSearch,
        which Claude Code sends to `permisos(tool, input) -> bool` before running (owner's confirmation).
        aviso(tipo, valor): optional callback for a UI (text, tools, results, permissions)."""
        self.claude, self.modelo, self.manos, self.politica = claude, modelo, manos, politica
        self.permisos = permisos or permiso_por_terminal
        self.aviso = aviso or (lambda tipo, valor: None)
        self.cancelar = threading.Event()
        self.plazo = None                       # absolute deadline of the open permission question
        self.apagado = False
        self._vida = threading.RLock()
        self.p, self.cola, self.sid, self.turnos, self.ultimo, self.log = None, None, None, 0, None, None

    def _comando(self):
        if not self.manos:
            return [self.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                    "--include-partial-messages", "--tools", TOOLS, "--strict-mcp-config", "--max-turns", MAX_TURNOS,
                    "--append-system-prompt", PROMPT_VOZ.read_text(encoding="utf-8"), "--model", self.modelo]
        config = {"mcpServers": {"manos": {"command": str(VENV_APP), "args": ["-I", "-B", str(MANOS_SERVIDOR)]},
                                 "agenda": {"command": str(VENV_APP), "args": ["-I", "-B", str(AGENDA_SERVIDOR)]}}}
        permitidas = ",".join(["mcp__manos__*"] + [f"mcp__agenda__{h}" for h in AGENDA_SIN_PREGUNTAR])
        return [self.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--tools", TOOLS_MANOS, "--strict-mcp-config",
                "--mcp-config", json.dumps(config), "--allowedTools", permitidas,
                "--permission-prompt-tool", "stdio", "--max-turns", MAX_TURNOS_MANOS,
                "--append-system-prompt", prompt_manos(), "--model", self.modelo]

    def iniciar(self, reanudar=False):
        with self._vida:                        # start and shutdown never interleave: nothing restarts after apagar
            self._iniciar(reanudar)

    def _iniciar(self, reanudar):
        if self.apagado:
            raise RuntimeError("la sesión ya se cerró")
        self.cerrar()
        self.log = abrir_log("claude.log")      # why claude died must never be lost (validator A1)
        cmd = self._comando()
        if reanudar and self.sid:
            cmd += ["--resume", self.sid]
        else:
            self.sid = None
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                  text=True, encoding="utf-8", cwd=str(NIKLAUS),
                                  env=entorno_claude(), start_new_session=True)
        self.turnos, self.ultimo, self.cola = 0, None, queue.Queue()
        proceso, cola = self.p, self.cola

        def leer():                             # each process has its own queue: no stale events across restarts
            try:
                for e in eventos(proceso.stdout):
                    cola.put(e)
            except Exception as exc:            # e.g. undecodable output: say it and end this process cleanly
                print(f"  ⚠️ dejé de poder leer a Claude ({type(exc).__name__}); reinicio la sesión")
                with contextlib.suppress(Exception):
                    matar_grupo(proceso, espera=2)
            finally:
                cola.put(None)
        threading.Thread(target=leer, daemon=True).start()

    def nueva(self):
        self.iniciar(reanudar=False)

    def vivo(self):
        return self.p is not None and self.p.poll() is None

    def _enviar(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=True) + "\n")
        self.p.stdin.flush()

    def _preguntar(self, herramienta, entrada, voz):
        """Ask the owner (UI or terminal handler) with a hard deadline; anything but an explicit yes is a no.
        self.plazo is the absolute deadline (time.monotonic()) that a UI must honour too."""
        self.plazo = time.monotonic() + self.ESPERA_PERMISO
        self.motivo_negacion = None
        if self.manos and self.politica:
            decision = clasificar_permiso(herramienta, entrada)
            if decision == "permitir":
                self.aviso("auto", (herramienta, entrada))
                return True
            if decision == "negar":
                self.motivo_negacion = ("Ese comando toca secretos (llaves, Llavero, cookies, credenciales o archivos "
                                        "de shell): está bloqueado siempre. No lo intentes de otra forma.")
                self.aviso("error", f"Bloqueé un comando que tocaba secretos: {describir_permiso(herramienta, entrada)}"[:300])
                return False
        try:
            voz.decir(f"Necesito tu permiso para {describir_permiso(herramienta, entrada, corto=True)}. ¿Lo hago?")
        except Exception as exc:                # the card still shows the request; never block on the voice
            print(f"  ⚠️ no pude decir la pregunta: {type(exc).__name__}")
        resultado = queue.Queue()

        def preguntar():
            try:
                resultado.put(self.permisos(herramienta, entrada) is True)     # only an explicit True is a yes
            except Exception as exc:            # a broken handler must never mean "yes"
                print(f"  ⚠️ el manejador de permisos falló: {type(exc).__name__}")
                resultado.put(False)
        threading.Thread(target=preguntar, daemon=True).start()
        try:
            return resultado.get(timeout=max(0.0, self.plazo - time.monotonic())) is True
        except queue.Empty:
            print("  ⏱  sin respuesta: lo niego")
            self.aviso("permiso_vencido", herramienta)      # the UI closes its card as NO
            return False

    def _responder_permiso(self, rid, permitido, entrada):
        """True if Claude Code got the answer."""
        respuesta = ({"behavior": "allow", "updatedInput": entrada} if permitido else
                     {"behavior": "deny", "message": getattr(self, "motivo_negacion", None) or
                      "El dueño no lo autorizó. No lo intentes de otra forma; pregúntale qué prefiere."})
        try:
            self._enviar({"type": "control_response",
                          "response": {"subtype": "success", "request_id": rid, "response": respuesta}})
            return True
        except (OSError, ValueError, AttributeError) as exc:     # AttributeError: the session was just closed
            print(f"  ⚠️ no pude contestarle el permiso a Claude: {type(exc).__name__}")
            return False

    def _interrumpir(self):
        """Claude Code's interrupt; False if the pipe is already broken (then restart right away)."""
        try:
            self._enviar({"type": "control_request", "request_id": f"int-{time.monotonic_ns()}",
                          "request": {"subtype": "interrupt"}})
            return True
        except (OSError, ValueError):
            return False

    def _drenar(self):
        """After an interrupt, consume the rest of the turn (plus a short grace for a late result); if it never
        ends, restart resuming the session."""
        limite = time.monotonic() + self.ESPERA_INTERRUPCION
        while time.monotonic() < limite:
            try:
                e = self.cola.get(timeout=0.1)
            except queue.Empty:
                continue
            if e is None:
                return
            if e[0] == "sid":
                self.sid = e[1]
            elif e[0] == "permiso":                     # never leave Claude waiting for an answer
                self._responder_permiso(e[1][0], False, {})
            elif e[0] == "fin":
                time.sleep(0.3)
                self._purgar()
                return
        self.iniciar(reanudar=True)

    def _purgar(self):
        """Drop anything left from an earlier turn (keeping the session id): no stale events (validator M2)."""
        while True:
            try:
                e = self.cola.get_nowait()
            except queue.Empty:
                return
            if e is None:                       # the process ended: keep the marker for the next read
                self.cola.put(None)
                return
            if e[0] == "sid":
                self.sid = e[1]
            elif e[0] == "permiso":                     # never leave Claude waiting for an answer
                self._responder_permiso(e[1][0], False, {})

    def _recuperar(self, voz):
        """The process died before answering: resume once if possible, else start clean, and say so."""
        tenia_sesion = self.sid is not None
        self.iniciar(reanudar=not self._fallo_reanudar)
        if tenia_sesion and self.sid is None:
            voz.decir("Perdí la conversación anterior; empiezo de cero.")
            print("  ⚠️ no pude retomar la sesión; empiezo una nueva")
            self.aviso("error", "No pude retomar la conversación anterior; empecé una nueva.")
        self._fallo_reanudar = True

    _fallo_reanudar = False

    def preguntar(self, texto, voz):
        """One spoken turn; returns the time of the first text token (or None). Never loops, never hangs."""
        self.cancelar.clear()                   # a late interrupt of the previous turn never cuts this one
        if not self.vivo():
            self._recuperar(voz)
        elif self.ultimo is not None and (time.monotonic() - self.ultimo > self.MAX_INACTIVO
                                          or self.turnos >= self.MAX_TURNOS_SESION):
            print("  (sesión renovada: mucho tiempo inactiva o muchos turnos)")
            voz.decir("Empiezo una conversación nueva.")
            self.nueva()
        for intento in (1, 2):
            estado, t_token = self._turno(texto, voz)
            if estado != "sin_respuesta":
                self._fallo_reanudar = False
                return t_token
            if intento == 1:
                self._recuperar(voz)
        voz.decir("No puedo hablar con Claude ahora. Revisa la terminal.")
        ultimo = mostrar(ultima_linea(DATA / 'claude.log'))[:200]
        print(f"  ⚠️ Claude no responde. Último error: {ultimo}")
        self.aviso("error", f"Claude no responde. Último error: {ultimo}")
        return None

    def _turno(self, texto, voz):
        self._purgar()
        if sys.stdin.isatty():
            termios.tcflush(sys.stdin, termios.TCIFLUSH)    # a stray Enter must not cancel this turn (M1)
        self.turnos += 1
        try:
            self._enviar({"type": "user", "message": {"role": "user", "content": texto}})
        except (OSError, ValueError):            # died between the check and the write
            return "sin_respuesta", None
        trocear, t_token, interrumpido, error, avisado, vio_algo = Trocear(), None, False, False, False, False
        herramientas = {}                       # tool_use id -> tool name, to pair each result with its tool
        terminado, limite, limite_voz, hablado, todo = False, time.monotonic() + MAX_SEGUNDOS, None, 0, []
        tope_absoluto = time.monotonic() + (MAX_SEGUNDOS_TRABAJO if self.manos else MAX_SEGUNDOS)

        def decir(frase):
            nonlocal hablado
            hablado += len(frase)
            voz.decir(frase)

        def tope_voz():                         # long answers are never cut (validator N1)
            return time.monotonic() + self.ESPERA_VOZ + hablado / self.CARACTERES_POR_SEGUNDO

        def cortar():                           # interrupt first, then record the whole turn (validator N5)
            if self._interrumpir():
                self._drenar()
            else:
                self.iniciar(reanudar=True)

        while not terminado or voz.ocupada():
            if terminado and time.monotonic() > limite_voz:     # the voice never blocks the app (validator A2)
                voz.callar()
                print("  ⚠️ la voz no terminó a tiempo; la corto")
                break
            if not terminado and time.monotonic() > limite:     # M1: never let a turn run away
                voz.callar()
                decir("Me estoy tardando demasiado; lo corto aquí. Pídemelo de otra forma.")
                motivo = (f"{MAX_SEGUNDOS} s sin actividad" if time.monotonic() < tope_absoluto
                          else f"el máximo de {int((MAX_SEGUNDOS_TRABAJO if self.manos else MAX_SEGUNDOS) / 60)} min")
                print(f"  ⏱  turno cortado: {motivo}")
                self.aviso("error", f"Corté la respuesta: {motivo}.")
                cortar()
                anotar_bitacora(f"(turno cortado a los {MAX_SEGUNDOS} s) {''.join(todo)}")
                terminado, limite_voz = True, tope_voz()
                continue
            pedido = self.cancelar.is_set()          # the desktop app interrupts through this flag
            if pedido or (sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]):
                if not pedido:
                    sys.stdin.readline()
                self.cancelar.clear()
                interrumpido = True
                voz.callar()
                print("\n  ⏹ interrumpido")        # before the (bounded) drain, so the owner sees it at once
                if not terminado:
                    cortar()
                    anotar_bitacora(f"(turno interrumpido) {''.join(todo)}")
                break
            if terminado:
                time.sleep(0.05)
                continue
            try:
                e = self.cola.get(timeout=0.05)
            except queue.Empty:
                continue
            if self.manos and e is not None:    # working: every event from Claude extends the cap, up to the maximum
                limite = max(limite, min(time.monotonic() + MAX_SEGUNDOS, tope_absoluto))
            if e is None:                       # the process died
                if not vio_algo:
                    return "sin_respuesta", None
                error, terminado = True, True
            elif e[0] == "sid":
                self.sid = e[1]
            elif e[0] == "herramienta":
                vio_algo = True
                if not avisado and t_token is None:     # silence while it reads: say something right away
                    avisado = True
                    decir("Déjame revisar.")
                print(f"  🔧 {mostrar(e[1])}")
                self.aviso("herramienta", e[1])
            elif e[0] == "herramienta_id":
                herramientas[e[1][0]] = e[1][1]
            elif e[0] == "resultado":
                texto_r, error_r, id_r = e[1]
                self.aviso("resultado", (texto_r, error_r, herramientas.get(id_r, "")))
            elif e[0] == "permiso":                     # the owner decides; the wait does not count against the cap
                vio_algo = True
                rid, herramienta, entrada = e[1]
                t_pregunta, permitido = time.monotonic(), False
                try:
                    if not isinstance(entrada, dict):
                        entrada = {}
                    permitido = self._preguntar(herramienta, entrada, voz)
                except Exception as exc:                # any failure here is a NO, and Claude always gets it
                    print(f"  ⚠️ falló la pregunta de permiso: {type(exc).__name__}: {exc}")
                    self.aviso("permiso_vencido", herramienta)
                finally:
                    limite += time.monotonic() - t_pregunta
                    if not self._responder_permiso(rid, permitido, entrada):
                        permitido = False
                        self.aviso("error", "No pude contestarle el permiso a Claude; repítemelo, por favor.")
                print(f"  {'✅ permitido' if permitido else '⛔ negado'}: {mostrar(herramienta)}")
                self.aviso("permiso_resuelto", (herramienta, permitido))
            elif e[0] == "mcp":
                if self.manos and not any(n == "manos" and st in ("connected", "pending") for n, st in e[1]):
                    estado_manos = next((st for n, st in e[1] if n == "manos"), "ausente")
                    print(f"  ⚠️ el servidor de las manos no conectó ({estado_manos})")
                    self.aviso("error", f"Las manos no están disponibles (servidor: {estado_manos}). "
                                        "Puedo leer y buscar, pero no mover ni escribir.")
            elif e[0] == "texto":
                vio_algo = True
                todo.append(e[1])
                self.aviso("texto", e[1])
                t_token = t_token or time.monotonic()
                for f in trocear.alimentar(e[1]):
                    print(f"  🔊 {mostrar(f)}")
                    decir(f)
            elif e[0] == "fin":
                error, terminado = e[1], True
            if terminado:
                for f in trocear.terminar()[0]:
                    print(f"  🔊 {mostrar(f)}")
                    decir(f)
                if error and not interrumpido:          # say it, not only print it (validator M4)
                    decir("Algo falló en esta respuesta; repítemelo, por favor.")
                    ultimo = mostrar(ultima_linea(DATA / 'claude.log'))[:160]
                    print(f"  ⚠️ Claude devolvió un error. {ultimo}")
                    self.aviso("error", f"Claude devolvió un error. {ultimo}")
                limite_voz = tope_voz()
        self.ultimo = time.monotonic()
        pantalla = trocear.pantalla.strip()
        if pantalla and not interrumpido:
            print("  ── en pantalla ──\n  " + mostrar(pantalla).replace("\n", "\n  "))
        return "ok", t_token

    def apagar(self):
        """Final shutdown: nothing restarts the session afterwards (not even a turn in flight)."""
        self.apagado = True
        with self._vida:
            self.cerrar()

    def cerrar(self):
        with self._vida:
            p, self.p = self.p, None
            log, self.log = self.log, None
            if p is not None:
                matar_grupo(p, espera=5)
            if log is not None:
                log.close()


# ---------- main loop ----------

NUEVA_CONVERSACION = re.compile(
    r"(?:(?:oye|niklaus|serena)\s+)*(?:por favor\s+)?"
    r"(?:(?:(?:inicia|empieza|empecemos|abre|haz)\s+)?(?:una\s+)?(?:nueva|otra) conversacion"
    r"|empecemos de (?:cero|nuevo))(?:\s+por favor)?")


def es_nueva_conversacion(texto):
    """True only when the whole utterance is the command (validator M5): "nueva conversación", "empecemos de
    cero"… A sentence that merely mentions a conversation never resets the session. Accents in any Unicode form."""
    t = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    t = " ".join(re.sub(r"[^\w ]", " ", t).split())
    return bool(NUEVA_CONVERSACION.fullmatch(t))


# ---------- permission policy (owner's decision, 2026-11-15: "solo lo delicado") ----------
# An ALLOWLIST, not a blocklist: a Bash command runs without asking ONLY when it is clearly harmless. Everything
# else is asked, so a never-seen trick is asked, not run. Every check runs on the SHELL-PARSED tokens (shlex),
# never on the raw text, because split quotes (.c''onfig) and globs otherwise slip past a text match.
import shlex as _shlex

_SEGURO = {
    # read-only, no side effects, no network, no code execution, no file-writing option used with these here:
    "ls", "pwd", "echo", "cat", "head", "tail", "wc", "date", "cal", "whoami", "hostname", "uptime", "df", "du",
    "stat", "file", "basename", "dirname", "realpath", "fold", "seq", "sw_vers", "uname", "which", "type",
    "mkdir", "afplay", "vm_stat", "uptime", "hostname",
    # NOT here: arch (runs the program you give it), memory_pressure (applies real memory pressure), sysctl (-w writes).
    # NOT here on purpose: sort/uniq (-o and a 2nd operand write a file), say (-o writes, -n sends to the network),
    # pbpaste (leaks the clipboard), caffeinate (runs the command you give it), pmset/sips (change settings/files),
    # printenv/env/ps (environment), any interpreter.
}
_SECRETOS = re.compile(
    r"(\.ssh\b|\.aws\b|\.gnupg|\.netrc|\.git-credentials|\.npmrc|\.pypirc|\.kube\b|\.docker\b|\.config/"
    r"|keychain|\bcookies\b|\.claude\.json|\.claude/projects|\.credentials|\.zsh\w*|\.bash\w*|\.profile\b|zsh_history"
    r"|\bsecurity\b|login\.keychain|\bid_(rsa|ed25519|ecdsa|dsa)|\.env\b|\.local/share/claude"
    r"|zsh_sessions|application support/claude|git\s+(config|credential)|_token|_secret|_key|password"
    r"|/library/(application support/(google|brave|firefox|bravesoftware|microsoft edge|arc)|containers/com\.apple\.safari)"
    r"|safari/)", re.I)
# globs, home/parent shortcuts and shell operators: a segment with any of these cannot be judged safe → ask
_GLOBS = re.compile(r"[*?\[\]{}]|(^|\s)~(/|\s|$)|(^|/)\.\.(/|\s|$)")
_META = re.compile(r"[;&|`$<>(){}*?\[\]\\\n]|(^|\s)~|\.\.")

_VOZ_PRIVADA = re.compile(r"data/voz/|turno\.wav|darkroom/data/(videos|output/(medios|watch-))", re.I)
_RANGO = {"permitir": 0, "preguntar": 1, "negar": 2}
HOME_REAL = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def _tokens(seg):
    """Shell-parsed tokens of `seg`, or None if the quoting is unbalanced. Adjacent quoted parts join, so
    `.c''onfig` becomes the single token `.config` and the secret checks catch it."""
    try:
        return _shlex.split(seg)
    except ValueError:
        return None


def _sin_comillas(seg):
    return re.sub(r"'[^']*'|\"[^\"]*\"", "", seg)


_RUTAS_SISTEMA = ("/bin/", "/usr/bin/", "/usr/sbin/", "/sbin/")


def _programa(seg):
    """The program a segment runs, or "" when it cannot be trusted to be what its name says: a variable
    assignment in front (PATH=… ls), zsh's `=cmd` expansion, or a path outside the system folders (./ls)."""
    toks = _tokens(seg)
    if not toks:
        return ""
    w = toks[0]
    if "=" in w or any(t.startswith("=") for t in toks):
        return ""
    if "/" in w:
        return os.path.basename(w).lower() if w.startswith(_RUTAS_SISTEMA) and w.count("/") <= 3 else ""
    return w.lower()


# ---- orbit (Jira, GitLab, Confluence…): read runs, write/notify asks, credentials refused ----
_ORBIT_LEE = {"list", "view", "get", "show", "search", "status", "diff", "ls", "info", "whoami", "me", "members",
              "member", "history", "ancestors", "children", "tree", "summary", "dashboard", "filters", "fields",
              "statuses", "types", "sprints", "boards", "comments", "transitions", "versions"}
_ORBIT_NUNCA = {"auth", "login", "logout", "token", "tokens", "config", "configure", "secret", "secrets", "credential",
                "credentials", "key", "keys", "encrypt", "profile", "profiles", "tracking", "tkm"}


def _orbit(toks):
    if toks and toks[0] == "rtk":
        toks = toks[1:]
    if not toks or os.path.basename(toks[0]) != "orbit" or toks[0] not in ("orbit", "/opt/homebrew/bin/orbit"):
        return None
    palabras = {x.lower() for x in toks[1:] if not x.startswith("-")}
    if palabras & _ORBIT_NUNCA:
        return "negar"
    # an option that writes (--comment=…, --set=…, -o/--output…): ask
    if any("=" in o or re.match(r"-o$|--output", o) for o in toks[1:] if o.startswith("-")):
        return "preguntar"
    if not palabras & _ORBIT_LEE:
        return "preguntar"                      # any non-read verb (create, comment, export, log, deploy…): ask
    return "permitir"


# ---- git: only read subcommands, only known-safe options (never a pager/output option that runs code or writes) ----
_GIT_LEE = {"log", "show", "diff", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "status", "branch",
            "cat-file", "describe", "shortlog", "rev-list", "merge-base", "reflog"}
_GIT_OPC = re.compile(
    r"-[0-9]+$|-[nUMCpsravlbwitz]$|-[MC][0-9]+$|-U[0-9]+$"
    r"|--(oneline|stat|numstat|shortstat|name-only|name-status|graph|decorate|no-decorate|abbrev-commit|no-abbrev"
    r"|no-color|color|reverse|first-parent|all|cached|staged|no-patch|short|long|list|word-diff|follow|source"
    r"|patch|no-merges|merges|date-order|topo-order|boundary|full-history|simplify-merges|raw|summary|check)$"
    r"|--(max-count|skip|since|until|before|after|author|committer|grep|pretty|format|unified|contains|date|abbrev)=")


def _git(toks):
    if toks[0] != "git":
        return None
    i = 1
    while i + 1 < len(toks) and toks[i] == "-C":    # git -C <repo> …
        i += 2
    if i >= len(toks) or toks[i].startswith("-"):   # a top-level option (-c, --exec-path, --git-dir…): ask
        return "preguntar"
    sub, args = toks[i], toks[i + 1:]
    if sub in ("config", "credential", "credential-osxkeychain"):
        return "negar"
    if sub not in _GIT_LEE:
        return "preguntar"
    if sub == "diff" and "--no-index" in args:
        return "preguntar"                          # reads two arbitrary files, incl. secrets
    if sub == "reflog" and (not args or args[0] != "show"):
        return "preguntar"                          # only `reflog show`; never expire/delete
    if sub == "branch" and any(not a.startswith("-") for a in args):
        return "preguntar"                          # a positional ⇒ create/rename/delete a branch
    for a in args:
        if a.startswith("-") and not _GIT_OPC.match(a):
            return "preguntar"                      # unknown option: could run a pager or write a file (-O, --output…)
    return "permitir"


# ---- filters allowed after a pipe: they read stdin only (no file operand, no output option) ----
_FILTROS = {"head", "tail", "wc", "grep", "egrep", "cut", "column", "nl", "tr", "jq"}


def _filtro_ok(filtro):
    """A pipe stage is safe only if it reads stdin and writes stdout: no file operand, no output option, no code."""
    if re.search(r"[;&`$<>\n\\]", _sin_comillas(filtro)) or _GLOBS.search(_sin_comillas(filtro)):
        return False
    toks = _tokens(filtro)
    if not toks:
        return False
    prog = os.path.basename(toks[0])
    if prog not in _FILTROS or _SECRETOS.search(" ".join(toks)):
        return False
    opts = [x for x in toks[1:] if x.startswith("-") and x != "-"]
    pos = [x for x in toks[1:] if not (x.startswith("-") and x != "-")]
    if prog == "tr":
        return True                                 # no file/network/output capability at all
    if prog in ("grep", "egrep"):
        if len(pos) > 1:                            # a 2nd operand is a file to read
            return False
        return not any(re.match(r"-[frRdOI]$|--(file|include|exclude|recursive|open|directories|devices)", o)
                       for o in opts)
    if prog == "jq":
        if len(pos) > 1:
            return False
        if any(re.match(r"-f$|--(from-file|rawfile|slurpfile|argfile)", o) for o in opts):
            return False
        return not re.search(r"\$ENV|\benv\b|input_filename|\binputs?\b|\$__loc__", filtro)
    # pure stdin filters (head, tail, wc, cut, column, nl): operands only numeric, never an -o/--output option
    if any(re.match(r"-o$|--output", o) for o in opts):
        return False
    return all(re.fullmatch(r"[+-]?\d+", p) for p in pos)


def _con_filtros(seg, clasificador):
    """`seg` is a base command optionally followed by harmless pipe filters (| head, | grep, | jq…) and 2>&1.
    None if it is not that shape."""
    seg = re.sub(r"\s+2>(&1|/dev/null)(?=\s|\||$)", "", seg)
    if "||" in seg:
        return None
    trozos = _partir(seg, "|")
    if not trozos:
        return None
    for filtro in trozos[1:]:
        if not _filtro_ok(filtro):
            return None
    base = trozos[0]
    if re.search(r"[;&|`$<>\n\\]", _sin_comillas(base)):
        return None
    toks = _tokens(base)
    if not toks:
        return None
    if toks[0] == "rtk":
        toks = toks[1:]
    return clasificador(toks) if toks else None


def _partir(cmd, sep):
    """Splits at `sep` outside quotes; None if quotes are unbalanced."""
    partes, actual, comilla, i = [], "", None, 0
    while i < len(cmd):
        ch = cmd[i]
        if comilla:
            comilla = None if ch == comilla else comilla
        elif ch in "'\"":
            comilla = ch
        elif cmd.startswith(sep, i):
            partes.append(actual)
            actual, i = "", i + len(sep)
            continue
        actual += ch
        i += 1
    return None if comilla else partes + [actual]


# ---- local/ : the owner's private layer (git-ignored). Optional; the app works without it. ----
LOCAL = NIKLAUS / "local"


def _archivo_local(nombre):
    """Path of local/<nombre> if it is a plain file of this user that nobody else can write; else None."""
    ruta = LOCAL / nombre
    try:
        for p in (LOCAL, ruta):
            st = os.lstat(p)
            if os.path.islink(p) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                return None
        return ruta if ruta.is_file() else None
    except OSError:
        return None


def _cargar_locales():
    """Classifiers for the owner's own programs from local/politica.py: {name: f(tokens)}. Fails closed: with any
    problem there are no extras, and those programs simply ask like any unknown command."""
    ruta = _archivo_local("politica.py")
    if ruta is None:
        return {}
    try:
        ambito = {"__name__": "politica_local", "__file__": str(ruta)}
        exec(compile(ruta.read_text(encoding="utf-8"), str(ruta), "exec"), ambito)
        tabla = ambito.get("CLASIFICADORES")
    except (Exception, SystemExit) as exc:          # a broken local file must never take the app down
        print(f"local/politica.py no se cargó ({type(exc).__name__}: {exc}); sigo sin él", file=sys.stderr)
        return {}
    if not isinstance(tabla, dict):
        return {}

    def envolver(nombre, fn):
        def seguro(toks):
            # only when the program that RUNS is exactly that bare name (found in PATH): never a path to some
            # other file with the same name, never the name as a mere argument of another program.
            if not toks or toks[0] != nombre:
                return None
            try:
                r = fn(toks)
            except (Exception, SystemExit):
                return "preguntar"
            return r if r in ("permitir", "preguntar", "negar", None) else "preguntar"
        return seguro
    return {n: envolver(n, f) for n, f in tabla.items()
            if isinstance(n, str) and re.fullmatch(r"[\w.-]{2,40}", n) and callable(f)}


_LOCALES = _cargar_locales()


def _nombres_locales():
    """The owner's own wake names from local/ajustes.json ({"nombres": ["Viernes", "Vier"]}): single words, the first
    one is the name it goes by. None keeps the built-in ones."""
    ruta = _archivo_local("ajustes.json")
    if ruta is None:
        return None
    try:
        lista = json.loads(ruta.read_text(encoding="utf-8")).get("nombres")
    except (OSError, ValueError, AttributeError, RecursionError) as exc:
        print(f"local/ajustes.json no se leyó ({exc}); sigo con los nombres de fábrica", file=sys.stderr)
        return None
    if not isinstance(lista, list):
        return None
    def simple(n):                                   # what the ear can match: plain letters once accents are removed
        s = "".join(c for c in unicodedata.normalize("NFKD", n.lower()) if not unicodedata.combining(c))
        return re.fullmatch(r"[a-z]{2,20}", s) is not None
    buenos = [n.strip() for n in lista if isinstance(n, str) and simple(n.strip())]
    return buenos[:8] or None


NOMBRES_PROPIOS = _nombres_locales()
PALABRAS_CLAVE = ", ".join(NOMBRES_PROPIOS) if NOMBRES_PROPIOS else "Niklaus, Nik, Nick, Marcus"


def prompt_manos():
    """The public prompt plus the owner's private section (local/prompt.md: their work, tools and preferences)."""
    texto = PROMPT_MANOS.read_text(encoding="utf-8")
    if NOMBRES_PROPIOS:
        texto += (f"\n- Para este dueño te llamas {NOMBRES_PROPIOS[0]} (respondes a: {', '.join(NOMBRES_PROPIOS)}); "
                  "ese nombre sustituye al de la primera línea.\n")
    propio = _archivo_local("prompt.md")
    if propio is not None:
        try:
            texto += "\n" + propio.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"local/prompt.md no se leyó ({exc}); sigo sin él", file=sys.stderr)
    return texto


def _clasificar_segmento(seg):
    seg = seg.strip()
    if not seg:
        return "preguntar"
    # the global rtk hook rewrites every command as `rtk <command>` (a token-optimizing proxy of the owner's);
    # judge what it actually runs, so rtk-wrapped reads do not each ask for permission.
    sin_rtk = re.sub(r"^rtk\s+(proxy\s+)?", "", seg)
    if sin_rtk != seg:
        return _clasificar_segmento(sin_rtk)
    toks = _tokens(seg)
    if toks is None:
        return "preguntar"
    if _SECRETOS.search(" ".join(toks)):            # secrets, on the joined tokens (split quotes rejoin here)
        return "negar"
    m = re.fullmatch(r"cd\s+(\"([^\"$`\\]+)\"|'([^']+)'|([\w./~+@-]+))", seg)
    if m:
        destino = next(g for g in m.groups()[1:] if g)
        partes = [x.casefold() for x in destino.split("/") if x not in ("", "~")]
        if (destino.startswith(("/", "~/")) and partes and destino.rstrip("/") not in ("~", HOME_REAL)
                and ".." not in partes and not any(x.startswith(".") or x == "library" for x in partes)):
            return "permitir"                       # cd to a literal, visible, absolute path: harmless by itself
        return "preguntar"
    if re.fullmatch(r"open\s+-a\s+(\"[\w .+-]+\"|'[\w .+-]+'|[\w.+-]+)", seg) \
            or re.fullmatch(r"open\s+https?://[\w./%?=&#+-]+", seg):
        return "permitir"                           # an app, or an https page in the browser; never a file/.command
    if _GLOBS.search(_sin_comillas(seg)):           # a glob, ~ or .. anywhere: ask (orbit, git and the owner's tools never need them)
        return "preguntar"
    if re.search(r"\borbit\b", seg):
        return _con_filtros(seg, _orbit) or "preguntar"
    for nombre, propio in _LOCALES.items():         # the owner's own tools (local/politica.py, never published)
        if re.search(rf"\b{re.escape(nombre)}\b", seg):
            return _con_filtros(seg, propio) or "preguntar"
    if re.match(r"(rtk\s+)?git\b", seg):
        return _con_filtros(seg, _git) or "preguntar"
    if re.search(r"[;&|`$<>(){}\\\n]", _sin_comillas(seg)):
        return "preguntar"                          # a pipe or operator with a non-filter base: ask
    if any(re.search(r"(^|/)\.[^./]", x) for x in toks[1:]):
        return "preguntar"                          # a hidden file/folder operand (dotfiles, .git, .claude…): ask
    if any(re.search(r"/library(/|$)", x, re.I) for x in toks[1:]):
        return "preguntar"                          # ~/Library and /Library: app data, tokens, LaunchAgents
    if _VOZ_PRIVADA.search(" ".join(toks)):
        return "negar"                              # rule 3: the owner's recorded voice and videos stay unread
    return "permitir" if _programa(seg) in _SEGURO else "preguntar"


def clasificar_permiso(herramienta, entrada):
    """'permitir' | 'preguntar' | 'negar'. Only Claude Code's permission requests reach here. Anything not clearly
    harmless is asked. A plain `a && b` is judged piece by piece (the worst piece decides)."""
    if herramienta in ("WebSearch", "WebFetch"):
        return "permitir"                           # the owner wants web lookups without asking (read-only)
    if herramienta in ("Agent", "Task", "Skill"):
        return "permitir"                           # a subagent or skill: their own commands go through this policy
    if herramienta in ("Read", "Glob", "Grep"):
        texto = json.dumps(entrada, ensure_ascii=False)
        if _SECRETOS.search(texto) or _VOZ_PRIVADA.search(texto) or re.search(r"\.jsonl\b|/\.claude/projects", texto):
            return "negar"
        ruta = str(entrada.get("file_path") or entrada.get("path") or "")
        partes = [x for x in ruta.split("/") if x]
        en_home = ruta.startswith(HOME_REAL + "/") or ruta.startswith("~") or ruta in ("", HOME_REAL)
        skills = any(x in ruta + "/" for x in ("/.claude/skills/", "/.claude/agents/", "/.claude/state/"))
        if not ruta or ruta in (HOME_REAL, "~", "/"):
            return "preguntar"                      # a search over the whole home (or no path): ask
        if en_home and not skills and (any(x.startswith(".") for x in partes) or "Library" in partes):
            return "preguntar"                      # hidden folders and ~/Library hold tokens of many apps: ask
        if "/" not in ruta.replace(HOME_REAL + "/", "", 1) and en_home and "." not in partes[-1]:
            return "preguntar"                      # a bare file in the home without extension (e.g. ~/zshrc): ask
        return "permitir"
    if herramienta != "Bash":
        return "preguntar"
    cmd = str(entrada.get("command", "")).strip()
    if not cmd:
        return "preguntar"
    if _SECRETOS.search(cmd):
        return "negar"
    segmentos = _partir(cmd, "&&")
    if not segmentos:
        return "preguntar"
    return max((_clasificar_segmento(x) for x in segmentos), key=_RANGO.get)


def entorno_claude():
    """Claude's (and its commands') environment without tokens, keys or passwords (rule 6)."""
    malo = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY|AUTH)", re.I)
    return {k: v for k, v in os.environ.items() if not malo.search(k)} | {"NIKLAUS_TERMINAL": "voz"}


_OCULTOS = ((0x00, 0x08), (0x09, 0x1f), (0x7f, 0x9f), (0xad, 0xad), (0x200b, 0x200f), (0x2028, 0x202e), (0x2060, 0x2069), (0xfeff, 0xfeff))


def describir_permiso(herramienta, entrada, corto=False):
    """What the owner is asked to approve, in plain words (the exact command is always shown on screen)."""
    if herramienta == "Bash":
        cmd = str(entrada.get("command", ""))
        if not corto:
            return f"ejecutar: {cmd}"
        if re.search(r"[;&|`$<>()\n\\]", cmd):        # compound: naming the first program would mislead
            return "ejecutar un comando compuesto; míralo en la pantalla"
        programa = re.sub(r"[^\w.+-]", "", os.path.basename(cmd.strip().split(" ")[0] if cmd.strip() else ""))[:24]
        return f"ejecutar el comando {programa}" if programa else "ejecutar un comando"
    if herramienta == "WebFetch":
        if not corto:
            return f"abrir: {entrada.get('url', '')}"
        try:
            host = urllib.parse.urlsplit(str(entrada.get("url", ""))).hostname or ""
        except ValueError:
            host = ""
        dominio = re.sub(r"[^\w.-]", "", host)[:60]
        return f"abrir la página {dominio}" if dominio else "abrir una página web"
    if herramienta == "WebSearch":
        return "buscar en internet" if corto else f"buscar: {entrada.get('query', '')}"
    if herramienta in ("mcp__agenda__borrar_evento", "mcp__agenda__borrar_recordatorio"):
        que = "el evento" if herramienta.endswith("evento") else "el recordatorio"
        def limpio(clave, tope):                 # no control, bidi or zero-width characters on the card
            s = "".join(" " if any(a <= ord(c) <= b for a, b in _OCULTOS) else c for c in str(entrada.get(clave, "")))
            s = re.sub(r"\s+", " ", s).strip()
            return s if len(s) <= tope else s[:tope] + "…"
        titulo = limpio("titulo", 80) + (f" ({limpio('inicio', 16)})" if entrada.get("inicio") else "")      # the server only deletes on an exact title
        return f"borrar {que} del calendario; míralo en la pantalla" if corto else f"borrar {que}: «{titulo}»"
    return f"usar {herramienta}" if corto else f"usar {herramienta}: {json.dumps(entrada, ensure_ascii=False)[:300]}"


_NO_VOZ = re.compile(r"\b(no|nel|nunca|canc[eé]l\w*|det[eé]n\w*|para|espera|alto)\b")
# "sí" only with its accent: a bare "si" is also "if" ("si… borra"), so it is asked again
_SI_VOZ = re.compile(r"(sí|sí hazlo|hazlo|adelante|sí adelante|confirmo|sí confirmo|sí por favor|"
                     r"sí ejec[uú]talo|ejec[uú]talo|autorizo|sí autorizo)")


def respuesta_de_voz(texto):
    """A spoken answer to a permission card: True only for a short, unambiguous yes; False for anything with a no;
    None (ask again) otherwise. Any "no" wins: "claro que no", "ok, mejor no", "sí… no" are NO."""
    t = unicodedata.normalize("NFC", str(texto or "")).lower()
    pregunta = "?" in t or "¿" in t
    t = re.sub(r"[^\w\sáéíóúüñ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    if _NO_VOZ.search(t):
        return False
    return True if _SI_VOZ.fullmatch(t) and not pregunta else None


def permiso_por_terminal(herramienta, entrada):
    """Terminal handler (voz.py without a window): show the exact action and read y/n within the timeout."""
    print(f"\n  🔐 Niklaus quiere {mostrar(describir_permiso(herramienta, entrada))[:400]}")
    print("     ¿Lo permito? escribe 's' y ⏎ (cualquier otra cosa o nada en 60 s = no): ", end="", flush=True)
    if not sys.stdin.isatty():
        return False
    listo, _, _ = select.select([sys.stdin], [], [], Cerebro.ESPERA_PERMISO - 1)
    return bool(listo) and sys.stdin.readline().strip().lower() in ("s", "si", "sí", "y", "yes")


def ultima_linea(ruta):
    """Last non-empty line of an app log, read without following links (for error messages)."""
    try:
        fd = os.open(ruta, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 4096))
            lineas = [x for x in fh.read().decode("utf-8", "replace").splitlines() if x.strip()]
        return lineas[-1] if lineas else "(sin detalles)"
    except OSError:
        return "(sin detalles)"


def anotar_bitacora(texto):
    """Interrupted turns never reach the Stop hook: record them in the session log ourselves (verifier)."""
    try:
        r = subprocess.run([PYTHON_SISTEMA, "-I", "-B", "-S", str(NIKLAUS / "tools" / "bitacora.py")],
                           input=json.dumps({"last_assistant_message": texto[:4000]}), text=True,
                           env=dict(os.environ, NIKLAUS_TERMINAL="voz"), timeout=10, capture_output=True)
        if r.returncode != 0:
            print(f"  ⚠️ la bitácora devolvió {r.returncode}: {mostrar(r.stderr)[-160:]}")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ⚠️ no pude anotar el turno en la bitácora: {type(exc).__name__}")

def main():
    ap = argparse.ArgumentParser(description="Niklaus: app de voz v0")
    ap.add_argument("--modelo", default="sonnet", help="modelo de Claude (sonnet por defecto, por velocidad)")
    ap.add_argument("--motor", default="serena", choices=["serena", "say"],
                    help="serena: Qwen3-TTS local (MLX, sin red); say: voz de macOS (respaldo)")
    ap.add_argument("--hablante", default="serena", help="voz de Qwen3-TTS (serena por defecto)")
    ap.add_argument("--voz", default="Paulina", help="con --motor say: voz de macOS (say -v '?' | grep es_)")
    ap.add_argument("--velocidad", type=int, default=190, help="con --motor say: palabras por minuto")
    ap.add_argument("--whisper", default="small", choices=["small", "medium"])
    ap.add_argument("--mic", default="0", help="índice del micrófono de avfoundation")
    ap.add_argument("--manos", action="store_true", help="dar las manos (acciones reversibles; comandos con tu ok)")
    a = ap.parse_args()
    if not re.fullmatch(r"[0-9]{1,2}", a.mic):
        sys.exit("[voz] --mic debe ser un número")
    os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))   # SystemExit -> the finally block cleans up
    claude = shutil.which("claude")
    if not claude:
        sys.exit("[voz] no encuentro Claude Code (claude) en el PATH")
    darkroom = ruta_darkroom()
    if not re.fullmatch(r"[a-z_]{2,20}", a.hablante):
        sys.exit("[voz] --hablante inválido")
    preparar_carpetas()
    print("Niklaus · voz — cargando la voz y el transcriptor (sin red)…")
    voz = Serena(darkroom, a.hablante) if a.motor == "serena" else Voz(a.voz, a.velocidad)
    cerebro, stt = Cerebro(claude, a.modelo, manos=a.manos), None
    try:
        cerebro.iniciar()                        # warms up while the voice and the transcriber load
        stt = Transcriptor(darkroom, a.whisper)
        if a.motor == "serena":
            voz.esperar()
            voz.decir("Hola. Ya estoy lista.")
        while True:
            orden = input("\n⏎ para hablar · 'salir' para terminar: ").strip().lower()
            if orden in ("salir", "q", "exit"):
                break
            if getattr(voz, "muerta", False) and isinstance(voz, Serena):   # never go on mute (validator N2)
                voz.cerrar()
                voz = Voz(a.voz, a.velocidad)
                print("  ⚠️ sigo con la voz de macOS mientras Serena no esté disponible")
            t_fin = grabar(a.mic)
            if t_fin is None:
                continue
            texto, s_stt = stt.transcribir()
            t_texto = time.monotonic()
            if not texto:
                print("  (no te escuché)")
                continue
            print(f"  🗣  {mostrar(texto)}")
            if es_nueva_conversacion(texto):
                cerebro.nueva()
                voz.decir("Listo, empezamos de cero.")
                print("  (nueva conversación)")
                continue
            voz.primera = None
            t_token = cerebro.preguntar(texto, voz)
            ms = lambda t: f"{(t - t_fin):.1f}" if t else "-"
            print(f"  ⏱  texto {ms(t_texto)} s · primer token {ms(t_token)} s · primera voz {ms(voz.primera)} s")
            try:                                 # a log problem must never end the conversation (validator M8)
                with abrir_log(TIEMPOS.name) as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{a.modelo}\t{a.whisper}\t{s_stt}\t"
                             f"{ms(t_texto)}\t{ms(t_token)}\t{ms(voz.primera)}\n")
            except OSError as exc:
                print(f"  ⚠️ no pude anotar los tiempos: {type(exc).__name__}")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        cerebro.cerrar()
        voz.callar()
        if hasattr(voz, "cerrar"):
            voz.cerrar()
        if stt:
            stt.cerrar()
        if AUDIO.exists() and not AUDIO.is_symlink():
            AUDIO.unlink()
        print("\nNiklaus · voz: hasta luego.")


if __name__ == "__main__":
    main()
