# Mirror Registry

[中文文档](README.md)

Single-node private Docker image cache with a local Registry, a management panel, and a scheduled sync worker. The project is now scoped for personal-server use: login, mirror configuration, registry credentials, sync runs, storage, and logs.

## Services

- `registry`: official `registry:2`, storing local image layers.
- `panel`: FastAPI plus the React management panel on port `8080`.
- `sync`: Python worker that mirrors upstream images into the local Registry with `skopeo copy`.

## Production Deployment

Production servers pull published images instead of building locally:

```powershell
docker compose pull
docker compose up -d
docker compose ps
```

You can also update in one command:

```powershell
docker compose pull && docker compose up -d
```

Open:

```text
http://localhost:8080
```

Runtime data is stored in Docker named volumes:

- `mirror-registry-config`: generated `mirrors.yml`.
- `mirror-registry-data`: SQLite, logs, trigger files, and sync state.
- `mirror-registry-storage`: Registry image layers.

## .env Example

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-strong-admin-password
SESSION_TTL_SECONDS=604800
SESSION_COOKIE_NAME=mirror_registry_session
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
APP_ENV=development
MIRROR_REGISTRY_IMAGE_TAG=latest
APP_VERSION=v4
DATABASE_URL=sqlite:////data/mirror-registry.db
SYNC_CONCURRENCY=2
SYNC_RETRY_COUNT=2
SYNC_RETRY_BACKOFF_SECONDS=2
DISK_LOW_BYTES=2147483648
NOTIFY_WEBHOOK_URL=
NOTIFY_DEDUPE_SECONDS=1800
SKOPEO_COPY_ALL=1
SKOPEO_DEST_TLS_VERIFY=false
```

`SESSION_COOKIE_SECURE=false` is correct for plain HTTP intranet access. Set it to `true` only when the panel is served through HTTPS. If login returns 200 but `/api/auth/me` returns 401 immediately after login, check this value first.

On first startup, if the data volume has no administrator, the panel initializes one from `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

## Terminal Password Reset

If the administrator is locked out, run this on the deployment host:

```powershell
docker compose exec panel python -m panel.password_reset admin
```

If the panel container is stopped:

```powershell
docker compose run --rm --no-deps panel python -m panel.password_reset admin
```

From a source checkout:

```powershell
.\.venv\Scripts\python.exe scripts\reset-admin-password.py admin
```

The command updates existing users by default. For explicit recovery creation:

```powershell
docker compose run --rm --no-deps panel python -m panel.password_reset admin --create-if-missing
```

Use `--database-url` for an external database or a temporary SQLite file. The command prompts with hidden input and confirmation, invalidates all sessions for the user, and writes an audit entry without the plaintext password.

## Registry Credentials

The Credentials page stores source or target Registry username plus token/password pairs. New credentials no longer require an extra master secret, which keeps single-node personal deployments simple.

If credentials were saved by an older version and sync says they cannot be decrypted, open the Credentials page, edit the GHCR/Docker Hub credential, enter the token/password again, and save it once. The newly saved value will be readable by the sync worker.

## Common Commands

```powershell
docker compose logs -f panel
docker compose logs -f sync
docker compose restart panel sync
docker compose pull && docker compose up -d
```

Trigger sync from the panel or with:

```powershell
curl -X POST http://localhost:8080/api/sync
```

## Production Smoke

The smoke script checks only the core path: environment, Compose, login, `/api/status`, Registry `/v2/`, and optional sync when service startup is explicitly requested.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prod-smoke.ps1
```

Linux/macOS:

```bash
scripts/prod-smoke.sh
```

To let the script pull images and start services:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prod-smoke.ps1 -StartServices
```

## Local Development

```powershell
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml ps
```

Frontend checks:

```powershell
npm.cmd --prefix panel run typecheck
npm.cmd --prefix panel run build
```

Python checks:

```powershell
python scripts\verify.py
python -m pytest --basetemp .pytest-basetemp
```

Full local check:

```powershell
.\scripts\check-runtime.ps1
```

## Configuration

For production, maintain mirrors through the panel. For development, you can edit `config/mirrors.yml` directly:

```yaml
mirrors:
  - source: docker.io/library/busybox:latest
    target: localhost:5000/library/busybox:latest
    registry: local
    group: default
    project: default
    environment: local
    namespace: library

settings:
  check_interval_minutes: 30
  registry_url: http://registry:5000
  database_url: sqlite:////data/mirror-registry.db
  sync_concurrency: 2
  sync_retry_count: 2
```

Restart sync if you need a changed interval to apply immediately:

```powershell
docker compose restart sync
```

## Storage Cleanup

Deletion marks in the panel record cleanup intent only. To actually release Registry storage, delete manifests according to Docker Registry rules and then run garbage collection. Back up the data volume first on personal deployments.
