"""Terminal password reset utility for Mirror Registry panel users."""

from __future__ import annotations

import argparse
import getpass
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .auth import audit_log, password_hash, public_user, user_row
from .db import db_execute


class PasswordResetError(RuntimeError):
    """Raised when a terminal password reset cannot be completed."""


@dataclass
class PasswordResetResult:
    username: str
    created: bool
    sessions_invalidated: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset a panel user's password from the terminal.")
    parser.add_argument(
        "username",
        nargs="?",
        default=os.getenv("ADMIN_USERNAME", "admin"),
        help="user to reset, default: ADMIN_USERNAME or admin",
    )
    parser.add_argument("--password", help="new password; omit to prompt securely")
    parser.add_argument("--database-url", help="override DATABASE_URL, e.g. sqlite:////data/mirror-registry.db")
    parser.add_argument("--create-if-missing", action="store_true", help="create the user when it does not already exist")
    parser.add_argument("--role", choices=["admin", "operator", "viewer"], default="admin", help="role for --create-if-missing")
    return parser.parse_args(argv)


def read_password(value: str | None) -> str:
    if value is None:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise PasswordResetError("Passwords do not match.")
    else:
        password = value
    if len(password) < 8:
        raise PasswordResetError("Password must be at least 8 characters.")
    return password


def reset_panel_password(
    username: str,
    password: str,
    *,
    create_if_missing: bool = False,
    role: str = "admin",
    actor: str = "terminal",
) -> PasswordResetResult:
    clean_username = username.strip()
    if not clean_username:
        raise PasswordResetError("Username is required.")
    if role not in {"admin", "operator", "viewer"}:
        raise PasswordResetError("Role must be admin, operator, or viewer.")

    existing = user_row(clean_username)
    new_hash = password_hash(password)
    if not existing:
        if not create_if_missing:
            raise PasswordResetError(f"User not found: {clean_username}")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        db_execute(
            "INSERT INTO users(username, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (clean_username, new_hash, role, now, now),
        )
        audit_log("password_reset", "user", clean_username, {"mode": "terminal", "created": True, "role": role}, actor=actor)
        return PasswordResetResult(username=clean_username, created=True, sessions_invalidated=False)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    db_execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?", (new_hash, now, clean_username))
    db_execute("DELETE FROM sessions WHERE username = ?", (clean_username,))
    audit_log("password_reset", "user", clean_username, {"mode": "terminal", "created": False}, actor=actor)
    return PasswordResetResult(username=public_user(user_row(clean_username))["username"], created=False, sessions_invalidated=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    try:
        password = read_password(args.password)
        result = reset_panel_password(
            args.username,
            password,
            create_if_missing=args.create_if_missing,
            role=args.role,
        )
    except PasswordResetError as exc:
        raise SystemExit(str(exc)) from exc

    action = "created and password reset" if result.created else "password reset"
    print(f"User {action}: {result.username}")
    if result.sessions_invalidated:
        print("Existing sessions for this user have been invalidated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
