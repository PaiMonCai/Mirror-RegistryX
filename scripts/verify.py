import compileall
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"OK: {message}")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def compose_service_names(compose: str) -> set[str]:
    names: set[str] = set()
    in_services = False
    for line in compose.splitlines():
        if line == "services:":
            in_services = True
            continue
        if not in_services:
            continue
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  ([a-zA-Z0-9_-]+):$", line)
        if match:
            names.add(match.group(1))
    return names


def require_paths() -> None:
    required = [
        "README.md",
        "README.en.md",
        ".env.example",
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "requirements-dev.txt",
        "config/mirrors.yml",
        "config/registry-config.yml",
        "panel/app.py",
        "panel/main.py",
        "panel/password_reset.py",
        "panel/frontend/src/main.tsx",
        "panel/frontend/src/navigation.tsx",
        "panel/frontend/src/types.ts",
        "panel/frontend/src/views.tsx",
        "panel/static/index.html",
        "sync/worker.py",
        "sync/sync.py",
        "mirror_registry_core/config.py",
        "scripts/check-runtime.ps1",
        "scripts/prod-smoke.ps1",
        "scripts/prod-smoke.sh",
        "scripts/reset-admin-password.py",
        "tests/test_panel.py",
        "tests/test_sync.py",
    ]
    missing = [path for path in required if not (ROOT / path).exists()]
    if missing:
        fail(f"missing required paths: {missing}")

    removed_frontend_views = [
        "AccessControl.tsx",
        "AuditLogs.tsx",
        "Diagnostics.tsx",
        "Governance.tsx",
        "InstallUpgrade.tsx",
        "Observability.tsx",
        "Platform.tsx",
        "Schedules.tsx",
        "Security.tsx",
        "Workers.tsx",
    ]
    leftovers = [name for name in removed_frontend_views if (ROOT / "panel/frontend/src/views" / name).exists()]
    if leftovers:
        fail(f"non-core frontend views still exist: {leftovers}")
    ok("core directory structure is present")


def require_python_compiles() -> None:
    for path in [ROOT / "panel", ROOT / "sync", ROOT / "mirror_registry_core", ROOT / "tests", ROOT / "scripts"]:
        if not compileall.compile_dir(str(path), quiet=1):
            fail(f"{path.relative_to(ROOT)} Python files do not compile")
    ok("Python files compile")


def require_compose_shape() -> None:
    compose = read("docker-compose.yml")
    dev_compose = read("docker-compose.dev.yml")
    for name, source in [("docker-compose.yml", compose), ("docker-compose.dev.yml", dev_compose)]:
        for snippet in [
            "image: registry:2",
            "APP_VERSION: v4",
            "DATABASE_URL: ${DATABASE_URL:-sqlite:////data/mirror-registry.db}",
            "ADMIN_USERNAME: ${ADMIN_USERNAME:-admin}",
            "ADMIN_PASSWORD: ${ADMIN_PASSWORD:-}",
            "SESSION_COOKIE_NAME: ${SESSION_COOKIE_NAME:-mirror_registry_session}",
            "SESSION_COOKIE_SECURE: ${SESSION_COOKIE_SECURE:-false}",
            "SYNC_CONCURRENCY: ${SYNC_CONCURRENCY:-2}",
            "REGISTRY_STORAGE_PATH: /data/registry",
        ]:
            if snippet not in source:
                fail(f"{name} missing {snippet!r}")
        if "CREDENTIALS_SECRET_KEY" in source:
            fail(f"{name} still passes CREDENTIALS_SECRET_KEY")

    if compose_service_names(compose) != {"registry", "panel", "sync"}:
        fail("docker-compose.yml service set must be registry, panel, sync")
    if compose_service_names(dev_compose) != {"registry", "panel", "sync"}:
        fail("docker-compose.dev.yml service set must be registry, panel, sync")
    if "build:" in compose:
        fail("production docker-compose.yml must pull images instead of building locally")
    ok("compose files keep the single-node core shape")


def require_credentials_are_personal_use_friendly() -> None:
    panel_source = read("panel/legacy.py")
    sync_source = read("sync/worker.py")
    tests = read("tests/test_panel.py") + "\n" + read("tests/test_sync.py")
    for source_name, source in [("panel/legacy.py", panel_source), ("sync/worker.py", sync_source)]:
        for snippet in [
            'PLAIN_SECRET_PREFIX = "plain:"',
            "base64.urlsafe_b64",
            "CREDENTIALS_SECRET_KEY",
        ]:
            if snippet not in source:
                fail(f"{source_name} missing credential snippet {snippet!r}")
    if "test_credentials_do_not_require_secret_key" not in tests:
        fail("tests must cover saving credentials without CREDENTIALS_SECRET_KEY")
    if "test_plain_credentials_work_without_secret_key" not in tests:
        fail("tests must cover sync reading plain credentials without CREDENTIALS_SECRET_KEY")
    ok("registry credentials no longer require a master key for new personal deployments")


