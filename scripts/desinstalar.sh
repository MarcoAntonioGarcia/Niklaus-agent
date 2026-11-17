#!/usr/bin/env bash
# desinstalar-niklaus.sh — removes the agent (this folder) and what it placed in ~/.claude.
# Asks for confirmation. Tools registered in herramientas.toml have their own uninstallers; this
# script only OFFERS to run each one, asking tool by tool.
set -euo pipefail

# The agent folder is the parent of this script's folder, wherever it lives.
AGENT_DIR="$(CDPATH= cd -P -- "$(dirname -- "$(/bin/realpath -- "${BASH_SOURCE[0]}")")/.." >/dev/null && pwd -P)"
AGENT_DIR_REAL="$(cd "${AGENT_DIR}" && pwd -P)"
REGISTRY="${AGENT_DIR}/herramientas.toml"
LINKS=("${HOME}/.claude/agents/revisor-seguridad.md" "${HOME}/.claude/skills/app-lista-produccion")
AGENTS_DIR="${HOME}/.claude/agents"
GLOBAL_CLAUDE="${HOME}/.claude/CLAUDE.md"
GLOBAL_LINE="En proyectos de software: al iniciar el proyecto, antes de cada lanzamiento y antes de cobrar, aplica la skill app-lista-produccion y repórtame primero los bloqueantes."

die() { echo "[desinstalar] $*" >&2; exit 2; }

# Is this really the agent folder? It must hold herramientas.toml and must not be HOME or /.
[[ -f "${REGISTRY}" && ! -L "${REGISTRY}" ]] || die "${AGENT_DIR} no tiene herramientas.toml; no parece la carpeta del agente. No hago nada."
for bad in "/" "${HOME}" "$(cd "${HOME}" && pwd -P)"; do
  [[ "${AGENT_DIR}" != "${bad}" && "${AGENT_DIR_REAL}" != "${bad}" ]] || die "la carpeta del agente resolvió a ${bad}; no hago nada."
done

echo "Esto eliminará:"
echo "  1. Los enlaces ${LINKS[*]}"
echo "     (solo si apuntan dentro de ${AGENT_DIR}; y ${AGENTS_DIR} solo si queda vacío)"
echo "  2. Solo la línea de app-lista-produccion de ${GLOBAL_CLAUDE} (el resto del archivo no se toca)"
echo "  3. La carpeta completa del agente: ${AGENT_DIR}"
echo "     (incluye herramientas.toml, la bitácora y su hook Stop en .claude/settings.json)"
echo "Antes, para cada herramienta de herramientas.toml, se te preguntará si quieres correr su"
echo "propio desinstalador. Las carpetas de las herramientas no se tocan si respondes que no."
read -r -p "¿Continuar? Escribe 'si' para confirmar: " answer
[[ "${answer}" == "si" ]] || { echo "Cancelado."; exit 0; }

# 0. Tools: read herramientas.toml (Python 3.11+ for tomllib) before the agent folder disappears.
find_python() {
  local candidate
  for candidate in "$(command -v python3 || true)" \
                   /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
                   /opt/homebrew/bin/python3; do
    [[ -n "${candidate}" && -x "${candidate}" ]] || continue
    "${candidate}" -I -B -c 'import tomllib' 2>/dev/null && { echo "${candidate}"; return 0; }
  done
  return 1
}

# Prints one "name<TAB>uninstaller" line per tool. Every path is checked: the uninstaller must be
# a regular file inside the tool's folder (relative, no "..", no link escaping the folder).
read -r -d '' LIST_TOOLS <<'PY' || true
import os, re, sys, tomllib
registry, agent_dir, home = sys.argv[1], sys.argv[2], sys.argv[3]
with open(registry, "rb") as fh:
    data = tomllib.load(fh)
def warn(msg):
    print(f"AVISO: {msg}", file=sys.stderr)
