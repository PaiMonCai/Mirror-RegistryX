# Mirror Monitoring Phase 1 Plan

## Goal

Build Mirror-RegistryX into a simple self-hosted mirror monitoring appliance:

1. Operators add mirror rules in the panel.
2. The service periodically checks source image digests.
3. Changed images are automatically pushed to the configured target registry when allowed.
4. Failures are retried with visible state and concise Feishu notifications.
5. The panel remains simple by default, while advanced controls stay available behind explicit settings.

This phase focuses on the monitoring, update, and push loop. Host operations through an `ops-agent` are planned for a later phase.

## Confirmed Product Decisions

- Deployment target is a private, self-hosted appliance, not a multi-tenant SaaS.
- The core model is a mirror rule: one `source` image to one `target` image.
- Monitoring scope is the minimum loop: digest/tag availability and pullability checks.
- Rules are configured manually in the panel. Auto-discovery can suggest rules later.
- Rule modes are `auto_push` and `monitor_only`.
- Scheduling is rule-level, driven by `next_check_at`, with a global scheduler scanning due rules.
- Check tasks and push tasks are separate.
- Current state and append-only history are both required.
- Target tags are updated in place by default. Archive tags are not part of phase 1.
- Failed push tasks preserve `pending_push_digest` and retry independently of future checks.
- Notifications are global by default with rule-level overrides later.
- Credentials remain independent records referenced by rules.
- `skopeo` remains the first copy engine, wrapped behind a replaceable interface.
- Concurrency uses separate check and push limits, plus per-registry throttling and per-rule locking.
- The database becomes the runtime source of truth. `config/mirrors.yml` becomes seed/import/export data.
- `latest` can be monitored, but automatic pushing of `latest` requires explicit `allow_latest_push`.
- Source-missing tags are reported, not automatically deleted.

## Non-Goals

- Multi-tenant accounts, billing, quotas, or organization isolation.
- Vulnerability scanning, SBOM, image signing, or policy attestation.
- Automatic deletion of target images when source tags disappear.
- Multi-target rules. Users can create multiple rules for multiple targets.
- Historical archive tags by default.
- Arbitrary shell or arbitrary Docker Compose commands from the panel.
- The `ops-agent` implementation. It remains Phase 2.

## User Flow

### Add Rule

The default form should ask for:

- Source image, for example `docker.io/library/nginx:1.27`.
- Target registry, for example `registry.local`.
- Namespace, optional.
- Credential references, optional.
- Auto push toggle.

The panel auto-generates the target image:

```text
source: docker.io/library/nginx:1.27
target registry: registry.local
namespace: library
generated target: registry.local/library/nginx:1.27
```

Advanced users can override the full target image.

### Monitor and Push

```text
global scheduler wakes every minute
  -> finds mirror rules with next_check_at <= now
  -> enqueues check tasks

check task
  -> inspect source digest
  -> compare with last_source_digest
  -> record check result
  -> if changed, record change event
  -> if mode=auto_push and policy allows it, enqueue push task

push task
  -> copy source to target with skopeo
  -> verify target digest when possible
  -> update rule state
  -> emit event and notification if needed
```

### Failure Handling

Check failures:

- Do not change `last_source_digest`.
- Increment check failure count.
- Schedule next check with backoff.
- Notify only after a threshold.

Push failures:

- Preserve `pending_push_digest`.
- Increment push attempts.
- Schedule `next_push_at` with backoff.
- Notify on failure and degraded state.

## Data Model Changes

Extend the existing `mirrors` table rather than introducing a parallel rule table.

Suggested columns:

```text
mode TEXT NOT NULL DEFAULT 'auto_push'
check_interval_minutes INTEGER NOT NULL DEFAULT 30
next_check_at TEXT
last_checked_at TEXT
last_source_digest TEXT
last_target_digest TEXT
last_change_at TEXT
last_push_at TEXT
pending_push_digest TEXT
pending_push_target TEXT
push_status TEXT NOT NULL DEFAULT 'idle'
check_failures INTEGER NOT NULL DEFAULT 0
push_failures INTEGER NOT NULL DEFAULT 0
next_push_at TEXT
last_error TEXT
allow_latest_push INTEGER NOT NULL DEFAULT 0
source_credential_id TEXT
target_credential_id TEXT
```

Recommended status values:

```text
push_status = idle | pending | running | succeeded | failed | degraded | skipped
```

Add append-only event history if current tables are not enough:

```text
mirror_events
- id
- mirror_id
- type: check | change_detected | push | notify | source_missing
- status: succeeded | skipped | failed | degraded
- old_digest
- new_digest
- message
- detail_json
- created_at
```

Existing `sync_runs`, `sync_run_items`, `sync_queue`, and `audit_logs` should be reused where practical. Avoid duplicating run history if `sync_run_items` can represent push attempts cleanly.

## Scheduling Design

Use one global scheduler loop:

```text
every 60 seconds:
  due_rules = SELECT * FROM mirrors
              WHERE enabled = 1
                AND next_check_at <= now
                AND no active check lock
  enqueue check tasks
```

After each check:

```text
next_check_at = now + check_interval_minutes
```

For failures, use bounded backoff:

```text
check backoff: 1m, 5m, 15m, 1h
push backoff: 5m, 30m, 2h
```

## Queue Design

Phase 1 can extend the existing `sync_queue` or introduce a typed queue. The important requirement is task separation:

```text
task_type = check | push
mirror_id
source
target
digest
status
priority
scheduled_at
attempts
claimed_by
claimed_at
lease_expires_at
message
```

