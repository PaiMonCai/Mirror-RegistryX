# Backup and Restore Runbook

Mirror-Registry state lives in Docker volumes or the equivalent mounted directories. Back up config, data, registry storage, and the secret material needed to decrypt credentials.

## What must be backed up

Required:

- `.env`
- `CREDENTIALS_SECRET_KEY` from the original environment
- config volume or mounted `config/`
- data volume or mounted `data/`
- SQLite database: `data/mirror-registry.db`
- sync log/state if operational continuity matters
- registry storage volume or mounted `data/registry/`

Recommended:

- `docker-compose.yml`
- release tag and commit SHA
- output from `/api/backup-restore/verify`
- output from `/api/migration/preflight`

## Pre-backup verification

From a host that can access the panel:

```bash
scripts/prod-smoke.sh --env-file .env --skip-sync
```

In the panel, use the Backup / Migration pages or APIs to verify the backup plan:

- `GET /api/backup-restore-guide`
- `POST /api/backup-restore/verify`
- `POST /api/migration/preflight`

The backup must not export plaintext credentials. Encrypted credentials require the same original `CREDENTIALS_SECRET_KEY` during restore.

## File/volume backup example

For local bind mounts:

```bash
mkdir -p backups
BACKUP_ID="mirror-registry-$(date -u +%Y%m%dT%H%M%SZ)"
tar -czf "backups/${BACKUP_ID}.tgz" \
  .env \
  docker-compose.yml \
  config \
  data
sha256sum "backups/${BACKUP_ID}.tgz" > "backups/${BACKUP_ID}.tgz.sha256"
```

For named Docker volumes, adapt the volume names if changed:

```bash
mkdir -p backups
BACKUP_ID="mirror-registry-$(date -u +%Y%m%dT%H%M%SZ)"
docker run --rm \
  -v mirror-registry-config:/backup/config:ro \
  -v mirror-registry-data:/backup/data:ro \
  -v mirror-registry-storage:/backup/storage:ro \
  -v "$PWD/backups:/out" \
  alpine sh -c "cd /backup && tar -czf /out/${BACKUP_ID}.tgz config data storage"
sha256sum "backups/${BACKUP_ID}.tgz" > "backups/${BACKUP_ID}.tgz.sha256"
```

Store the archive and checksum outside the host.

## Restore drill

A restore drill must use an isolated host or isolated Docker project name.

1. Copy the backup archive and checksum to the drill host.
2. Verify checksum:

   ```bash
   sha256sum -c mirror-registry-YYYYMMDDTHHMMSSZ.tgz.sha256
   ```

3. Restore files or volumes into the drill environment.
4. Restore `.env` and the original `CREDENTIALS_SECRET_KEY`.
5. Start services with the same release tag or the target upgrade tag.
6. Run:

   ```bash
   scripts/prod-smoke.sh --env-file .env --skip-sync --allow-insecure-local
   ```

7. Confirm credentials decrypt if credential-backed mirrors exist.
8. Confirm registry `/v2/` and at least one expected repository/tag is readable.

## Restore to production

Only restore production data during an approved maintenance window.

1. Stop write paths:

   ```bash
   docker compose stop sync panel
   ```

2. Take a final emergency copy of current volumes/directories.
3. Restore the selected backup as a matched set.
4. Restore `.env` and original `CREDENTIALS_SECRET_KEY`.
5. Start panel first:

   ```bash
   docker compose up -d panel registry
   ```

6. Validate read-only:

   ```bash
   scripts/prod-smoke.sh --env-file .env --skip-sync
   ```

7. Start sync after validation:

   ```bash
   docker compose up -d sync
   ```

## Failure handling

- If credential decrypt fails, stop services and restore the original `CREDENTIALS_SECRET_KEY`.
- If migration fails, keep the failed database copy for investigation and restore the previous backup.
- If registry blobs are missing, restore registry storage from the same backup point as the database/config.
- If smoke fails after restore, do not trigger sync; follow `docs/rollback.md`.
