# Changelog

All notable changes to Mirror-Registry are recorded here.

This project follows a lightweight release log format:

- `Added` for new capabilities.
- `Changed` for behavior or UX changes.
- `Fixed` for bug fixes.
- `Security` for hardening and vulnerability-related work.
- `Operations` for deployment, smoke, migration, backup, and release process changes.

## Unreleased

### Added

- Linux/macOS production smoke script: `scripts/prod-smoke.sh`.
- Linux/macOS E2E smoke script: `scripts/e2e-smoke.sh`.
- Release quality gate: `scripts/release-check.sh`.
- Release, rollback, and backup/restore runbooks.
- `/api/security-checks` and security baseline cards on the Security page.

### Changed

- Backend application assembly now uses domain routers while preserving existing API paths.
- API errors use a unified envelope: `code`, `message`, `suggestion`, `details`.
- Observability summary includes queue health, worker heartbeat health, sync duration stats, failed mirror Top N, and webhook status.
- Browser and panel API access use HttpOnly session cookies; the legacy Bearer automation path has been removed.

### Security

- Revocable panel API tokens have been removed; panel write access now follows logged-in user roles.
- Session cookie `SameSite` is configurable through `SESSION_COOKIE_SAMESITE`.
- Security checks warn or fail on weak admin password, missing credentials secret, and insecure cookie settings.
- Sensitive export and diagnostic paths retain redaction for secrets, tokens, passwords, and encrypted credential data.

### Operations

- SQLite schema migrations are versioned through `schema_migrations` and `panel/migrations/`.
- Production smoke checks validate `.env`, compose configuration, session login, Registry `/v2/`, diagnostics, and backup/restore verification.

## Release template

Copy this block when cutting a release:

```markdown
## vX.Y.Z - YYYY-MM-DD

### Added

- ...

### Changed

- ...

### Fixed

- ...

### Security

- ...

### Operations

- ...
```
