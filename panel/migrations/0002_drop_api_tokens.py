"""Remove deprecated panel API tokens.

Panel API access now uses browser login sessions only. Remote workers keep their
separate WORKER_TOKEN path and do not use this table.
"""

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS api_tokens")
