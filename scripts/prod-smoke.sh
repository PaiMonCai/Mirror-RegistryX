#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE=".env"
COMPOSE_FILE="docker-compose.yml"
PANEL_URL="http://localhost:8080"
REGISTRY_URL="http://localhost:5000"
ADMIN_USERNAME=""
ADMIN_PASSWORD=""
START_SERVICES=0
ALLOW_INSECURE_LOCAL=0
SKIP_SYNC=0
SERVICE_TIMEOUT_SECONDS=120
SYNC_TIMEOUT_SECONDS=300

failures=()
warnings=()
TMP_DIR=""
COOKIE_JAR=""

usage() {
  cat <<'EOF'
Usage: scripts/prod-smoke.sh [options]

Production smoke test for Linux/macOS. By default it performs safety checks and
read-only probes against an already running deployment. It starts services only
when --start-services is set.

Options:
  --env-file PATH                 dotenv file to inspect (default: .env)
  --compose-file PATH             compose file to validate/start (default: docker-compose.yml)
  --panel-url URL                 panel base URL (default: http://localhost:8080)
  --registry-url URL              registry base URL (default: http://localhost:5000)
  --admin-username USER           login username override
  --admin-password PASSWORD       login password override
  --start-services                docker compose pull && up -d before probing
  --allow-insecure-local          downgrade production safety failures to warnings
  --skip-sync                     skip sync trigger even with --start-services
  --service-timeout-seconds N     service readiness timeout (default: 120)
  --sync-timeout-seconds N        sync completion timeout (default: 300)
  -h, --help                      show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --panel-url) PANEL_URL="$2"; shift 2 ;;
    --registry-url) REGISTRY_URL="$2"; shift 2 ;;
    --admin-username) ADMIN_USERNAME="$2"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
    --start-services) START_SERVICES=1; shift ;;
    --allow-insecure-local) ALLOW_INSECURE_LOCAL=1; shift ;;
    --skip-sync) SKIP_SYNC=1; shift ;;
    --service-timeout-seconds) SERVICE_TIMEOUT_SECONDS="$2"; shift 2 ;;
    --sync-timeout-seconds) SYNC_TIMEOUT_SECONDS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '[ok] %s\n' "$*"; }
warn() { warnings+=("$*"); printf '[warn] %s\n' "$*" >&2; }
fail() { failures+=("$*"); printf '[fail] %s\n' "$*" >&2; }
security_issue() { if [[ "$ALLOW_INSECURE_LOCAL" == "1" ]]; then warn "Allowed by --allow-insecure-local: $*"; else fail "$*"; fi; }

