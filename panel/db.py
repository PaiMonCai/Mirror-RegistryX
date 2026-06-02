import importlib
import os
import pkgutil
import sqlite3
from pathlib import Path

from fastapi import HTTPException

try:
    from sqlalchemy import create_engine, text
except ImportError:  # pragma: no cover - exercised only when external DB deps are absent
    create_engine = None
    text = None


def database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:////data/mirror-registry.db")


def database_backend(url: str | None = None) -> str:
    value = url or database_url()
    if value.startswith("sqlite"):
        return "sqlite"
    if value.startswith("postgresql") or value.startswith("postgres"):
        return "postgresql"
    if value.startswith("mysql"):
        return "mysql"
    return "unknown"


def database_path(url: str | None = None) -> Path:
    value = url or database_url()
    if value.startswith("sqlite:///"):
        return Path(value.removeprefix("sqlite:///"))
    return Path(value)


DB_PATH = database_path()
ENGINES = {}


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mirrors (
    source TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_digest TEXT,
    registry TEXT NOT NULL DEFAULT 'local',
    mirror_group TEXT NOT NULL DEFAULT 'default',
    project TEXT NOT NULL DEFAULT 'default',
    environment TEXT NOT NULL DEFAULT 'local',
    namespace TEXT NOT NULL DEFAULT 'library',
    mode TEXT NOT NULL DEFAULT 'auto_push',
    check_interval_minutes INTEGER NOT NULL DEFAULT 30,
    next_check_at TEXT,
    last_checked_at TEXT,
    last_source_digest TEXT,
    last_target_digest TEXT,
    last_change_at TEXT,
    last_push_at TEXT,
    pending_push_digest TEXT,
    pending_push_target TEXT,
    push_status TEXT NOT NULL DEFAULT 'idle',
    check_failures INTEGER NOT NULL DEFAULT 0,
    push_failures INTEGER NOT NULL DEFAULT 0,
    next_push_at TEXT,
    last_error TEXT,
    allow_latest_push INTEGER NOT NULL DEFAULT 0,
    source_credential_id TEXT,
    target_credential_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    only_source TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS sync_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    copy_target TEXT,
    status TEXT NOT NULL,
    old_digest TEXT,
    new_digest TEXT,
    step TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms INTEGER,
    FOREIGN KEY(run_id) REFERENCES sync_runs(id)
);

CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    sources TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_id INTEGER,
    message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    task_type TEXT NOT NULL DEFAULT 'sync',
    mirror_source TEXT,
    mirror_target TEXT,
    digest TEXT,
    claimed_by TEXT,
    claimed_at TEXT,
    lease_expires_at TEXT
);

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

CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    labels TEXT NOT NULL,
    environment TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    status TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    version TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id TEXT NOT NULL,
    queue_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    finished_at TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS log_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    run_id INTEGER,
    source TEXT,
    target TEXT,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deletion_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    tag TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(repo, tag)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    registry_host TEXT NOT NULL,
    username TEXT NOT NULL,
    encrypted_secret TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tag_protection_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_pattern TEXT NOT NULL,
    tag_pattern TEXT NOT NULL,
    environment TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retention_policies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_pattern TEXT NOT NULL,
    environment TEXT NOT NULL,
    keep_last INTEGER NOT NULL,
    max_age_days INTEGER,
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_push_policies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    cron TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    allow_latest INTEGER NOT NULL DEFAULT 0,
    source_credential_id TEXT,
    target_credential_id TEXT,
    last_run_at TEXT,
    next_run_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_stats (
    repo TEXT NOT NULL,
    tag TEXT NOT NULL,
    manifest_digest TEXT,
    logical_size_bytes INTEGER NOT NULL DEFAULT 0,
    deduplicated_size_bytes INTEGER NOT NULL DEFAULT 0,
    shared_blob_count INTEGER NOT NULL DEFAULT 0,
    platforms TEXT NOT NULL,
    blobs TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repo, tag)
);

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

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS mirrors (
        source VARCHAR(255) PRIMARY KEY,
        target VARCHAR(255) NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_digest TEXT,
        registry VARCHAR(64) NOT NULL DEFAULT 'local',
        mirror_group VARCHAR(64) NOT NULL DEFAULT 'default',
        project VARCHAR(64) NOT NULL DEFAULT 'default',
        environment VARCHAR(64) NOT NULL DEFAULT 'local',
        namespace VARCHAR(128) NOT NULL DEFAULT 'library',
        mode VARCHAR(32) NOT NULL DEFAULT 'auto_push',
        check_interval_minutes INTEGER NOT NULL DEFAULT 30,
        next_check_at VARCHAR(64),
        last_checked_at VARCHAR(64),
        last_source_digest TEXT,
        last_target_digest TEXT,
        last_change_at VARCHAR(64),
        last_push_at VARCHAR(64),
        pending_push_digest TEXT,
        pending_push_target TEXT,
        push_status VARCHAR(32) NOT NULL DEFAULT 'idle',
        check_failures INTEGER NOT NULL DEFAULT 0,
        push_failures INTEGER NOT NULL DEFAULT 0,
        next_push_at VARCHAR(64),
        last_error TEXT,
        allow_latest_push INTEGER NOT NULL DEFAULT 0,
        source_credential_id VARCHAR(64),
        target_credential_id VARCHAR(64),
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key VARCHAR(255) PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_runs (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        reason VARCHAR(255) NOT NULL,
        status VARCHAR(64) NOT NULL,
        only_source TEXT,
        started_at VARCHAR(64) NOT NULL,
        ended_at VARCHAR(64),
        total INTEGER NOT NULL DEFAULT 0,
        updated INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_run_items (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        run_id INTEGER NOT NULL,
        source VARCHAR(255) NOT NULL,
        target VARCHAR(255) NOT NULL,
        copy_target TEXT,
        status VARCHAR(64) NOT NULL,
        old_digest TEXT,
        new_digest TEXT,
        step TEXT,
        error TEXT,
        started_at VARCHAR(64) NOT NULL,
        ended_at VARCHAR(64),
        duration_ms INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_queue (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        reason VARCHAR(255) NOT NULL,
        sources TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 100,
        status VARCHAR(64) NOT NULL,
        dedupe_key VARCHAR(255) NOT NULL,
        scheduled_at VARCHAR(64) NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        run_id INTEGER,
        message TEXT,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL,
        started_at VARCHAR(64),
        finished_at VARCHAR(64),
        task_type VARCHAR(32) NOT NULL DEFAULT 'sync',
        mirror_source VARCHAR(255),
        mirror_target TEXT,
        digest TEXT,
        claimed_by VARCHAR(64),
        claimed_at VARCHAR(64),
        lease_expires_at VARCHAR(64)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mirror_events (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        mirror_id VARCHAR(255) NOT NULL,
        type VARCHAR(64) NOT NULL,
        status VARCHAR(64) NOT NULL,
        old_digest TEXT,
        new_digest TEXT,
        message TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workers (
        worker_id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        labels TEXT NOT NULL,
        environment VARCHAR(64) NOT NULL,
        capabilities TEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        last_heartbeat VARCHAR(64) NOT NULL,
        version VARCHAR(64),
        message TEXT,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS worker_claims (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        worker_id VARCHAR(64) NOT NULL,
        queue_id INTEGER NOT NULL,
        status VARCHAR(32) NOT NULL,
        claimed_at VARCHAR(64) NOT NULL,
        finished_at VARCHAR(64),
        message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS log_events (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        created_at VARCHAR(64) NOT NULL,
        level VARCHAR(64) NOT NULL,
        run_id INTEGER,
        source TEXT,
        target TEXT,
        message TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_state (
        key VARCHAR(255) PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deletion_marks (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        repo VARCHAR(255) NOT NULL,
        tag VARCHAR(128) NOT NULL,
        reason TEXT,
        created_at VARCHAR(64) NOT NULL,
        UNIQUE(repo, tag)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        created_at VARCHAR(64) NOT NULL,
        actor VARCHAR(128) NOT NULL,
        action VARCHAR(128) NOT NULL,
        resource_type VARCHAR(128) NOT NULL,
        resource_id VARCHAR(255) NOT NULL,
        detail TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS credentials (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        registry_host VARCHAR(255) NOT NULL,
        username VARCHAR(255) NOT NULL,
        encrypted_secret TEXT NOT NULL,
        scope VARCHAR(16) NOT NULL,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_protection_rules (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        repo_pattern VARCHAR(255) NOT NULL,
        tag_pattern VARCHAR(128) NOT NULL,
        environment VARCHAR(64) NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        reason TEXT,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retention_policies (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        repo_pattern VARCHAR(255) NOT NULL,
        environment VARCHAR(64) NOT NULL,
        keep_last INTEGER NOT NULL,
        max_age_days INTEGER,
        enabled INTEGER NOT NULL DEFAULT 0,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduled_push_policies (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        source VARCHAR(255) NOT NULL,
        target VARCHAR(255) NOT NULL,
        cron VARCHAR(120) NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0,
        allow_latest INTEGER NOT NULL DEFAULT 0,
        source_credential_id VARCHAR(64),
        target_credential_id VARCHAR(64),
        last_run_at VARCHAR(64),
        next_run_at VARCHAR(64),
        last_error TEXT,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS storage_stats (
        repo VARCHAR(255) NOT NULL,
        tag VARCHAR(128) NOT NULL,
        manifest_digest VARCHAR(255),
        logical_size_bytes INTEGER NOT NULL DEFAULT 0,
        deduplicated_size_bytes INTEGER NOT NULL DEFAULT 0,
        shared_blob_count INTEGER NOT NULL DEFAULT 0,
        platforms TEXT NOT NULL,
        blobs TEXT NOT NULL,
        updated_at VARCHAR(64) NOT NULL,
        PRIMARY KEY(repo, tag)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ops_agents (
        agent_id VARCHAR(64) PRIMARY KEY,
        host_label VARCHAR(120) NOT NULL,
        environment VARCHAR(64) NOT NULL DEFAULT 'prod',
        capabilities_json TEXT NOT NULL DEFAULT '[]',
        status VARCHAR(32) NOT NULL DEFAULT 'offline',
        last_heartbeat_at VARCHAR(64),
        version VARCHAR(64),
        message TEXT,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ops_tasks (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        action VARCHAR(64) NOT NULL,
        params_json TEXT NOT NULL DEFAULT '{}',
        status VARCHAR(32) NOT NULL,
        agent_id VARCHAR(64),
        requested_by VARCHAR(120) NOT NULL DEFAULT 'panel',
        confirm_token VARCHAR(128),
        confirmed_at VARCHAR(64),
        lease_expires_at VARCHAR(64),
        started_at VARCHAR(64),
        finished_at VARCHAR(64),
        timeout_seconds INTEGER NOT NULL DEFAULT 900,
        exit_code INTEGER,
        log_tail TEXT,
        error TEXT,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ops_task_events (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        task_id INTEGER NOT NULL,
        type VARCHAR(64) NOT NULL,
        message TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        username VARCHAR(120) PRIMARY KEY,
        password_hash TEXT NOT NULL,
        role VARCHAR(32) NOT NULL DEFAULT 'admin',
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id VARCHAR(128) PRIMARY KEY,
        username VARCHAR(120) NOT NULL,
        created_at VARCHAR(64) NOT NULL,
        expires_at VARCHAR(64) NOT NULL,
        last_seen_at VARCHAR(64) NOT NULL
    )
    """,
]

MYSQL_SCHEMA_STATEMENTS = [
    statement.replace("INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", "INTEGER PRIMARY KEY AUTO_INCREMENT")
    .replace("key VARCHAR(255) PRIMARY KEY", "`key` VARCHAR(255) PRIMARY KEY")
    for statement in POSTGRES_SCHEMA_STATEMENTS
]


def connect_db() -> sqlite3.Connection:
    url = database_url()
    if database_backend(url) != "sqlite":
        raise RuntimeError("connect_db is only used for the default SQLite backend")
    db_path = database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def available_migrations() -> list[tuple[str, object]]:
    package = importlib.import_module("panel.migrations")
    migrations = []
    for module_info in pkgutil.iter_modules(package.__path__):
        name = module_info.name
        if not name[:1].isdigit():
            continue
        migrations.append((name, importlib.import_module(f"panel.migrations.{name}")))
    return sorted(migrations, key=lambda item: item[0])


def applied_migration_versions(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    }


def run_migrations(conn: sqlite3.Connection) -> None:
    ensure_schema_migrations(conn)
    applied = applied_migration_versions(conn)
    for version, module in available_migrations():
        if version in applied:
            continue
        try:
            conn.execute("BEGIN")
            module.upgrade(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, now_iso()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db(conn: sqlite3.Connection) -> None:
    run_migrations(conn)


def external_engine():
    url = database_url()
    if url in ENGINES:
        return ENGINES[url]
    if create_engine is None or text is None:
        raise HTTPException(500, "外部数据库需要安装 SQLAlchemy 和对应 PostgreSQL/MySQL 驱动")
    engine = create_engine(url, pool_pre_ping=True, future=True)
    backend = database_backend(url)
    statements = MYSQL_SCHEMA_STATEMENTS if backend == "mysql" else POSTGRES_SCHEMA_STATEMENTS
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    ENGINES[url] = engine
    return engine


def bind_sql(sql: str, params: tuple) -> tuple[str, dict]:
    bound = {}
    converted = sql
    for index, value in enumerate(params):
        name = f"p{index}"
        converted = converted.replace("?", f":{name}", 1)
        bound[name] = value
    return converted, bound


def mysql_compatible_sql(sql: str) -> str:
    converted = sql
    converted = converted.replace("settings(key,", "settings(`key`,")
    converted = converted.replace("runtime_state(key,", "runtime_state(`key`,")
    converted = converted.replace("SELECT key,", "SELECT `key`,")
    converted = converted.replace("WHERE key =", "WHERE `key` =")
    converted = converted.replace(
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
    )
    converted = converted.replace(
        "ON CONFLICT(source) DO UPDATE SET\n            target = excluded.target,\n            last_digest = COALESCE(excluded.last_digest, mirrors.last_digest),\n            updated_at = excluded.updated_at",
        "ON DUPLICATE KEY UPDATE target = VALUES(target), last_digest = COALESCE(VALUES(last_digest), last_digest), updated_at = VALUES(updated_at)",
    )
    converted = converted.replace(
        "ON CONFLICT(repo, tag) DO UPDATE SET reason = excluded.reason, created_at = excluded.created_at",
        "ON DUPLICATE KEY UPDATE reason = VALUES(reason), created_at = VALUES(created_at)",
    )
    return converted


def db_rows(sql: str, params: tuple = ()) -> list[dict]:
    url = database_url()
    if database_backend(url) != "sqlite":
        try:
            engine = external_engine()
            if database_backend(url) == "mysql":
                sql = mysql_compatible_sql(sql)
            converted, bound = bind_sql(sql, params)
            with engine.begin() as conn:
                result = conn.execute(text(converted), bound)
                return [dict(row._mapping) for row in result.fetchall()]
        except Exception as exc:
            raise HTTPException(500, f"外部数据库读取失败: {exc}") from exc
    try:
        with connect_db() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.Error as exc:
        raise HTTPException(500, f"数据库读取失败: {exc}") from exc


def db_one(sql: str, params: tuple = ()) -> dict | None:
    rows = db_rows(sql, params)
    return rows[0] if rows else None


def db_execute(sql: str, params: tuple = ()) -> int:
    url = database_url()
    if database_backend(url) != "sqlite":
        try:
            engine = external_engine()
            if database_backend(url) == "mysql":
                sql = mysql_compatible_sql(sql)
            converted, bound = bind_sql(sql, params)
            with engine.begin() as conn:
                result = conn.execute(text(converted), bound)
                lastrowid = int(getattr(result, "lastrowid", 0) or 0)
                if not lastrowid and sql.lstrip().upper().startswith("INSERT"):
                    backend = database_backend(url)
                    if backend == "postgresql":
                        lastrowid = int(conn.execute(text("SELECT LASTVAL()")).scalar() or 0)
                    elif backend == "mysql":
                        lastrowid = int(conn.execute(text("SELECT LAST_INSERT_ID()")).scalar() or 0)
                return lastrowid
        except Exception as exc:
            raise HTTPException(500, f"外部数据库写入失败: {exc}") from exc
    try:
        with connect_db() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return int(cursor.lastrowid or 0)
    except sqlite3.Error as exc:
        raise HTTPException(500, f"数据库写入失败: {exc}") from exc
