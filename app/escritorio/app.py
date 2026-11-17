"""Niklaus desktop app: a native macOS window (pywebview) with the particle orb and the conversation, wired to the
voice pipeline: the microphone always open (local), the wake word "Niklaus", local transcription, Serena, and a
persistent Claude Code session with hands.

    ~/Apps/Niklaus/venv/app/bin/python -I -B ~/Apps/Niklaus/app/escritorio/app.py

No ports: the UI is local files loaded with file:// (pywebview starts no HTTP server for file:// URLs) and talks to
Python through pywebview's JS bridge. Every text from the model or from files is rendered as text by the UI.
pywebview exposes to JS every public attribute of the js_api object, recursively: everything internal here starts
with "_" and only the UI methods are public.
Audio never leaves the Mac: utterances without the wake word are transcribed locally and discarded.
"""
import fcntl
import json
import os
import queue
import shutil
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path

AQUI = Path(__file__).resolve().parent
NIKLAUS = AQUI.parents[1]
sys.path.insert(0, str(NIKLAUS / "app" / "voz"))
sys.path.insert(0, str(NIKLAUS / "app" / "manos"))
sys.path.insert(0, str(AQUI))
import voz  # noqa: E402
import nucleo  # noqa: E402
import oido  # noqa: E402
import webview  # noqa: E402

ESPERA_VISTO = 5          # seconds for the window to confirm it drew a permission card (else: NO)
ATENTO = 12               # seconds it keeps listening without the wake word after answering or being called
ESPERA_DIARIO = 3         # cap for the window's reads of the hands' journal
LOG_APP = voz.DATA / "app.log"


