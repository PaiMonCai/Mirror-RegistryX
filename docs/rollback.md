# Rollback Runbook

Use this when a Mirror-Registry release must be reverted.

## Rollback principles

- Prefer image tag rollback before data restore.
- Do not delete current volumes during the first rollback attempt.
- Preserve logs, `.env`, compose file, and database copy before making changes.
- If `CREDENTIALS_SECRET_KEY` is wrong or missing, stop and restore the original key first.

## 1. Capture current state

```bash
date -u +%FT%TZ
docker compose ps
docker compose logs --tail=300 panel sync registry > rollback-logs-$(date -u +%Y%m%dT%H%M%SZ).log
cp .env rollback-env-$(date -u +%Y%m%dT%H%M%SZ).env
```

Record:

- current `MIRROR_REGISTRY_IMAGE_TAG`
- previous known-good tag
- symptom and first failure time
- whether migrations ran
- whether sync jobs were active

## 2. Pause risky activity

If the panel is reachable, pause or cancel active sync queue items before rollback.

If the panel is not reachable, stop only the sync service first to avoid new writes:

```bash
docker compose stop sync
```

## 3. Roll back image tag

Set `.env` to the previous known-good image tag:

```dotenv
MIRROR_REGISTRY_IMAGE_TAG=vX.Y.Z
```

Then pull and recreate services:

```bash
docker compose pull panel sync
docker compose up -d panel sync
```

Check status:

```bash
docker compose ps
scripts/prod-smoke.sh --env-file .env --skip-sync
```

If smoke passes, keep monitoring queue, logs, and `/api/observability/summary` for at least one sync interval.

## 4. Restore data only if image rollback is insufficient

Data restore is more invasive. Use only when:

- migration corrupted or removed required data
- database cannot open after image rollback
- config volume was overwritten incorrectly
- registry storage state is inconsistent and cannot be repaired

Follow `docs/backup-restore.md` and restore these as a matched set from the same backup point:

- `config/` or `mirror-registry-config` volume
- `data/mirror-registry.db` or `mirror-registry-data` volume
- `data/sync-state.json`
- registry storage volume if artifact state must be reverted
- `.env`
- original `CREDENTIALS_SECRET_KEY`

## 5. Validate after rollback

Minimum validation:

```bash
scripts/prod-smoke.sh --env-file .env --skip-sync
```

Then confirm manually:

- login works
- `/api/status` returns expected mirror count
- `/api/sync-queue` has no stuck active tasks
- `/api/observability/summary` has no new critical warnings
- registry `/v2/` responds
- credentials can be decrypted if credentials are used

## 6. Resume sync

When the rollback is stable:

```bash
docker compose up -d sync
```

Run a low-risk sync or preflight first. Avoid mass replay until the cause is understood.

## 7. Post-rollback notes

Open a follow-up issue or changelog note with:

- failed version
- rollback target version
- root cause if known
- data restore used: yes/no
- commands run
- validation evidence
