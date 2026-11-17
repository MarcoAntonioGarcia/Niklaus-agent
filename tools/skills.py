"""Skills catalog of Niklaus (skills.toml): list, search, check and apply where each skill is loaded.

    python3.11 -I tools/skills.py listar [--categoria X] [--ambito global|niklaus|biblioteca]
    python3.11 -I tools/skills.py buscar <texto>
    python3.11 -I tools/skills.py estado            # catalog vs. the real links; skills nobody catalogued
    python3.11 -I tools/skills.py aplicar [--global] # make the links match the catalog

Only skills managed by "niklaus" or "darkroom" are ever linked or unlinked. Skills managed by anyone else (a work toolchain, for example) are
reported, never touched. `aplicar` only creates or removes SYMLINKS, never files or folders, and only links that
point at the skill's own catalogued folder. Touching ~/.claude/skills needs --global (and the owner's OK).
Standard library only.
"""
import argparse
import os
import sys
import tomllib
import unicodedata
from pathlib import Path

NIKLAUS = Path(__file__).resolve().parents[1]
CATALOGO = NIKLAUS / "skills.toml"
DESTINOS = {"global": Path.home() / ".claude" / "skills", "niklaus": NIKLAUS / ".claude" / "skills"}
GESTIONADAS = {"niklaus", "darkroom"}
SISTEMA = {"synced"}          # managed by Claude Code itself (skills synced from the account): never reported or touched
AMBITOS = {"global", "niklaus", "biblioteca"}


def normal(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s).lower()) if not unicodedata.combining(c))


def cargar():
    with open(CATALOGO, "rb") as fh:
        datos = tomllib.load(fh)
    for nombre, s in datos.items():
        if s.get("ambito") not in AMBITOS:
            sys.exit(f"[skills] {nombre}: ámbito inválido {s.get('ambito')!r}")
        s["_ruta"] = Path(os.path.expanduser(s["ruta"]))
    return datos


def descripcion(ruta):
    try:
        for linea in (ruta / "SKILL.md").read_text(encoding="utf-8").splitlines()[:12]:
            if linea.startswith("description:"):
                return linea.split(":", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def listar(datos, categoria=None, ambito=None):
    for nombre, s in sorted(datos.items(), key=lambda x: (x[1]["categoria"], x[0])):
        if (categoria and normal(s["categoria"]) != normal(categoria)) or (ambito and s["ambito"] != ambito):
            continue
        print(f"{nombre:30} {s['categoria']:13} {s['ambito']:10} {s['gestor']:9} {descripcion(s['_ruta'])[:70]}")


def buscar(datos, texto):
    q = normal(texto).split()
    res = []
    for nombre, s in datos.items():
        campo = normal(" ".join([nombre, s["categoria"], " ".join(s.get("etiquetas", [])), descripcion(s["_ruta"])]))
        puntos = sum(campo.count(w) for w in q)
        if puntos:
            res.append((puntos, nombre, s))
    if not res:
        print("Sin coincidencias en el catálogo. Si hace falta una skill nueva: buscarla, auditarla y catalogarla.")
    for _, nombre, s in sorted(res, key=lambda r: (-r[0], r[1]))[:8]:
        print(f"{nombre:30} [{s['categoria']}, {s['ambito']}, {s['gestor']}] {descripcion(s['_ruta'])[:90]}")
        if s.get("riesgos"):
            print(f"{'':30} riesgos: {s['riesgos']}")


def enlaces(destino):
    """{nombre: target} for the entries of a skills folder (links resolved, plain folders as None)."""
    if not destino.is_dir():
        return {}
    out = {}
    for e in destino.iterdir():
        if e.name.startswith(".") or e.name in SISTEMA:
            continue
        out[e.name] = Path(os.path.realpath(e)) if e.is_symlink() else None
    return out


def estado(datos):
    problemas = 0
    for ambito, destino in DESTINOS.items():
        reales = enlaces(destino)
        for nombre, objetivo in sorted(reales.items()):
            s = datos.get(nombre)
            if s is None:
                print(f"⚠️  {destino}/{nombre}: no está en el catálogo")
                problemas += 1
            elif s["ambito"] != ambito and s["gestor"] in GESTIONADAS:
                print(f"⚠️  {nombre}: cargada en '{ambito}' pero el catálogo dice '{s['ambito']}' (corre aplicar)")
                problemas += 1
        for nombre, s in sorted(datos.items()):
            if s["ambito"] == ambito and nombre not in reales:
                print(f"⚠️  {nombre}: el catálogo la quiere en '{ambito}' y no está enlazada (corre aplicar)")
                problemas += 1
    for nombre, s in sorted(datos.items()):
        if not (s["_ruta"] / "SKILL.md").is_file():
            print(f"⚠️  {nombre}: falta {s['_ruta']}/SKILL.md")
            problemas += 1
    print("Catálogo y enlaces coinciden." if not problemas else f"{problemas} diferencia(s).")
    return problemas


def aplicar(datos, permitir_global):
    cambios = 0
    for nombre, s in sorted(datos.items()):
        if s["gestor"] not in GESTIONADAS:
            continue
        objetivo = Path(os.path.realpath(s["_ruta"]))
        for ambito, destino in DESTINOS.items():
            if ambito == "global" and not permitir_global:
                if (s["ambito"] == "global") != (destino / nombre).exists():
                    print(f"·  {nombre}: el cambio en ~/.claude/skills requiere --global (y el OK del dueño)")
                continue
            enlace = destino / nombre
            quiere = s["ambito"] == ambito
            if quiere and not enlace.exists() and not enlace.is_symlink():
                destino.mkdir(parents=True, exist_ok=True)
                enlace.symlink_to(objetivo)
                print(f"+  {enlace} -> {objetivo}")
                cambios += 1
            elif not quiere and enlace.is_symlink():
                if Path(os.path.realpath(enlace)) == objetivo:          # never remove someone else's link
                    enlace.unlink()
                    print(f"-  {enlace}")
                    cambios += 1
                else:
                    print(f"·  {enlace} apunta a otra parte; no lo toco")
            elif quiere and enlace.exists() and not enlace.is_symlink():
                print(f"·  {enlace} es una carpeta real, no un enlace; no la toco")
    print(f"{cambios} cambio(s).")


def main():
    ap = argparse.ArgumentParser(description="Catálogo de skills de Niklaus")
    sub = ap.add_subparsers(dest="orden", required=True)
    l = sub.add_parser("listar")
    l.add_argument("--categoria")
    l.add_argument("--ambito", choices=sorted(AMBITOS))
    b = sub.add_parser("buscar")
    b.add_argument("texto", nargs="+")
    sub.add_parser("estado")
    a = sub.add_parser("aplicar")
    a.add_argument("--global", dest="permitir_global", action="store_true")
    args = ap.parse_args()
    datos = cargar()
    if args.orden == "listar":
        listar(datos, args.categoria, args.ambito)
    elif args.orden == "buscar":
        buscar(datos, " ".join(args.texto))
    elif args.orden == "estado":
        sys.exit(1 if estado(datos) else 0)
    else:
        aplicar(datos, args.permitir_global)


if __name__ == "__main__":
    main()