Workers must recover stale tasks when `lease_expires_at < now`.

Concurrency defaults:

```text
global_check_concurrency = 10
global_push_concurrency = 2
per_registry_check_concurrency = 3
per_registry_push_concurrency = 1
```

The same mirror rule must not run two check/push tasks concurrently.

## Copy Engine

Keep `skopeo` as the default engine and wrap it:

```text
CopyEngine.inspect(source, credential) -> digest
CopyEngine.copy(source, target, source_credential, target_credential) -> result
CopyEngine.verify(target, expected_digest) -> result
```

This keeps future support for `oras`, `crane`, or Docker-based engines possible without changing rule logic.

## Notification Strategy

Phase 1 event types:

```text
change_detected
push_succeeded
push_failed
check_failed
rule_degraded
```

Default behavior:

- `monitor_only` change detected: notify.
- `auto_push` change detected: do not notify separately.
- Push succeeded: default off.
- Push failed: default on.
- Check failed: notify after threshold.
- Rule degraded: default on.

Keep the existing global Feishu webhook. Rule-level override can be added after the rule model is stable.

## API Changes

Mirror rule APIs should expose simple defaults first:

```text
GET    /api/mirrors
POST   /api/mirrors
PUT    /api/mirrors/{id}
DELETE /api/mirrors/{id}
POST   /api/mirrors/{id}/check
POST   /api/mirrors/{id}/push
POST   /api/mirrors/{id}/pause
POST   /api/mirrors/{id}/resume
POST   /api/mirrors/{id}/skip-pending-push
```

Operational views:

```text
GET /api/mirror-events?mirror_id=&limit=
GET /api/sync-queue?type=check|push
```

Manual push should use a known digest when possible:

```text
POST /api/mirrors/{id}/push
body: { digest?: string }
```

## Frontend Changes

Keep the panel simple:

- Mirror rule list shows source, target, mode, last digest, next check, push status, last error.
- Add/edit form defaults most fields.
- Advanced section contains interval, credentials, allow latest push, target override.
- Detail drawer or page shows event history and pending push digest.
- Runs page separates check tasks and push tasks with clear labels.
- Settings page keeps global interval/concurrency defaults and Feishu webhook.

Avoid exposing queue internals in the default flow unless a rule is failing.

## Migration and Compatibility

Migration strategy:

1. Add columns to `mirrors` with safe defaults.
2. Backfill:
   - `mode = auto_push`
   - `check_interval_minutes` from existing settings.
   - `last_source_digest` from `last_digest`.
   - `next_check_at = now` for enabled rules.
3. Keep `config/mirrors.yml` import/export compatible.
4. Panel writes to DB.
5. Worker reads DB as source of truth.

`config/mirrors.yml` remains useful for:

- First-run seed.
- Backup/export.
- Manual recovery.

It should not remain the runtime authority after Phase 1.

## Test Plan

Backend tests:

- Migrates old mirrors into rule defaults.
- Enqueues due check tasks from `next_check_at`.
- Check task skips unchanged digest.
- Check task records change and queues push when `auto_push`.
- `monitor_only` records change without push.
- `latest` auto push is blocked unless `allow_latest_push=true`.
- Push failure preserves `pending_push_digest`.
- Push retry uses pending digest even when source digest is unchanged.
- Source missing does not delete target.
- Notification dedupe works for failure events.

Worker tests:

- `CopyEngine.inspect` errors do not mutate last digest.
- Push success updates last source and target digest.
- Lease recovery requeues stale tasks.
- Per-rule lock prevents duplicate task execution.

Frontend/type tests:

- Mirror form auto-generates target.
- Advanced target override works.
- Rule mode and latest protection are visible.
- Failed/pending push state is visible and actionable.

End-to-end smoke:

1. Add `docker.io/library/busybox:latest` as `monitor_only`.
2. Check detects digest and does not push.
3. Enable `auto_push` with `allow_latest_push`.
4. Manual push succeeds to local registry.
5. A forced push failure leaves pending state and retry action.

## Implementation Slices

### Slice 1: Rule State Migration

- Extend `mirrors` schema.
- Add migration and tests.
- Update public mirror serialization.
- Keep old API responses compatible.

### Slice 2: Check Task

- Add task type or equivalent queue metadata.
- Implement due-rule scanner.
- Implement source digest inspection task.
- Update rule current state and event history.

### Slice 3: Push Task

- Split push execution from check execution.
- Preserve pending digest on failure.
- Add retry/backoff.
- Update run history.

### Slice 4: Panel Defaults

- Simplify add/edit mirror form.
- Auto-generate target.
- Add `mode`, interval, and latest protection controls.
- Show rule state clearly.

### Slice 5: Notifications

- Map event types to Feishu messages.
- Add threshold and dedupe behavior.
- Show last notification error in panel.

### Slice 6: Hardening

- Add lease recovery.
- Add per-rule locking.
- Add registry-level throttles.
- Add E2E smoke for one real mirror rule.

## Phase 1 Acceptance Criteria

Phase 1 is complete when these are true:

1. The panel can add a source-to-target mirror rule.
2. The service automatically checks due rules by `next_check_at`.
3. Unchanged digests are skipped and `last_checked_at` is recorded.
4. Changed digests generate a change event.
5. `auto_push` rules enqueue a push task when policy allows.
6. Push success updates source/target digest and push timestamps.
7. Push failure preserves pending digest and retries with backoff.
8. Feishu notifications are limited to failures, degraded state, and `monitor_only` updates by default.

