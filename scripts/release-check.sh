#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION=""
ALLOW_DIRTY=0
SKIP_TESTS=0
SKIP_DOCKER_CONFIG=0
WITH_DOCKER_BUILD=0
WITH_SMOKE=0
SMOKE_ARGS=""

usage() {
  cat <<'EOF'
Usage: scripts/release-check.sh [options]

Release quality gate for Mirror-Registry.

Options:
  --version vX.Y.Z            expected release version/tag; validates SemVer format
  --allow-dirty               allow uncommitted worktree changes
  --skip-tests                skip scripts/test-local.sh (not for final release)
  --skip-docker-config        skip docker compose config validation
  --with-docker-build         build panel and sync Docker images locally
  --with-smoke                run scripts/prod-smoke.sh after local checks
  --smoke-args "ARGS"         extra args passed to scripts/prod-smoke.sh
  -h, --help                  show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --skip-docker-config) SKIP_DOCKER_CONFIG=1; shift ;;
    --with-docker-build) WITH_DOCKER_BUILD=1; shift ;;
    --with-smoke) WITH_SMOKE=1; shift ;;
    --smoke-args) SMOKE_ARGS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '[ok] %s\n' "$*"; }
fail() { printf '[fail] %s\n' "$*" >&2; exit 1; }
need_command() { command -v "$1" >/dev/null 2>&1 || fail "$1 is required but was not found in PATH"; }

step "release metadata"
if [[ -n "$VERSION" ]]; then
  [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || fail "--version must look like vX.Y.Z"
  ok "version format: $VERSION"
else
  echo "[warn] --version not provided; release tag consistency was not checked" >&2
fi

step "required release files"
for path in CHANGELOG.md RELEASE.md scripts/release-check.sh scripts/prod-smoke.sh scripts/e2e-smoke.sh; do
  [[ -s "$path" ]] || fail "required release file is missing or empty: $path"
  ok "$path"
done

step "git worktree"
need_command git
if [[ "$ALLOW_DIRTY" != "1" ]]; then
  [[ -z "$(git status --short)" ]] || fail "worktree is dirty; commit or use --allow-dirty for local rehearsal"
  ok "clean worktree"
else
  echo "[warn] dirty worktree allowed" >&2
  git status --short
fi

if [[ "$SKIP_TESTS" != "1" ]]; then
  step "local test gate"
  bash scripts/test-local.sh
else
  echo "[warn] skipped scripts/test-local.sh" >&2
fi

if [[ "$SKIP_DOCKER_CONFIG" != "1" ]]; then
  step "docker compose config"
  need_command docker
  docker compose -f docker-compose.yml config >/tmp/mirror-registry-compose-config.yml
  ok "docker-compose.yml is valid"
else
  echo "[warn] skipped docker compose config validation" >&2
fi

if [[ "$WITH_DOCKER_BUILD" == "1" ]]; then
  step "docker image build"
  need_command docker
  tag="${VERSION:-release-check}"
  docker build -f panel/Dockerfile -t "mirror-registryx-panel:${tag}" .
  docker build -f sync/Dockerfile -t "mirror-registryx-sync:${tag}" .
  ok "panel and sync images built"
fi

if [[ "$WITH_SMOKE" == "1" ]]; then
  step "production smoke"
  # shellcheck disable=SC2086
  bash scripts/prod-smoke.sh $SMOKE_ARGS
  ok "production smoke passed"
fi

step "summary"
ok "release checks passed"
