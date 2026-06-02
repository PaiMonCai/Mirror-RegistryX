"""Add governance policy, discovery, bulk operation, and runtime linkage tables."""

import sqlite3


GOVERNANCE_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mirror_rule_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_registry_pattern TEXT NOT NULL DEFAULT '*',
    source_namespace_pattern TEXT NOT NULL DEFAULT '*',
    source_repo_pattern TEXT NOT NULL DEFAULT '*',
    target_registry TEXT NOT NULL,
    target_namespace_template TEXT,
    mode TEXT NOT NULL DEFAULT 'auto_push',
    check_interval_minutes INTEGER NOT NULL DEFAULT 30,
    allow_latest_push INTEGER NOT NULL DEFAULT 0,
    source_credential_id TEXT,
    target_credential_id TEXT,
    notification_policy_id TEXT,
    push_window_id TEXT,
    retention_policy_id TEXT,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    location TEXT,
    content TEXT,
    scan_interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scanned_at TEXT,
    next_scan_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    source_image TEXT NOT NULL,
    location TEXT,
    recommended_target TEXT,
    recommended_template_id TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    existing_rule_source TEXT,
    ignored_reason TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_policies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    webhook_url_encrypted TEXT,
    events_json TEXT NOT NULL DEFAULT '{}',
    min_severity TEXT NOT NULL DEFAULT 'warning',
    dedupe_seconds INTEGER NOT NULL DEFAULT 1800,
    quiet_hours_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_windows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    allow_windows_json TEXT NOT NULL DEFAULT '[]',
    freeze_windows_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bulk_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    requested_by TEXT NOT NULL DEFAULT 'panel',
    total INTEGER NOT NULL DEFAULT 0,
    succeeded INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS bulk_operation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    mirror_source TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(operation_id) REFERENCES bulk_operations(id)
);

CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id TEXT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    dedupe_key TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_status ON discovery_candidates(status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source_image ON discovery_candidates(source_id, source_image);
CREATE INDEX IF NOT EXISTS idx_bulk_operation_items_operation ON bulk_operation_items(operation_id);
CREATE INDEX IF NOT EXISTS idx_notification_events_policy ON notification_events(policy_id, created_at);
"""

MIRROR_PHASE3_COLUMNS = [
    ("template_id", "TEXT"),
    ("notification_policy_id", "TEXT"),
    ("push_window_id", "TEXT"),
    ("retention_policy_id", "TEXT"),
    ("governance_status", "TEXT NOT NULL DEFAULT 'active'"),
    ("governance_note", "TEXT"),
]

MIRROR_GROUP_PHASE3_COLUMNS = [
    ("notification_policy_id", "TEXT"),
    ("push_window_id", "TEXT"),
]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_column_if_missing(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(GOVERNANCE_SQLITE_SCHEMA)
    for name, definition in MIRROR_PHASE3_COLUMNS:
        add_column_if_missing(conn, "mirrors", name, definition)