def log(msg):
    """Never raises: a closed Terminal must not break anything (stderr goes to data/voz/app.log)."""
    try:
        print(f"[niklaus {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def encontrar_claude():
    c = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not os.access(c, os.X_OK):
        raise RuntimeError("no encuentro Claude Code (claude)")
    return c


class Puente:
    """Methods exposed to the UI (window.pywebview.api.*). Each call runs on its own thread."""

    def __init__(self):
        self._ventana = None
        self._extras = []                  # extra windows (second screen): they get the same events
        self._turno = threading.Lock()
        self._pendiente = None             # open permission card: {"id", "evento", "visto", "decision", "resuelto"}
        self._candado_permiso = threading.Lock()
        self._listo = threading.Event()
        self._cerrando = False
        self._voz = self._stt = self._cerebro = self._oido = None
        self._ultima_herramienta = ""
        self._lotes_mostrados = set()      # batches that already have a card in the window
        self._nota_para_claude = ""        # actions done during an interrupted turn, told on the next one
        self._segmentos = queue.Queue(maxsize=4)   # utterances waiting to be transcribed
        self._atento_hasta = 0.0           # until then, no wake word needed (follow-up)
        self._fin_voz = 0.0                # when Serena last stopped talking (her echo is never a command)
        self._orden = None                 # a spoken order being put together (it may continue after a pause)
        self._orden_t = 0.0
        self._voz_activa = False           # the owner is speaking right now
        self._abrir_atento = False         # open the follow-up window when she stops (only after an answer)
        self._dicho = deque(maxlen=200)    # (time, words) Serena said recently: what the mic hears of her is echo

    # ----- to the UI -----
    def _emitir(self, tipo, **datos):
        if self._ventana and not self._cerrando:
            evento = json.dumps(dict(datos, tipo=tipo), ensure_ascii=True)   # a JSON literal, never HTML
            try:
                # run_js = WebKit's evaluateJavaScript as is (evaluate_js wraps the code in eval()). The event is a
                # JSON literal built here, never HTML or code from outside.
                self._ventana.run_js(f"window.niklaus ? window.niklaus.recibir({evento}) : "
                                     f"(window.__pendientes = (window.__pendientes || [])).push({evento})")
            except Exception as exc:             # never silent: the window would just stop updating
                log(f"la ventana no recibió «{tipo}»: {type(exc).__name__}: {exc}")
            for v in list(self._extras):         # second screen: only state matters there; a closed one is dropped
                if tipo == "estado":
                    try:
                        v.run_js(f"window.niklaus && window.niklaus.recibir({evento})")
                    except Exception:
                        self._extras.remove(v)

    def _estado(self, estado):
        self._emitir("estado", estado=estado)

    def _recordar_dicho(self, texto):
        self._dicho.append((time.monotonic(), set(oido.normalizar(texto).split())))

    def _es_eco(self, texto):
        """What the mic heard is mostly words Serena said in the last 20 s: her own voice, never a command."""
        palabras = set(oido.normalizar(texto).split())
        if not palabras:
            return False
        recientes = set().union(*[w for t, w in self._dicho if time.monotonic() - t < 20] or [set()])
        return len(palabras & recientes) / len(palabras) >= 0.6

    def _decir(self, frase):
        self._recordar_dicho(frase)
        try:
            if self._voz:
                self._voz.decir(frase)
        except Exception as exc:
            log(f"la voz falló: {type(exc).__name__}: {exc}")

    # ----- start-up -----
    def _arrancar(self):
        try:
            voz.preparar_carpetas()
            darkroom = voz.ruta_darkroom()
            self._estado("cargando")
            self._voz = voz.Serena(darkroom, "serena")
            if not self._cerrando:
                self._cerebro = voz.Cerebro(encontrar_claude(), "sonnet", manos=True,
                                            permisos=self._permiso, aviso=self._aviso)
                self._cerebro.iniciar()
            if not self._cerrando:
                self._stt = voz.Transcriptor(darkroom, "small")
                self._voz.esperar()
            if not self._cerrando:
                self._oido = oido.Oido(voz.FFMPEG, "0", self._al_empezar_voz, self._al_terminar_voz, self._oido_fallo)
                self._oido.iniciar()
                log("micrófono abierto")
            if self._cerrando:                          # the window closed while starting: stop what started
                self._apagar_partes()
                return
        except (Exception, SystemExit) as exc:
            log(f"no pude arrancar: {type(exc).__name__}: {exc}")
            self._estado("error")
            self._emitir("error", texto=f"No pude arrancar: {type(exc).__name__}: {exc}. Cierra y vuelve a abrir; "
                                        f"el registro está en {LOG_APP}.")
            self._apagar_partes()
            return
        self._listo.set()
        self._estado("reposo")
        self._emitir("sistema", texto="Lista. Di «Niklaus» y lo que necesites.")
        self._decir("Hola, ya estoy lista. Llámame por mi nombre cuando me necesites.")
        threading.Thread(target=self._latido, daemon=True).start()
        threading.Thread(target=self._transcribir_segmentos, daemon=True).start()

    def _latido(self):
        """Keeps the orb in sync with the voice, and opens the follow-up window when Serena stops talking."""
        hablaba = False
        while not self._cerrando:
            try:
                habla = bool(self._voz and self._voz.ocupada())
                if habla and not hablaba and not self._pendiente:
                    self._estado("hablando")
                elif hablaba and not habla:
                    self._fin_voz = time.monotonic()
                    if self._abrir_atento and not self._turno.locked():
                        self._abrir_atento = False
                        self._atento_hasta = time.monotonic() + ATENTO
                    if not self._pendiente:
                        self._estado("pensando" if self._turno.locked() else "atento")
                elif not habla and not self._pendiente and not self._turno.locked() \
                        and 0 < self._atento_hasta < time.monotonic():
                    self._atento_hasta = 0.0
                    self._estado("reposo")
                hablaba = habla
                if self._oido:                          # dictating: just called by name, or putting an order together
                    self._oido.dictando = self._orden is not None or time.monotonic() < self._atento_hasta
            except Exception as exc:
                log(f"latido: {type(exc).__name__}: {exc}")
            time.sleep(0.25)

    # ----- the ear -----
    def _hablaba(self):
        """Serena is talking or just stopped: what the mic hears now may be her own voice."""
        return bool(self._voz and self._voz.ocupada()) or time.monotonic() - self._fin_voz < 0.6

    def _despachar_orden(self):
        """Sends the spoken order once the owner has stopped adding to it."""
        while not self._cerrando:
            time.sleep(0.2)
            if self._orden is None:
                return
            if self._voz_activa or not self._segmentos.empty():
                self._orden_t = time.monotonic()        # still talking or still transcribing: keep waiting
                continue
            if time.monotonic() - self._orden_t > 1.6:
                orden, self._orden = self._orden, None
                self._emitir("usuario", texto=orden)
                self._preguntar(orden)
                return

    def _al_empezar_voz(self):
        self._voz_activa = True
        self._inicio = (self._hablaba(), self._pendiente)    # judged at the START of the utterance
        if not self._pendiente and not self._turno.locked() and time.monotonic() < self._atento_hasta:
            self._estado("escuchando")

    def _al_terminar_voz(self, pcm, t_inicio):
        self._voz_activa = False
        hablaba, tarjeta = getattr(self, "_inicio", (False, None))
        segmento = (pcm, hablaba or self._hablaba(), tarjeta)
        try:
            self._segmentos.put_nowait(segmento)
        except queue.Full:                              # never let the ear fall behind: drop the oldest
            try:
                self._segmentos.get_nowait()
            except queue.Empty:
                pass
            self._segmentos.put_nowait(segmento)

    def _oido_fallo(self, motivo):
        if self._cerrando:
            return
        log(f"micrófono: {motivo}")
        self._emitir("error", texto=f"El micrófono dejó de funcionar ({motivo}). ¿La Terminal tiene permiso de "
                                    f"micrófono? Puedes escribirme mientras tanto.")

    def _transcribir_segmentos(self):
        while not self._cerrando:
            try:
                pcm, hablaba, tarjeta = self._segmentos.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                oido.guardar_wav(str(voz.AUDIO), pcm)
                texto, _ = self._stt.transcribir()
            except Exception as exc:
                log(f"transcripción: {type(exc).__name__}: {exc}")
                self._emitir("error", texto=f"No pude transcribir lo que dijiste: {exc}")
                continue
            try:
                self._oir(texto.strip(), hablaba, tarjeta)
            except Exception as exc:
                log(f"oír: {type(exc).__name__}: {exc}")
                self._emitir("error", texto=f"{type(exc).__name__}: {exc}")

    def _oir(self, texto, hablaba, tarjeta):
        if not texto:
            log("oí algo, pero la transcripción salió vacía")
            return
        if self._es_eco(texto):
            log("oí mi propia voz (eco); la ignoro")
            return                                      # Serena's own voice coming back through the mic
        nombre, orden = oido.buscar_nombre(texto)
        if self._orden is not None and not hablaba and self._pendiente is None:   # he kept talking: same order
            self._orden += " " + (orden if nombre and orden else texto)
            self._orden_t = time.monotonic()
            log(f"sumé {len(texto.split())} palabra(s) a la orden en curso")
            return
        if not nombre and len(texto.split()) <= 3 and time.monotonic() > self._atento_hasta:
            # tuning aid for the wake word: only the FIRST word of very short phrases, to learn how Whisper writes the name
            log(f"frase corta sin mi nombre; primera palabra: «{voz.mostrar(texto.split()[0])[:20]}»")
        # diagnosis without storing what was said: only whether the name was heard
        log(f"oí una frase de {len(texto.split())} palabra(s): {'con' if nombre else 'sin'} mi nombre"
            f"{'' if nombre or time.monotonic() < self._atento_hasta else ' → descartada'}")
        if tarjeta is not None and tarjeta is self._pendiente and not hablaba:
            # A spoken "yes" approves ONLY with the wake word ("Niklaus, sí"), so the TV or a video cannot approve
            # a card; a "no" cancels with or without the name (any "no" wins).
            self._emitir("usuario", texto=texto)
            r = voz.respuesta_de_voz(orden if nombre else texto)
            if nombre and oido.normalizar(orden) in ("c", "si", "ci", "se"):   # Whisper writes "Niklaus, sí" as "Niklaus C."
                r = True
            if r is True and not nombre:
                self._decir("Para confirmar di: Niklaus, sí.")
                self._estado("confirmando")
            elif r is None:
                self._decir("Dime sí o no.")
                self._estado("confirmando")
            else:
                self._resolver(r, tarjeta)              # True only when the name was present; False cancels
            return
        ocupado = self._turno.locked() or hablaba
        if ocupado:
            if not nombre:
                return                                  # her own voice, the TV, other people: ignored
            self.interrumpir()                          # "Niklaus…" while she talks or works: stop and listen
            if not orden:
                self._atento_hasta = time.monotonic() + ATENTO
                self._estado("atento")
                return
        elif not nombre and time.monotonic() > self._atento_hasta:
            return                                      # not for her: discarded, never shown or stored
        if nombre and not orden:                      # just the name: listen right away, in silence (speaking
            self._atento_hasta = time.monotonic() + ATENTO    # "¿Sí?" made her deaf while she said it)
            self._estado("escuchando")
            return
        self._atento_hasta = 0.0
        self._orden, self._orden_t = (orden if nombre else texto), time.monotonic()
        self._estado("escuchando")                      # he may go on after a pause: wait a moment before sending
        threading.Thread(target=self._despachar_orden, daemon=True).start()

    # ----- from the Claude session -----
    def _aviso(self, tipo, valor):
        if tipo == "texto":
            self._recordar_dicho(valor)
            self._emitir("texto", texto=valor)
        elif tipo == "error":
            self._emitir("error", texto=str(valor))
        elif tipo == "herramienta":
            self._ultima_herramienta = valor
            self._estado("actuando" if valor.startswith("mcp__manos__") or valor == "Bash" else "pensando")
        elif tipo == "resultado":
            texto, es_error, h = valor                  # h: the tool this result belongs to (paired by id)
            if h.startswith("mcp__manos__"):
                accion = h.split("__")[-1]
                todos = nucleo.LOTE_RE.findall(texto or "")
                lote = todos[-1] if todos and accion not in ("historial", "deshacer") else None
                self._emitir("accion", accion=accion, texto=texto, error=es_error, lote=lote)
                if lote:
                    self._lotes_mostrados.add(lote)
            elif h == "Bash":
                self._emitir("accion", accion="comando", texto=(texto or "(sin salida)")[:500], error=es_error,
                             lote=None)
        elif tipo == "permiso_vencido":
            self._resolver(False)

    def _permiso(self, herramienta, entrada):
        """Blocks the Claude turn until the owner answers in the window or by voice, or the deadline passes."""
        p = {"id": str(time.monotonic_ns()), "evento": threading.Event(), "visto": threading.Event(),
             "decision": False, "resuelto": False}
        plazo = (self._cerebro.plazo if self._cerebro and self._cerebro.plazo
                 else time.monotonic() + voz.Cerebro.ESPERA_PERMISO) - 1
        with self._candado_permiso:
            self._pendiente = p
        try:
            self._estado("confirmando")
            log(f"pidió permiso: {voz.mostrar(voz.describir_permiso(herramienta, entrada))[:300]}")
            self._emitir("permiso", id=p["id"], herramienta=herramienta,
                         descripcion=voz.describir_permiso(herramienta, entrada, corto=True),
                         detalle=voz.describir_permiso(herramienta, entrada))
            if not p["visto"].wait(ESPERA_VISTO) and not p["resuelto"]:     # the owner never saw the card: NO
                log("la ventana no confirmó la tarjeta de permiso; lo niego")
                self._emitir("error", texto="No pude mostrarte la solicitud de permiso; la negué.")
                self._resolver(False, p)
            else:
                p["evento"].wait(max(0.0, plazo - time.monotonic()))
        except Exception as exc:
            log(f"permiso: {type(exc).__name__}: {exc}")
            self._emitir("error", texto=f"Falló la solicitud de permiso ({type(exc).__name__}); la negué.")
        finally:
            decision = self._resolver(False, p)      # always closed; returns the one decision that stands
            self._estado("pensando")
        return decision

    def _resolver(self, si, p=None):
        """Closes the open card exactly once. Returns the decision that stands (what Claude is told)."""
        cerrada = False
        with self._candado_permiso:
            p = p or self._pendiente
            if p is None:
                return False
            if not p["resuelto"]:
                p["resuelto"], p["decision"], cerrada = True, si is True, True
                if self._pendiente is p:
                    self._pendiente = None
                p["evento"].set()
            decision = p["decision"]
        if cerrada:                                   # never call into the UI while holding the lock
            self._emitir("permiso_cerrado", id=p["id"], decision=decision)
        return decision

    # ----- API for the UI -----
    def permiso_visto(self, id_):
        p = self._pendiente
        if p and p["id"] == id_:
            p["visto"].set()

    def responder_permiso(self, id_, si):
        p = self._pendiente
        if p and p["id"] == id_:
            self._resolver(si is True, p)

    def registrar_error(self, mensaje):
        log(f"error en la ventana: {voz.mostrar(str(mensaje))[:500]}")

    def segunda_pantalla(self):
        """Opens the brain alone in another window, to drag to a second screen."""
        if len(self._extras) >= 2:
            return False
        v = webview.create_window("Niklaus · núcleo", url=(AQUI / "ui" / "index.html").as_uri() + "?solo=1",
                                  js_api=self, width=900, height=700, background_color="#03050C")
        self._extras.append(v)
        return True

    def conocimiento(self):
        """What Niklaus knows, for the knowledge map: names and counts only, all local, nothing secret."""
        import tomllib
        nodos = []
        def contar(ruta, patron="*"):
            try:
                return sum(1 for _ in Path(ruta).glob(patron))
            except OSError:
                return 0
        try:
            with open(NIKLAUS / "skills.toml", "rb") as fh:
                cats = {}
                for _, sk in tomllib.load(fh).items():
                    if isinstance(sk, dict):
                        cats[sk.get("categoria", "otras")] = cats.get(sk.get("categoria", "otras"), 0) + 1
            for cat, n in sorted(cats.items(), key=lambda x: -x[1])[:6]:
                nodos.append({"nombre": f"Skills · {cat}", "peso": n})
        except (OSError, ValueError) as exc:
            log(f"conocimiento: skills.toml: {exc}")
        try:
            with open(NIKLAUS / "herramientas.toml", "rb") as fh:
                for nombre, h in tomllib.load(fh).items():
                    if isinstance(h, dict):
                        ruta = Path(os.path.expanduser(str(h.get("ruta", ""))))
                        n = contar(ruta / "data" / "output" / "reportes", "*/reporte.md")
                        nodos.append({"nombre": f"{nombre.capitalize()} · {n} videos analizados", "peso": max(3, n)})
        except (OSError, ValueError) as exc:
            log(f"conocimiento: herramientas.toml: {exc}")
        propio = voz._archivo_local("mapa.json")    # the owner's own nodes (local/, never published)
        if propio is not None:
            try:
                extra = json.loads(propio.read_text(encoding="utf-8"))
                for n in extra.get("nodos", [])[:6]:
                    if isinstance(n, dict) and isinstance(n.get("nombre"), str):
                        nodos.append({"nombre": n["nombre"][:60], "peso": int(n.get("peso", 5))})
                if isinstance(extra.get("estado_proyecto"), str):
                    with open(os.path.expanduser(extra["estado_proyecto"]), encoding="utf-8") as fh:
                        proyecto = json.load(fh).get("project")
                    if isinstance(proyecto, str) and proyecto:
                        nodos.append({"nombre": f"Trabajo · {proyecto[:40]}", "peso": 8})
            except (OSError, ValueError, AttributeError, TypeError) as exc:
                log(f"conocimiento: local/mapa.json: {exc}")
        nodos += [{"nombre": "Manos · archivos de la Mac", "peso": 6},
                  {"nombre": f"Bitácora · {contar(NIKLAUS / 'data' / 'bitacora', '*.md')} días", "peso": 4},
                  {"nombre": "Voz y oído locales", "peso": 4},
                  {"nombre": "Internet", "peso": 3}]
        return nodos[:14]

    def silenciar(self, si):
        """The owner can close the ear (privacy): while silenced nothing is heard or transcribed."""
        if self._oido:
            self._oido.silenciado = si is True
        self._emitir("microfono", silenciado=si is True)
        return si is True

    def enviar_texto(self, texto):
        """False if it could not be sent (the UI keeps the text)."""
        texto = str(texto or "").strip()[:4000]
        if not texto or not self._listo.is_set():
            return False
        self._emitir("usuario", texto=texto)
        threading.Thread(target=self._preguntar, args=(texto,), daemon=True).start()
        return True

    def _preguntar(self, texto):
        with self._turno:
            if voz.es_nueva_conversacion(texto):
                try:
                    self._cerebro.nueva()
                    self._nota_para_claude = ""
                    self._decir("Listo, empezamos de cero.")
                    self._emitir("sistema", texto="Nueva conversación.")
                except Exception as exc:
                    log(f"nueva conversación: {type(exc).__name__}: {exc}")
                    self._emitir("error", texto=f"No pude empezar una conversación nueva: {exc}")
                self._estado("reposo")
                return
            self._estado("pensando")
            self._emitir("respuesta_inicio")
            try:
                marca = len(nucleo._leer_diario(ESPERA_DIARIO)[0])
            except Exception as exc:
                marca = None
                log(f"no pude leer el diario antes del turno: {exc}")
                self._emitir("sistema", texto=f"Aviso: no pude revisar el diario de las manos ({exc}); si se "
                                               f"interrumpe este turno, mira el historial.")
            nota, self._nota_para_claude = self._nota_para_claude, ""
            entregada = False
            try:
                entregada = self._cerebro.preguntar(nota + texto, self._voz) is not None
            except Exception as exc:
                log(f"turno: {type(exc).__name__}: {exc}")
                self._emitir("error", texto=f"{type(exc).__name__}: {exc}")
            if nota and not entregada:
                self._nota_para_claude = nota + self._nota_para_claude   # not delivered: keep it for next time
            self._emitir("respuesta_fin")
            self._reconciliar(marca)
            if self._hablaba():
                self._abrir_atento = True               # the follow-up window opens when she stops talking
            else:
                self._atento_hasta = time.monotonic() + ATENTO
            self._estado("atento")

    def _reconciliar(self, marca):
        """Batches the hands journaled in this turn but that never got a card (interrupted, cut or restarted turn):
        the owner sees them with their Undo button, and Claude is told on the next turn."""
        if marca is None:
            return
        try:
            nuevos = [x for x in nucleo.lotes_desde(marca, ESPERA_DIARIO) if x[0] not in self._lotes_mostrados]
        except Exception as exc:
            log(f"no pude revisar el diario tras el turno: {exc}")
            self._emitir("error", texto=f"No pude revisar qué hicieron las manos en este turno ({exc}); "
                                        f"mira el historial.")
            return
        for lote, desc, n in nuevos:
            self._lotes_mostrados.add(lote)
            self._emitir("accion", accion="interrumpida", lote=lote, error=False,
                         texto=f"Se hizo aunque la respuesta se cortó: {desc} ({n} paso(s)). [lote {lote}]")
        if nuevos:
            limpio = lambda d: " ".join(str(d).replace("[", "(").replace("]", ")").split())[:120]   # noqa: E731
            self._nota_para_claude = ("[Nota de la app: en el turno anterior, que se interrumpió, las manos sí "
                                      "hicieron: " + "; ".join(f"{limpio(d)} (lote {lote})" for lote, d, _ in nuevos)
                                      + ".]\n")

    def interrumpir(self):
        if self._pendiente:
            self._resolver(False)                 # Stop also answers an open card with NO
        if self._voz:
            self._voz.callar()
        if self._cerebro and self._turno.locked():
            self._cerebro.cancelar.set()

    def deshacer(self, lote=None):
        lote = lote if isinstance(lote, str) and nucleo.LOTE_RE.fullmatch(f"[lote {lote}]") else None
        try:
            _, mensaje, ok = nucleo.deshacer(1, lote, espera=ESPERA_DIARIO)
        except Exception as exc:
            mensaje, ok = f"No pude deshacer: {exc}", False
        self._emitir("accion", accion="deshacer", texto=mensaje, error=not ok, lote=None)
        if ok and mensaje.startswith("Deshice"):
            self._decir("Listo, deshecho.")
        elif mensaje.startswith("Deshice solo una parte"):
            self._decir("Solo pude deshacer una parte; mira la ventana.")
        else:
            self._decir(mensaje.split(". ")[0].rstrip(".") + ".")    # e.g. "No hay nada que deshacer."
        return ok

    # ----- shutdown -----
    def _apagar_partes(self):
        for nombre, parte in (("oído", self._oido), ("cerebro", self._cerebro), ("voz", self._voz),
                              ("transcriptor", self._stt)):
            if not parte:
                continue
            try:
                if isinstance(parte, voz.Serena):
                    parte.callar()
                (parte.apagar if isinstance(parte, voz.Cerebro) else parte.cerrar)()
            except Exception as exc:
                log(f"no pude cerrar {nombre}: {type(exc).__name__}: {exc}")

    def _cerrar(self):
        if self._cerrando:
            return
        self._cerrando = True                         # first: no more UI calls from here on
        try:
            self._resolver(False)
        except Exception as exc:
            log(f"cierre (permiso): {exc}")
        self._apagar_partes()
        try:
            for f in (voz.AUDIO, Path(f"{voz.AUDIO}.tmp")):
                if f.exists() and not f.is_symlink():
                    f.unlink()
        except OSError as exc:
            log(f"no pude borrar turno.wav: {exc}")


SENALES = {signal.SIGTERM, signal.SIGHUP, signal.SIGINT}


def una_sola_instancia():
    """A second window would fight over the mic and the session: refuse it (the lock dies with the process)."""
    voz.preparar_carpetas()
    fd = os.open(voz.DATA / ".app.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("[niklaus] Niklaus ya está abierta en otra ventana.")
    return fd


def redirigir_salida():
    """stdout/stderr to data/voz/app.log: closing the launcher's Terminal never breaks a print."""
    fd = os.open(LOG_APP, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    sys.stdout = os.fdopen(1, "w", buffering=1, encoding="utf-8", errors="replace", closefd=False)
    sys.stderr = os.fdopen(2, "w", buffering=1, encoding="utf-8", errors="replace", closefd=False)


def main():
    # Python signal handlers never run while the main thread sits in Cocoa's run loop: block the signals in every
    # thread (set before any thread exists, so all inherit it) and wait for them in a dedicated one.
    signal.pthread_sigmask(signal.SIG_BLOCK, SENALES)
    os.umask(0o077)
    candado = una_sola_instancia()                # noqa: F841  (kept open for the life of the process)
    print(f"[niklaus] abriendo… (registro: {LOG_APP})", flush=True)
    redirigir_salida()
    log("arranque")
    puente = Puente()
    ventana = webview.create_window("Niklaus", url=(AQUI / "ui" / "index.html").as_uri(), js_api=puente,
                                    width=1100, height=760, min_size=(820, 600), background_color="#05070F")
    puente._ventana = ventana
    ventana.events.closed += puente._cerrar

    def esperar_senal():
        s = signal.sigwait(SENALES)
        try:
            log(f"señal {signal.Signals(s).name}: cierro")
            puente._cerrar()
        finally:
            ventana.destroy()
    threading.Thread(target=esperar_senal, daemon=True).start()
    try:
        webview.start(puente._arrancar, http_server=False, private_mode=True, debug=False)
    finally:
        puente._cerrar()                          # window closed by any path: never leave workers behind
        log("cerrada")


if __name__ == "__main__":
    main()
