import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from .db import db_execute, db_one, db_rows
from .errors import api_error_response
from .schemas import AccessUserIn, LoginIn


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


WORKER_TOKEN = os.getenv("WORKER_TOKEN", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_TTL_SECONDS = env_int("SESSION_TTL_SECONDS", 604800, 300, 60 * 60 * 24 * 30)
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "mirror_registry_session")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().lower()
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"

router = APIRouter(prefix="/api")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def audit_log(action: str, resource_type: str, resource_id: str, detail: dict | None = None, actor: str = "panel") -> None:
    db_execute(
        """
        INSERT INTO audit_logs(created_at, actor, action, resource_type, resource_id, detail)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now_iso(), actor, action, resource_type, resource_id, json.dumps(detail or {}, ensure_ascii=False)),
    )


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 200_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}


def normalize_role(value: str) -> str:
    role = (value or "viewer").strip().lower()
    if role not in {"admin", "operator", "viewer"}:
        raise HTTPException(400, "role 必须是 admin、operator 或 viewer")
    return role


def role_allows(actual: str, required: str) -> bool:
    return ROLE_ORDER.get(actual, -1) >= ROLE_ORDER.get(required, 0)


def admin_user_exists() -> bool:
    return bool(db_rows("SELECT username FROM users LIMIT 1"))


def ensure_admin_user() -> bool:
    if admin_user_exists():
        return True
    if not ADMIN_PASSWORD.strip():
        return False
    now = now_iso()
    db_execute(
        """
        INSERT INTO users(username, password_hash, role, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ADMIN_USERNAME, password_hash(ADMIN_PASSWORD), "admin", now, now),
    )
    audit_log("bootstrap_admin", "user", ADMIN_USERNAME, {"source": "environment"}, actor="system")
    return True


def user_row(username: str) -> dict | None:
    clean_username = username.strip()
    if not clean_username:
        return None
    return db_one("SELECT username, password_hash, role, created_at, updated_at FROM users WHERE username = ?", (clean_username,))


