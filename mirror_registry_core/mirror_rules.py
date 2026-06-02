from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


MIRROR_MODES = {"auto_push", "monitor_only"}
PUSH_STATUSES = {"idle", "pending", "pending_window", "running", "succeeded", "failed", "degraded", "skipped"}

MIRROR_RULE_COLUMNS: list[tuple[str, str]] = [
    ("registry", "TEXT NOT NULL DEFAULT 'local'"),
    ("mirror_group", "TEXT NOT NULL DEFAULT 'default'"),
    ("project", "TEXT NOT NULL DEFAULT 'default'"),
    ("environment", "TEXT NOT NULL DEFAULT 'local'"),
    ("namespace", "TEXT NOT NULL DEFAULT 'library'"),
    ("mode", "TEXT NOT NULL DEFAULT 'auto_push'"),
    ("check_interval_minutes", "INTEGER NOT NULL DEFAULT 30"),
    ("next_check_at", "TEXT"),
    ("last_checked_at", "TEXT"),
    ("last_source_digest", "TEXT"),
    ("last_target_digest", "TEXT"),
    ("last_change_at", "TEXT"),
    ("last_push_at", "TEXT"),
    ("pending_push_digest", "TEXT"),
    ("pending_push_target", "TEXT"),
    ("push_status", "TEXT NOT NULL DEFAULT 'idle'"),
    ("check_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("push_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("next_push_at", "TEXT"),
    ("last_error", "TEXT"),
    ("allow_latest_push", "INTEGER NOT NULL DEFAULT 0"),
    ("source_credential_id", "TEXT"),
    ("target_credential_id", "TEXT"),
    ("template_id", "TEXT"),
    ("notification_policy_id", "TEXT"),
    ("push_window_id", "TEXT"),
    ("retention_policy_id", "TEXT"),
    ("governance_status", "TEXT NOT NULL DEFAULT 'active'"),
    ("governance_note", "TEXT"),
]

SYNC_QUEUE_COLUMNS: list[tuple[str, str]] = [
    ("task_type", "TEXT NOT NULL DEFAULT 'sync'"),
    ("mirror_source", "TEXT"),
    ("mirror_target", "TEXT"),
    ("digest", "TEXT"),
    ("claimed_by", "TEXT"),
    ("claimed_at", "TEXT"),
    ("lease_expires_at", "TEXT"),
]

MIRROR_EVENTS_SQLITE = """
CREATE TABLE IF NOT EXISTS mirror_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mirror_id TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    old_digest TEXT,
    new_digest TEXT,
    message TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def add_minutes(base_iso: str | None, minutes: int) -> str:
    base = parse_iso(base_iso) or datetime.now(timezone.utc).replace(microsecond=0)
    return (base + timedelta(minutes=max(0, int(minutes)))).replace(microsecond=0).isoformat()


def normalize_mode(value: object) -> str:
    mode = str(value or "auto_push").strip().lower()
    return mode if mode in MIRROR_MODES else "auto_push"


def normalize_push_status(value: object) -> str:
    status = str(value or "idle").strip().lower()
    return status if status in PUSH_STATUSES else "idle"


def bool_int(value: object) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    return 1 if str(value or "").strip().lower() in {"1", "true", "yes", "on"} else 0


def bounded_interval(value: object, default: int = 30) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 1440))


def image_repo_tag(image: str) -> tuple[str, str]:
    if ":" not in image:
        return image, ""
    repo, tag = image.rsplit(":", 1)
    if "/" in tag:
        return image, ""
    if "/" in repo:
        first, rest = repo.split("/", 1)
        if "." in first or ":" in first or first == "localhost":
            return rest, tag
    return repo, tag


def image_is_latest(image: str) -> bool:
    return image_repo_tag(image)[1] == "latest"


def source_ref_for_digest(source: str, digest: str | None) -> str:
    clean_digest = str(digest or "").strip()
    if not clean_digest:
        return source
    if "@" in source:
        return source
    if ":" not in source:
        return f"{source}@{clean_digest}"
    name, tag = source.rsplit(":", 1)
    if "/" in tag:
        return f"{source}@{clean_digest}"
    return f"{name}@{clean_digest}"


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_column_if_missing(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ensure_sqlite_phase1_schema(conn: sqlite3.Connection) -> None:
    for name, definition in MIRROR_RULE_COLUMNS:
        add_column_if_missing(conn, "mirrors", name, definition)
    for name, definition in SYNC_QUEUE_COLUMNS:
        add_column_if_missing(conn, "sync_queue", name, definition)
    conn.executescript(MIRROR_EVENTS_SQLITE)
    stamp = now_iso()
    conn.execute(
        """
        UPDATE mirrors
        SET mode = COALESCE(NULLIF(mode, ''), 'auto_push'),
            check_interval_minutes = CASE WHEN check_interval_minutes IS NULL OR check_interval_minutes < 1 THEN 30 ELSE check_interval_minutes END,
            next_check_at = CASE WHEN enabled = 1 AND next_check_at IS NULL THEN ? ELSE next_check_at END,
            last_source_digest = COALESCE(last_source_digest, last_digest),
            push_status = COALESCE(NULLIF(push_status, ''), 'idle'),
            check_failures = COALESCE(check_failures, 0),
            push_failures = COALESCE(push_failures, 0),
            allow_latest_push = COALESCE(allow_latest_push, 0),
            registry = COALESCE(NULLIF(registry, ''), 'local'),
            mirror_group = COALESCE(NULLIF(mirror_group, ''), 'default'),
            project = COALESCE(NULLIF(project, ''), 'default'),
            environment = COALESCE(NULLIF(environment, ''), 'local'),
            namespace = COALESCE(NULLIF(namespace, ''), 'library'),
            governance_status = COALESCE(NULLIF(governance_status, ''), 'active')
        """,
        (stamp,),
    )
    conn.execute("UPDATE sync_queue SET task_type = COALESCE(NULLIF(task_type, ''), 'sync')")
