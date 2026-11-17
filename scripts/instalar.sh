#!/usr/bin/env bash
# instalar.sh — builds what the voice app needs INSIDE this folder: two Python environments with pinned
# packages, the voice model and the data folders. It installs nothing outside the folder and never uses sudo.
#
#   scripts/instalar.sh              install (safe to run again: it only adds what is missing)
#   scripts/instalar.sh --comprobar  only check the prerequisites and what is already in place
#
# Network: PyPI (packages) and huggingface.co (the voice model, ~1.8 GB), only during this script.
# The app itself runs its speech workers with no network at all.
set -euo pipefail
umask 077

RAIZ="$(CDPATH= cd -P -- "$(dirname -- "$(/bin/realpath -- "${BASH_SOURCE[0]}")")/.." >/dev/null && pwd -P)"
PY="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11"
FFMPEG="/opt/homebrew/bin/ffmpeg"
MODELO="mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
REVISION="049ef77fe8816b536193c0c25f9a214d17921282"
SOLO_COMPROBAR=0
[[ "${1:-}" == "--comprobar" ]] && SOLO_COMPROBAR=1
[[ $# -le 1 && ( $# -eq 0 || "${1}" == "--comprobar" ) ]] || { echo "uso: scripts/instalar.sh [--comprobar]" >&2; exit 2; }

faltan=0
ok()   { printf '  ok     %s\n' "$*"; }
falta() { printf '  FALTA  %s\n' "$*"; faltan=$((faltan + 1)); }
paso() { printf '\n== %s\n' "$*"; }

[[ -f "${RAIZ}/herramientas.toml" && ! -L "${RAIZ}/herramientas.toml" ]] \
  || { echo "[instalar] ${RAIZ} no parece la carpeta del agente (no hay herramientas.toml)." >&2; exit 2; }

# The file tools refuse to work under system paths (/private, /Volumes…): the folder must be in your home.
case "${RAIZ}" in "${HOME}"/*) ;; *) echo "[instalar] la carpeta debe estar dentro de tu HOME (${HOME})." >&2; exit 2;; esac

# Nothing this script writes to may be a link: it would write outside the folder.
for p in venv venv/app venv/qwen-mlx models models/huggingface models/cache data data/voz data/manos data/bitacora \
         local skills.toml requirements; do
  [[ ! -L "${RAIZ}/${p}" ]] || { echo "[instalar] ${RAIZ}/${p} es un enlace; no sigo." >&2; exit 2; }
done

paso "Requisitos (esto no lo instala el script)"
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] && ok "macOS en Apple Silicon" \
  || falta "macOS en Apple Silicon (la voz usa MLX; no hay versión para Intel, Linux ni Windows)"
[[ -x "${PY}" ]] && ok "Python 3.11 de python.org" \
  || falta "Python 3.11 de python.org en ${PY} (https://www.python.org/downloads/macos/)"
[[ -x "${FFMPEG}" ]] && ok "ffmpeg" || falta "ffmpeg en ${FFMPEG} (brew install ffmpeg)"
[[ -x /usr/bin/sandbox-exec ]] && ok "sandbox-exec" || falta "/usr/bin/sandbox-exec (viene con macOS)"
CLAUDE="$(command -v claude || true)"; [[ -n "${CLAUDE}" || -x "${HOME}/.local/bin/claude" ]] && ok "Claude Code" \
  || falta "Claude Code con tu sesión iniciada (https://claude.com/claude-code)"

# The speech-to-text worker reuses Darkroom's environment and Whisper models (see herramientas.toml).
DARKROOM=""
if [[ -x "${PY}" ]]; then
  DARKROOM="$("${PY}" -I -B -c 'import os,sys,tomllib
with open(sys.argv[1],"rb") as f: print(os.path.expanduser(tomllib.load(f)["darkroom"]["ruta"]))' "${RAIZ}/herramientas.toml" 2>/dev/null || true)"
fi
if [[ -n "${DARKROOM}" && -x "${DARKROOM}/venv/bin/python" && -d "${DARKROOM}/models/huggingface" \
      && -f "${DARKROOM}/scripts/sandbox/sin-red.sb" ]]; then
  ok "Darkroom en ${DARKROOM} (oído: faster-whisper)"
else
  falta "Darkroom instalado en la ruta de herramientas.toml (https://github.com/MarcoAntonioGarcia/Darkroom, ver su INSTALL.md)"
fi

if [[ ${faltan} -gt 0 ]]; then
  printf '\nFaltan %d requisitos. Instálalos y vuelve a correr este script.\n' "${faltan}"; exit 1
fi

crear_venv() {   # crear_venv <nombre> <archivo de requisitos> <módulo de prueba> [paquetes sin wheel, separados por coma]
  local dir="${RAIZ}/venv/$1" req="${RAIZ}/requirements/$2" prueba="$3" fuente="${4:-}"
  if [[ -x "${dir}/bin/python" ]] && "${dir}/bin/python" -I -B -c "import ${prueba}" 2>/dev/null; then
    ok "venv/$1"; return 0
  fi
  if [[ ${SOLO_COMPROBAR} -eq 1 ]]; then falta "venv/$1"; return 0; fi
  echo "  creando venv/$1 …"
  [[ -x "${dir}/bin/python" ]] || "${PY}" -I -m venv "${dir}"
  # Wheels only (no setup.py of a dependency runs), exact versions, no user or global configuration. The few
  # packages published without a wheel are named one by one; everything else must be a wheel.
  # --isolated: no pip configuration and no PIP_* variables (a private index or another target folder).
  PIP_CONFIG_FILE=/dev/null "${dir}/bin/python" -I -m pip --isolated --disable-pip-version-check install \
    --quiet --only-binary :all: ${fuente:+--no-binary "${fuente}"} --no-input -r "${req}"
  if "${dir}/bin/python" -I -B -c "import ${prueba}"; then ok "venv/$1"; else falta "venv/$1 (se instaló pero no carga)"; fi
}

paso "Entornos de Python (dentro de venv/)"
crear_venv app app.txt "webview, mcp" "proxy-tools"
crear_venv qwen-mlx voz.txt "mlx_audio, sounddevice"

paso "Modelo de voz (dentro de models/)"
HF="${RAIZ}/models/huggingface"
SNAP="${HF}/hub/models--${MODELO//\//--}/snapshots/${REVISION}"
if [[ -f "${SNAP}/model.safetensors" && -f "${SNAP}/config.json" ]]; then
  ok "${MODELO} @ ${REVISION:0:7}"
elif [[ ${SOLO_COMPROBAR} -eq 1 ]]; then
  falta "${MODELO}"
else
  echo "  descargando ${MODELO} (~1.8 GB) …"
  mkdir -p "${HF}"
  env -u HF_HUB_CACHE -u HUGGINGFACE_HUB_CACHE -u HF_ENDPOINT -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN \
    HF_HOME="${HF}" HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    "${RAIZ}/venv/qwen-mlx/bin/python" -I -B -c 'import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1], revision=sys.argv[2], token=False, cache_dir=sys.argv[3],
                  endpoint="https://huggingface.co")' "${MODELO}" "${REVISION}" "${HF}/hub"
  if [[ -f "${SNAP}/model.safetensors" && -f "${SNAP}/config.json" ]]; then ok "${MODELO} @ ${REVISION:0:7}"
  else falta "${MODELO} (la descarga terminó pero el modelo no quedó en models/)"; fi
fi

paso "Carpetas de datos y tu capa privada"
if [[ ${SOLO_COMPROBAR} -eq 0 ]]; then
  mkdir -p "${RAIZ}/data/voz/tmp" "${RAIZ}/data/manos/respaldos" "${RAIZ}/data/bitacora" "${RAIZ}/models/cache"
  chmod 700 "${RAIZ}/data" "${RAIZ}/data/voz" "${RAIZ}/data/manos" "${RAIZ}/data/bitacora"
  [[ -e "${RAIZ}/skills.toml" ]] || cp "${RAIZ}/skills.ejemplo.toml" "${RAIZ}/skills.toml"
  if [[ ! -e "${RAIZ}/local" ]]; then
    mkdir -m 700 "${RAIZ}/local"
    cp "${RAIZ}/local.ejemplo/README.md" "${RAIZ}/local/README.md"; chmod 600 "${RAIZ}/local/README.md"
  fi
  chmod +x "${RAIZ}/app/escritorio/abrir.command"
fi
[[ -d "${RAIZ}/data/voz" ]] && ok "data/" || falta "data/"
[[ -e "${RAIZ}/skills.toml" ]] && ok "skills.toml" || falta "skills.toml (copia de skills.ejemplo.toml)"
[[ -d "${RAIZ}/local" ]] && ok "local/ (tu capa privada; ver local/README.md)" || falta "local/"

if [[ ${faltan} -gt 0 ]]; then printf '\nFaltan %d cosas. Corre scripts/instalar.sh sin --comprobar.\n' "${faltan}"; exit 1; fi
printf '\nListo. Abre la app con doble clic en app/escritorio/abrir.command\n'
printf '(la primera vez macOS pedirá permiso de micrófono para la Terminal).\n'
