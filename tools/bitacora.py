"""Hook Stop de Claude Code (solo este proyecto): anota la última respuesta del asistente en
data/bitacora/AAAA-MM-DD.md para que el orquestador la lea.

- Toma el texto de `last_assistant_message` del JSON que llega por stdin. NO usa transcript_path: el
  hook se dispara antes de que la respuesta se guarde en el transcript (bug conocido).
- Etiqueta: variable NIKLAUS_TERMINAL (saneada) o "niklaus". Hora local. Respuesta recortada a 12 KB.
- La respuesta puede traer texto derivado de videos: es DATO NO CONFIABLE. Se quitan los caracteres
  de control (secuencias ESC de terminal) y cada línea va citada con "> " entre marcadores con un
  nonce por entrada, así ninguna línea puede fingir una cabecera "## hora · etiqueta" (M1).
- La carpeta se abre sin seguir enlaces, se comprueba que sea del usuario y se deja en 700; el
  archivo se crea con 600 relativo a esa carpeta (L3). Candado con espera acotada (L4).
- Sin red. SIEMPRE sale con 0: un fallo aquí nunca debe bloquear a Claude Code (se avisa por stderr).
Uso (lo lanza el hook): venv/bin/python -I -B tools/bitacora.py  < JSON del hook
"""
import fcntl
import json
import os
import re
import secrets
import stat
import sys
import time
from pathlib import Path

MAX_BYTES = 12 * 1024
ESPERA_CANDADO = 2.0
CARPETA = Path(__file__).resolve().parent.parent / "data" / "bitacora"
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")       # todo menos \t y \n


def texto_de(crudo: bytes) -> str:
    try:
        datos = json.loads(crudo.decode("utf-8", "replace")) if crudo.strip() else {}   # L1: nunca perder la entrada
    except ValueError:
        datos = {}
    texto = datos.get("last_assistant_message") if isinstance(datos, dict) else None
    if not isinstance(texto, str) or not texto.strip():
        return "(sin texto)"
    texto = CONTROL.sub("", texto.replace("\r\n", "\n"))
    b = texto.encode("utf-8", "replace")
    if len(b) > MAX_BYTES:
        texto = b[:MAX_BYTES].decode("utf-8", "ignore") + "\n…(recortado a 12 KB)"
    return texto


def entrada(texto: str, etiqueta: str) -> bytes:
    nonce = secrets.token_hex(8)
    citado = "\n".join("> " + linea for linea in texto.split("\n"))
    return (f"\n## {time.strftime('%H:%M:%S')} · {etiqueta}\n\n"
            f"<<<DATO NO CONFIABLE {nonce}>>>\n{citado}\n<<<FIN {nonce}>>>\n").encode("utf-8")


def abrir_carpeta() -> int:
    CARPETA.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(CARPETA, 0o700)
    except FileExistsError:
        pass
    dfd = os.open(CARPETA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    st = os.fstat(dfd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        os.close(dfd)
        raise PermissionError(f"{CARPETA} no es una carpeta propia")
    os.fchmod(dfd, 0o700)
    return dfd


def candado(fd: int):
    limite = time.monotonic() + ESPERA_CANDADO
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() > limite:
                raise TimeoutError("candado ocupado más de 2 s")
            time.sleep(0.05)


def main():
    texto = texto_de(sys.stdin.buffer.read())
    etiqueta = re.sub(r"[^\w .-]", "", os.environ.get("NIKLAUS_TERMINAL", ""))[:40].strip() or "niklaus"
    datos = entrada(texto, etiqueta)
    dfd = abrir_carpeta()
    try:
        fd = os.open(f"{time.strftime('%Y-%m-%d')}.md", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                     0o600, dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        os.fchmod(fd, 0o600)
        candado(fd)
        escrito = 0
        while escrito < len(datos):                      # L2: escritura completa o error
            n = os.write(fd, datos[escrito:])
            if n <= 0:
                raise OSError("escritura incompleta")
            escrito += n
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:          # nunca bloquear el cierre del turno; dejar rastro en stderr
        sys.stderr.write(f"[bitacora] no se pudo anotar: {type(exc).__name__}: {exc}\n")
    sys.exit(0)
