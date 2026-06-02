#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$ROOT/.venv/bin/python"
VENV_PIP="$ROOT/.venv/bin/pip"
VENV_PYTEST="$ROOT/.venv/bin/pytest"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "[setup] creating Python virtualenv at .venv"
  if ! "$PYTHON_BIN" -m venv .venv; then
    cat >&2 <<'EOF'
[blocked] Could not create .venv.

This container is missing Python venv/ensurepip support. Install the base tooling once in the container, then re-run this script:

  apt-get update
  apt-get install -y python3.11-venv python3-pip

The project dependencies themselves will still be installed only into .venv/.
EOF
    exit 2
  fi
fi

if [ ! -x "$VENV_PIP" ]; then
  cat >&2 <<'EOF'
[blocked] .venv exists but pip is unavailable inside it.
Install python3.11-venv/python3-pip in the container, then remove .venv and re-run:

  rm -rf .venv
  bash scripts/test-local.sh
EOF
  exit 2
fi

if [ ! -f "$ROOT/.venv/.requirements-dev.installed" ] || [ requirements-dev.txt -nt "$ROOT/.venv/.requirements-dev.installed" ] || [ panel/requirements.txt -nt "$ROOT/.venv/.requirements-dev.installed" ] || [ sync/requirements.txt -nt "$ROOT/.venv/.requirements-dev.installed" ] || [ ops-agent/requirements.txt -nt "$ROOT/.venv/.requirements-dev.installed" ]; then
  echo "[setup] installing Python dependencies into .venv"
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PIP" install -r requirements-dev.txt
  date -u +%FT%TZ > "$ROOT/.venv/.requirements-dev.installed"
fi

if [ ! -d "$ROOT/panel/node_modules" ] || [ panel/package-lock.json -nt "$ROOT/panel/node_modules" ] || [ panel/package.json -nt "$ROOT/panel/node_modules" ]; then
  echo "[setup] installing frontend dependencies into panel/node_modules"
  npm --prefix panel ci --include=dev
fi

echo "[check] python syntax"
"$VENV_PYTHON" -m compileall -q mirror_registry_core panel sync ops_agent tests scripts

echo "[check] frontend typecheck"
npm --prefix panel run typecheck

echo "[check] frontend build"
npm run build

echo "[check] pytest"
bash scripts/dev-env.sh "$VENV_PYTEST" -q "$@"