def public_user(row: dict) -> dict:
    return {
        "username": row["username"],
        "role": row["role"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_access_users() -> list[dict]:
    return [public_user(row) for row in db_rows("SELECT username, role, created_at, updated_at FROM users ORDER BY username")]


def upsert_access_user(body: AccessUserIn) -> dict:
    username = body.username.strip()
    role = normalize_role(body.role)
    existing = user_row(username)
    now = now_iso()
    if existing:
        if body.password:
            db_execute("UPDATE users SET password_hash = ?, role = ?, updated_at = ? WHERE username = ?", (password_hash(body.password), role, now, username))
        else:
            db_execute("UPDATE users SET role = ?, updated_at = ? WHERE username = ?", (role, now, username))
        audit_log("update", "user", username, {"role": role}, actor="panel")
    else:
        if not body.password:
            raise HTTPException(400, "创建用户必须提供 password")
        db_execute(
            "INSERT INTO users(username, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash(body.password), role, now, now),
        )
        audit_log("create", "user", username, {"role": role}, actor="panel")
    return public_user(user_row(username))


def delete_access_user(username: str) -> None:
    clean_username = username.strip()
    if clean_username == ADMIN_USERNAME:
        raise HTTPException(400, "不能删除环境变量初始化的默认管理员")
    if not user_row(clean_username):
        raise HTTPException(404, "用户不存在")
    db_execute("DELETE FROM users WHERE username = ?", (clean_username,))
    db_execute("DELETE FROM sessions WHERE username = ?", (clean_username,))
    audit_log("delete", "user", clean_username, {}, actor="panel")


def worker_token_valid(token: str | None) -> bool:
    return bool(WORKER_TOKEN and token and hmac.compare_digest(token, WORKER_TOKEN))


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(username: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = (now + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()
    db_execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
    db_execute(
        """
        INSERT INTO sessions(id, username, created_at, expires_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_hash(token), username, now.isoformat(), expires_at, now.isoformat()),
    )
    return token, expires_at


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    row = db_one(
        """
        SELECT id, username, created_at, expires_at, last_seen_at
        FROM sessions
        WHERE id = ? AND expires_at > ?
        """,
        (session_hash(token), now_iso()),
    )
    if not row:
        return None
    db_execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now_iso(), row["id"]))
    user = user_row(row["username"])
    if not user:
        return None
    return {"username": row["username"], "role": user["role"], "auth_method": "session", "expires_at": row["expires_at"]}


def delete_session(token: str | None) -> None:
    if token:
        db_execute("DELETE FROM sessions WHERE id = ?", (session_hash(token),))


def authenticate_request(request: Request) -> dict | None:
    if request.url.path.startswith("/api/workers/") and worker_token_valid(request.headers.get("x-worker-token")):
        return {"username": "worker", "role": "worker", "auth_method": "worker"}
    return session_user(request.cookies.get(SESSION_COOKIE_NAME))


async def require_api_auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/auth/login":
        user = authenticate_request(request)
        if not user:
            return api_error_response(401, "需要登录")
        request.state.auth_user = user
    return await call_next(request)


def require_write_token(request: Request) -> None:
    user = getattr(request.state, "auth_user", None)
    if user and role_allows(user.get("role", ""), "operator"):
        return
    raise HTTPException(403, "写操作需要 operator 或 admin 权限")


def require_admin(request: Request) -> None:
    user = getattr(request.state, "auth_user", None)
    if user and role_allows(user.get("role", ""), "admin"):
        return
    raise HTTPException(403, "该操作需要 admin 权限")


def require_worker_token(request: Request, x_worker_token: Annotated[str | None, Header(alias="X-Worker-Token")] = None) -> None:
    if worker_token_valid(x_worker_token):
        request.state.auth_user = {"username": "worker", "role": "worker", "auth_method": "worker"}
        return
    raise HTTPException(401, "worker 操作需要有效 WORKER_TOKEN")


@router.post("/auth/login")
def login(body: LoginIn, response: Response):
    initialized = ensure_admin_user()
    if not initialized:
        raise HTTPException(503, "管理员账号未初始化，请设置 ADMIN_USERNAME 和 ADMIN_PASSWORD 后重启 panel")
    row = user_row(body.username)
    if not row or not verify_password(body.password, row["password_hash"]):
        audit_log("login_failed", "auth", body.username.strip() or "unknown", {"reason": "invalid_credentials"}, actor=body.username.strip() or "anonymous")
        raise HTTPException(401, "用户名或密码错误")
    token, expires_at = create_session(row["username"])
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite=SESSION_COOKIE_SAMESITE,
        path="/",
    )
    audit_log("login", "auth", row["username"], {"method": "password", "expires_at": expires_at}, actor=row["username"])
    return {"ok": True, "user": {"username": row["username"], "role": row["role"]}, "expires_at": expires_at}


@router.get("/auth/me")
def auth_me(request: Request):
    user = authenticate_request(request)
    return {
        "authenticated": bool(user),
        "user": user,
        "admin_initialized": admin_user_exists(),
        "auth_required": True,
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    user = getattr(request.state, "auth_user", None) or {"username": "unknown", "auth_method": "unknown"}
    delete_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="lax")
    audit_log("logout", "auth", user.get("username", "unknown"), {"method": user.get("auth_method", "unknown")}, actor=user.get("username", "unknown"))
    return {"ok": True}


@router.get("/access/users", dependencies=[Depends(require_admin)])
def get_access_users():
    return list_access_users()


@router.post("/access/users", dependencies=[Depends(require_admin)])
def save_access_user(body: AccessUserIn):
    return {"ok": True, "user": upsert_access_user(body)}


@router.delete("/access/users/{username}", dependencies=[Depends(require_admin)])
def remove_access_user(username: str):
    delete_access_user(username)
    return {"ok": True}
