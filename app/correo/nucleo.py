"""Mail through the macOS Mail app (the accounts the owner added to it: Gmail, Outlook/Exchange, iCloud…).

Reading and drafting only. NOTHING here sends: a draft opens in a Mail window and the owner presses Send.
Every text from a message is untrusted (anyone can write an email); it is cleaned and fenced by the server.
Programs are fixed JXA texts that get their values as argv.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "comun"))
from seguro import HerramientaError, limpiar, marcar_lectura_privada, osa_json  # noqa: E402

MAX_CORREOS, MAX_CUERPO = 60, 6000
_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")

_LISTAR = r"""
function run(argv) {
  const Mail = Application('Mail');
  const dias = parseInt(argv[0]), tope = parseInt(argv[1]), soloNoLeidos = argv[2] === '1';
  const desde = new Date(Date.now() - dias * 86400000);
  let filtro = {dateReceived: {_greaterThan: desde}};
  if (soloNoLeidos) filtro = {_and: [filtro, {readStatus: false}]};
  const m = Mail.inbox.messages.whose(filtro);
  const ids = m.id(), asuntos = m.subject(), de = m.sender(), fechas = m.dateReceived(), leidos = m.readStatus();
  const out = [];
  for (let i = 0; i < ids.length; i++)
    out.push({id: ids[i], asunto: asuntos[i], de: de[i], fecha: fechas[i].toISOString(), leido: leidos[i]});
  out.sort((a, b) => a.fecha < b.fecha ? 1 : -1);
  const vista = out.slice(0, tope);
  for (const c of vista) {                  // which account each one came to (the bulk getter cannot say)
    try { c.cuenta = m.whose({id: c.id})()[0].mailbox().account().name(); } catch (e) { c.cuenta = ''; }
  }
  return JSON.stringify({total: out.length, correos: vista});
}"""

_LEER = r"""
function run(argv) {
  const Mail = Application('Mail');
  const m = Mail.inbox.messages.whose({id: parseInt(argv[0])})();
  if (!m.length) return JSON.stringify(null);
  const x = m[0];
  let cuenta = '';
  try { cuenta = x.mailbox().account().name(); } catch (e) {}
  return JSON.stringify({id: x.id(), asunto: x.subject(), de: x.sender(), para: x.toRecipients.address(),
                         fecha: x.dateReceived().toISOString(), cuenta: cuenta, texto: x.content()});
}"""

_BORRADOR = r"""
function run(argv) {
  const Mail = Application('Mail');
  const para = JSON.parse(argv[0]), asunto = argv[1], texto = argv[2];
  const m = Mail.OutgoingMessage({subject: asunto, content: texto, visible: true});
  Mail.outgoingMessages.push(m);
  for (const a of para) m.toRecipients.push(Mail.Recipient({address: a}));
  Mail.activate();
  return JSON.stringify({abierto: true});
}"""

_RESPONDER = r"""
function run(argv) {
  // Mail ignores text set on its own reply windows (tested on macOS 27), so the reply is a NEW message:
  // "Re:" subject, addressed to the sender (and the others with a_todos), the original quoted below.
  const Mail = Application('Mail');
  const m = Mail.inbox.messages.whose({id: parseInt(argv[0])})();
  if (!m.length) return JSON.stringify(null);
  const x = m[0];
  const asunto = /^re:/i.test(x.subject()) ? x.subject() : 'Re: ' + x.subject();
  const de = Mail.extractAddressFrom(x.sender());
  const destinos = [de];
  if (argv[2] === '1') for (const a of x.toRecipients.address().concat(x.ccRecipients.address()))
    if (destinos.indexOf(a) < 0) destinos.push(a);
  const original = x.content().slice(0, 4000).split('\n').map(l => '> ' + l).join('\n');
  const fecha = x.dateReceived().toLocaleString();
  const r = Mail.OutgoingMessage({subject: asunto, visible: true,
    content: argv[1] + '\n\n' + 'El ' + fecha + ', ' + x.sender() + ' escribió:\n' + original});
  Mail.outgoingMessages.push(r);
  for (const a of destinos) r.toRecipients.push(Mail.Recipient({address: a}));
  Mail.activate();
  return JSON.stringify({abierto: true, asunto: asunto, para: destinos});
}"""


def _remitente(s):
    s = limpiar(s, 160)
    m = _CORREO.search(s)
    nombre = limpiar(s.split("<")[0].strip(' "'), 80) if "<" in s else ""
    return {"nombre": nombre, "correo": m.group(0).lower() if m else s}


def correos(dias=1, solo_no_leidos=False, tope=30):
    marcar_lectura_privada()
    try:
        dias, tope = int(dias), int(tope)
    except (TypeError, ValueError):
        raise HerramientaError("días y tope deben ser números") from None
    if not 1 <= dias <= 31 or not 1 <= tope <= MAX_CORREOS:
        raise HerramientaError(f"de 1 a 31 días y de 1 a {MAX_CORREOS} correos")
    r = osa_json(_LISTAR, dias, tope, "1" if solo_no_leidos else "0", timeout=90)
    if not isinstance(r, dict):
        raise HerramientaError("Mail no devolvió la bandeja")
    lista = [{"id": int(c["id"]), "asunto": limpiar(c.get("asunto"), 200), "de": _remitente(c.get("de")),
              "fecha": limpiar(c.get("fecha"), 25)[:16].replace("T", " "), "leido": bool(c.get("leido")),
              "cuenta": limpiar(c.get("cuenta"), 80)}
             for c in r.get("correos", []) if isinstance(c, dict) and str(c.get("id", "")).isdigit()]
    return lista, int(r.get("total", len(lista)))


def leer_correo(ident):
    marcar_lectura_privada()
    if not str(ident).isdigit():
        raise HerramientaError("el id del correo es un número; lista los correos para verlo")
    c = osa_json(_LEER, int(ident), timeout=60)
    if not isinstance(c, dict):
        raise HerramientaError("no encuentro ese correo en la bandeja de entrada")
    para = c.get("para") if isinstance(c.get("para"), list) else []
    return {"id": int(c["id"]), "asunto": limpiar(c.get("asunto"), 200), "de": _remitente(c.get("de")),
            "para": [limpiar(x, 120) for x in para][:20], "cuenta": limpiar(c.get("cuenta"), 80),
            "fecha": limpiar(c.get("fecha"), 25)[:16].replace("T", " "),
            "texto": limpiar(c.get("texto"), MAX_CUERPO, lineas=True)}


def _destinatarios(para):
    lista = para if isinstance(para, list) else re.split(r"[,;\s]+", str(para or ""))
    limpias = [x.strip().lower() for x in lista if str(x).strip()]
    if not limpias or len(limpias) > 10 or not all(_CORREO.fullmatch(x) for x in limpias):
        raise HerramientaError("los destinatarios deben ser de 1 a 10 correos válidos")
    return limpias


def borrador(para, asunto, texto):
    """Open a NEW message in Mail, filled in, for the owner to review and send. Never sends."""
    import json
    destinos = _destinatarios(para)
    asunto, texto = limpiar(asunto, 200), limpiar(texto, MAX_CUERPO, lineas=True)
    if not texto:
        raise HerramientaError("el borrador necesita texto")
    osa_json(_BORRADOR, json.dumps(destinos), asunto, texto, timeout=30)
    return {"borrador": "abierto en Mail, sin enviar", "para": destinos, "asunto": asunto}


def responder(ident, texto, a_todos=False):
    """Open a reply to a message, with the text on top, for the owner to review and send. Never sends."""
    if not str(ident).isdigit():
        raise HerramientaError("el id del correo es un número; lista los correos para verlo")
    texto = limpiar(texto, MAX_CUERPO, lineas=True)
    if not texto:
        raise HerramientaError("la respuesta necesita texto")
    r = osa_json(_RESPONDER, int(ident), texto, "1" if a_todos else "0", timeout=30)
    if not isinstance(r, dict):
        raise HerramientaError("no encuentro ese correo en la bandeja de entrada")
    para = r.get("para") if isinstance(r.get("para"), list) else []
    return {"borrador": "respuesta abierta en Mail, sin enviar", "asunto": limpiar(r.get("asunto"), 200),
            "para": [limpiar(x, 120) for x in para][:20]}
