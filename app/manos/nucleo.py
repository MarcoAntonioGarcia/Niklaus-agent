"""Niklaus's hands: reversible file actions over the whole computer (owner's decision, 2026-11-14).

Every action is recorded in data/manos/diario.jsonl and can be undone. Nothing is ever deleted for good:
"delete" moves to the real macOS Trash (NSFileManager.trashItemAtURL), overwriting keeps a backup first, and
undoing a write sends the current version to the Trash before restoring.
The limits are enforced HERE, not by the model, and fail closed:
  - never the secrets vaults or HOME's configuration: every ~/.<name> except ~/.Trash (SSH keys, shell files,
    ~/.gitconfig, ~/.local, ~/.config, ~/.claude…), ~/Library (Keychains, cookies, LaunchAgents) and browser profiles;
  - never the system (/System, /Library, /usr, /bin, /sbin, /private, /opt, /Applications, .app bundles);
  - never anything Claude Code or git loads by itself (.claude, .git, CLAUDE.md, CLAUDE.local.md, AGENTS.md,
    .mcp.json, anywhere) nor the agent's own controls or Darkroom;
  - never a folder that CONTAINS the system, the secrets or the agent's controls (moving it would carry them
    along). An ordinary project folder that has its own .git or CLAUDE.md inside can be moved or copied whole.
Paths are compared case- and normalization-insensitively (APFS) and by file identity (st_dev, st_ino).
Standard library plus PyObjC (Foundation) for the Trash.
"""
import contextlib
import ctypes
import errno
import fcntl
import json
import os
import pwd
import re
import shutil
import stat
import threading
import time
import unicodedata
import uuid
from pathlib import Path

HOME = Path(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))
NIKLAUS = Path(os.path.realpath(Path(__file__).resolve().parents[2]))
DATA = NIKLAUS / "data" / "manos"
DIARIO = DATA / "diario.jsonl"
RESPALDOS = DATA / "respaldos"
MAX_LOTE = 500
MAX_TEXTO = 2_000_000
MAX_RESPALDO = 200 * 1024 * 1024
LOTE_RE = re.compile(r"\[lote ([0-9a-f]{12})\]")

SISTEMA = [Path(p) for p in ("/System", "/Library", "/usr", "/bin", "/sbin", "/private", "/etc", "/var", "/tmp",
                             "/dev", "/cores", "/opt", "/Applications", "/Volumes", "/Network")] + \
          [HOME / "Library", HOME / "Applications"]
NOMBRES_PROHIBIDOS = {".claude", ".git", "claude.md", "claude.local.md", "agents.md", ".mcp.json"}

