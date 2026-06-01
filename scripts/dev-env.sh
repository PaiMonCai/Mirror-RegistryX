#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${MIRROR_REGISTRY_RUNTIME_DIR:-$ROOT/.runtime}"

export CONFIG_PATH="${CONFIG_PATH:-$RUNTIME_DIR/config/mirrors.yml}"
export STATE_PATH="${STATE_PATH:-$RUNTIME_DIR/data/sync-state.json}"
export LOG_PATH="${LOG_PATH:-$RUNTIME_DIR/data/sync.log}"
export TRIGGER_PATH="${TRIGGER_PATH:-$RUNTIME_DIR/data/.trigger}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///$RUNTIME_DIR/data/mirror-registry.db}"
export REGISTRY_URL="${REGISTRY_URL:-http://registry:5000}"
export REGISTRY_STORAGE_PATH="${REGISTRY_STORAGE_PATH:-$RUNTIME_DIR/registry}"
export STATIC_DIR="${STATIC_DIR:-$ROOT/panel/static}"

export WORKER_TOKEN="${WORKER_TOKEN:-dev-worker-token-change-me}"
export ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin-password-change-me}"
export SESSION_TTL_SECONDS="${SESSION_TTL_SECONDS:-604800}"
export SESSION_COOKIE_NAME="${SESSION_COOKIE_NAME:-mirror_registry_session}"
export SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-false}"
export MIRROR_REGISTRY_IMAGE_TAG="${MIRROR_REGISTRY_IMAGE_TAG:-latest}"
export APP_VERSION="${APP_VERSION:-dev}"
export CREDENTIALS_SECRET_KEY="${CREDENTIALS_SECRET_KEY:-dev-credentials-secret-change-me}"

export SYNC_ENGINE="${SYNC_ENGINE:-skopeo}"
export SYNC_RETRY_COUNT="${SYNC_RETRY_COUNT:-2}"
export SYNC_CONCURRENCY="${SYNC_CONCURRENCY:-2}"
export SYNC_RETRY_BACKOFF_SECONDS="${SYNC_RETRY_BACKOFF_SECONDS:-2}"
export DISK_LOW_BYTES="${DISK_LOW_BYTES:-2147483648}"
export NOTIFY_WEBHOOK_URL="${NOTIFY_WEBHOOK_URL:-}"
export NOTIFY_DEDUPE_SECONDS="${NOTIFY_DEDUPE_SECONDS:-1800}"
export SKOPEO_COPY_ALL="${SKOPEO_COPY_ALL:-1}"
export SKOPEO_DEST_TLS_VERIFY="${SKOPEO_DEST_TLS_VERIFY:-false}"
export SYNC_TARGET_REGISTRY="${SYNC_TARGET_REGISTRY:-registry:5000}"
export COMMAND_TIMEOUT_SECONDS="${COMMAND_TIMEOUT_SECONDS:-900}"
export WORKER_ID="${WORKER_ID:-local-dev-sync}"
export WORKER_NAME="${WORKER_NAME:-Local Dev Sync Worker}"
export WORKER_LABELS="${WORKER_LABELS:-local,dev,sync}"
export PYTHONPATH="${PYTHONPATH:-$ROOT}"

mkdir -p \
  "$(dirname "$CONFIG_PATH")" \
  "$(dirname "$STATE_PATH")" \
  "$(dirname "$LOG_PATH")" \
  "$(dirname "$TRIGGER_PATH")" \
  "$REGISTRY_STORAGE_PATH" \
  "$STATIC_DIR"

if [ ! -f "$CONFIG_PATH" ]; then
  if [ -f "$ROOT/config/mirrors.yml" ]; then
    cp "$ROOT/config/mirrors.yml" "$CONFIG_PATH"
  else
    cat > "$CONFIG_PATH" <<'YAML'
mirrors: []
settings:
  check_interval_minutes: 30
  registry_url: http://registry:5000
YAML
  fi
fi

if [ "$#" -eq 0 ]; then
  cat <<EOF
Mirror Registry local dev environment loaded.

Runtime dir: $RUNTIME_DIR
Python venv: $ROOT/.venv
Panel deps:  $ROOT/panel/node_modules

Run a command through this isolated environment, for example:
  bash scripts/dev-env.sh ./.venv/bin/pytest -q
  bash scripts/dev-env.sh ./.venv/bin/uvicorn panel.main:app --reload --host 0.0.0.0 --port 8080
EOF
  exit 0
fi

cd "$ROOT"
exec "$@"