for name, entry in data.items():
    if not isinstance(entry, dict) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        warn(f"entrada [{name!r}] no válida; la omito.")
        continue
    ruta, rel = entry.get("ruta"), entry.get("desinstalador")
    if not isinstance(ruta, str) or not isinstance(rel, str) or not rel:
        warn(f"[{name}] no tiene ruta o desinstalador; quítala a mano si hace falta.")
        continue
    if "\t" in ruta + rel or "\n" in ruta + rel:
        warn(f"[{name}] tiene caracteres de control en sus rutas; la omito.")
        continue
    if ruta == "~" or ruta.startswith("~/"):
        ruta = home + ruta[1:]
    if not os.path.isabs(ruta):
        warn(f"[{name}] ruta no absoluta ({ruta}); la omito.")
        continue
    if os.path.isabs(rel) or ".." in rel.split("/"):
        warn(f"[{name}] desinstalador fuera de su carpeta ({rel}); lo omito.")
        continue
    base = os.path.realpath(ruta)
    if base in ("/", os.path.realpath(home), agent_dir):
        warn(f"[{name}] su ruta es /, HOME o la carpeta del agente; la omito.")
        continue
    if not os.path.isdir(base):
        warn(f"[{name}] su carpeta {ruta} no existe; nada que desinstalar.")
        continue
    target = os.path.realpath(os.path.join(base, rel))
    if not target.startswith(base + "/") or not os.path.isfile(target):
        warn(f"[{name}] desinstalador {rel} no existe dentro de {ruta}; lo omito.")
        continue
    print(f"{name}\t{target}")
PY

tools=()
if PYTHON="$(find_python)"; then
  while IFS= read -r line; do
    tools+=("${line}")
  done < <("${PYTHON}" -I -B -c "${LIST_TOOLS}" "${REGISTRY}" "${AGENT_DIR_REAL}" "${HOME}" \
             || echo "AVISO: no pude leer ${REGISTRY}; corre a mano los desinstaladores de las herramientas." >&2)
else
  echo "AVISO: no encontré Python 3.11+ (tomllib) para leer ${REGISTRY}."
  echo "       Corre a mano el desinstalador de cada herramienta listada ahí (clave 'desinstalador')."
fi

for line in ${tools[@]+"${tools[@]}"}; do
  name="${line%%$'\t'*}"; uninstaller="${line#*$'\t'}"
  read -r -p "¿Correr el desinstalador de la herramienta '${name}' (${uninstaller})? Escribe 'si': " answer
  if [[ "${answer}" == "si" ]]; then
    bash "${uninstaller}" || echo "AVISO: el desinstalador de '${name}' terminó con error; revísalo a mano."
  else
    echo "Herramienta '${name}' conservada."
  fi
done

# 1. Links — only if each is a symlink pointing inside the agent folder.
for link in "${LINKS[@]}"; do
  if [[ -L "${link}" ]]; then
    target="$(readlink "${link}")"
    [[ "${target}" == /* ]] || target="$(dirname "${link}")/${target}"
    if [[ "${target}" == "${AGENT_DIR}/"* || "${target}" == "${AGENT_DIR_REAL}/"* ]]; then
      rm -- "${link}" && echo "Enlace eliminado: ${link}"
    else
      echo "AVISO: ${link} apunta fuera de la carpeta del agente (${target}); no lo toco."
    fi
  else
    echo "Enlace ${link} no existe; nada que quitar."
  fi
done

# ~/.claude/agents: remove it only if nothing else lives there.
if [[ -d "${AGENTS_DIR}" && ! -L "${AGENTS_DIR}" ]]; then
  rmdir "${AGENTS_DIR}" 2>/dev/null && echo "Carpeta vacía eliminada: ${AGENTS_DIR}" \
    || echo "${AGENTS_DIR} tiene otros archivos; se conserva."
fi

# 2. Only the exact line added to the global CLAUDE.md (whole-line, literal match), same mode.
if [[ -f "${GLOBAL_CLAUDE}" ]] && grep -qxF -- "${GLOBAL_LINE}" "${GLOBAL_CLAUDE}"; then
  tmp="$(mktemp "${GLOBAL_CLAUDE}.XXXXXX")"
  grep -vxF -- "${GLOBAL_LINE}" "${GLOBAL_CLAUDE}" > "${tmp}" || true
  chmod "$(stat -f '%Lp' "${GLOBAL_CLAUDE}")" "${tmp}"
  mv -f -- "${tmp}" "${GLOBAL_CLAUDE}"
  echo "Línea de app-lista-produccion quitada de ${GLOBAL_CLAUDE}"
else
  echo "La línea de app-lista-produccion no está en ${GLOBAL_CLAUDE}; nada que quitar."
fi

# 3. The agent folder itself (checked again: still a real folder holding herramientas.toml).
if [[ -d "${AGENT_DIR}" && ! -L "${AGENT_DIR}" && -f "${REGISTRY}" ]]; then
  cd "${HOME}"
  rm -rf -- "${AGENT_DIR}"
  echo "Carpeta eliminada: ${AGENT_DIR}"
else
  echo "AVISO: ${AGENT_DIR} ya no parece la carpeta del agente; no la borro."
fi

echo
echo "Listo."
