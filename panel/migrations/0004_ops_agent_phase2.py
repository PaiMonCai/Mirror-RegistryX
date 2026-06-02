"""Add ops-agent registry, task queue, and task event tables."""

import sqlite3


OPS_AGENT_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_agents (
    agent_id TEXT PRIMARY KEY,
    host_label TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'prod',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'offline',
    last_heartbeat_at TEXT,
    version TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    agent_id TEXT,
    requested_by TEXT NOT NULL DEFAULT 'panel',
    confirm_token TEXT,
    confirmed_at TEXT,
    lease_expires_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    timeout_seconds INTEGER NOT NULL DEFAULT 900,
    exit_code INTEGER,
    log_tail TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    message TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES ops_tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_ops_tasks_status_created ON ops_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ops_tasks_agent_status ON ops_tasks(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_ops_task_events_task_created ON ops_task_events(task_id, created_at);
"""


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(OPS_AGENT_SQLITE_SCHEMA)