# Extension → folder, for organizar(criterio="tipo").
TIPOS = {
    "Imágenes": {"jpg", "jpeg", "png", "gif", "heic", "webp", "tiff", "bmp", "svg", "raw", "cr2", "nef"},
    "Videos": {"mp4", "mov", "m4v", "avi", "mkv", "webm", "wmv"},
    "Audio": {"mp3", "m4a", "wav", "aac", "flac", "ogg", "aiff", "opus"},
    "Documentos": {"pdf", "doc", "docx", "txt", "md", "rtf", "pages", "odt", "tex", "epub"},
    "Hojas de cálculo": {"xls", "xlsx", "csv", "numbers", "ods", "tsv"},
    "Presentaciones": {"ppt", "pptx", "key", "odp"},
    "Comprimidos": {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz"},
    "Instaladores": {"dmg", "pkg", "iso"},
    "Código": {"py", "js", "ts", "html", "css", "json", "sh", "c", "cpp", "h", "java", "go", "rs", "ipynb", "m", "nb"},
}


class ManosError(Exception):
    """A refused or failed action, with a message meant for the owner."""


class DiarioError(ManosError):
    """The journal could not be written: the batch stops right there and says what was done."""


# ---------- paths ----------

def _canon(p):
    """APFS compares names case- and normalization-insensitively: so do we."""
    return unicodedata.normalize("NFC", str(p)).casefold()


def _controles():
    """Protected bases, computed on every call (DATA can change in tests)."""
    return [NIKLAUS / p for p in (".claude", ".git", "tools", "app", "scripts", "skills", "venv", "models", "local",
                                  "herramientas.toml", "skills.toml", "INVENTARIO.md", "PENDIENTES.md")] + \
           [NIKLAUS / "data" / "manos", NIKLAUS / "data" / "bitacora", NIKLAUS / "data" / "voz",
            NIKLAUS / "data" / "memoria", DATA,
            HOME / "Apps" / "darkroom"] + SISTEMA


def _bajo(cr, cb):
    return cr == cb or cr.startswith(cb.rstrip("/") + "/")


def _motivo(real):
    """Why `real` is off limits, or None. `real` is absolute with its parent already resolved."""
    cr = _canon(real)
    ch = _canon(HOME)
    partes = [_canon(x) for x in real.parts[1:]]
    if any(x in NOMBRES_PROHIBIDOS for x in partes):
        return "es algo que Claude Code o git cargan solos (.claude, .git, CLAUDE.md, .mcp.json…)"
    if any(x.endswith(".app") for x in partes[:-1]):
        return "es el contenido de una aplicación"
    if cr in (ch, "/", _canon(HOME.parent)):
        return "es tu carpeta de inicio completa o la contiene"
    if _bajo(cr, ch):
        primera = cr[len(ch):].lstrip("/").split("/")[0]
        if primera.startswith(".") and primera != ".trash":
            return "es configuración o secretos de tu carpeta de inicio (~/.algo)"
    bases = [Path(os.path.realpath(b)) for b in _controles()]
    for b in bases:
        cb = _canon(b)
        if _bajo(cr, cb):
            return "es parte del sistema o de los controles de Niklaus"
        if _bajo(cb, cr):
            return "contiene algo protegido (sistema, secretos o controles de Niklaus)"
    # identity: catches any spelling the string checks above missed
    ids = set()
    for b in bases + [HOME / ".ssh"]:
        try:
            s = os.stat(b)
            ids.add((s.st_dev, s.st_ino))
        except OSError:
            pass
    for a in [real] + list(real.parents):
        try:
            s = os.lstat(a) if a == real else os.stat(a)
        except OSError:
            continue
        if (s.st_dev, s.st_ino) in ids:
            return "es parte del sistema, de tus secretos o de los controles de Niklaus"
    try:
        s, h = os.lstat(real), os.stat(HOME)
        if (s.st_dev, s.st_ino) == (h.st_dev, h.st_ino):
            return "es tu carpeta de inicio completa"
    except OSError:
        pass
    return None


def validar(ruta, debe_existir=True):
    """Absolute, real path the hands may touch, or ManosError. The parent is resolved (links in the middle are
    followed and checked); the last component itself is not followed, so moving a link moves the link — but a link
    that points into a protected place is refused too."""
    if not isinstance(ruta, (str, os.PathLike)):
        raise ManosError("ruta inválida")
    ruta = str(ruta).strip()
    if not ruta or "\x00" in ruta or len(ruta) > 4096:
        raise ManosError("ruta vacía o inválida")
    p = Path(os.path.expanduser(ruta))
    if not p.is_absolute():
        raise ManosError(f"la ruta debe ser absoluta (o empezar con ~): {ruta}")
    if p.name in ("", ".", "..") or (ruta.endswith("/") and ruta != "/"):
        raise ManosError(f"ruta inválida: {ruta}")
    real = Path(os.path.realpath(p.parent)) / p.name
    motivo = _motivo(real)
    if motivo is None and os.path.islink(real):
        motivo = _motivo(Path(os.path.realpath(real)))
    if motivo:
        raise ManosError(f"no toco {real}: {motivo}")
    if debe_existir and not os.path.lexists(real):
        raise ManosError(f"no existe: {real}")
    return real


def carpeta_real(ruta):
    """A validated, existing, real directory (not a link to one)."""
    c = validar(ruta)
    if c.is_symlink() or not c.is_dir():
        raise ManosError(f"no es una carpeta (o es un enlace): {c}")
    return c


def libre(destino):
    """A destination that does not exist yet: "name (2).ext", "name (3).ext"… Never overwrites."""
    if not os.path.lexists(destino):
        return destino
    base, ext = destino.stem, destino.suffix
    for k in range(2, 10_000):
        cand = destino.with_name(f"{base} ({k}){ext}")
        if not os.path.lexists(cand):
            return cand
    raise ManosError(f"demasiados archivos llamados {destino.name}")


# ---------- journal (one lock for actions, undo and reads, across processes) ----------

_local = threading.local()


def _preparar():
    for d in (DATA, RESPALDOS):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        if d.is_symlink() or not d.is_dir():
            raise ManosError(f"{d} es un enlace o no es carpeta; no sigo")


@contextlib.contextmanager
def _candado(espera=None):
    """One lock across processes. espera=None waits as long as needed (the hands' own actions); a number is a cap in
    seconds for readers that must never hang (the window): ManosError if the hands stay busy longer."""
    if getattr(_local, "n", 0):
        _local.n += 1
        try:
            yield
        finally:
            _local.n -= 1
        return
    _preparar()
    fd = os.open(DATA / ".candado", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if espera is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            limite = time.monotonic() + espera
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > limite:
                        raise ManosError("las manos siguen ocupadas con otra acción; inténtalo en un momento") from None
                    time.sleep(0.05)
        _local.n = 1
        try:
            yield
        finally:
            _local.n = 0
    finally:
        os.close(fd)


def _anotar(entradas):
    with _candado():
        fd = os.open(DIARIO, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+b") as fh:
            tam = os.fstat(fh.fileno()).st_size
            # a line cut by a crash must never swallow the next, valid entry
            prefijo = b"\n" if tam and os.pread(fh.fileno(), 1, tam - 1) != b"\n" else b""
            fh.write(prefijo + b"".join((json.dumps(e, ensure_ascii=False) + "\n").encode("utf-8")
                                        for e in entradas))
            fh.flush()
            os.fsync(fh.fileno())


def _leer_diario(espera=None):
    """(entries, number of corrupt lines). Follow-up records ("dato") are merged into their entry."""
    with _candado(espera):
        try:
            fd = os.open(DIARIO, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return [], 0
        with os.fdopen(fd, encoding="utf-8", errors="replace") as fh:
            out, malas, por_id = [], 0, {}
            for i, linea in enumerate(fh):
                if not linea.strip():
                    continue
                try:
                    e = json.loads(linea)
                except ValueError:
                    malas += 1
                    continue
                if not isinstance(e, dict):
                    malas += 1
                    continue
                if e.get("op") == "dato":
                    if e.get("entrada") in por_id:
                        por_id[e["entrada"]].update({k: v for k, v in e.items() if k not in ("op", "entrada", "t")})
                    continue
                if e.get("lote") and "e" not in e and e.get("op") not in ("deshacer", "deshecho"):
                    e["e"] = f"{e['lote']}:{i}"          # entries written before per-entry ids
                if e.get("e"):
                    por_id[e["e"]] = e
                out.append(e)
            return out, malas


class Lote:
    """One tool call = one batch: undone together.
    Write-ahead: every action is journaled (fsynced) BEFORE it happens, with what is known by then; what is only
    known afterwards (the Trash name, the inode) is added with completar(). Undo looks at which path exists, so
    a kill at any moment never leaves done work out of the journal."""

    def __init__(self, descripcion):
        self.id, self.descripcion, self.entradas = uuid.uuid4().hex[:12], descripcion, []

    def _escribir(self, registro, que):
        try:
            _anotar([registro])
        except Exception as exc:
            hecho = "; ".join(f"{x['op']} {x.get('de', '')} → {x.get('a', '')}" for x in self.entradas
                              if not x.get("fallo"))
            raise DiarioError(f"No pude anotar en el diario ({type(exc).__name__}: {exc}); {que}. Me detuve aquí. "
                              f"Hecho y anotado antes: {hecho or 'nada'} {self.etiqueta()}") from None

    def anotar(self, **e):
        """Before the action. Returns the entry id. If the journal fails, nothing was done for this entry."""
        entrada = dict(e, lote=self.id, e=uuid.uuid4().hex[:12], desc=self.descripcion,
                       t=time.strftime("%Y-%m-%d %H:%M:%S"))
        self._escribir(entrada, "no hice esta acción")
        self.entradas.append(entrada)
        return entrada["e"]

    def completar(self, eid, **campos):
        """After the action: data only known then. fallo=True marks an action that raised before doing anything."""
        self._escribir(dict(campos, op="dato", entrada=eid, t=time.strftime("%Y-%m-%d %H:%M:%S")),
                       "la acción sí se hizo, pero su detalle no quedó anotado")
        for x in self.entradas:
            if x["e"] == eid:
                x.update(campos)

    def hacer(self, accion, **intento):
        """Journal, act, and mark a clean failure (an exception from the action means it did not happen)."""
        eid = self.anotar(**intento)
        try:
            return eid, accion()
        except Exception:
            with contextlib.suppress(Exception):
                self.completar(eid, fallo=True)
            raise

    def cerrar(self):
        return self.id

    def etiqueta(self):
        return f"[lote {self.id}]"


# ---------- primitive operations ----------

_libc = ctypes.CDLL(None, use_errno=True)
_libc.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
RENAME_EXCL = 0x00000004


class PapeleraError(OSError):
    """macOS could not trash the item (possibly transient: an undo that hits it can be retried)."""


def _mover(src, dst):
    """Rename that never replaces an existing destination (RENAME_EXCL); copy+delete only across volumes."""
    if _libc.renamex_np(os.fsencode(str(src)), os.fsencode(str(dst)), RENAME_EXCL) == 0:
        return
    err = ctypes.get_errno()
    if err != errno.EXDEV:
        raise OSError(err, os.strerror(err), str(dst))
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "ya existe", str(dst))
    shutil.move(str(src), str(dst))


def _papelera(p):
    from Foundation import NSURL, NSFileManager      # PyObjC
    ok, destino, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
        NSURL.fileURLWithPath_(str(p)), None, None)
    if not ok:
        raise PapeleraError(errno.EIO, f"macOS no pudo mandar {p.name} a la Papelera: {error}", str(p))
    return Path(str(destino.path()))


def _abrir_regular(p):
    """Read-only fd of a regular file, never through a link."""
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ManosError(f"{p} no es un archivo normal")
    return fd


def _copiar_fd(origen_fd, out):
    os.lseek(origen_fd, 0, os.SEEK_SET)
    while True:
        trozo = os.read(origen_fd, 1 << 20)
        if not trozo:
            break
        out.write(trozo)


def _escribir_atomico(p, origen_fd=None, extra=b"", modo=0o644):
    """Writes [contents of origen_fd] + extra to a new temp file next to p, then renames it over p.
    Never follows a link at p; keeps `modo` exactly (fchmod: the umask does not cut it), never execute bits;
    the temp is ours and is removed if anything fails."""
    tmp = p.with_name(f".{p.name}.niklaus-{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), modo & 0o666)
            if origen_fd is not None:
                _copiar_fd(origen_fd, out)
            out.write(extra)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ---------- actions (each returns (lote_id, message)) ----------

def mover(origen, destino_carpeta, nuevo_nombre=None):
    if nuevo_nombre is not None and (not isinstance(nuevo_nombre, str) or "/" in nuevo_nombre
                                     or nuevo_nombre in ("", ".", "..")):
        raise ManosError("el nombre nuevo no puede contener '/'")
    src = validar(origen)
    carpeta = carpeta_real(destino_carpeta)
    with _candado():
        dst = libre(validar(str(carpeta / (nuevo_nombre or src.name)), debe_existir=False))
        if _bajo(_canon(dst), _canon(src)):
            raise ManosError("no puedo mover una carpeta dentro de sí misma")
        lote = Lote(f"mover {src.name}")
        lote.hacer(lambda: _mover(src, dst), op="mover", de=str(src), a=str(dst))
    return lote.id, f"Moví {src.name} a {dst.parent} como {dst.name}. {lote.etiqueta()}"


def renombrar(ruta, nuevo_nombre):
    if not isinstance(nuevo_nombre, str) or "/" in nuevo_nombre or nuevo_nombre in ("", ".", ".."):
        raise ManosError("el nombre nuevo no puede contener '/'")
    src = validar(ruta)
    with _candado():
        dst = libre(validar(str(src.with_name(nuevo_nombre)), debe_existir=False))
        lote = Lote(f"renombrar {src.name}")
        lote.hacer(lambda: _mover(src, dst), op="mover", de=str(src), a=str(dst))
    return lote.id, f"Renombré {src.name} a {dst.name}. {lote.etiqueta()}"


def copiar(origen, destino_carpeta):
    src = validar(origen)
    carpeta = carpeta_real(destino_carpeta)
    with _candado():
        dst = libre(validar(str(carpeta / src.name), debe_existir=False))
        lote = Lote(f"copiar {src.name}")
        eid = lote.anotar(op="copiar", a=str(dst))
        os.mkdir(dst) if src.is_dir() and not src.is_symlink() else os.close(
            os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))   # reserve the name
    if True:                                    # the (possibly long) copy runs without holding the lock
        try:
            if src.is_dir() and not src.is_symlink():
                shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
            elif src.is_symlink():
                os.unlink(dst)                  # the reserved placeholder: a link is copied as a link
                os.symlink(os.readlink(src), dst)
            else:                               # into our own placeholder, never through a link planted there
                origen = _abrir_regular(src)
                try:
                    with os.fdopen(os.open(dst, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW), "wb") as out:
                        _copiar_fd(origen, out)
                        out.flush()
                        os.fsync(out.fileno())
                finally:
                    os.close(origen)
                shutil.copystat(src, dst, follow_symlinks=False)
        except Exception as exc:
            nota = ""
            if os.path.lexists(dst):
                try:
                    _papelera(dst)
                    nota = " Lo que alcancé a copiar quedó en la Papelera."
                except Exception as exc2:
                    nota = f" Quedó una copia incompleta en {dst} (no pude mandarla a la Papelera: {exc2})."
            with contextlib.suppress(Exception):
                lote.completar(eid, fallo=True)
            raise ManosError(f"La copia de {src.name} falló ({type(exc).__name__}: {exc}).{nota}") from None
        lote.completar(eid, ino=os.lstat(dst).st_ino)
    return lote.id, f"Copié {src.name} a {dst}. {lote.etiqueta()}"


def crear_carpeta(ruta):
    p = validar(ruta, debe_existir=False)
    carpeta_real(str(p.parent))
    with _candado():
        if os.path.lexists(p):
            raise ManosError(f"ya existe: {p}")
        lote = Lote(f"crear carpeta {p.name}")
        lote.hacer(p.mkdir, op="carpeta", a=str(p))
    return lote.id, f"Creé la carpeta {p}. {lote.etiqueta()}"


def a_papelera(rutas):
    if isinstance(rutas, str):
        rutas = [rutas]
    if not isinstance(rutas, list) or not rutas or len(rutas) > MAX_LOTE:
        raise ManosError(f"entre 1 y {MAX_LOTE} rutas")
    validadas = [validar(r) for r in rutas]          # all checked before touching anything
    lote = Lote(f"papelera ({len(validadas)})")
    errores = []
    with _candado():
        for p in validadas:
            try:
                eid, destino = lote.hacer(lambda p=p: _papelera(p), op="papelera", de=str(p))
            except DiarioError:
                raise
            except Exception as exc:
                errores.append(f"{p.name}: {exc}")
                continue
            lote.completar(eid, a=str(destino))
    hechos = len(validadas) - len(errores)
    if errores:
        raise ManosError(f"PARCIAL: mandé a la Papelera {hechos} de {len(validadas)}; fallaron "
                         + "; ".join(errores[:10]) + (f" {lote.etiqueta()}" if hechos else ""))
    return lote.id, (f"Mandé a la Papelera {hechos} elemento(s). Se pueden sacar desde ahí o con «deshaz». "
                     f"{lote.etiqueta()}")


def escribir(ruta, texto, anexar=False):
    if not isinstance(texto, str) or len(texto) > MAX_TEXTO:
        raise ManosError("texto inválido o demasiado grande")
    p = validar(ruta, debe_existir=False)
    carpeta_real(str(p.parent))
    with _candado():
        lote = Lote(f"escribir {p.name}")
        respaldo, fd, modo = None, None, 0o644
        if os.path.lexists(p):
            st = os.lstat(p)
            if not stat.S_ISREG(st.st_mode):
                raise ManosError(f"no escribo sobre enlaces ni carpetas: {p}")
            if st.st_nlink > 1:
                raise ManosError(f"{p.name} tiene enlaces duros; no lo toco")
            if st.st_size > MAX_RESPALDO:
                raise ManosError(f"{p.name} es demasiado grande para respaldarlo antes")
            modo = st.st_mode & 0o666                # never keeps execute bits
            fd = _abrir_regular(p)
            _preparar()
            respaldo = RESPALDOS / f"{lote.id}-{p.name}"
        try:
            eid = lote.anotar(op="escribir", a=str(p), respaldo=str(respaldo) if respaldo else None, modo=modo)
            try:
                if fd is not None:
                    rfd = os.open(respaldo, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(rfd, "wb") as out:
                        _copiar_fd(fd, out)
                        out.flush()
                        os.fsync(out.fileno())
                _escribir_atomico(p, fd if anexar else None, texto.encode("utf-8"), modo)
            except Exception:
                with contextlib.suppress(Exception):
                    lote.completar(eid, fallo=True)
                raise
        finally:
            if fd is not None:
                os.close(fd)
        lote.completar(eid, ino=os.lstat(p).st_ino)
    return lote.id, (f"Escribí {p.name}" + (" (con respaldo del anterior). " if respaldo else " (archivo nuevo). ")
                     + lote.etiqueta())


def organizar(carpeta, criterio="tipo"):
    base = carpeta_real(carpeta)
    archivos = []
    for f in sorted(base.iterdir()):
        try:
            if f.name.startswith(".") or not stat.S_ISREG(os.lstat(f).st_mode):
                continue
        except OSError:
            continue
        archivos.append(f)
    if len(archivos) > MAX_LOTE:
        raise ManosError(f"{len(archivos)} archivos: más que el tope de {MAX_LOTE} por lote")
    lote = Lote(f"organizar {base.name} por {criterio}")
    movidos, omitidos, errores, malos = 0, [], [], set()
    with _candado():
        for f in archivos:
            try:
                validar(str(f))
            except ManosError:
                omitidos.append(f.name)                 # protected (CLAUDE.md…): left where it is
                continue
            try:
                if criterio == "fecha":
                    grupo = time.strftime("%Y-%m", time.localtime(os.lstat(f).st_mtime))
                else:
                    ext = f.suffix.lower().lstrip(".")
                    grupo = next((g for g, exts in TIPOS.items() if ext in exts), "Otros")
            except OSError as exc:
                errores.append(f"{f.name}: {type(exc).__name__}: {exc}")
                continue
            destino = base / grupo
            if grupo in malos:
                omitidos.append(f.name)
                continue
            try:
                if os.path.lexists(destino):
                    if destino.is_symlink() or not destino.is_dir():
                        malos.add(grupo)                # a file or a link named like the folder: never follow it
                        omitidos.append(f.name)
                        continue
                else:
                    lote.hacer(destino.mkdir, op="carpeta", a=str(destino))
                dst = libre(validar(str(destino / f.name), debe_existir=False))
                lote.hacer(lambda f=f, dst=dst: _mover(f, dst), op="mover", de=str(f), a=str(dst))
                movidos += 1
            except DiarioError:
                raise
            except Exception as exc:
                errores.append(f"{f.name}: {type(exc).__name__}: {exc}")
    nota = ((f" Dejé {len(omitidos)} en su lugar ({', '.join(omitidos[:5])}{'…' if len(omitidos) > 5 else ''})."
             if omitidos else "")
            + (f" No uso {', '.join(sorted(malos))}: ya existe y no es una carpeta." if malos else ""))
    if errores:
        raise ManosError(f"PARCIAL: organicé {movidos} de {len(archivos)}; fallaron " + "; ".join(errores[:10])
                         + nota + (f" {lote.etiqueta()}" if lote.entradas else ""))
    return lote.id, f"Organicé {movidos} archivo(s) de {base.name} por {criterio}.{nota} {lote.etiqueta()}"


# ---------- undo ----------

def _revertir(e):
    """(ok, message, final). Decides by what exists on disk, so an entry journaled just before a kill (the
    action may or may not have happened) is handled right. final=True: retrying would not help."""
    op = e.get("op")
    if op in ("mover", "papelera"):
        original = Path(e["de"])
        actual = Path(e["a"]) if e.get("a") else None
        if actual is not None and os.path.lexists(actual):
            validar(str(original), debe_existir=False)
            validar(str(actual), debe_existir=False)       # a forged journal must not move a protected path
            destino = libre(original)
            _mover(actual, destino)
            return True, f"devolví {destino.name} a {destino.parent}", True
        if os.path.lexists(original):
            return True, f"{original.name} sigue en su lugar (no se llegó a mover)", True
        if actual is None:                              # trashed, but killed before its Trash name was noted
            return False, (f"{original.name} está en la Papelera pero no sé con qué nombre; búscalo ahí como "
                           f"«{original.name}» y usa «Devolver»"), True
        return False, f"ya no existe {actual.name}; no pude devolverlo", True
    if op == "copiar":
        p = validar(e["a"], debe_existir=False)            # a forged journal must not trash a protected path
        if not os.path.lexists(p):
            return True, f"la copia {p.name} ya no existe", True
        if e.get("ino") is not None and os.lstat(p).st_ino != e["ino"]:
            return False, f"{p.name} ya no es la copia que hice; lo dejo", True
        _papelera(p)
        return True, f"mandé la copia {p.name} a la Papelera", True
    if op == "carpeta":
        p = validar(e["a"], debe_existir=False)     # a forged journal must not rmdir a protected/foreign folder
        try:
            p.rmdir()
            return True, f"quité la carpeta vacía {p.name}", True
        except FileNotFoundError:
            return True, f"la carpeta {p.name} ya no existe", True
        except OSError as exc:
            if exc.errno == errno.ENOTEMPTY:
                return False, f"la carpeta {p.name} no está vacía; la dejo", True
            raise
    if op == "escribir":
        p = validar(e["a"], debe_existir=False)
        fd = None
        if e.get("respaldo"):                           # backup first: nothing moves if it cannot be restored
            r = Path(e["respaldo"])
            if Path(os.path.realpath(r.parent)) != Path(os.path.realpath(RESPALDOS)):
                return False, f"el respaldo de {p.name} no está donde debe; no toqué el archivo", True
            try:
                fd = _abrir_regular(r)
            except (OSError, ManosError) as exc:
                if e.get("ino") is None:                # killed before the backup existed: nothing was written
                    return True, f"{p.name} no se llegó a escribir", True
                return False, f"no encuentro el respaldo de {p.name} ({type(exc).__name__}); no toqué el archivo", True
        try:
            enviada = ""
            if os.path.lexists(p):
                if not stat.S_ISREG(os.lstat(p).st_mode):
                    return False, f"{p.name} ya no es un archivo normal; no lo toco", True
                en_papelera = _papelera(p)              # the current version is never lost
                enviada = f" (la versión que había quedó en la Papelera como «{en_papelera.name}»)"
            if fd is None:
                return True, (f"mandé el archivo nuevo {p.name} a la Papelera" if enviada
                              else f"{p.name} ya no existe"), True
            try:
                _escribir_atomico(p, fd, b"", int(e.get("modo") or 0o644))
            except Exception as exc:
                return False, (f"no pude restaurar {p.name} ({type(exc).__name__}: {exc}){enviada}; "
                               f"el respaldo sigue en {e['respaldo']}"), False
            return True, f"restauré {p.name} desde su respaldo{enviada}", True
        finally:
            if fd is not None:
                os.close(fd)
    return False, f"no sé deshacer «{op}»", True


def _estado(diario):
    """Per batch: its ordered entries, and those still pending (failed actions never count)."""
    hechas = {x.get("entrada") for x in diario if x.get("op") == "deshecho"}
    lotes_viejos = {x.get("deshace") for x in diario if x.get("op") == "deshacer"}   # pre-2026-11-15 format
    lotes = {}
    for x in diario:
        lid = x.get("lote")
        if lid and x.get("op") not in ("deshacer", "deshecho") and not x.get("fallo"):
            lotes.setdefault(lid, []).append(x)
    pendientes = {lid: [x for x in es if x["e"] not in hechas and lid not in lotes_viejos]
                  for lid, es in lotes.items()}
    return lotes, pendientes


def _con_fallos(diario):
    return {x.get("lote") for x in diario if x.get("op") == "deshecho" and x.get("ok") is False}


def deshacer(n=1, lote=None, espera=None):
    """Undo the last n batches with something pending (or exactly `lote`). Returns (lote, message, ok).
    An entry that fails for a transient reason stays pending (it can be retried); one that cannot be undone
    (the file is gone or changed) is closed and reported. If the journal cannot record an undo that did happen,
    the message says so."""
    with _candado(espera):
        diario, malas = _leer_diario()
        lotes, pendientes = _estado(diario)
        aviso = f" Ojo: {malas} línea(s) dañadas en el diario (se ignoraron)." if malas else ""
        if lote is not None:
            if lote not in lotes:
                return None, f"No encuentro ese lote ({lote}).{aviso}", False
            if not pendientes[lote]:
                if lote in _con_fallos(diario):
                    return lote, (f"«{lotes[lote][0].get('desc')}» ya se deshizo, pero en parte: lo que faltó no "
                                  f"se puede revertir (el archivo ya no estaba o cambió).{aviso}"), False
                return lote, f"«{lotes[lote][0].get('desc')}» ya estaba deshecho.{aviso}", True
            elegidos = [lote]
        else:
            elegidos = []
            for x in reversed(diario):
                lid = x.get("lote")
                if pendientes.get(lid) and lid not in elegidos:
                    elegidos.append(lid)
                    if len(elegidos) >= max(1, min(int(n), 20)):
                        break
        if not elegidos:
            return None, "No hay nada que deshacer." + aviso, True
        mensajes, ok_total = [], True
        for lid in elegidos:
            mensajes.append(f"«{lotes[lid][0].get('desc')}»:")
            for x in reversed(pendientes[lid]):
                try:
                    ok, msg, final = _revertir(x)
                except ManosError as exc:                   # deterministic (a limit): retrying would not help
                    ok, final, msg = False, True, f"no pude revertir {x.get('op')}: {exc}"
                except Exception as exc:                    # transient (incl. the Trash): stays pending
                    ok, final = False, False
                    msg = f"no pude revertir {x.get('op')} ({type(exc).__name__}: {exc}); se puede reintentar"
                if final:
                    try:
                        _anotar([{"op": "deshecho", "entrada": x["e"], "lote": lid, "ok": ok,
                                  "t": time.strftime("%Y-%m-%d %H:%M:%S")}])
                    except Exception as exc:
                        msg += (f" (lo revertí, pero no pude anotarlo en el diario: {type(exc).__name__}; "
                                f"no lo deshagas otra vez)") if ok else f" (y no pude anotarlo: {type(exc).__name__})"
                        ok = False
                mensajes.append(msg + ";")
                ok_total = ok_total and ok
        return elegidos[0], ("Deshice: " if ok_total else "Deshice solo una parte: ") + " ".join(mensajes) + aviso, \
            ok_total


def historial(n=15):
    diario, malas = _leer_diario()
    lotes, pendientes = _estado(diario)
    fallos = _con_fallos(diario)
    vistos, out = set(), []
    for x in reversed(diario):
        lid = x.get("lote")
        if lid not in lotes or lid in vistos:
            continue
        vistos.add(lid)
        primera = lotes[lid][0]
        quedan = len(pendientes[lid])
        marca = ("" if quedan == len(lotes[lid]) else "  (deshecho en parte)" if quedan or lid in fallos
                 else "  (deshecho)")
        out.append(f"{primera.get('t', '?')}  {primera.get('desc', primera.get('op'))}{marca}  [lote {lid}]")
        if len(out) >= max(1, min(int(n), 100)):
            break
    if malas:
        out.append(f"ojo: {malas} línea(s) dañadas en el diario")
    return "\n".join(out) or "Sin acciones todavía."


def lotes_desde(marca, espera=None):
    """Batches journaled after `marca` (a count of entries from len(_leer_diario()[0])), with their summary:
    used by the app to show actions done during an interrupted turn."""
    diario, _ = _leer_diario(espera)
    lotes, _ = _estado(diario[marca:])
    return [(lid, es[0].get("desc", es[0].get("op")), len(es)) for lid, es in lotes.items()]
