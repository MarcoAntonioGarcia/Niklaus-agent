"""Shared start-up for the small MCP servers (mac, correo, notas): telemetry off, one error wrapper."""
import os
import sys

for _k, _v in {"OTEL_SDK_DISABLED": "true", "OTEL_TRACES_EXPORTER": "none", "OTEL_METRICS_EXPORTER": "none",
               "OTEL_LOGS_EXPORTER": "none", "DO_NOT_TRACK": "1"}.items():
    os.environ[_k] = _v

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from seguro import HerramientaError  # noqa: E402


def hacer(nombre, funcion, *args, **kw):
    try:
        return funcion(*args, **kw)
    except HerramientaError as exc:
        print(f"[{nombre}] {funcion.__name__}: {exc}", file=sys.stderr)
        raise ToolError(str(exc)) from None
    except Exception as exc:                    # never silent: the cause goes to the log, its type to the model
        print(f"[{nombre}] {funcion.__name__}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise ToolError(f"falló {nombre} ({type(exc).__name__}); el detalle está en el registro de la app") from None
