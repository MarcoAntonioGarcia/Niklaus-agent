"""Apple Notes: list and search, read one note, create notes, and append to a note (the last one asks the owner).

Locked notes are never opened, and folders listed in local/ajustes.json ("notas_excluidas") are invisible here.
Note bodies are untrusted text (a shared note can be written by others). Programs are fixed JXA texts with argv.
Nothing here deletes a note; appending refuses shared notes and notes with attachments.
"""
import html
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "comun"))
from seguro import HerramientaError, limpiar, marcar_lectura_privada, osa, osa_json  # noqa: E402

NIKLAUS = Path(__file__).resolve().parents[2]
MAX_NOTAS, MAX_CUERPO, MAX_AGREGAR = 80, 8000, 1000
_BORRADAS = {"recently deleted", "eliminados recientemente", "eliminadas recientemente", "borrados recientemente"}

_LISTAR = r"""
function run(argv) {
  const N = Application('Notes');
  const ids = N.notes.id(), nombres = N.notes.name(), fechas = N.notes.modificationDate();
  const cerradas = N.notes.passwordProtected();
  const out = [];
  for (let i = 0; i < Math.min(ids.length, 2000); i++) {
    const ruta = [];                        // every folder up to the account; null when it cannot be known
    try {
      let c = N.notes.byId(ids[i]).container();
      for (let k = 0; k < 20; k++) {
        ruta.push(c.name());
        try { c = c.container(); if (c.class() !== 'folder') break; } catch (e) { break; }
      }
    } catch (e) { ruta.length = 0; }
    out.push({id: ids[i], titulo: nombres[i], fecha: fechas[i].toISOString(), carpeta: ruta.length ? ruta[0] : '',
              ruta: ruta.length ? ruta : null, cerrada: cerradas[i]});
  }
  return JSON.stringify(out);
}"""

_LEER = r"""
function run(argv) {
  const N = Application('Notes');
  const n = N.notes.byId(argv[0]);
  try { n.name(); } catch (e) { return JSON.stringify(null); }
  if (n.passwordProtected()) return JSON.stringify({cerrada: true});
  let carpeta = '';
  try { carpeta = n.container().name(); } catch (e) {}
  return JSON.stringify({id: n.id(), titulo: n.name(), carpeta: carpeta,
                         fecha: n.modificationDate().toISOString(), texto: n.plaintext()});
}"""

_CREAR = """on run argv
	tell application "Notes"
		if shared of default folder of default account then error "carpeta compartida"
		return id of (make new note at default folder of default account with properties {body:(item 1 of argv)})
	end tell
end run"""

_AGREGAR = r"""
function run(argv) {
  const N = Application('Notes');
  const n = N.notes.byId(argv[0]);
  try { n.name(); } catch (e) { return JSON.stringify(null); }
  if (n.passwordProtected()) return JSON.stringify({cerrada: true});
  if (n.name() !== argv[1]) return JSON.stringify({otro: true});
  if (n.shared()) return JSON.stringify({compartida: true});
  if (n.attachments.length > 0) return JSON.stringify({adjuntos: true});
  n.body = n.body() + argv[2];
  return JSON.stringify({id: n.id(), titulo: n.name()});
}"""


def _excluidas():
    ruta = NIKLAUS / "local" / "ajustes.json"
    try:
        st = os.lstat(ruta)
        if os.path.islink(ruta) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            return set()
        lista = json.loads(ruta.read_text(encoding="utf-8")).get("notas_excluidas", [])
    except (OSError, ValueError, AttributeError, RecursionError):
        return set()
    return {limpiar(x, 80).casefold() for x in lista if isinstance(x, str)} if isinstance(lista, list) else set()


def _id_ok(ident):
    s = str(ident or "")
    if not re.fullmatch(r"x-coredata://[A-Za-z0-9-]+/ICNote/p\d+", s):
        raise HerramientaError("ese id de nota no es válido; lista las notas para verlo")
    return s


def _todas():
    r = osa_json(_LISTAR, timeout=60)
    fuera = _excluidas() | _BORRADAS                   # the owner's excluded folders, and Notes' own trash
    def visible(n):                                 # fails closed: a note whose folders are unknown is not shown
        ruta = n.get("ruta")
        return isinstance(ruta, list) and ruta and not any(limpiar(x, 80).casefold() in fuera for x in ruta)
    return [n for n in (r or []) if isinstance(n, dict) and visible(n)]


