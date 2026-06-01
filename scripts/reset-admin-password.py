#!/usr/bin/env python3
"""Reset a Mirror-RegistryX local user password from the terminal."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from panel.auth import password_hash  # noqa: E402
from panel.db import database_backend, database_path, database_url  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset a panel user's password in the configured database.")
    parser.add_argument("username", nargs="?", default=os.getenv("ADMIN_USERNAME", "admin"), help="user to reset, default: ADMIN_USERNAME or admin")
    parser.add_argument("--password", help="new password; omit to prompt securely")
    parser.add_argument("--database-url", help="override DATABASE_URL, e.g. sqlite:////data/mirror-registry.db")
    return parser.parse_args()


def read_password(value: str | None) -> str:
    if value is not None:
        password = value
    else:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match.")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    return password


def main() -> int:
    args = parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    url = database_url()
    if database_backend(url) != "sqlite":
        raise SystemExit("Terminal reset currently supports SQLite DATABASE_URL only. Use the panel API for external databases.")

    import sqlite3

    db_path = database_path(url)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    username = args.username.strip()
    if not username:
        raise SystemExit("Username is required.")
    new_hash = password_hash(read_password(args.password))

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise SystemExit(f"User not found: {username}")
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(microsecond=0).isoformat()
        conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?", (new_hash, now, username))
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        conn.commit()

    print(f"Password reset for user: {username}")
    print("Existing sessions for this user have been invalidated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
