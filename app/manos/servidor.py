"""MCP server "manos" (stdio): Niklaus's reversible hands over the whole computer. Thin layer over nucleo.py.

Launched by Claude Code (the voice/desktop app session) with --mcp-config. Every tool is reversible and recorded
(data/manos/diario.jsonl), so the app allows them without asking; commands and programs are NOT here (they go
through Claude Code's Bash, which the app confirms with the owner).
"""
import os

for _k, _v in {"OTEL_SDK_DISABLED": "true", "OTEL_TRACES_EXPORTER": "none", "OTEL_METRICS_EXPORTER": "none",
               "OTEL_LOGS_EXPORTER": "none", "DO_NOT_TRACK": "1"}.items():
    os.environ[_k] = _v

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nucleo  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

mcp = MCPServer(
    name="manos",
    title="manos",
    instructions=(
        "Manos de Niklaus: acciones REVERSIBLES sobre archivos de toda la computadora del dueño. Rutas absolutas o "
        "con ~. Nada se borra para siempre: a_papelera usa la Papelera real de macOS. Todo queda en el diario y "
        "'deshacer' lo revierte. No tocan secretos, sistema ni los controles de Niklaus. Para leer o buscar usa "
        "Read, Glob y Grep; para comandos o programas, Bash (la app pide confirmación solo de lo delicado)."),
)


def _hacer(funcion, *args):
    try:
        _, mensaje = funcion(*args)
        return mensaje
    except nucleo.ManosError as exc:
        print(f"[manos] {funcion.__name__}: {exc}", file=sys.stderr)
        raise ToolError(str(exc)) from None
    except Exception as exc:                    # never a silent failure: the model and the owner see the cause
        print(f"[manos] {funcion.__name__}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise ToolError(f"{type(exc).__name__}: {exc}") from None


@mcp.tool(description="Mueve un archivo o carpeta a otra carpeta (nunca sobrescribe; si el nombre existe añade (2)). "
                      "Opcional: nuevo_nombre.")
def mover(origen: str, destino_carpeta: str, nuevo_nombre: str | None = None) -> str:
    return _hacer(nucleo.mover, origen, destino_carpeta, nuevo_nombre)


@mcp.tool(description="Renombra un archivo o carpeta en su mismo lugar (nunca sobrescribe).")
def renombrar(ruta: str, nuevo_nombre: str) -> str:
    return _hacer(nucleo.renombrar, ruta, nuevo_nombre)


@mcp.tool(description="Copia un archivo o carpeta a otra carpeta (nunca sobrescribe).")
def copiar(origen: str, destino_carpeta: str) -> str:
    return _hacer(nucleo.copiar, origen, destino_carpeta)


@mcp.tool(description="Crea una carpeta nueva.")
def crear_carpeta(ruta: str) -> str:
    return _hacer(nucleo.crear_carpeta, ruta)


@mcp.tool(description="'Borra': manda archivos o carpetas a la Papelera real de macOS (recuperables). Lista de rutas.")
def a_papelera(rutas: list[str]) -> str:
    return _hacer(nucleo.a_papelera, rutas)


@mcp.tool(description="Escribe un archivo de texto (crea o sobrescribe; si existía guarda antes un respaldo). "
                      "anexar=true agrega al final.")
def escribir(ruta: str, texto: str, anexar: bool = False) -> str:
    return _hacer(nucleo.escribir, ruta, texto, anexar)


@mcp.tool(description="Ordena los archivos sueltos de una carpeta en subcarpetas por 'tipo' (Imágenes, Videos, "
                      "Documentos…) o por 'fecha' (AAAA-MM). Se deshace completo.")
def organizar(carpeta: str, criterio: str = "tipo") -> str:
    if criterio not in ("tipo", "fecha"):
        raise ToolError("criterio: 'tipo' o 'fecha'")
    return _hacer(nucleo.organizar, carpeta, criterio)


@mcp.tool(description="Deshace las últimas n acciones de las manos (por defecto la última), o exactamente la "
                      "del lote indicado (el id que aparece como [lote …]).")
def deshacer(n: int = 1, lote: str | None = None) -> str:
    try:
        _, mensaje, ok = nucleo.deshacer(n, lote)
    except Exception as exc:
        print(f"[manos] deshacer: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise ToolError(f"{type(exc).__name__}: {exc}") from None
    if not ok:
        raise ToolError(("PARCIAL: " if mensaje.startswith("Deshice solo una parte") else "") + mensaje)
    return mensaje


@mcp.tool(description="Lista las últimas acciones de las manos (y cuáles ya se deshicieron).")
def historial(n: int = 15) -> str:
    try:
        return nucleo.historial(n)
    except Exception as exc:
        print(f"[manos] historial: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise ToolError(f"{type(exc).__name__}: {exc}") from None


if __name__ == "__main__":
    mcp.run(transport="stdio")
