"""Agenda: the owner's calendar and reminders through EventKit (the macOS store that Calendar and Reminders use).

Local: no account tokens, no network of its own; macOS asks the owner once for access (attributed to the app that
launched this process, normally Terminal). Rules enforced here, not by the model:
- creating never invites anyone (EventKit cannot add attendees);
- an event with attendees or a repeating one is never moved or deleted here (other people would be notified, or a
  whole series changed): the owner does that in Calendar;
- deleting needs the exact title as well as the id, so the confirmation card the owner sees names the real event;
- every text that comes from an event or reminder is untrusted data (an invitation can be written by anyone).
"""
import datetime as dt
import re
import threading

import EventKit as EK
import Foundation as F

MAX_DIAS, MAX_EVENTOS, MAX_TEXTO = 366, 200, 300
# control, soft-hyphen, zero-width and bidi characters (written as code points: no invisible text in this file)
_OCULTOS = ((0x00, 0x08), (0x0b, 0x1f), (0x7f, 0x9f), (0xad, 0xad), (0x200b, 0x200f), (0x2028, 0x202e), (0x2060, 0x2069), (0xfeff, 0xfeff))
_CONTROL = re.compile("[" + "".join(f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in _OCULTOS) + "]")
_almacen = None
_candado = threading.Lock()


class AgendaError(Exception):
    pass


def _texto(valor, tope=MAX_TEXTO):
    s = _CONTROL.sub(" ", str(valor or "")).strip()
    return s if len(s) <= tope else s[:tope] + "…"


def _store():
    global _almacen
    with _candado:
        if _almacen is None:
            _almacen = EK.EKEventStore.alloc().init()
        return _almacen


def _acceso(tipo):
    """Full access to events or reminders, asking macOS once if the owner has not decided yet."""
    que = "el calendario" if tipo == EK.EKEntityTypeEvent else "los recordatorios"
    estado = EK.EKEventStore.authorizationStatusForEntityType_(tipo)
    if estado == 0:                                          # not determined: macOS shows its dialog
        listo = threading.Event()
        pedir = (_store().requestFullAccessToEventsWithCompletion_ if tipo == EK.EKEntityTypeEvent
                 else _store().requestFullAccessToRemindersWithCompletion_)
        pedir(lambda ok, err: listo.set())
        listo.wait(60)
        estado = EK.EKEventStore.authorizationStatusForEntityType_(tipo)
    if estado != 3:                                          # 3 = full access
        raise AgendaError(f"no tengo permiso para {que}. Se da en Ajustes del Sistema → Privacidad y seguridad → "
                          f"{'Calendarios' if tipo == EK.EKEntityTypeEvent else 'Recordatorios'} → Terminal (acceso total).")
    return _store()


def _fecha(texto, nombre="la fecha"):
    """'2026-11-17' or '2026-11-17T16:30' in the Mac's local time."""
    try:
        d = dt.datetime.fromisoformat(str(texto).strip())
    except ValueError:
        raise AgendaError(f"{nombre} debe ir como AAAA-MM-DD o AAAA-MM-DDTHH:MM (recibí {_texto(texto, 40)!r})") from None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    if not 2000 <= d.year <= 2100:
        raise AgendaError(f"{nombre} está fuera de rango")
    return d


def _ns(d):
    return F.NSDate.dateWithTimeIntervalSince1970_(d.timestamp())


def _py(nsdate):
    return dt.datetime.fromtimestamp(nsdate.timeIntervalSince1970()) if nsdate is not None else None


def _iso(d, todo_el_dia=False):
    return "" if d is None else (d.strftime("%Y-%m-%d") if todo_el_dia else d.strftime("%Y-%m-%dT%H:%M"))


def _calendario(store, tipo, nombre):
    """The named calendar/list (case-insensitive), or the default one. It must accept changes."""
    if nombre:
        iguales = [c for c in store.calendarsForEntityType_(tipo) if str(c.title()).casefold() == str(nombre).casefold()]
        if not iguales:
            raise AgendaError(f"no existe {'ese calendario' if tipo == EK.EKEntityTypeEvent else 'esa lista'}")
        cal = iguales[0]
    else:
        cal = (store.defaultCalendarForNewEvents() if tipo == EK.EKEntityTypeEvent
               else store.defaultCalendarForNewReminders())
        if cal is None:
            raise AgendaError("no hay un calendario o lista por defecto; dime en cuál")
    if not cal.allowsContentModifications():
        raise AgendaError("ese calendario o lista es de solo lectura")      # never echo a name a third party chose
    return cal


def calendarios():
    store = _acceso(EK.EKEntityTypeEvent)
    return [{"nombre": _texto(c.title(), 80), "editable": bool(c.allowsContentModifications())}
            for c in store.calendarsForEntityType_(EK.EKEntityTypeEvent)]


