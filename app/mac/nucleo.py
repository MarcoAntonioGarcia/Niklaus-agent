"""Mac control: volume, media keys, Spotify in the browser, dark mode, display sleep, open apps and Shortcuts.

A fixed set of actions; nothing here runs a command the model wrote. Media keys act on whatever is playing
(Spotify's web player in the browser, YouTube, Music…) and need the Accessibility permission of the app that
launched this process (Terminal).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "comun"))
from seguro import HerramientaError, limpiar, osa  # noqa: E402

NIKLAUS = Path(__file__).resolve().parents[2]
SHORTCUTS = "/usr/bin/shortcuts"
PMSET = "/usr/bin/pmset"
OPEN = "/usr/bin/open"
_TECLAS = {"reproducir_pausar": 16, "siguiente": 17, "anterior": 18}


def _correr(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HOME": os.path.expanduser("~")})
    except subprocess.TimeoutExpired:
        raise HerramientaError(f"{os.path.basename(cmd[0])} no respondió en {timeout} s") from None
    if r.returncode != 0:
        raise HerramientaError(f"{os.path.basename(cmd[0])} falló: {limpiar(r.stderr or r.stdout, 200)}")
    return r.stdout


# ---- sound ----
def volumen(nivel=None, silencio=None):
    """Set the output volume (0-100) and/or mute; returns the resulting state."""
    if nivel is not None:
        try:
            n = int(nivel)
        except (TypeError, ValueError):
            raise HerramientaError("el volumen va de 0 a 100") from None
        if not 0 <= n <= 100:
            raise HerramientaError("el volumen va de 0 a 100")
        osa("on run argv\nset volume output volume (item 1 of argv as integer)\nend run", n, js=False)
    if silencio is not None:
        osa("on run argv\nset volume output muted (item 1 of argv = \"1\")\nend run", "1" if silencio else "0", js=False)
    s = osa("set v to get volume settings\nreturn (output volume of v as text) & \",\" & (output muted of v as text)",
            js=False).strip()
    nivel_actual, mudo = (s.split(",") + ["", ""])[:2]
    return {"volumen": int(nivel_actual) if nivel_actual.isdigit() else None, "silenciado": mudo == "true"}


def multimedia(accion):
    """Press a media key (play/pause, next, previous) for whatever is playing."""
    if accion not in _TECLAS:
        raise HerramientaError(f"acción desconocida; usa una de: {', '.join(_TECLAS)}")
    import ctypes
    ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    if not ax.AXIsProcessTrusted():
        raise HerramientaError("necesito el permiso de Accesibilidad: Ajustes del Sistema → Privacidad y seguridad → "
                               "Accesibilidad → Terminal")
    import Quartz
    from AppKit import NSEvent
    tecla = _TECLAS[accion]
    for estado in (0xa, 0xb):                        # key down, key up
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0, 0), estado << 8, 0, 0, None, 8, (tecla << 16) | (estado << 8), -1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev.CGEvent())
    return {"tecla": accion}


def spotify(busqueda=None, enlace=None):
    """Open Spotify's web player in the default browser: a search, or an open.spotify.com link (track, album,
    playlist, artist). Only that host is accepted."""
    if enlace:
        u = urllib.parse.urlsplit(str(enlace).strip())
        if u.scheme != "https" or u.hostname != "open.spotify.com" or not re.fullmatch(
                r"/(intl-[a-z]{2}/)?(track|album|playlist|artist|episode|show)/[A-Za-z0-9]{10,40}", u.path):
            raise HerramientaError("solo abro enlaces de https://open.spotify.com (canción, álbum, lista o artista)")
        url = f"https://open.spotify.com{u.path}"
    else:
        q = limpiar(busqueda, 120)
        if not q:
            raise HerramientaError("dime qué buscar en Spotify")
        url = "https://open.spotify.com/search/" + urllib.parse.quote(q, safe="")
    _correr([OPEN, url])
    return {"abierto": url}


# ---- screen and system ----
def modo_oscuro(activar):
    v = "true" if activar else "false"
    real = osa(f"tell application \"System Events\" to tell appearance preferences\nset dark mode to {v}\n"
               "return dark mode as text\nend tell", js=False).strip()
    return {"modo_oscuro": real == "true"}


def dormir_pantalla():
    _correr([PMSET, "displaysleepnow"])
    return {"pantalla": "apagada"}


def estado():
    bat = limpiar(_correr([PMSET, "-g", "batt"]), 200)
    m = re.search(r"(\d+)%;\s*([^;]+)", bat)
    return {"bateria": int(m.group(1)) if m else None, "carga": limpiar(m.group(2), 40) if m else "",
            "corriente": "AC Power" in bat, **volumen()}


def apps_abiertas():
    salida = osa("tell application \"System Events\" to get name of every process whose background only is false",
                 js=False)
    return sorted({limpiar(x, 60) for x in salida.strip().split(", ") if x.strip()})


def cerrar_app(nombre):
    """Quit a running app the normal way (it still asks to save unsaved work). Only names that are open now."""
    abiertas = apps_abiertas()
    iguales = [a for a in abiertas if a.casefold() == limpiar(nombre, 60).casefold()]
    if not iguales:
        raise HerramientaError("esa app no está abierta")
    if iguales[0] in ("Finder", "Terminal", "loginwindow", "Dock", "SystemUIServer"):
        raise HerramientaError("esa app no la cierro: el sistema o yo dependemos de ella")
    osa("on run argv\ntell application (item 1 of argv) to quit\nend run", iguales[0], js=False)
    import time
    for _ in range(10):
        time.sleep(0.3)
        if iguales[0] not in apps_abiertas():
            return {"cerrada": iguales[0]}
    return {"cerrada": None, "nota": f"{iguales[0]} sigue abierta (quizá pregunta si guardar); revísala"}


# ---- Shortcuts (the owner builds them in the Shortcuts app; the assistant only runs them by name) ----
def atajos():
    return sorted({limpiar(x, 80) for x in _correr([SHORTCUTS, "list"]).splitlines() if x.strip()})


def correr_atajo(nombre, entrada=None):
    lista = atajos()
    iguales = [a for a in lista if a.casefold() == limpiar(nombre, 80).casefold()]
    if not iguales:
        raise HerramientaError("no existe un atajo con ese nombre; pide la lista de atajos")
    cmd = [SHORTCUTS, "run", iguales[0]]
    ruta = None
    try:
        if entrada:
            tmp = NIKLAUS / "data" / "mac"
            tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, ruta = tempfile.mkstemp(dir=tmp, suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                texto = limpiar(entrada, lineas=True)
                if len(texto) > 500:                 # the owner's card shows the whole input, so it stays short
                    raise HerramientaError("la entrada de un atajo va hasta 500 caracteres")
                fh.write(texto)
            cmd += ["--input-path", ruta]
        salida = _correr(cmd, timeout=120)
    finally:
        if ruta:
            os.unlink(ruta)
    return {"atajo": iguales[0], "salida": limpiar(salida, 1500, lineas=True)}


def atajos_libres():
    """Names the owner lets run without asking (local/ajustes.json: {"atajos_libres": ["Modo reunión"]})."""
    ruta = NIKLAUS / "local" / "ajustes.json"
    try:
        st = os.lstat(ruta)
        if os.path.islink(ruta) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            return set()
        lista = json.loads(ruta.read_text(encoding="utf-8")).get("atajos_libres", [])
    except (OSError, ValueError, AttributeError, RecursionError):
        return set()
    return {limpiar(x, 80).casefold() for x in lista if isinstance(x, str)} if isinstance(lista, list) else set()
