"""Tests for Niklaus's hands. Run with the app venv:
    ~/Apps/Niklaus/venv/app/bin/python -I ~/Apps/Niklaus/app/manos/pruebas.py
They work in a scratch folder data/manos-pruebas/<random> and use the REAL macOS Trash (then undo restores from it).
The scratch folder is sent to the Trash at the end (recoverable), never deleted for good.
"""
import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nucleo  # noqa: E402


class ManosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raiz = nucleo.NIKLAUS / "data" / "manos-pruebas" / uuid.uuid4().hex[:8]
        cls.raiz.mkdir(parents=True)
        # Own journal and backups: tests never mix with (or undo) the owner's real actions.
        nucleo.DATA = cls.raiz.with_name(cls.raiz.name + "-diario")      # beside, never inside, the work folder
        nucleo.DIARIO = nucleo.DATA / "diario.jsonl"
        nucleo.RESPALDOS = nucleo.DATA / "respaldos"

    @classmethod
    def tearDownClass(cls):
        for d in (cls.raiz, nucleo.DATA):
            try:
                nucleo._papelera(d)
            except Exception:
                pass

    def nuevo(self, nombre, texto="hola"):
        p = self.raiz / nombre
        p.write_text(texto, encoding="utf-8")
        return p

    def test_mover_y_deshacer(self):
        a = self.nuevo("a.txt")
        dest = self.raiz / "dest"
        dest.mkdir()
        nucleo.mover(str(a), str(dest))
        self.assertTrue((dest / "a.txt").exists())
        nucleo.deshacer()
        self.assertTrue(a.exists())
        self.assertFalse((dest / "a.txt").exists())

    def test_nunca_sobrescribe(self):
        self.nuevo("b.txt", "uno")
        dest = self.raiz / "dest2"
        dest.mkdir()
        (dest / "b.txt").write_text("dos", encoding="utf-8")
        nucleo.mover(str(self.raiz / "b.txt"), str(dest))
        self.assertEqual((dest / "b.txt").read_text(encoding="utf-8"), "dos")
        self.assertEqual((dest / "b (2).txt").read_text(encoding="utf-8"), "uno")

    def test_papelera_real_y_deshacer(self):
        c = self.nuevo("c.txt", "papelera")
        _, msg = nucleo.a_papelera([str(c)])
        self.assertFalse(c.exists())
        en_papelera = [e for e in nucleo._leer_diario()[0] if e.get("op") == "papelera" and e["de"] == str(c)][-1]["a"]
        self.assertIn("/.Trash/", en_papelera)
        self.assertTrue(os.path.exists(en_papelera))
        nucleo.deshacer()
        self.assertEqual(c.read_text(encoding="utf-8"), "papelera")

    def test_escribir_con_respaldo_y_deshacer(self):
        d = self.nuevo("d.txt", "original")
        nucleo.escribir(str(d), "nuevo")
        self.assertEqual(d.read_text(encoding="utf-8"), "nuevo")
        nucleo.deshacer()
        self.assertEqual(d.read_text(encoding="utf-8"), "original")

    def test_organizar_por_tipo_y_deshacer_todo(self):
        caja = self.raiz / "caja"
        caja.mkdir()
        for n in ("foto.jpg", "clip.mp4", "nota.pdf", "raro.xyz"):
            (caja / n).write_text("x", encoding="utf-8")
        nucleo.organizar(str(caja))
        self.assertTrue((caja / "Imágenes" / "foto.jpg").exists())
        self.assertTrue((caja / "Videos" / "clip.mp4").exists())
        self.assertTrue((caja / "Otros" / "raro.xyz").exists())
        nucleo.deshacer()
        self.assertEqual(sorted(p.name for p in caja.iterdir()), ["clip.mp4", "foto.jpg", "nota.pdf", "raro.xyz"])

    def test_limites(self):
        casa = Path(nucleo.HOME)
        for ruta in (str(casa / ".ssh" / "id_ed25519"), str(casa / ".zshrc"), "/System/Library", "/usr/bin/ls",
                     str(casa / "Library" / "Keychains"), str(nucleo.NIKLAUS / ".claude" / "settings.json"),
                     str(nucleo.NIKLAUS / "CLAUDE.md"), str(casa / "Apps" / "darkroom" / "scripts"), str(casa), "/",
                     "relativa/no", "/private/tmp/x"):
            with self.assertRaises(nucleo.ManosError, msg=ruta):
                nucleo.validar(ruta, debe_existir=False)

    def test_enlace_no_lleva_a_secretos(self):
        enlace = self.raiz / "trampa"
        os.symlink(str(nucleo.HOME / ".ssh"), enlace)
        with self.assertRaises(nucleo.ManosError):
            nucleo.validar(str(enlace / "id_ed25519"), debe_existir=False)
        with self.assertRaises(nucleo.ManosError):
            nucleo.validar(str(enlace), debe_existir=False)

    def test_limites_mayusculas_contenedores_y_cargables(self):
        casa, nk = str(nucleo.HOME), str(nucleo.NIKLAUS)
        for ruta in (casa.upper() + "/.SSH/config", "~/.Ssh", "~/library/Keychains", "/system/Library", "/USR/bin",
                     nk.upper() + "/.CLAUDE/settings.json", nk + "/App/manos/nucleo.py", "~/Apps/DARKROOM",
                     "~/Apps", nk, nk + "/data", "/Users", "~/.ſſh/config", "~/.zlogin", "~/.gitconfig",
                     "~/.local/bin/x", nk + "/.mcp.json", "~/CLAUDE.md", "~/x/claude.LOCAL.md", "~/p/.git/hooks/x",
                     "~/p/.claude/settings.json", "/Applications/Safari.app", "~/Applications/A.app/Contents/x"):
            with self.assertRaises(nucleo.ManosError, msg=ruta):
                nucleo.validar(ruta, debe_existir=False)
        for ruta in ("~/Desktop/x.txt", "~/Documents", "~/.Trash/x", str(self.raiz / "y")):
            nucleo.validar(ruta, debe_existir=False)

    def test_organizar_respeta_protegidos_y_no_sigue_enlaces(self):
        caja = self.raiz / "caja2"
        caja.mkdir()
        (caja / "CLAUDE.md").write_text("reglas", encoding="utf-8")
        (caja / "malo.plist").write_text("x", encoding="utf-8")
        (caja / "foto.png").write_text("x", encoding="utf-8")
        destino_secreto = self.raiz / "secreto"
        destino_secreto.mkdir()
        os.symlink(str(destino_secreto), caja / "Otros")
        _, msg = nucleo.organizar(str(caja))
        self.assertTrue((caja / "CLAUDE.md").exists())
        self.assertTrue((caja / "malo.plist").exists())
        self.assertEqual(list(destino_secreto.iterdir()), [])
        self.assertTrue((caja / "Imágenes" / "foto.png").exists())
        self.assertIn("No uso Otros", msg)

    def test_deshacer_escribir_no_pierde_la_edicion_posterior(self):
        d = self.nuevo("doc.txt", "v1")
        nucleo.escribir(str(d), "v2")
        d.write_text("v3-del-dueno", encoding="utf-8")
        _, msg, ok = nucleo.deshacer()
        self.assertTrue(ok, msg)
        self.assertEqual(d.read_text(encoding="utf-8"), "v1")
        nombre = re.search(r"Papelera como «([^»]+)»", msg).group(1)
        self.assertEqual((nucleo.HOME / ".Trash" / nombre).read_text(encoding="utf-8"), "v3-del-dueno")

    def test_escribir_y_deshacer_no_siguen_enlaces(self):
        victima = self.nuevo("victima.txt", "INTACTA")
        enlace = self.raiz / "enlace.txt"
        os.symlink(str(victima), enlace)
        with self.assertRaises(nucleo.ManosError):
            nucleo.escribir(str(enlace), "pisado")
        d = self.nuevo("doc2.txt", "orig")
        nucleo.escribir(str(d), "nuevo")
        d.unlink()
        os.symlink(str(victima), d)
        _, msg, ok = nucleo.deshacer()
        self.assertFalse(ok, msg)
        self.assertEqual(victima.read_text(encoding="utf-8"), "INTACTA")

    def test_escribir_quita_permiso_de_ejecucion(self):
        x = self.nuevo("script.sh", "echo hola")
        os.chmod(x, 0o755)
        nucleo.escribir(str(x), "echo otro")
        self.assertEqual(os.stat(x).st_mode & 0o111, 0)

    def test_deshacer_parcial_se_dice_y_deshacer_por_lote(self):
        a, b = self.nuevo("pa.txt"), self.nuevo("pb.txt")
        dest = self.raiz / "dest3"
        dest.mkdir()
        lote_a, _ = nucleo.mover(str(a), str(dest))
        lote_b, _ = nucleo.mover(str(b), str(dest))
        (dest / "pa.txt").unlink()                     # someone else removed it
        _, msg, ok = nucleo.deshacer(lote=lote_a)      # the older batch, not the last one
        self.assertFalse(ok)
        self.assertIn("solo una parte", msg)
        self.assertTrue((dest / "pb.txt").exists())    # the newer batch was not touched
        _, msg, ok = nucleo.deshacer()                 # now the last pending one is lote_b
        self.assertTrue(ok, msg)
        self.assertTrue(b.exists())
        self.assertIn("No hay nada", nucleo.deshacer()[1])

    def test_lote_interrumpido_queda_en_el_diario(self):
        caja = self.raiz / "caja3"
        caja.mkdir()
        for n in ("a.txt", "b.txt", "c.txt", "d.txt"):
            (caja / n).write_text("x", encoding="utf-8")
        original, llamadas = nucleo._mover, []

        def muere_al_tercero(src, dst):
            llamadas.append(src)
            if len(llamadas) == 3:
                raise KeyboardInterrupt          # like a kill in the middle of the batch
            original(src, dst)
        nucleo._mover = muere_al_tercero
        try:
            with self.assertRaises(KeyboardInterrupt):
                nucleo.organizar(str(caja))
        finally:
            nucleo._mover = original
        movidos = [e for e in nucleo._leer_diario()[0] if e.get("op") == "mover" and "caja3" in e.get("de", "")]
        self.assertEqual(len(movidos), 3)              # the 3rd is journaled before it was (not) done
        _, msg, ok = nucleo.deshacer()
        self.assertTrue(ok, msg)
        self.assertEqual(sorted(p.name for p in caja.iterdir() if p.is_file()), ["a.txt", "b.txt", "c.txt", "d.txt"])

    def test_muerte_justo_despues_de_actuar_no_pierde_la_accion(self):
        caja = self.raiz / "caja4"
        caja.mkdir()
        for n in ("a.pdf", "b.pdf", "c.pdf"):
            (caja / n).write_text("x", encoding="utf-8")
        original, llamadas = nucleo._mover, []

        def muere_tras_el_segundo(src, dst):
            original(src, dst)
            llamadas.append(src)
            if len(llamadas) == 2:
                raise KeyboardInterrupt          # done on disk, killed before anything else ran
        nucleo._mover = muere_tras_el_segundo
        try:
            with self.assertRaises(KeyboardInterrupt):
                nucleo.organizar(str(caja))
        finally:
            nucleo._mover = original
        _, msg, ok = nucleo.deshacer()
        self.assertTrue(ok, msg)
        self.assertEqual(sorted(p.name for p in caja.iterdir()), ["a.pdf", "b.pdf", "c.pdf"])

    def test_papelera_muerta_antes_de_anotar_el_nombre_se_dice(self):
        f = self.nuevo("t.txt", "x")
        original = nucleo._papelera

        def muere(p):
            original(p)
            raise KeyboardInterrupt
        nucleo._papelera = muere
        try:
            with self.assertRaises(KeyboardInterrupt):
                nucleo.a_papelera([str(f)])
        finally:
            nucleo._papelera = original
        _, msg, ok = nucleo.deshacer()
        self.assertFalse(ok)                            # never a silent "Deshice"
        self.assertIn("está en la Papelera", msg)

    def test_restaura_el_modo_original_con_umask_estricto(self):
        viejo = os.umask(0o077)
        try:
            d = self.nuevo("modo.txt", "v1")
            os.chmod(d, 0o640)
            nucleo.escribir(str(d), "v2")
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o640)
            nucleo.deshacer()
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o640)
        finally:
            os.umask(viejo)

    def test_linea_cortada_no_se_come_la_siguiente(self):
        nucleo._preparar()
        with open(nucleo.DIARIO, "ab") as fh:
            fh.write(b'{"op": "mover", "cortada')       # a crash in the middle of a line
        f = self.nuevo("tras-corte.txt")
        dest = self.raiz / "dest5"
        dest.mkdir()
        nucleo.mover(str(f), str(dest))
        _, msg, ok = nucleo.deshacer()
        self.assertTrue(ok, msg)                        # a damaged line is reported, it does not make it partial
        self.assertIn("dañadas", msg)
        self.assertTrue(f.exists())

    def test_deshacer_sin_respaldo_no_mueve_el_archivo(self):
        d = self.nuevo("doc3.txt", "v1")
        nucleo.escribir(str(d), "v2")
        respaldo = [e for e in nucleo._leer_diario()[0] if e.get("op") == "escribir" and e["a"] == str(d)][-1]["respaldo"]
        nucleo._papelera(Path(respaldo))
        _, msg, ok = nucleo.deshacer()
        self.assertFalse(ok)
        self.assertIn("no toqué el archivo", msg)
        self.assertEqual(d.read_text(encoding="utf-8"), "v2")

    def test_deshacer_con_diario_forjado_no_toca_protegidos(self):
        # a forged entry claims a protected destination; undo must refuse to move/trash it
        protegido = self.raiz / "sacado.txt"
        protegido.write_text("fuera", encoding="utf-8")
        falso = nucleo.NIKLAUS / "herramientas.toml"   # a real protected control
        import json as _j, uuid as _u
        nucleo._preparar()
        lid = _u.uuid4().hex[:12]
        with open(nucleo.DIARIO, "a", encoding="utf-8") as fh:
            fh.write(_j.dumps({"op": "mover", "de": str(self.raiz / "x"), "a": str(falso),
                               "lote": lid, "e": _u.uuid4().hex[:12], "desc": "forjado",
                               "t": "2026-01-01 00:00:00"}) + "\n")
        antes = falso.read_text(encoding="utf-8")
        _, msg, ok = nucleo.deshacer(lote=lid)
        self.assertFalse(ok)
        self.assertEqual(falso.read_text(encoding="utf-8"), antes)   # untouched
        self.assertTrue(falso.exists())

    def test_servidor_mcp_lista_herramientas(self):
        """The MCP server starts over stdio and exposes the 9 tools."""
        codigo = (
            "import asyncio, sys\n"
            "from mcp import ClientSession, StdioServerParameters\n"
            "from mcp.client.stdio import stdio_client\n"
            "async def main():\n"
            "  p = StdioServerParameters(command=sys.executable, args=['-I', '-B', sys.argv[1]])\n"
            "  async with stdio_client(p) as (r, w):\n"
            "    async with ClientSession(r, w) as s:\n"
            "      await s.initialize()\n"
            "      t = await s.list_tools()\n"
            "      print(sorted(x.name for x in t.tools))\n"
            "asyncio.run(main())\n")
        r = subprocess.run([sys.executable, "-I", "-c", codigo, str(Path(__file__).with_name("servidor.py"))],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), str(sorted(["a_papelera", "copiar", "crear_carpeta", "deshacer", "escribir",
                                                         "historial", "mover", "organizar", "renombrar"])), r.stderr[-400:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