def notas(busqueda=None, tope=30):
    marcar_lectura_privada()
    try:
        tope = int(tope)
    except (TypeError, ValueError):
        raise HerramientaError("el tope debe ser un número") from None
    if not 1 <= tope <= MAX_NOTAS:
        raise HerramientaError(f"de 1 a {MAX_NOTAS} notas")
    q = limpiar(busqueda, 80).casefold()
    lista = [{"id": limpiar(n.get("id"), 120), "titulo": limpiar(n.get("titulo"), 160),
              "carpeta": limpiar(n.get("carpeta"), 80), "fecha": limpiar(n.get("fecha"), 25)[:16].replace("T", " "),
              "cerrada": bool(n.get("cerrada"))} for n in _todas()]
    if q:
        lista = [n for n in lista if q in n["titulo"].casefold()]
    lista.sort(key=lambda n: n["fecha"], reverse=True)
    return lista[:tope], len(lista)


def _visible(ident):
    """The note must exist and not sit in an excluded folder."""
    ident = _id_ok(ident)
    if not any(n.get("id") == ident for n in _todas()):
        raise HerramientaError("no encuentro esa nota (o está en una carpeta que no puedo ver)")
    return ident


def leer_nota(ident):
    marcar_lectura_privada()
    n = osa_json(_LEER, _visible(ident), timeout=30)
    if not isinstance(n, dict):
        raise HerramientaError("no encuentro esa nota")
    if n.get("cerrada"):
        raise HerramientaError("esa nota está cerrada con contraseña; no la abro")
    return {"id": limpiar(n.get("id"), 120), "titulo": limpiar(n.get("titulo"), 160),
            "carpeta": limpiar(n.get("carpeta"), 80), "fecha": limpiar(n.get("fecha"), 25)[:16].replace("T", " "),
            "texto": limpiar(n.get("texto"), MAX_CUERPO, lineas=True)}


def _html(texto):
    return "".join(f"<div>{html.escape(l) or '<br>'}</div>" for l in texto.split("\n"))


def crear_nota(titulo, texto=""):
    titulo, texto = limpiar(titulo, 160), limpiar(texto, MAX_CUERPO, lineas=True)
    if not titulo:
        raise HerramientaError("la nota necesita un título")
    cuerpo = f"<div><h1>{html.escape(titulo)}</h1></div>" + (_html(texto) if texto else "")
    ident = _id_ok(osa(_CREAR, cuerpo, js=False, timeout=30).strip())
    n = osa_json(_LEER, ident, timeout=30)
    if not isinstance(n, dict):
        raise HerramientaError("Notas no confirmó la nota nueva")
    return {"id": ident, "titulo": limpiar(n.get("titulo"), 160), "carpeta": limpiar(n.get("carpeta"), 80)}


def agregar_a_nota(ident, titulo, texto):
    """Append text at the end of a note. `titulo` must be its exact title (the owner approves a card that shows it)."""
    texto = limpiar(texto, lineas=True)
    if not texto:
        raise HerramientaError("no hay texto que agregar")
    if len(texto) > MAX_AGREGAR:                     # the owner's card shows the whole text, so it stays short
        raise HerramientaError(f"agrego hasta {MAX_AGREGAR} caracteres a la vez")
    r = osa_json(_AGREGAR, _visible(ident), str(titulo or ""), _html(texto), timeout=30)
    if not isinstance(r, dict):
        raise HerramientaError("no encuentro esa nota")
    if r.get("cerrada"):
        raise HerramientaError("esa nota está cerrada con contraseña; no la toco")
    if r.get("otro"):
        raise HerramientaError("el título no coincide con el de la nota; no cambio nada")
    if r.get("compartida"):
        raise HerramientaError("esa nota es compartida: agregarle texto lo verían otras personas. Hazlo tú en Notas.")
    if r.get("adjuntos"):
        raise HerramientaError("esa nota tiene adjuntos y podría perder formato; agrégale tú el texto en Notas")
    if texto.split("\n")[-1][:60] not in leer_nota(ident)["texto"]:
        raise HerramientaError("Notas no confirmó el cambio; revisa la nota")
    return {"id": limpiar(r.get("id"), 120), "titulo": limpiar(r.get("titulo"), 160), "agregado": len(texto)}
