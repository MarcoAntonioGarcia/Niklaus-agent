"""MCP server "mac" (stdio): volume, media keys, Spotify web, dark mode, display sleep, apps and Shortcuts."""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / "comun")]
from servidor_base import hacer  # noqa: E402  (must come first: it turns telemetry off)
import nucleo  # noqa: E402
from seguro import dato  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer(name="mac", title="mac", instructions=(
    "Control de la Mac del dueño: volumen, teclas multimedia (sirven para Spotify en el navegador, YouTube o "
    "Música), abrir Spotify web, modo oscuro, apagar la pantalla, apps abiertas y sus Atajos. Cerrar una app y "
    "correr un atajo los confirma el dueño (salvo los atajos que él marcó como libres)."))


@mcp.tool(description="Pone el volumen (0-100) y/o lo silencia (silencio=true/false). Sin argumentos, lo consulta.")
def volumen(nivel: int | None = None, silencio: bool | None = None) -> str:
    return dato("volumen", hacer("mac", nucleo.volumen, nivel, silencio))


@mcp.tool(description="Tecla multimedia para lo que esté sonando: reproducir_pausar, siguiente o anterior.")
def multimedia(accion: str) -> str:
    return dato("multimedia", hacer("mac", nucleo.multimedia, accion))


@mcp.tool(description="Abre Spotify en el navegador: una búsqueda, o un enlace https://open.spotify.com/... "
                      "(canción, álbum, lista, artista). La primera vez el dueño da clic en reproducir.")
def spotify(busqueda: str | None = None, enlace: str | None = None) -> str:
    return dato("spotify", hacer("mac", nucleo.spotify, busqueda, enlace))


@mcp.tool(description="Activa o desactiva el modo oscuro.")
def modo_oscuro(activar: bool) -> str:
    return dato("apariencia", hacer("mac", nucleo.modo_oscuro, activar))


@mcp.tool(description="Apaga la pantalla (la Mac sigue encendida).")
def dormir_pantalla() -> str:
    return dato("pantalla", hacer("mac", nucleo.dormir_pantalla))


@mcp.tool(description="Batería, si está conectada a la corriente, y volumen.")
def estado() -> str:
    return dato("estado de la Mac", hacer("mac", nucleo.estado))


@mcp.tool(description="Lista las apps abiertas.")
def apps_abiertas() -> str:
    return dato("apps abiertas", hacer("mac", nucleo.apps_abiertas))


@mcp.tool(description="Cierra una app abierta de forma normal (si hay trabajo sin guardar, la app lo pregunta).")
def cerrar_app(nombre: str) -> str:
    return dato("app cerrada", hacer("mac", nucleo.cerrar_app, nombre))


@mcp.tool(description="Lista los Atajos (app Atajos) que el dueño tiene creados.")
def atajos() -> str:
    return dato("atajos", hacer("mac", nucleo.atajos))


@mcp.tool(description="Corre un Atajo del dueño por su nombre exacto; 'entrada' es texto opcional para el atajo.")
def correr_atajo(nombre: str, entrada: str | None = None) -> str:
    return dato("resultado del atajo", hacer("mac", nucleo.correr_atajo, nombre, entrada))


if __name__ == "__main__":
    mcp.run(transport="stdio")
