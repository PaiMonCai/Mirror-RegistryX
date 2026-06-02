"""Add mirror monitoring rule state and typed queue metadata."""

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    from mirror_registry_core.mirror_rules import ensure_sqlite_phase1_schema

    ensure_sqlite_phase1_schema(conn)
