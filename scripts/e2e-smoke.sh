#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PANEL_URL="http://localhost:8080"
REGISTRY_URL="http://localhost:5000"
ENV_FILE=".env"
PANEL_TOKEN_VALUE=""
SOURCE_IMAGE="docker.io/library/alpine:latest"
TARGET_IMAGE=""
RUN_SYNC=0
SYNC_TIMEOUT_SECONDS=300
TMP_DIR=""
COOKIE_JAR=""
CREATED_INDEX=""
CREATED_SOURCE=""

usage() {
  cat <<'EOF'
Usage: scripts/e2e-smoke.sh [options]

Linux/macOS E2E smoke for an already running deployment. It validates the panel
API path by adding a temporary mirror when possible, running local preflight,
checking queue/history/diagnostics/observability, then cleaning up the temp
mirror. It does not trigger an actual sync unless --run-sync is set.

Options:
  --env-file PATH              dotenv file to read PANEL_TOKEN from (default: .env)
  --panel-url URL              panel base URL (default: http://localhost:8080)
  --registry-url URL           registry base URL (default: http://localhost:5000)
  --panel-token TOKEN          Bearer token override
  --source IMAGE               source image for temp mirror (default: docker.io/library/alpine:latest)
  --target IMAGE               target image override (default: localhost:5000/e2e/alpine-smoke-<ts>:latest)
  --run-sync                   trigger one sync for the selected mirror and wait for completion
  --sync-timeout-seconds N     sync completion timeout (default: 300)
  -h, --help                   show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --panel-url) PANEL_URL="$2"; shift 2 ;;
    --registry-url) REGISTRY_URL="$2"; shift 2 ;;
    --panel-token) PANEL_TOKEN_VALUE="$2"; shift 2 ;;
    --source) SOURCE_IMAGE="$2"; shift 2 ;;
    --target) TARGET_IMAGE="$2"; shift 2 ;;
    --run-sync) RUN_SYNC=1; shift ;;
    --sync-timeout-seconds) SYNC_TIMEOUT_SECONDS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '[ok] %s\n' "$*"; }

cleanup() {
  local status=$?
  if [[ -n "$CREATED_INDEX" ]]; then
    echo "Cleaning up temporary mirror index $CREATED_INDEX ($CREATED_SOURCE)"
    api_json DELETE "/mirrors/$CREATED_INDEX" '' >/dev/null 2>&1 || true
  fi
  [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
  exit "$status"
}
trap cleanup EXIT

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
  local name="$1" value=""
  value="$(read_dotenv_value "$name" || true)"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  elif [[ -n "${!name:-}" ]]; then
    printf '%s' "${!name}"
  fi
}

join_url() {
  local base="${1%/}" path="${2#/}"
  printf '%s/%s' "$base" "$path"
}

json_get() {
  python3 -c 'import json, sys
path = sys.argv[1].split(".") if sys.argv[1] else []
data = json.load(sys.stdin)
for part in path:
    data = data[int(part)] if isinstance(data, list) else data.get(part)
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
  local token="$PANEL_TOKEN_VALUE" url
  [[ -n "$token" ]] || token="$(config_value PANEL_TOKEN)"
  if [[ -z "$token" ]]; then
    echo "PANEL_TOKEN is required. Pass --panel-token or set it in $ENV_FILE." >&2
    return 2
  fi
  url="$(join_url "$PANEL_URL" "api${path}")"
  if [[ -n "$body" ]]; then
    curl -k -sS --fail-with-body --max-time 30 -X "$method" "$url" \
      -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
      -b "$COOKIE_JAR" -c "$COOKIE_JAR" --data "$body"
  else
    curl -k -sS --fail-with-body --max-time 30 -X "$method" "$url" \
      -H "Authorization: Bearer $token" -b "$COOKIE_JAR" -c "$COOKIE_JAR"
  fi
}

max_run_id() {
  api_json GET '/sync-runs?limit=50' '' | python3 -c 'import json,sys; print(max([0]+[int(x.get("id") or 0) for x in json.load(sys.stdin)]))'
}

wait_sync_run() {
  local after_id="$1" timeout="$2" deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    local runs candidate
    runs="$(api_json GET '/sync-runs?limit=50' '')"
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

command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 2; }
TMP_DIR="$(mktemp -d)"
COOKIE_JAR="$TMP_DIR/cookies.txt"

if [[ -z "$TARGET_IMAGE" ]]; then
  ts="$(date -u +%Y%m%d%H%M%S)"
  TARGET_IMAGE="${REGISTRY_URL#http://}"
  TARGET_IMAGE="${TARGET_IMAGE#https://}"
  TARGET_IMAGE="${TARGET_IMAGE%/}/e2e/alpine-smoke-${ts}:latest"
fi

