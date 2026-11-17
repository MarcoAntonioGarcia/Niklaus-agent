"""Tests for the agenda against the real macOS store. Run:  venv/app/bin/python -I -B app/agenda/pruebas.py
They create items titled "prueba-niklaus-<hex>" in 2031 and delete them; skipped where macOS access is not granted.
"""
import secrets
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import EventKit as EK  # noqa: E402
import nucleo  # noqa: E402


def _con(tipo):
    return EK.EKEventStore.authorizationStatusForEntityType_(tipo) == 3


class TextoTest(unittest.TestCase):
    def test_texto_sin_controles_y_acotado(self):
        self.assertEqual(nucleo._texto("a\x1b[2J" + chr(0x202e) + "b" + chr(0x200b) + "c\nd"), "a [2J b c\nd")
        self.assertEqual(len(nucleo._texto("x" * 999)), nucleo.MAX_TEXTO + 1)

    def test_fechas(self):
        self.assertEqual(nucleo._fecha("2031-01-15T16:30").hour, 16)
        for mala in ("mañana", "2031-13-40", "1999-01-01", "", None, "2031-01-15; rm"):
            with self.assertRaises(nucleo.AgendaError):
                nucleo._fecha(mala)


@unittest.skipUnless(_con(EK.EKEntityTypeEvent), "sin permiso de calendario")
class EventosTest(unittest.TestCase):
    def setUp(self):
        self.t = "prueba-niklaus-" + secrets.token_hex(4)

    def tearDown(self):
        for e in nucleo.eventos("2031-01-10", "2031-01-20")[0]:
            if e["titulo"].startswith("prueba-niklaus-"):
                nucleo.borrar_evento(e["id"], e["titulo"], e["inicio"])

    def test_crear_listar_mover_borrar(self):
        e = nucleo.crear_evento(self.t, "2031-01-15T16:30", notas="prueba")
        self.assertEqual((e["inicio"], e["fin"], e["con_invitados"]), ("2031-01-15T16:30", "2031-01-15T17:30", False))
        self.assertIn(self.t, [x["titulo"] for x in nucleo.eventos("2031-01-15")[0]])
        _, ahora = nucleo.mover_evento(e["id"], "2031-01-16T09:00")
        self.assertEqual((ahora["inicio"], ahora["fin"]), ("2031-01-16T09:00", "2031-01-16T10:00"))
        with self.assertRaises(nucleo.AgendaError):
            nucleo.borrar_evento(ahora["id"], "otro título", ahora["inicio"])     # the title must match
        with self.assertRaises(nucleo.AgendaError):
            nucleo.borrar_evento(ahora["id"], self.t, "2031-01-15T16:30")          # and so must the start
        self.assertIn(self.t, [x["titulo"] for x in nucleo.eventos("2031-01-16")[0]])
        nucleo.borrar_evento(ahora["id"], self.t, ahora["inicio"])
        self.assertNotIn(self.t, [x["titulo"] for x in nucleo.eventos("2031-01-16")[0]])

    def test_todo_el_dia_y_limites(self):
        e = nucleo.crear_evento(self.t, "2031-01-15", todo_el_dia=True)
        self.assertEqual((e["todo_el_dia"], e["inicio"]), (True, "2031-01-15"))
        solo_lectura = [c["nombre"] for c in nucleo.calendarios() if not c["editable"]]
        casos = [lambda: nucleo.eventos("2026-01-01", "2028-01-01"), lambda: nucleo.mover_evento("no-existe", "2031-01-16"),
                 lambda: nucleo.crear_evento("", "2031-01-15T10:00"), lambda: nucleo.crear_evento("x", "2031-01-15", "2031-01-14"),
                 lambda: nucleo.crear_evento("x", "2031-01-15T10:00", calendario="no existe este calendario")]
        if solo_lectura:
            casos.append(lambda: nucleo.crear_evento("x", "2031-01-15T10:00", calendario=solo_lectura[0]))
        for caso in casos:
            with self.assertRaises(nucleo.AgendaError):
                caso()


@unittest.skipUnless(_con(EK.EKEntityTypeReminder), "sin permiso de recordatorios")
class RecordatoriosTest(unittest.TestCase):
    def test_crear_completar_borrar(self):
        t = "prueba-niklaus-" + secrets.token_hex(4)
        r = nucleo.crear_recordatorio(t, "2031-01-15T09:00")
        try:
            self.assertEqual((r["vence"], r["hecho"]), ("2031-01-15T09:00", False))
            self.assertIn(t, [x["titulo"] for x in nucleo.recordatorios()[0]])
            self.assertTrue(nucleo.completar_recordatorio(r["id"])["hecho"])
            self.assertNotIn(t, [x["titulo"] for x in nucleo.recordatorios()[0]])
            with self.assertRaises(nucleo.AgendaError):
                nucleo.borrar_recordatorio(r["id"], "otro")
        finally:
            nucleo.borrar_recordatorio(r["id"], t)
        self.assertNotIn(t, [x["titulo"] for x in nucleo.recordatorios(True)[0]])


if __name__ == "__main__":
    unittest.main(verbosity=1)
