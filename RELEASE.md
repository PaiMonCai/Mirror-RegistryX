# Release Runbook

Use this runbook for every tagged Mirror-Registry release.

## 1. Choose the version

Use semantic versioning: `vMAJOR.MINOR.PATCH`.

- `MAJOR`: incompatible deployment or data changes.
- `MINOR`: backward-compatible feature or operational improvements.
- `PATCH`: bug fix, security hardening, docs, or release-process update.

Docker tag policy:

- `latest`: stable newest release.
- `vX.Y.Z`: immutable release tag.
- `main-<sha>`: main branch snapshot.
- `dev`: development/test image.

## 2. Prepare the release branch

1. Make sure the worktree is clean.
2. Update `CHANGELOG.md` by moving relevant `Unreleased` entries under `vX.Y.Z - YYYY-MM-DD`.
3. Update version fields if the release changes them:
   - `panel/package.json`
   - image labels or deployment metadata, if added later
4. Confirm `.env.example` still documents any new required environment variables.

## 3. Run the release gate

Local mandatory gate:

```bash
scripts/release-check.sh --version vX.Y.Z
```

This checks:

- clean git worktree unless `--allow-dirty` is set
- required release docs exist
- Python syntax
- frontend typecheck
- frontend build
- pytest
- Docker Compose config syntax

Full pre-publish gate when Docker is available:

```bash
scripts/release-check.sh --version vX.Y.Z --with-docker-build
```

Production/staging smoke gate against an already running deployment:

```bash
scripts/release-check.sh --version vX.Y.Z --with-smoke --smoke-args "--panel-url https://panel.example.com --registry-url https://registry.example.com"
```

For a new staging machine where the script may start services:

```bash
scripts/release-check.sh --version vX.Y.Z --with-smoke --smoke-args "--env-file .env --start-services --skip-sync"
```

## 4. Backup and restore drill

Before upgrading production, complete the backup/restore checklist in `docs/backup-restore.md`.

Minimum evidence to keep with release notes:

- backup archive path or object key
- `/api/backup-restore/verify` result
- restore drill environment name
- drill timestamp
- operator

## 5. Tag and publish

After all checks pass:

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

Build and publish images with both immutable and channel tags:

```bash
gh run list --workflow release-images.yml --limit 5
```

The `Release Images` workflow publishes both `vX.Y.Z` and `latest` images for panel and sync.

## 6. Deploy

1. Set `MIRROR_REGISTRY_IMAGE_TAG=vX.Y.Z` in production `.env`.
2. Run read-only production smoke first:

   ```bash
   scripts/prod-smoke.sh --env-file .env
   ```

3. Pull and restart through Docker Compose:

   ```bash
   docker compose pull
   docker compose up -d
   ```

4. Run post-deploy smoke:

   ```bash
   scripts/prod-smoke.sh --env-file .env --skip-sync
   ```

5. If a real sync validation is required and acceptable:

   ```bash
   scripts/prod-smoke.sh --env-file .env --start-services
   ```

## 7. Rollback trigger

Rollback immediately if any of these occur after deploy:

- panel cannot authenticate or load `/api/status`
- sync queue cannot enqueue or claim tasks
- registry `/v2/` is unavailable
- database migration fails or data is missing
- backup/restore verification fails
- sustained 5xx errors appear in panel or sync logs

Use `docs/rollback.md`.
