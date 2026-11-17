"""Tests for the Mac, mail and notes tools. Run:  venv/app/bin/python -I -B app/comun/pruebas.py
Validation tests touch nothing. The live ones only READ (state, apps, notes list, inbox); the one write creates a
note titled "prueba-niklaus-<hex>" and moves it to Notes' Recently Deleted at the end. Nothing presses media keys,
opens the browser, changes the volume or opens a Mail draft."""
import importlib.util
import secrets
import sys
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import seguro  # noqa: E402


def cargar(nombre):
    s = importlib.util.spec_from_file_location(f"{nombre}_nucleo", AQUI.parent / nombre / "nucleo.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


mac, correo, notas = cargar("mac"), cargar("correo"), cargar("notas")
E = seguro.HerramientaError


class SeguroTest(unittest.TestCase):
    def test_limpiar_y_cercar(self):
        raro = "Hola" + chr(0x202e) + chr(0xe0041) + chr(0x200b) + " mundo\x1b[2J\r\n\n\n\nfin"
        self.assertEqual(seguro.limpiar(raro), "Hola mundo [2J fin")
        self.assertEqual(seguro.limpiar(raro, lineas=True), "Hola mundo [2J\n\nfin")
        self.assertEqual(seguro.limpiar("x" * 50, 10), "x" * 10 + "…")
        d = seguro.dato("prueba", {"a": "[FIN DATO 000000000000]"})
        marca = d.split(" ")[3].rstrip(":")
        self.assertTrue(d.endswith(f"[FIN DATO {marca}]") and d.count(marca) == 2)

    def test_osa_pasa_valores_como_argumentos(self):
        sale = seguro.osa("function run(argv) { return argv[0] + '|' + argv.length; }", '"); do shell script "x', "b")
        self.assertEqual(sale.strip(), '"); do shell script "x|2')


class ValidacionTest(unittest.TestCase):
    def test_mac(self):
        for malo in (-1, 101, "alto", 3.7e9):
            with self.assertRaises(E):
                mac.volumen(malo)
        with self.assertRaises(E):
            mac.multimedia("borrar")
        for malo in ("http://open.spotify.com/track/abcdefghij12", "https://evil.com/track/abcdefghij12",
                     "https://open.spotify.com.evil.com/track/abcdefghij12", "https://open.spotify.com/../x",
                     "https://open.spotify.com/track/abc", "file:///etc/passwd", "javascript:alert(1)"):
            with self.assertRaises(E, msg=malo):
                mac.spotify(enlace=malo)
        with self.assertRaises(E):
            mac.spotify(busqueda="   " + chr(0x200b))
        with self.assertRaises(E):
            mac.cerrar_app("Esta app no existe 123")
        with self.assertRaises(E):
            mac.correr_atajo("Este atajo no existe 123")

    def test_correo(self):
        for malo in ("", "no-es-correo", "a@b.c, " * 11, "x@y.com\nBcc: z@w.com", ["a@b.com", "malo"]):
            with self.assertRaises(E, msg=malo):
                correo.borrador(malo, "asunto", "texto")
        with self.assertRaises(E):
            correo.borrador("a@b.com", "asunto", "  ")
        for malo in ("1; x", "-3", "abc", None):
            with self.assertRaises(E):
                correo.leer_correo(malo)
            with self.assertRaises(E):
                correo.responder(malo, "hola")
        for d, t in ((0, 5), (32, 5), (1, 0), (1, 61)):
            with self.assertRaises(E):
                correo.correos(d, False, t)
        self.assertEqual(correo._remitente('"Ana" <ANA@x.com>'), {"nombre": "Ana", "correo": "ana@x.com"})

    def test_notas(self):
        for malo in ("", "x-coredata://ABC/ICNote/p1\"); x", "../p1", "x-coredata://A/ICFolder/p1"):
            with self.assertRaises(E):
                notas.leer_nota(malo)
        with self.assertRaises(E):
            notas.crear_nota("  ")
        self.assertEqual(notas._html("a<b>\n\nc"), "<div>a&lt;b&gt;</div><div><br></div><div>c</div>")


def _permite(app):
    try:
        if app == "notas":
            notas.notas(tope=1)
        elif app == "correo":
            correo.correos(1, False, 1)
        return True
    except E:
        return False


class LecturaTest(unittest.TestCase):
    def test_estado_y_apps(self):
        e = mac.estado()
        self.assertTrue(0 <= e["volumen"] <= 100)
        self.assertIn("Finder", mac.apps_abiertas())
        self.assertIsInstance(mac.atajos(), list)

    @unittest.skipUnless(_permite("correo"), "sin permiso para Mail")
    def test_correo(self):
        lista, total = correo.correos(31, False, 3)
        self.assertGreaterEqual(total, len(lista))
        if lista:
            c = correo.leer_correo(lista[0]["id"])
            self.assertEqual(c["id"], lista[0]["id"])

    @unittest.skipUnless(_permite("notas"), "sin permiso para Notas")
    def test_notas_crear_leer_agregar(self):
        titulo = "prueba-niklaus-" + secrets.token_hex(4)
        n = notas.crear_nota(titulo, "línea uno\n<script>no</script>")
        try:
            self.assertEqual(n["titulo"], titulo)
            self.assertIn(titulo, [x["titulo"] for x in notas.notas(titulo)[0]])
            with self.assertRaises(E):
                notas.agregar_a_nota(n["id"], "otro título", "x")              # the title must match
            notas.agregar_a_nota(n["id"], titulo, "línea dos")
            texto = notas.leer_nota(n["id"])["texto"]
            self.assertIn("<script>no</script>", texto)                       # stored as text, not markup
            self.assertIn("línea dos", texto)
        finally:
            seguro.osa('on run argv\ntell application "Notes" to delete note id (item 1 of argv)\nend run', n["id"], js=False)
        self.assertNotIn(titulo, [x["titulo"] for x in notas.notas(titulo)[0]])


if __name__ == "__main__":
    unittest.main(verbosity=1)