finish_failures() {
  if [[ ${#failures[@]} -eq 0 ]]; then
    return 0
  fi
  printf '\nProduction smoke failed:\n' >&2
  for item in "${failures[@]}"; do
    printf '  - %s\n' "$item" >&2
  done
  exit 1
}

cleanup() {
  [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT

need_command() {
  command -v "$1" >/dev/null 2>&1 || { fail "$1 is required but was not found in PATH."; return 1; }
}

trim() {
  local value="$1"
  value="${value#${value%%[![:space:]]*}}"
  value="${value%${value##*[![:space:]]}}"
  printf '%s' "$value"
}

read_dotenv_value() {
  local name="$1"
  [[ -f "$ENV_FILE" ]] || return 1
  local line value
  line="$(grep -E "^[[:space:]]*${name}[[:space:]]*=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  value="$(trim "$value")"
  if [[ "${#value}" -ge 2 ]]; then
    if [[ ( "${value:0:1}" == '"' && "${value: -1}" == '"' ) || ( "${value:0:1}" == "'" && "${value: -1}" == "'" ) ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "$value"
}

config_value() {
  local name="$1" default="${2:-}" value=""
  value="$(read_dotenv_value "$name" || true)"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  elif [[ -n "${!name:-}" ]]; then
    printf '%s' "${!name}"
  else
    printf '%s' "$default"
  fi
}

placeholder_value() {
  local value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | xargs)"
  shift || true
  [[ -z "$value" ]] && return 0
  [[ "$value" == replace-with-* ]] && return 0
  local placeholder
  for placeholder in "$@"; do
    [[ "$value" == "$(printf '%s' "$placeholder" | tr '[:upper:]' '[:lower:]')" ]] && return 0
  done
  return 1
}

join_url() {
  local base="${1%/}" path="${2#/}"
  printf '%s/%s' "$base" "$path"
}

http_status() {
  curl -k -sS -o /dev/null -w '%{http_code}' --max-time 8 "$1"
}

wait_http_status() {
  local url="$1" accepted_csv="$2" timeout="$3"
  local deadline=$((SECONDS + timeout)) status last=""
  while (( SECONDS < deadline )); do
    status="$(http_status "$url" 2>/tmp/prod-smoke-curl.err || true)"
    if [[ -n "$status" && ",$accepted_csv," == *",$status,"* ]]; then
      printf '%s' "$status"
      return 0
    fi
    last="HTTP ${status:-$(cat /tmp/prod-smoke-curl.err 2>/dev/null || true)}"
    sleep 3
  done
  echo "Timed out waiting for $url. Last result: $last" >&2
  return 1
}

json_get() {
  python3 -c 'import json, sys
path = sys.argv[1].split(".") if sys.argv[1] else []
data = json.load(sys.stdin)
for part in path:
    if isinstance(data, list):
        data = data[int(part)]
    else:
        data = data.get(part)
    if data is None:
        print("")
        raise SystemExit
if isinstance(data, (dict, list)):
    print(json.dumps(data, ensure_ascii=False))
elif isinstance(data, bool):
    print("true" if data else "false")
else:
    print(data)' "$1"
}

api_json() {
  local method="$1" path="$2" body="${3:-}"
  local url
  url="$(join_url "$PANEL_URL" "api${path}")"
  if [[ -n "$body" ]]; then
    curl -k -sS --fail-with-body --max-time 30 -X "$method" "$url" \
      -H 'Content-Type: application/json' \
      -b "$COOKIE_JAR" -c "$COOKIE_JAR" --data "$body"
  else
    curl -k -sS --fail-with-body --max-time 30 -X "$method" "$url" \
      -b "$COOKIE_JAR" -c "$COOKIE_JAR"
  fi
}

max_run_id() {
  api_json GET '/sync-runs?limit=50' | python3 -c 'import json,sys; print(max([0]+[int(x.get("id") or 0) for x in json.load(sys.stdin)]))'
}

wait_sync_run() {
  local after_id="$1" timeout="$2" deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    local runs candidate
    runs="$(api_json GET '/sync-runs?limit=50')"
    candidate="$(printf '%s' "$runs" | python3 -c 'import json, sys
after = int(sys.argv[1])
runs = json.load(sys.stdin)
candidates = [r for r in runs if int(r.get("id") or 0) > after]
candidates.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
for run in candidates:
    if run.get("status") != "running":
        print(json.dumps(run, ensure_ascii=False))
        break' "$after_id")"
    if [[ -n "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
    sleep 5
  done
  echo "Timed out waiting for a sync run after id $after_id" >&2
  return 1
}

wait_busybox_tag() {
  local timeout="$1" deadline=$((SECONDS + timeout)) url last=""
  url="$(join_url "$REGISTRY_URL" '/v2/library/busybox/tags/list')"
  while (( SECONDS < deadline )); do
    local payload
    payload="$(curl -k -sS --fail --max-time 10 "$url" 2>/tmp/prod-smoke-curl.err || true)"
    if [[ -n "$payload" ]] && printf '%s' "$payload" | python3 -c 'import json,sys; p=json.load(sys.stdin); raise SystemExit(0 if "latest" in (p.get("tags") or []) else 1)' 2>/dev/null; then
      return 0
    fi
    last="${payload:-$(cat /tmp/prod-smoke-curl.err 2>/dev/null || true)}"
    sleep 5
  done
  echo "Timed out waiting for library/busybox:latest in Registry. Last result: $last" >&2
  return 1
}

step "Checking prerequisites"
need_command curl || true
need_command python3 || true
finish_failures
TMP_DIR="$(mktemp -d)"
COOKIE_JAR="$TMP_DIR/cookies.txt"

step "Checking production environment settings"
if [[ ! -f "$ENV_FILE" ]]; then
  security_issue "Environment file not found: $ENV_FILE"
fi
admin_user="${ADMIN_USERNAME:-$(config_value ADMIN_USERNAME admin)}"
admin_pass="${ADMIN_PASSWORD:-$(config_value ADMIN_PASSWORD)}"
if placeholder_value "$admin_pass" change-me changeme password admin admin-password replace-with-a-strong-admin-password; then
  security_issue "ADMIN_PASSWORD is empty, weak, or a placeholder."
fi
secret_key="$(config_value CREDENTIALS_SECRET_KEY)"
if placeholder_value "$secret_key" change-me changeme replace-with-a-long-random-secret; then
  security_issue "CREDENTIALS_SECRET_KEY is empty or a placeholder."
fi
cookie_secure="$(config_value SESSION_COOKIE_SECURE false | tr '[:upper:]' '[:lower:]')"
if [[ "${PANEL_URL,,}" == https://* && ! "$cookie_secure" =~ ^(1|true|yes)$ ]]; then
  security_issue "PanelUrl is HTTPS but SESSION_COOKIE_SECURE is not true."
fi
[[ ${#failures[@]} -eq 0 ]] && ok "Production environment settings checked"
finish_failures

step "Checking Docker Compose"
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$COMPOSE_FILE" config >/dev/null
  if [[ "$START_SERVICES" == "1" ]]; then
    docker compose -f "$COMPOSE_FILE" pull
    docker compose -f "$COMPOSE_FILE" up -d
    docker compose -f "$COMPOSE_FILE" ps
  fi
  ok "Docker Compose checked"
else
  if [[ "$START_SERVICES" == "1" ]]; then
    fail "Docker CLI is required when --start-services is used."
  else
    warn "Docker CLI is not available; skipping docker compose config."
  fi
fi
finish_failures

step "Checking panel and Registry entrypoints"
entry_timeout=15
[[ "$START_SERVICES" == "1" ]] && entry_timeout="$SERVICE_TIMEOUT_SECONDS"
panel_status="$(wait_http_status "$(join_url "$PANEL_URL" '/api/auth/me')" '200,401' "$entry_timeout")" || fail "Panel entrypoint is not ready."
registry_status="$(wait_http_status "$(join_url "$REGISTRY_URL" '/v2/')" '200,401' "$entry_timeout")" || fail "Registry entrypoint is not ready."
[[ ${#failures[@]} -eq 0 ]] && { echo "Panel auth endpoint returned HTTP $panel_status"; echo "Registry /v2/ returned HTTP $registry_status"; ok "Entrypoints checked"; }
finish_failures

step "Checking panel login and protected APIs"
# shellcheck disable=SC2097,SC2098
login_body="$(SMOKE_ADMIN_USER="$admin_user" SMOKE_ADMIN_PASS="$admin_pass" python3 -c 'import json, os; print(json.dumps({"username": os.environ["SMOKE_ADMIN_USER"], "password": os.environ["SMOKE_ADMIN_PASS"]}))')"
login="$(api_json POST '/auth/login' "$login_body")" || fail "Login API failed."
if [[ ${#failures[@]} -eq 0 && "$(printf '%s' "$login" | json_get ok)" != "true" ]]; then
  fail "Login response did not report ok=true."
fi
session_status="$(api_json GET '/status')" || fail "Session status API failed."
if [[ ${#failures[@]} -eq 0 ]]; then
  echo "Session status: total mirrors=$(printf '%s' "$session_status" | json_get total), synced=$(printf '%s' "$session_status" | json_get synced)"
fi
diagnostics="$(api_json POST '/diagnostics/run' '{}')" || fail "Diagnostics API failed."
if [[ ${#failures[@]} -eq 0 ]]; then
  diag_errors="$(printf '%s' "$diagnostics" | python3 -c 'import json, sys
data=json.load(sys.stdin)
print("; ".join("{}: {}".format(c.get("name"), c.get("message")) for c in data.get("checks", []) if c.get("status") == "error"))')"
  diag_warnings="$(printf '%s' "$diagnostics" | python3 -c 'import json, sys
data=json.load(sys.stdin)
print("\n".join("Diagnostic warning: {}: {}".format(c.get("name"), c.get("message")) for c in data.get("checks", []) if c.get("status") == "warn"))')"
  [[ -n "$diag_warnings" ]] && while IFS= read -r line; do warn "$line"; done <<< "$diag_warnings"
  [[ -n "$diag_errors" ]] && fail "Diagnostic errors: $diag_errors"
fi
verify="$(api_json POST '/backup-restore/verify' '{"require_credentials_secret":true}')" || fail "Backup restore readiness API failed."
if [[ ${#failures[@]} -eq 0 && "$(printf '%s' "$verify" | json_get ok)" != "true" ]]; then
  failed_checks="$(printf '%s' "$verify" | python3 -c 'import json, sys
data=json.load(sys.stdin)
print(", ".join(c.get("name","unknown") for c in data.get("checks", []) if not c.get("ok")))')"
  fail "Backup restore readiness failed: $failed_checks"
fi
[[ ${#failures[@]} -eq 0 ]] && ok "Panel APIs checked"
finish_failures

step "Checking sync smoke"
if [[ "$START_SERVICES" != "1" ]]; then
  warn "Skipping sync trigger because --start-services was not set."
elif [[ "$SKIP_SYNC" == "1" ]]; then
  warn "Skipping sync trigger because --skip-sync was set."
else
  mirrors="$(api_json GET '/mirrors')" || fail "Mirrors API failed."
  mirror_count="$(printf '%s' "$mirrors" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  [[ "$mirror_count" == "0" ]] && fail "No mirrors are configured."
  has_busybox="$(printf '%s' "$mirrors" | python3 -c 'import json, sys
mirrors=json.load(sys.stdin)
print("1" if any(m.get("source") == "docker.io/library/busybox:latest" and "library/busybox:latest" in m.get("target","") for m in mirrors) else "0")')"

  [[ "$has_busybox" == "0" ]] && warn "Default busybox mirror is not configured; sync smoke will validate the current mirror set only."
  before_run_id="$(max_run_id)"
  api_json POST '/sync' '{}' >/dev/null || fail "Sync trigger API failed."
  if [[ ${#failures[@]} -eq 0 ]]; then
    run="$(wait_sync_run "$before_run_id" "$SYNC_TIMEOUT_SECONDS")" || fail "Sync run did not finish."
    if [[ ${#failures[@]} -eq 0 ]]; then
      run_id="$(printf '%s' "$run" | json_get id)"
      run_status="$(printf '%s' "$run" | json_get status)"
      run_failed="$(printf '%s' "$run" | json_get failed)"
      echo "Sync run $run_id finished with status=$run_status, failed=$run_failed"
      if [[ "$run_status" != "completed" || "${run_failed:-0}" -gt 0 ]]; then
        fail "Sync smoke failed. Run id=$run_id, status=$run_status, failed=$run_failed, message=$(printf '%s' "$run" | json_get message)"
      fi
      if [[ "$has_busybox" == "1" ]]; then
        wait_busybox_tag 60 || fail "Registry did not expose library/busybox:latest after sync."
        [[ ${#failures[@]} -eq 0 ]] && echo "Registry contains library/busybox:latest"
      fi
    fi
  fi
fi
finish_failures

printf '\n'
if [[ ${#warnings[@]} -gt 0 ]]; then
  echo "Production smoke completed with warnings:"
  for item in "${warnings[@]}"; do printf '  - %s\n' "$item"; done
else
  echo "Production smoke passed."
fi