def require_frontend_core_only() -> None:
    source = "\n".join(
        read(str(path.relative_to(ROOT)))
        for path in sorted((ROOT / "panel/frontend/src").rglob("*"))
        if path.is_file() and path.suffix in {".ts", ".tsx"}
    )
    for snippet in [
        "dashboard",
        "mirrors",
        "credentials",
        "storage",
        "runs",
        "logs",
        "settings",
        "/auth/me",
        "/auth/login",
        "/auth/logout",
        "/status",
        "/mirrors",
        "/credentials",
        "/storage",
        "/sync-runs",
        "/sync-queue",
        "/logs",
        "/settings",
    ]:
        if snippet not in source:
            fail(f"frontend missing core snippet {snippet!r}")
    for snippet in [
        "tag-protection",
        "retention-policies",
        "backup-restore",
        "migration",
        "observability",
        "workers/guide",
        "install-upgrade",
        "audit-logs",
        "access/users",
        "/schedules",
        "security-guide",
        "diagnostics/run",
    ]:
        if snippet in source:
            fail(f"frontend still exposes non-core feature {snippet!r}")
    if '<div id="root"></div>' not in read("panel/static/index.html"):
        fail("Vite build output is not present in panel/static/index.html")
    ok("frontend exposes only core personal-use pages")


def require_backend_core_only() -> None:
    app_source = read("panel/app.py")
    auth_source = read("panel/auth.py")
    queue_source = read("panel/queue.py")
    ops_source = read("panel/ops.py")
    credentials_source = read("panel/credentials.py")
    test_source = read("tests/test_panel.py")

    for snippet in [
        "_backup_migration",
        "_install_upgrade",
        "_observability",
        "_audit",
        "_governance",
    ]:
        if snippet in app_source:
            fail(f"panel/app.py still mounts non-core router {snippet!r}")

    for snippet in [
        '"/access/users"',
        '"/workers"',
        '"/workers/guide"',
        '"/workers/heartbeat"',
        '"/workers/claim"',
        '"/workers/complete"',
    ]:
        if snippet in auth_source or snippet in queue_source:
            fail(f"auth/queue still exposes non-core route {snippet!r}")

    for snippet in [
        "/api/ops",
        "/api/diagnostics",
        "/api/security-guide",
        "/api/security-checks",
        "/api/platform",
        "/api/deployment-modes",
        "/api/database-guide",
    ]:
        if snippet in ops_source:
            fail(f"ops router still exposes non-core path {snippet!r}")

    for snippet in ["/api/registries", "/api/mirror-groups"]:
        if snippet in credentials_source:
            fail(f"credentials router still exposes non-core path {snippet!r}")

    if "test_non_core_api_routes_are_not_exposed" not in test_source:
        fail("tests must verify hidden non-core API routes are not exposed")
    ok("backend API exposes only the personal-use core surface")


def require_smoke_is_core_only() -> None:
    for path in ["scripts/prod-smoke.ps1", "scripts/prod-smoke.sh"]:
        source = read(path)
        if "CREDENTIALS_SECRET_KEY" in source:
            fail(f"{path} still blocks on CREDENTIALS_SECRET_KEY")
        for snippet in ["/auth/login", "/status", "/sync", "/v2/library/busybox/tags/list"]:
            if snippet not in source:
                fail(f"{path} missing core smoke check {snippet!r}")
        for snippet in ["/diagnostics/run", "/backup-restore/verify"]:
            if snippet in source:
                fail(f"{path} still runs non-core smoke check {snippet!r}")
    ok("production smoke checks only login, status, Registry, and optional sync")


def require_docs_and_env_are_core_only() -> None:
    readme = read("README.md")
    readme_en = read("README.en.md")
    env_example = read(".env.example")
    for path, source in [("README.md", readme), ("README.en.md", readme_en), (".env.example", env_example)]:
        if "CREDENTIALS_SECRET_KEY" in source:
            fail(f"{path} still documents CREDENTIALS_SECRET_KEY as required")
    for snippet in [
        "docker compose pull",
        "docker compose up -d",
        "docker compose exec panel python -m panel.password_reset admin",
        "docker compose run --rm --no-deps panel python -m panel.password_reset admin",
        "SESSION_COOKIE_SECURE=false",
        "DATABASE_URL=sqlite:////data/mirror-registry.db",
    ]:
        if snippet not in readme:
            fail(f"README.md missing {snippet!r}")
        if snippet not in readme_en:
            fail(f"README.en.md missing {snippet!r}")
    for snippet in [
        "ADMIN_USERNAME=",
        "ADMIN_PASSWORD=",
        "SESSION_COOKIE_SECURE=false",
        "DATABASE_URL=sqlite:////data/mirror-registry.db",
        "SYNC_CONCURRENCY=2",
        "SKOPEO_COPY_ALL=1",
    ]:
        if snippet not in env_example:
            fail(f".env.example missing {snippet!r}")
    ok("README and .env.example describe the simplified core deployment")


def require_check_runtime() -> None:
    source = read("scripts/check-runtime.ps1")
    for snippet in [
        "python scripts\\verify.py",
        "python -m py_compile",
        "panel\\password_reset.py",
        "scripts\\reset-admin-password.py",
        "python -m pytest",
        "docker compose config",
    ]:
        if snippet not in source:
            fail(f"scripts/check-runtime.ps1 missing {snippet!r}")
    ok("runtime check script keeps core verification")


def main() -> None:
    require_paths()
    require_python_compiles()
    require_compose_shape()
    require_credentials_are_personal_use_friendly()
    require_frontend_core_only()
    require_backend_core_only()
    require_smoke_is_core_only()
    require_docs_and_env_are_core_only()
    require_check_runtime()
    print("Verification passed.")


if __name__ == "__main__":
    main()
