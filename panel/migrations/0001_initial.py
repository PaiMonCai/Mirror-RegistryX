"""Create the current panel schema.

This migration is intentionally idempotent so existing pre-migration SQLite
installations can be stamped safely while still receiving any missing tables.
"""

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    from panel.db import SQLITE_SCHEMA

    conn.executescript(SQLITE_SCHEMA)