def _evento_dict(e):
    dia = bool(e.isAllDay())
    return {"id": str(e.eventIdentifier()), "titulo": _texto(e.title(), 160),
            "inicio": _iso(_py(e.startDate()), dia), "fin": _iso(_py(e.endDate()), dia), "todo_el_dia": dia,
            "calendario": _texto(e.calendar().title(), 80) if e.calendar() is not None else "",
            "lugar": _texto(e.location(), 160), "notas": _texto(e.notes()),
            "con_invitados": bool(e.hasAttendees()), "se_repite": bool(e.hasRecurrenceRules())}


def eventos(desde, hasta=None, calendario=None):
    """Events between two dates (hasta defaults to the end of `desde`'s day), oldest first."""
    store = _acceso(EK.EKEntityTypeEvent)
    a = _fecha(desde, "desde")
    b = _fecha(hasta, "hasta") if hasta else a.replace(hour=0, minute=0) + dt.timedelta(days=1)
    if hasta and len(str(hasta).strip()) <= 10:              # a bare date as the end means the whole day
        b += dt.timedelta(days=1)
    if b <= a or (b - a).days > MAX_DIAS:
        raise AgendaError(f"el rango debe ser de entre un momento y {MAX_DIAS} días")
    cals = None
    if calendario:
        cals = [c for c in store.calendarsForEntityType_(EK.EKEntityTypeEvent)
                if str(c.title()).casefold() == str(calendario).casefold()]
        if not cals:
            raise AgendaError("no existe ese calendario")
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(_ns(a), _ns(b), cals)
    lista = sorted(store.eventsMatchingPredicate_(pred) or [], key=lambda e: e.startDate().timeIntervalSince1970())
    return [_evento_dict(e) for e in lista[:MAX_EVENTOS]], len(lista)


def crear_evento(titulo, inicio, fin=None, calendario=None, lugar=None, notas=None, todo_el_dia=False):
    store = _acceso(EK.EKEntityTypeEvent)
    titulo = _texto(titulo, 160)
    if not titulo:
        raise AgendaError("el evento necesita un título")
    a = _fecha(inicio, "inicio")
    b = _fecha(fin, "fin") if fin else a + (dt.timedelta(days=0) if todo_el_dia else dt.timedelta(hours=1))
    if b < a or (b - a).days > 31:
        raise AgendaError("el fin debe ser después del inicio (y a no más de un mes)")
    e = EK.EKEvent.eventWithEventStore_(store)
    e.setTitle_(titulo)
    e.setStartDate_(_ns(a))
    e.setEndDate_(_ns(b))
    e.setAllDay_(bool(todo_el_dia))
    e.setCalendar_(_calendario(store, EK.EKEntityTypeEvent, calendario))
    if lugar:
        e.setLocation_(_texto(lugar, 160))
    if notas:
        e.setNotes_(_texto(notas, 2000))
    ok, err = store.saveEvent_span_commit_error_(e, EK.EKSpanThisEvent, True, None)
    if not ok:
        raise AgendaError(f"macOS no guardó el evento: {_texto(err.localizedDescription() if err else '', 200)}")
    return _evento_dict(e)


def _buscar_evento(store, ident):
    e = store.eventWithIdentifier_(str(ident)) if ident else None
    if e is None:
        raise AgendaError("no encuentro ese evento; vuelve a listar la agenda para tener su id")
    if e.hasAttendees():
        raise AgendaError("ese evento tiene invitados: cambiarlo les avisaría. Hazlo tú en Calendario.")
    if e.hasRecurrenceRules():
        raise AgendaError("ese evento se repite: cambiarlo afecta la serie. Hazlo tú en Calendario.")
    if e.calendar() is None or not e.calendar().allowsContentModifications():
        raise AgendaError("ese evento está en un calendario de solo lectura")
    return e


def mover_evento(ident, inicio, fin=None):
    """Move an own, non-repeating event without attendees; keeps its duration unless `fin` is given."""
    store = _acceso(EK.EKEntityTypeEvent)
    e = _buscar_evento(store, ident)
    antes = _evento_dict(e)
    a = _fecha(inicio, "inicio")
    duracion = _py(e.endDate()) - _py(e.startDate())
    b = _fecha(fin, "fin") if fin else a + duracion
    if b < a or (b - a).days > 31:
        raise AgendaError("el fin debe ser después del inicio (y a no más de un mes)")
    e.setStartDate_(_ns(a))
    e.setEndDate_(_ns(b))
    ok, err = store.saveEvent_span_commit_error_(e, EK.EKSpanThisEvent, True, None)
    if not ok:
        raise AgendaError(f"macOS no guardó el cambio: {_texto(err.localizedDescription() if err else '', 200)}")
    return antes, _evento_dict(e)


