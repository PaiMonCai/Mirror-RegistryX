"""Add release, trust scan, promotion, rollback, and restore drill tables."""

import sqlite3


TRUST_ROLLBACK_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mirror_releases (
    id TEXT PRIMARY KEY,
    mirror_source TEXT NOT NULL,
    source_image TEXT NOT NULL,
    target_image TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    target_digest TEXT NOT NULL,
    target_repo TEXT NOT NULL,
    target_tag TEXT NOT NULL,
    rule_snapshot_json TEXT NOT NULL DEFAULT '{}',
    policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
    push_run_id INTEGER,
    push_event_id INTEGER,
    parent_release_id TEXT,
    release_type TEXT NOT NULL DEFAULT 'mirror_push',
    trust_status TEXT NOT NULL DEFAULT 'unknown',
    scan_status TEXT NOT NULL DEFAULT 'not_scanned',
    scanner TEXT,
    scanner_version TEXT,
    severity_critical INTEGER NOT NULL DEFAULT 0,
    severity_high INTEGER NOT NULL DEFAULT 0,
    severity_medium INTEGER NOT NULL DEFAULT 0,
    severity_low INTEGER NOT NULL DEFAULT 0,
    severity_unknown INTEGER NOT NULL DEFAULT 0,
    scan_report_path TEXT,
    sbom_path TEXT,
    metadata_path TEXT,
    signature_status TEXT NOT NULL DEFAULT 'skipped',
    signature_subject TEXT,
    signature_issuer TEXT,
    signature_checked_at TEXT,
    bypass_reason TEXT,
    bypassed_by TEXT,
    bypassed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS image_scan_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id TEXT NOT NULL,
    image_ref TEXT NOT NULL,
    scanner TEXT NOT NULL DEFAULT 'trivy',
    status TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    timeout_seconds INTEGER NOT NULL DEFAULT 1800,
    exit_code INTEGER,
    message TEXT,
    log_tail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT,
    message TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promotion_policies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_rule TEXT NOT NULL,
    target_image TEXT NOT NULL,
    require_scan_pass INTEGER NOT NULL DEFAULT 1,
    block_on_critical INTEGER NOT NULL DEFAULT 1,
    block_on_high INTEGER NOT NULL DEFAULT 0,
    require_confirmation INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS restore_drills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    scope_json TEXT NOT NULL DEFAULT '{}',
    report_json TEXT NOT NULL DEFAULT '{}',
    ops_task_id INTEGER,
    requested_by TEXT NOT NULL DEFAULT 'panel',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_mirror_releases_source_created ON mirror_releases(mirror_source, created_at);
CREATE INDEX IF NOT EXISTS idx_mirror_releases_target_created ON mirror_releases(target_image, created_at);
CREATE INDEX IF NOT EXISTS idx_mirror_releases_trust ON mirror_releases(trust_status, scan_status);
CREATE INDEX IF NOT EXISTS idx_image_scan_tasks_status ON image_scan_tasks(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_release_events_release ON release_events(release_id, created_at);
"""


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(TRUST_ROLLBACK_SQLITE_SCHEMA)
