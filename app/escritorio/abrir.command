#!/bin/bash
# Doble clic: abre la app de escritorio de Niklaus (ventana nativa, sin puertos).
# Corre en la Terminal para que macOS le aplique el permiso de micrófono que ya tiene la Terminal.
# Arranca con un entorno LIMPIO (env -i): ni la app ni Claude ni sus comandos ven los tokens de la Terminal
# (API tokens exported in the shell), que de otro modo quedarían visibles con `ps -E`.
set -euo pipefail
RAIZ="$(CDPATH= cd -P -- "$(dirname -- "$(/bin/realpath -- "${BASH_SOURCE[0]}")")/../.." >/dev/null && pwd -P)"
# Command-line tools the owner installed with npm live in nvm's newest Node.
NODE_BIN="$(ls -d "${HOME}"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)"
exec /usr/bin/env -i \
  HOME="${HOME}" \
  USER="${USER}" \
  LOGNAME="${LOGNAME:-${USER}}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  LANG="${LANG:-en_US.UTF-8}" \
  TERM="${TERM:-xterm-256color}" \
  SSH_AUTH_SOCK="" \
  PATH="${HOME}/.local/bin:${NODE_BIN:+${NODE_BIN}:}/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin" \
  "${RAIZ}/venv/app/bin/python" -I -B "${RAIZ}/app/escritorio/app.py"