step "Checking panel status"
status_payload="$(api_json GET '/status' '')"
echo "Panel status: total mirrors=$(printf '%s' "$status_payload" | json_get total), pending=$(printf '%s' "$status_payload" | json_get pending), app_version=$(printf '%s' "$status_payload" | json_get app_version)"
ok "Panel status reachable"

step "Selecting or creating E2E mirror"
mirrors_before="$(api_json GET '/mirrors' '')"
existing="$(printf '%s' "$mirrors_before" | python3 -c 'import json, sys
source=sys.argv[1]
for m in json.load(sys.stdin):
    if m.get("source") == source:
        print(json.dumps(m, ensure_ascii=False))
        break' "$SOURCE_IMAGE")"
if [[ -n "$existing" ]]; then
  selected_index="$(printf '%s' "$existing" | json_get index)"
  echo "Source already exists; using mirror index $selected_index without cleanup: $SOURCE_IMAGE"
else
  body="$(SOURCE_IMAGE="$SOURCE_IMAGE" TARGET_IMAGE="$TARGET_IMAGE" python3 -c 'import json, os
print(json.dumps({
  "source": os.environ["SOURCE_IMAGE"],
  "target": os.environ["TARGET_IMAGE"],
  "registry": "local",
  "group": "default",
  "project": "e2e",
  "environment": "smoke",
  "namespace": "e2e"
}))')"
  api_json POST '/mirrors' "$body" >/dev/null
  mirrors_after="$(api_json GET '/mirrors' '')"
  selected_index="$(printf '%s' "$mirrors_after" | python3 -c 'import json, sys
source=sys.argv[1]
for m in json.load(sys.stdin):
    if m.get("source") == source:
        print(m.get("index"))
        break' "$SOURCE_IMAGE")"
  if [[ -z "$selected_index" ]]; then
    echo "Created mirror was not found in /api/mirrors." >&2
    exit 1
  fi
  CREATED_INDEX="$selected_index"
  CREATED_SOURCE="$SOURCE_IMAGE"
  echo "Created temporary mirror index $selected_index: $SOURCE_IMAGE -> $TARGET_IMAGE"
fi
ok "E2E mirror selected"

step "Running local preflight"
preflight_body="$(SOURCE_IMAGE="$SOURCE_IMAGE" TARGET_IMAGE="$TARGET_IMAGE" python3 -c 'import json, os
print(json.dumps({
  "source": os.environ["SOURCE_IMAGE"],
  "target": os.environ["TARGET_IMAGE"],
  "registry": "local",
  "group": "default",
  "project": "e2e",
  "environment": "smoke",
  "namespace": "e2e",
  "check_remote": False
}))')"
preflight="$(api_json POST '/mirrors/preflight' "$preflight_body")"
preflight_status="$(printf '%s' "$preflight" | json_get status)"
echo "Preflight status: $preflight_status"
if [[ "$preflight_status" == "error" ]]; then
  echo "Preflight failed: $(printf '%s' "$preflight" | json_get message)" >&2
  exit 1
fi
ok "Local preflight passed"

if [[ "$RUN_SYNC" == "1" ]]; then
  step "Triggering selected mirror sync"
  before_run_id="$(max_run_id)"
  api_json POST "/mirrors/$selected_index/sync" '{}' >/dev/null
  run="$(wait_sync_run "$before_run_id" "$SYNC_TIMEOUT_SECONDS")"
  run_id="$(printf '%s' "$run" | json_get id)"
  run_status="$(printf '%s' "$run" | json_get status)"
  run_failed="$(printf '%s' "$run" | json_get failed)"
  echo "Sync run $run_id finished with status=$run_status, failed=$run_failed"
  if [[ "$run_status" != "completed" || "${run_failed:-0}" -gt 0 ]]; then
    echo "Sync E2E failed. Run payload: $run" >&2
    exit 1
  fi
  ok "Sync path passed"
else
  step "Checking sync queue/history APIs"
  queue_count="$(api_json GET '/sync-queue' '' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  runs_count="$(api_json GET '/sync-runs?limit=10' '' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  echo "Sync queue items=$queue_count, recent runs=$runs_count"
  ok "Queue/history APIs reachable"
fi

step "Checking diagnostics and observability"
diagnostics="$(api_json POST '/diagnostics/run' '{}')"
diag_error_count="$(printf '%s' "$diagnostics" | python3 -c 'import json,sys; print(sum(1 for c in json.load(sys.stdin).get("checks", []) if c.get("status") == "error"))')"
observability="$(api_json GET '/observability/summary' '')"
alerts="$(printf '%s' "$observability" | json_get active_alerts)"
echo "Diagnostics errors=$diag_error_count, active alerts=${alerts:-0}"
if [[ "$diag_error_count" -gt 0 ]]; then
  echo "Diagnostics reported errors during E2E smoke." >&2
  exit 1
fi
ok "Diagnostics/observability passed"

printf '\nE2E smoke passed.\n'