def borrar_evento(ident, titulo, inicio):
    """Delete an own, non-repeating event without attendees. `titulo` and `inicio` must be its exact title and
    start: the owner approves a card that shows these arguments, so the card names the event that gets deleted."""
    store = _acceso(EK.EKEntityTypeEvent)
    e = _buscar_evento(store, ident)
    datos = _evento_dict(e)
    if str(e.title() or "").strip() != str(titulo or "").strip():
        raise AgendaError("el título no coincide con el del evento; no borro nada")
    if _CONTROL.search(str(e.title() or "")):
        raise AgendaError("el título de ese evento trae caracteres ocultos; bórralo tú en Calendario")
    if str(inicio or "").strip() != datos["inicio"]:
        raise AgendaError("el inicio no coincide con el del evento; no borro nada")
    ok, err = store.removeEvent_span_commit_error_(e, EK.EKSpanThisEvent, True, None)
    if not ok:
        raise AgendaError(f"macOS no borró el evento: {_texto(err.localizedDescription() if err else '', 200)}")
    return datos


# ---- reminders ----
def _recordatorio_dict(r):
    c = r.dueDateComponents()
    vence = ""
    if c is not None:
        fecha = F.NSCalendar.currentCalendar().dateFromComponents_(c)
        con_hora = c.hour() != F.NSDateComponentUndefined
        vence = _iso(_py(fecha), not con_hora)
    return {"id": str(r.calendarItemIdentifier()), "titulo": _texto(r.title(), 160), "vence": vence,
            "lista": _texto(r.calendar().title(), 80) if r.calendar() is not None else "",
            "hecho": bool(r.isCompleted()), "notas": _texto(r.notes())}


def recordatorios(incluir_hechos=False):
    store = _acceso(EK.EKEntityTypeReminder)
    pred = (store.predicateForRemindersInCalendars_(None) if incluir_hechos
            else store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, None))
    listo, caja = threading.Event(), {}

    def al_terminar(lista):
        caja["r"] = list(lista or [])
        listo.set()
    store.fetchRemindersMatchingPredicate_completion_(pred, al_terminar)
    if not listo.wait(30):
        raise AgendaError("macOS no devolvió los recordatorios a tiempo")
    datos = [_recordatorio_dict(r) for r in caja["r"]]
    datos.sort(key=lambda x: (x["hecho"], x["vence"] or "9999"))
    return datos[:MAX_EVENTOS], len(datos)


def crear_recordatorio(titulo, cuando=None, lista=None, notas=None):
    store = _acceso(EK.EKEntityTypeReminder)
    titulo = _texto(titulo, 160)
    if not titulo:
        raise AgendaError("el recordatorio necesita un título")
    r = EK.EKReminder.reminderWithEventStore_(store)
    r.setTitle_(titulo)
    r.setCalendar_(_calendario(store, EK.EKEntityTypeReminder, lista))
    if notas:
        r.setNotes_(_texto(notas, 2000))
    if cuando:
        d = _fecha(cuando, "cuando")
        con_hora = len(str(cuando).strip()) > 10
        c = F.NSDateComponents.alloc().init()
        c.setYear_(d.year)
        c.setMonth_(d.month)
        c.setDay_(d.day)
        if con_hora:
            c.setHour_(d.hour)
            c.setMinute_(d.minute)
            r.addAlarm_(EK.EKAlarm.alarmWithAbsoluteDate_(_ns(d)))     # it rings at that time
        r.setDueDateComponents_(c)
    ok, err = store.saveReminder_commit_error_(r, True, None)
    if not ok:
        raise AgendaError(f"macOS no guardó el recordatorio: {_texto(err.localizedDescription() if err else '', 200)}")
    return _recordatorio_dict(r)


def completar_recordatorio(ident, hecho=True):
    store = _acceso(EK.EKEntityTypeReminder)
    r = store.calendarItemWithIdentifier_(str(ident)) if ident else None
    if r is None or not isinstance(r, EK.EKReminder):
        raise AgendaError("no encuentro ese recordatorio; vuelve a listarlos para tener su id")
    r.setCompleted_(bool(hecho))
    ok, err = store.saveReminder_commit_error_(r, True, None)
    if not ok:
        raise AgendaError(f"macOS no guardó el cambio: {_texto(err.localizedDescription() if err else '', 200)}")
    return _recordatorio_dict(r)


def borrar_recordatorio(ident, titulo):
    """Same rule as borrar_evento: the exact title must come with the id."""
    store = _acceso(EK.EKEntityTypeReminder)
    r = store.calendarItemWithIdentifier_(str(ident)) if ident else None
    if r is None or not isinstance(r, EK.EKReminder):
        raise AgendaError("no encuentro ese recordatorio; vuelve a listarlos para tener su id")
    if str(r.title() or "").strip() != str(titulo or "").strip():
        raise AgendaError("el título no coincide con el del recordatorio; no borro nada")
    if _CONTROL.search(str(r.title() or "")):
        raise AgendaError("el título de ese recordatorio trae caracteres ocultos; bórralo tú en Recordatorios")
    datos = _recordatorio_dict(r)
    ok, err = store.removeReminder_commit_error_(r, True, None)
    if not ok:
        raise AgendaError(f"macOS no borró el recordatorio: {_texto(err.localizedDescription() if err else '', 200)}")
    return datos
