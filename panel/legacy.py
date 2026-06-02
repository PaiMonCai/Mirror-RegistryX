import asyncio
import base64
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlparse

import httpx
import yaml
from cryptography.fernet import Fernet, InvalidToken
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from mirror_registry_core.config import default_config
from mirror_registry_core.mirror_rules import add_minutes, bool_int, bounded_interval, normalize_mode, normalize_push_status
from .schemas import (
    BackupRestoreDrillIn,
    BackupRestoreVerifyIn,
    CredentialIn,
    CredentialTestIn,
    InstallUpgradePreflightIn,
    MirrorDiscoveryIn,
    MirrorGroupIn,
    MirrorImportIn,
    MirrorIn,
    MirrorPreflightBatchIn,
    MirrorPreflightIn,
    MirrorPushIn,
    RegistryIn,
    RetentionPolicyIn,
    ScheduledPushPolicyIn,
    SettingsIn,
    StorageDeleteMarkIn,
    StorageGcStatusIn,
    TagProtectionRuleIn,
)
from .db import connect_db, database_backend, database_path as db_database_path, db_execute, db_one, db_rows
from .auth import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_COOKIE_SAMESITE,
    SESSION_TTL_SECONDS,
    WORKER_TOKEN,
    admin_user_exists,
    ensure_admin_user,
    require_admin,
    require_api_auth,
    require_write_token,
    router as auth_router,
)
from .queue import enqueue_sync_task, router as queue_router, write_trigger


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_config()
    ensure_admin_user()
    yield


app = FastAPI(title="Mirror Registry Panel", lifespan=lifespan)
app.middleware("http")(require_api_auth)
app.include_router(auth_router)
app.include_router(queue_router)

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/config/mirrors.yml"))
STATE_PATH = Path(os.getenv("STATE_PATH", "/data/sync-state.json"))
LOG_PATH = Path(os.getenv("LOG_PATH", "/data/sync.log"))
TRIGGER_PATH = Path(os.getenv("TRIGGER_PATH", "/data/.trigger"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/mirror-registry.db")
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://registry:5000").rstrip("/")
REGISTRY_STORAGE_PATH = Path(os.getenv("REGISTRY_STORAGE_PATH", "/data/registry"))
APP_VERSION = os.getenv("APP_VERSION", "v4")
IMAGE_TAG = os.getenv("MIRROR_REGISTRY_IMAGE_TAG", "latest")
CREDENTIALS_SECRET_KEY = os.getenv("CREDENTIALS_SECRET_KEY", "")
UPGRADE_CHECK_SCRIPT_LABEL = r"scripts\upgrade-check.ps1"
MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    ]
)

STATIC_DIR = Path(os.getenv("STATIC_DIR", "/panel/static"))
if not STATIC_DIR.exists():
    STATIC_DIR = Path(__file__).parent / "static"
IMAGE_REF_RE = re.compile(
    r"^(?=.{3,255}$)(?:[a-zA-Z0-9.-]+(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:(?:[._-][a-z0-9]+)+)?"
    r"(?:/[a-z0-9]+(?:(?:[._-][a-z0-9]+)+)?)*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"
)
REPO_RE = re.compile(r"^[a-z0-9]+(?:(?:[._-][a-z0-9]+)+)?(?:/[a-z0-9]+(?:(?:[._-][a-z0-9]+)+)?)*$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def database_path() -> Path:
    return db_database_path(DATABASE_URL)


DB_PATH = database_path()


def audit_log(action: str, resource_type: str, resource_id: str, detail: dict | None = None, actor: str = "panel") -> None:
    db_execute(
        """
        INSERT INTO audit_logs(created_at, actor, action, resource_type, resource_id, detail)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now_iso(), actor, action, resource_type, resource_id, json.dumps(detail or {}, ensure_ascii=False)),
    )


def credential_row(credential_id: str) -> dict:
    clean_id = validate_slug(credential_id, "credential_id")
    row = db_one(
        "SELECT id, name, registry_host, username, encrypted_secret, scope, created_at, updated_at FROM credentials WHERE id = ?",
        (clean_id,),
    )
    if not row:
        raise HTTPException(404, "凭据不存在")
    return row


def mirror_credential_references(config: dict, credential_id: str) -> list[str]:
    refs = []
    for mirror in valid_mirrors(config):
        if mirror.get("source_credential_id") == credential_id:
            refs.append(f"{mirror['source']} source")
        if mirror.get("target_credential_id") == credential_id:
            refs.append(f"{mirror['source']} target")
    return refs


def runtime_state() -> dict:
    rows = db_rows("SELECT key, value, updated_at FROM runtime_state")
    return {row["key"]: {"value": row["value"], "updated_at": row["updated_at"]} for row in rows}


def set_runtime_state(key: str, value: str) -> None:
    db_execute(
        """
        INSERT INTO runtime_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now_iso()),
    )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    ensure_parent(path)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        newline="\n",
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        config = default_config()
        save_config(config)
        return config
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    data.setdefault("mirrors", [])
    data.setdefault("settings", {})
    data.setdefault("registries", [])
    data.setdefault("mirror_groups", [])
    return data


def save_config(config: dict) -> None:
    content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    atomic_write_text(CONFIG_PATH, content)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"sync-state.json 无法解析: {exc}") from exc


def save_state(state: dict) -> None:
    atomic_write_text(STATE_PATH, json.dumps(state, indent=2, ensure_ascii=False))


def validate_image_ref(value: str, field_name: str) -> str:
    image = value.strip()
    if not IMAGE_REF_RE.match(image):
        raise HTTPException(400, f"{field_name} 必须是包含 tag 的镜像地址")
    return image


def split_image_ref(value: str) -> tuple[str, str, str]:
    image = value.strip()
    name, tag = image.rsplit(":", 1)
    first, rest = (name.split("/", 1) + [""])[:2]
    has_registry = bool(rest) and ("." in first or ":" in first or first == "localhost")
    repo = rest if has_registry else name
    registry = first if has_registry else ""
    return registry, repo, tag


def canonical_discovered_source(value: str) -> str:
    image = validate_image_ref(value, "source")
    registry, repo, tag = split_image_ref(image)
    if registry:
        return image
    if "/" not in repo:
        repo = f"library/{repo}"
    return f"docker.io/{repo}:{tag}"


def normalize_target_registry(value: str) -> str:
    raw = value.strip().rstrip("/")
    if not raw:
        raise HTTPException(400, "target_registry 不能为空")
    if "://" in raw:
        parsed = urlparse(raw)
        if not parsed.netloc:
            raise HTTPException(400, "target_registry 格式不合法")
        raw = parsed.netloc
    if "/" in raw or raw.startswith(".") or raw.endswith("."):
        raise HTTPException(400, "target_registry 只能是 Registry host，不包含路径")
    return raw


def discovery_target_for_source(source: str, target_registry: str) -> str:
    _, repo, tag = split_image_ref(source)
    return validate_image_ref(f"{normalize_target_registry(target_registry)}/{repo}:{tag}", "target")


def generated_target_for_source(source: str, target_registry: str, namespace: str = "") -> str:
    _, repo, tag = split_image_ref(source)
    clean_namespace = str(namespace or "").strip().strip("/")
    if clean_namespace:
        repo_name = repo.rsplit("/", 1)[-1]
        repo = f"{clean_namespace}/{repo_name}"
    return validate_image_ref(f"{normalize_target_registry(target_registry)}/{repo}:{tag}", "target")


def validate_repo_tag(repo: str, tag: str) -> tuple[str, str]:
    clean_repo = repo.strip()
    clean_tag = tag.strip()
    if not REPO_RE.match(clean_repo):
        raise HTTPException(400, "repo 格式不合法")
    if not TAG_RE.match(clean_tag):
        raise HTTPException(400, "tag 格式不合法")
    return clean_repo, clean_tag


def validate_slug(value: str, field_name: str) -> str:
    slug = value.strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", slug):
        raise HTTPException(400, f"{field_name} 只能包含字母、数字、点、下划线和短横线")
    return slug


def slug_candidate(value: str, default: str = "credential") -> str:
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-_")
    if not candidate:
        candidate = default
    if not re.match(r"^[A-Za-z0-9]", candidate):
        candidate = f"{default}-{candidate}"
    return candidate[:64]


def optional_slug(value: str | None, field_name: str) -> str:
    if value is None:
        return ""
    clean = str(value).strip()
    return validate_slug(clean, field_name) if clean else ""


def image_registry_host(value: str) -> str:
    first = value.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        return first
    return "docker.io"


def normalize_registry_host(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise HTTPException(400, "registry_host 不能为空")
    if "://" in raw:
        parsed = urlparse(raw)
        if not parsed.netloc:
            raise HTTPException(400, "registry_host 格式不合法")
        return parsed.netloc.lower()
    return image_registry_host(raw).lower() if "/" in raw else raw.lower()


def validate_credential_scope(value: str) -> str:
    scope = value.strip().lower() or "both"
    if scope not in {"source", "target", "both"}:
        raise HTTPException(400, "scope 必须是 source、target 或 both")
    return scope


PLAIN_SECRET_PREFIX = "plain:"


def require_credentials_secret() -> str:
    return CREDENTIALS_SECRET_KEY.strip()


def credential_fernet() -> Fernet | None:
    secret = CREDENTIALS_SECRET_KEY.strip()
    if not secret:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret: str) -> str:
    fernet = credential_fernet()
    if fernet:
        return fernet.encrypt(secret.encode("utf-8")).decode("ascii")
    return PLAIN_SECRET_PREFIX + base64.urlsafe_b64encode(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(encrypted_secret: str) -> str:
    value = encrypted_secret or ""
    if value.startswith(PLAIN_SECRET_PREFIX):
        try:
            return base64.urlsafe_b64decode(value.removeprefix(PLAIN_SECRET_PREFIX).encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "凭据格式损坏，请重新保存仓库凭据") from exc
    fernet = credential_fernet()
    if not fernet:
        raise HTTPException(400, "旧版本加密凭据无法在当前配置下解密；请在仓库凭据页面重新保存一次")
    try:
        return fernet.decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(400, "旧凭据无法解密；请在仓库凭据页面重新保存一次") from exc


def public_credential(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "registry_host": row["registry_host"],
        "username": row["username"],
        "scope": row["scope"],
        "configured": bool(row.get("encrypted_secret")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def image_repo_tag(image: str) -> tuple[str, str]:
    value = image.strip()
    if ":" not in value:
        raise HTTPException(400, "镜像地址必须包含 tag")
    first, rest = (value.split("/", 1) + [""])[:2]
    without_registry = rest if rest and ("." in first or ":" in first or first == "localhost") else value
    repo, tag = without_registry.rsplit(":", 1)
    return validate_repo_tag(repo, tag)


def registry_storage_root() -> Path:
    """Return the Docker distribution filesystem root used by the local registry."""
    candidate = REGISTRY_STORAGE_PATH / "docker" / "registry" / "v2"
    if candidate.exists():
        return candidate
    # Some deployments mount /var/lib/registry directly while tests/dev may point to
    # the v2 directory. Prefer the configured path when it already looks like v2.
    if (REGISTRY_STORAGE_PATH / "repositories").exists() or (REGISTRY_STORAGE_PATH / "blobs").exists():
        return REGISTRY_STORAGE_PATH
    return candidate


def safe_registry_file(path: Path) -> Path:
    root = registry_storage_root().resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise HTTPException(400, "registry storage path 越界")
    if not resolved.is_file():
        raise HTTPException(404, "本地 registry artifact 不存在，请先完成同步")
    return resolved


def read_registry_link(path: Path) -> str:
    value = safe_registry_file(path).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", value):
        raise HTTPException(500, "registry storage link 格式不合法")
    return value.lower()


def registry_blob_path(digest: str) -> Path:
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise HTTPException(500, "registry blob digest 格式不合法")
    hex_digest = digest.split(":", 1)[1]
    return registry_storage_root() / "blobs" / "sha256" / hex_digest[:2] / hex_digest / "data"


def collect_manifest_blob_digests(manifest: dict, depth: int = 0, seen: set[str] | None = None) -> list[str]:
    seen = seen or set()
    digests: list[str] = []

    def add(digest: str) -> None:
        clean = digest.lower()
        if clean not in seen:
            seen.add(clean)
            digests.append(clean)

    value = manifest.get("config")
    if isinstance(value, dict) and isinstance(value.get("digest"), str):
        add(value["digest"])
    for item in manifest.get("layers") or []:
        if isinstance(item, dict) and isinstance(item.get("digest"), str):
            add(item["digest"])
    if depth >= 4:
        return digests
    for item in manifest.get("manifests") or []:
        if not isinstance(item, dict) or not isinstance(item.get("digest"), str):
            continue
        child_digest = item["digest"]
        add(child_digest)
        try:
            child_manifest = json.loads(safe_registry_file(registry_blob_path(child_digest.lower())).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(500, "本地子 manifest JSON 无法解析") from exc
        digests.extend(collect_manifest_blob_digests(child_manifest, depth + 1, seen))
    return digests


def build_local_artifact_archive(image: str) -> tuple[Path, str]:
    repo, tag = image_repo_tag(image)
    root = registry_storage_root()
    manifest_digest = read_registry_link(root / "repositories" / repo / "_manifests" / "tags" / tag / "current" / "link")
    manifest_path = safe_registry_file(registry_blob_path(manifest_digest))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, "本地 manifest JSON 无法解析") from exc

    tmp = tempfile.NamedTemporaryFile(prefix="mirror-registry-artifact-", suffix=".tar", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    metadata = {
        "image": image,
        "repo": repo,
        "tag": tag,
        "manifest_digest": manifest_digest,
        "exported_at": now_iso(),
        "format": "registry-storage-artifact-v1",
        "limitation": "This archive contains the local registry manifest and blobs; it is not a Docker save/OCI layout tar.",
    }
    try:
        with tarfile.open(tmp_path, "w") as archive:
            meta_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
            meta_info = tarfile.TarInfo("metadata.json")
            meta_info.size = len(meta_bytes)
            archive.addfile(meta_info, fileobj=io.BytesIO(meta_bytes))
            archive.add(manifest_path, arcname=f"manifests/{manifest_digest.replace(':', '_')}.json")
            for digest in collect_manifest_blob_digests(manifest):
                blob_path = safe_registry_file(registry_blob_path(digest.lower()))
                archive.add(blob_path, arcname=f"blobs/{digest.replace(':', '/')}")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{repo}_{tag}").strip("_") or "image-artifact"
    return tmp_path, f"{filename}.registry-artifact.tar"


def pattern_matches(pattern: str, value: str) -> bool:
    clean_pattern = (pattern or "*").lower()
    return fnmatch.fnmatchcase(value.lower(), clean_pattern)


def mirror_context_for_tag(repo: str, tag: str, config: dict | None = None) -> dict:
    for mirror in valid_mirrors(config or load_config()):
        try:
            target_repo, target_tag = image_repo_tag(mirror["target"])
        except HTTPException:
            continue
        if target_repo == repo and target_tag == tag:
            return mirror
    return {}


def default_protection_reasons(tag: str, environment: str = "") -> list[str]:
    reasons = []
    env = environment.lower()
    if env in {"prod", "production"}:
        reasons.append("protected_environment")
    if re.match(r"^v\d", tag):
        reasons.append("release_tag")
    return reasons


def protection_rule_rows(enabled_only: bool = False) -> list[dict]:
    where = "WHERE enabled = 1" if enabled_only else ""
    return db_rows(
        f"""
        SELECT id, name, repo_pattern, tag_pattern, environment, enabled, reason, created_at, updated_at
        FROM tag_protection_rules
        {where}
        ORDER BY id
        """
    )


def protection_result(repo: str, tag: str, environment: str = "") -> dict:
    clean_repo, clean_tag = validate_repo_tag(repo, tag)
    env = environment or mirror_context_for_tag(clean_repo, clean_tag).get("environment", "")
    reasons = default_protection_reasons(clean_tag, env)
    matched_rules = []
    for row in protection_rule_rows(enabled_only=True):
        if not pattern_matches(row["repo_pattern"], clean_repo):
            continue
        if not pattern_matches(row["tag_pattern"], clean_tag):
            continue
        rule_env = row.get("environment") or "*"
        if rule_env != "*" and env and not pattern_matches(rule_env, env):
            continue
        if rule_env != "*" and not env:
            continue
        matched_rules.append(public_protection_rule(row))
        reasons.append(row.get("reason") or row.get("name") or row["id"])
    return {
        "repo": clean_repo,
        "tag": clean_tag,
        "environment": env or "",
        "protected": bool(reasons),
        "reasons": reasons,
        "rules": matched_rules,
    }


def assert_tag_mutation_allowed(repo: str, tag: str, action: str, environment: str = "") -> dict:
    result = protection_result(repo, tag, environment)
    if result["protected"]:
        raise HTTPException(
            409,
            f"受保护 tag 不能执行 {action}: {repo}:{tag} ({', '.join(result['reasons'])})",
        )
    return result


def public_protection_rule(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "repo_pattern": row["repo_pattern"],
        "tag_pattern": row["tag_pattern"],
        "environment": row["environment"],
        "enabled": bool(row["enabled"]),
        "reason": row.get("reason") or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def public_retention_policy(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "repo_pattern": row["repo_pattern"],
        "environment": row["environment"],
        "keep_last": int(row["keep_last"]),
        "max_age_days": row.get("max_age_days"),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def retention_policy_row(policy_id: str) -> dict:
    clean_id = validate_slug(policy_id, "policy_id")
    row = db_one(
        """
        SELECT id, name, repo_pattern, environment, keep_last, max_age_days, enabled, created_at, updated_at
        FROM retention_policies
        WHERE id = ?
        """,
        (clean_id,),
    )
    if not row:
        raise HTTPException(404, "保留策略不存在")
    return row


def parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def known_image_tags() -> list[dict]:
    rows = db_rows(
        """
        SELECT source, target, status, new_digest, ended_at, started_at
        FROM sync_run_items
        WHERE target != ''
        ORDER BY COALESCE(ended_at, started_at) DESC
        """
    )
    seen = {}
    config = load_config()
    for row in rows:
        try:
            repo, tag = image_repo_tag(row["target"])
        except HTTPException:
            continue
        ref = f"{repo}:{tag}"
        if ref in seen:
            continue
        context = mirror_context_for_tag(repo, tag, config)
        seen[ref] = {
            "repo": repo,
            "tag": tag,
            "source": row.get("source") or "",
            "target": row.get("target") or "",
            "digest": row.get("new_digest") or "",
            "status": row.get("status") or "",
            "synced_at": row.get("ended_at") or row.get("started_at") or "",
            "environment": context.get("environment", ""),
        }
    return list(seen.values())


def retention_dry_run(policy: dict) -> dict:
    public = public_retention_policy(policy)
    now = datetime.now(timezone.utc)
    grouped: dict[str, list[dict]] = {}
    for item in known_image_tags():
        if not pattern_matches(policy["repo_pattern"], item["repo"]):
            continue
        policy_env = policy.get("environment") or "*"
        item_env = item.get("environment") or ""
        if policy_env != "*" and not pattern_matches(policy_env, item_env):
            continue
        grouped.setdefault(item["repo"], []).append(item)

    candidates = []
    skipped_protected = []
    keep_last = int(policy["keep_last"])
    max_age_days = policy.get("max_age_days")
    for repo, items in grouped.items():
        sorted_items = sorted(items, key=lambda item: parse_iso(item["synced_at"]), reverse=True)
        for index, item in enumerate(sorted_items):
            reasons = []
            if index >= keep_last:
                reasons.append(f"exceeds_keep_last_{keep_last}")
            if max_age_days:
                age = now - parse_iso(item["synced_at"])
                if age > timedelta(days=int(max_age_days)):
                    reasons.append(f"older_than_{max_age_days}_days")
            if not reasons:
                continue
            protection = protection_result(item["repo"], item["tag"], item.get("environment", ""))
            entry = {**item, "reasons": reasons, "protection": protection}
            if protection["protected"]:
                skipped_protected.append(entry)
            else:
                candidates.append(entry)
    return {"policy": public, "candidates": candidates, "skipped_protected": skipped_protected}


def cron_field_matches(field: str, value: int, minimum: int, maximum: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("*/"):
            try:
                interval = int(part[2:])
            except ValueError:
                return False
            return interval > 0 and (value - minimum) % interval == 0
        try:
            candidate = int(part)
        except ValueError:
            return False
        if minimum <= candidate <= maximum and candidate == value:
            return True
    return False


def cron_matches(cron: str, candidate: datetime) -> bool:
    parts = cron.strip().split()
    if len(parts) != 5:
        return False
    minute, hour, day, month, weekday = parts
    cron_weekday = (candidate.weekday() + 1) % 7  # cron: Sunday=0, Python: Monday=0
    return (
        cron_field_matches(minute, candidate.minute, 0, 59)
        and cron_field_matches(hour, candidate.hour, 0, 23)
        and cron_field_matches(day, candidate.day, 1, 31)
        and cron_field_matches(month, candidate.month, 1, 12)
        and cron_field_matches(weekday, cron_weekday, 0, 6)
    )


def next_run_from_cron(cron: str, base: datetime | None = None) -> str:
    now = (base or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    candidate = now + timedelta(minutes=1)
    deadline = now + timedelta(days=366)
    while candidate <= deadline:
        if cron_matches(cron, candidate):
            return candidate.isoformat()
        candidate += timedelta(minutes=1)
    return (now + timedelta(hours=24)).isoformat()


def cron_is_supported(cron: str) -> bool:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return next_run_from_cron(cron, now) != (now + timedelta(hours=24)).isoformat() or cron_matches(cron, now + timedelta(hours=24))


def scheduled_policy_row(policy_id: str) -> dict:
    clean_id = validate_slug(policy_id, "schedule_id")
    row = db_one(
        """
        SELECT id, name, source, target, cron, enabled, allow_latest, source_credential_id, target_credential_id,
               last_run_at, next_run_at, last_error, created_at, updated_at
        FROM scheduled_push_policies
        WHERE id = ?
        """,
        (clean_id,),
    )
    if not row:
        raise HTTPException(404, "计划推送策略不存在")
    return row


def public_scheduled_policy(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
        "target": row["target"],
        "cron": row["cron"],
        "cron_timezone": "UTC",
        "next_run_explain": f"按 UTC 计算，下次运行 {row.get('next_run_at') or '-'}",
        "enabled": bool(row["enabled"]),
        "allow_latest": bool(row["allow_latest"]),
        "source_credential_id": row.get("source_credential_id") or "",
        "target_credential_id": row.get("target_credential_id") or "",
        "last_run_at": row.get("last_run_at") or "",
        "next_run_at": row.get("next_run_at") or "",
        "last_error": row.get("last_error") or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def assert_scheduled_policy_allowed(source: str, target: str, allow_latest: bool) -> tuple[str, str]:
    validate_image_ref(source, "source")
    validate_image_ref(target, "target")
    repo, tag = image_repo_tag(target)
    if tag == "latest" and not allow_latest:
        raise HTTPException(409, "计划推送默认不允许覆盖 latest，必须显式 allow_latest")
    assert_tag_mutation_allowed(repo, tag, "scheduled-push")
    return repo, tag


def validate_registry_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "registry url 必须是 http:// 或 https:// 地址")
    return url


def normalize_registry(item: dict) -> dict:
    registry_id = validate_slug(str(item.get("id") or item.get("name") or "local"), "registry id")
    url = validate_registry_url(str(item.get("url") or REGISTRY_URL))
    return {
        "id": registry_id,
        "name": str(item.get("name") or registry_id).strip() or registry_id,
        "url": url,
        "copy_host": str(item.get("copy_host") or "").strip(),
        "storage_path": str(item.get("storage_path") or "").strip(),
    }


def default_registry() -> dict:
    return {
        "id": "local",
        "name": "Local Registry",
        "url": REGISTRY_URL,
        "copy_host": "registry:5000",
        "storage_path": str(REGISTRY_STORAGE_PATH),
    }


def registry_map(config: dict | None = None) -> dict[str, dict]:
    config = config or load_config()
    registries = [default_registry()]
    for item in config.get("registries", []):
        if isinstance(item, dict):
            try:
                normalized = normalize_registry(item)
            except HTTPException:
                continue
            registries.append(normalized)
    return {item["id"]: item for item in registries}


def normalize_group(item: dict) -> dict:
    group_id = validate_slug(str(item.get("id") or item.get("name") or "default"), "group id")
    return {
        "id": group_id,
        "name": str(item.get("name") or group_id).strip() or group_id,
        "project": validate_slug(str(item.get("project") or "default"), "project"),
        "environment": validate_slug(str(item.get("environment") or "local"), "environment"),
        "namespace": str(item.get("namespace") or "library").strip() or "library",
        "registry": validate_slug(str(item.get("registry") or "local"), "registry"),
    }


def group_map(config: dict | None = None) -> dict[str, dict]:
    config = config or load_config()
    groups = [
        {
            "id": "default",
            "name": "Default",
            "project": "default",
            "environment": "local",
            "namespace": "library",
            "registry": "local",
        }
    ]
    for item in config.get("mirror_groups", []):
        if isinstance(item, dict):
            try:
                groups.append(normalize_group(item))
            except HTTPException:
                continue
    return {item["id"]: item for item in groups}


def normalize_mirror(item: dict, groups: dict[str, dict] | None = None) -> dict:
    source = validate_image_ref(str(item.get("source", "")), "source")
    target_value = str(item.get("target_override") or item.get("target") or "").strip()
    if not target_value:
        target_registry = str(item.get("target_registry") or "localhost:5000")
        target_value = generated_target_for_source(source, target_registry, str(item.get("target_namespace") or item.get("namespace") or ""))
    target = validate_image_ref(target_value, "target")
    group_id = validate_slug(str(item.get("group") or item.get("group_id") or "default"), "group")
    groups = groups or group_map()
    group = groups.get(group_id, groups["default"])
    registry = validate_slug(str(item.get("registry") or item.get("registry_id") or group.get("registry") or "local"), "registry")
    mode = normalize_mode(item.get("mode"))
    return {
        "source": source,
        "target": target,
        "registry": registry,
        "group": group_id,
        "project": validate_slug(str(item.get("project") or group.get("project") or "default"), "project"),
        "environment": validate_slug(str(item.get("environment") or group.get("environment") or "local"), "environment"),
        "namespace": str(item.get("namespace") or group.get("namespace") or "library").strip() or "library",
        "mode": mode,
        "check_interval_minutes": bounded_interval(item.get("check_interval_minutes"), settings_with_defaults()["check_interval_minutes"] if "settings_with_defaults" in globals() else 30),
        "allow_latest_push": bool(bool_int(item.get("allow_latest_push"))),
        "source_credential_id": optional_slug(item.get("source_credential_id"), "source_credential_id"),
        "target_credential_id": optional_slug(item.get("target_credential_id"), "target_credential_id"),
    }


def valid_mirrors(config: dict) -> list[dict]:
    result = []
    groups = group_map(config)
    for item in config.get("mirrors", []):
        if not isinstance(item, dict):
            continue
        try:
            result.append(normalize_mirror(item, groups))
        except HTTPException:
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            if source and target:
                result.append(
                    {
                        "source": source,
                        "target": target,
                        "registry": "local",
                        "group": "default",
                        "project": "default",
                        "environment": "local",
                        "namespace": "library",
                        "mode": "auto_push",
                        "check_interval_minutes": settings_with_defaults()["check_interval_minutes"],
                        "allow_latest_push": False,
                        "source_credential_id": "",
                        "target_credential_id": "",
                    }
                )
    return result


def settings_with_defaults() -> dict:
    settings = load_config().get("settings", {})
    database_url = str(settings.get("database_url") or DATABASE_URL)
    return {
        "check_interval_minutes": int(settings.get("check_interval_minutes", 30)),
        "registry_url": str(settings.get("registry_url") or REGISTRY_URL).rstrip("/"),
        "sync_concurrency": int(settings.get("sync_concurrency", 2)),
        "sync_retry_count": int(settings.get("sync_retry_count", 2)),
        "notify_webhook_configured": bool(str(settings.get("notify_webhook_url") or os.getenv("NOTIFY_WEBHOOK_URL", "")).strip()),
        "notify_webhook_url_masked": mask_url(str(settings.get("notify_webhook_url") or os.getenv("NOTIFY_WEBHOOK_URL", "")).strip()),
        "database_url": database_url,
        "database_backend": database_backend(database_url),
        "external_database_configured": not database_url.startswith("sqlite:"),
    }


def mask_url(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 18:
        return "***"
    return f"{value[:12]}...{value[-6:]}"


def upsert_mirror_db(source: str, target: str, digest: str | None = None, mirror: dict | None = None) -> None:
    mirror = mirror or {}
    interval = bounded_interval(mirror.get("check_interval_minutes"), settings_with_defaults()["check_interval_minutes"])
    mode = normalize_mode(mirror.get("mode"))
    next_check = add_minutes(now_iso(), 0)
    db_execute(
        """
        INSERT INTO mirrors(
            source, target, enabled, last_digest, registry, mirror_group, project, environment, namespace,
            mode, check_interval_minutes, next_check_at, last_source_digest, push_status,
            allow_latest_push, source_credential_id, target_credential_id, updated_at
        )
        VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            target = excluded.target,
            last_digest = COALESCE(excluded.last_digest, mirrors.last_digest),
            registry = excluded.registry,
            mirror_group = excluded.mirror_group,
            project = excluded.project,
            environment = excluded.environment,
            namespace = excluded.namespace,
            mode = excluded.mode,
            check_interval_minutes = excluded.check_interval_minutes,
            next_check_at = COALESCE(mirrors.next_check_at, excluded.next_check_at),
            last_source_digest = COALESCE(excluded.last_source_digest, mirrors.last_source_digest, mirrors.last_digest),
            allow_latest_push = excluded.allow_latest_push,
            source_credential_id = excluded.source_credential_id,
            target_credential_id = excluded.target_credential_id,
            updated_at = excluded.updated_at
        """,
        (
            source,
            target,
            digest,
            mirror.get("registry") or "local",
            mirror.get("group") or mirror.get("mirror_group") or "default",
            mirror.get("project") or "default",
            mirror.get("environment") or "local",
            mirror.get("namespace") or "library",
            mode,
            interval,
            next_check,
            digest,
            bool_int(mirror.get("allow_latest_push")),
            mirror.get("source_credential_id") or "",
            mirror.get("target_credential_id") or "",
            now_iso(),
        ),
    )


def delete_mirror_db(source: str) -> None:
    db_execute("DELETE FROM mirrors WHERE source = ?", (source,))


def mirror_rule_rows(enabled: bool | None = None) -> list[dict]:
    where = ""
    params: tuple = ()
    if enabled is not None:
        where = "WHERE enabled = ?"
        params = (1 if enabled else 0,)
    return db_rows(
        f"""
        SELECT source, target, enabled, last_digest, registry, mirror_group, project, environment, namespace,
               mode, check_interval_minutes, next_check_at, last_checked_at, last_source_digest,
               last_target_digest, last_change_at, last_push_at, pending_push_digest, pending_push_target,
               push_status, check_failures, push_failures, next_push_at, last_error, allow_latest_push,
               source_credential_id, target_credential_id, template_id, notification_policy_id,
               push_window_id, retention_policy_id, governance_status, governance_note, updated_at
        FROM mirrors
        {where}
        ORDER BY source
        """,
        params,
    )


def mirror_rule_by_index(index: int) -> dict:
    rows = mirror_rule_rows()
    if index < 0 or index >= len(rows):
        raise HTTPException(404, "镜像不存在")
    return rows[index]


def mirror_rule_by_source(source: str) -> dict | None:
    return db_one(
        """
        SELECT source, target, enabled, last_digest, registry, mirror_group, project, environment, namespace,
               mode, check_interval_minutes, next_check_at, last_checked_at, last_source_digest,
               last_target_digest, last_change_at, last_push_at, pending_push_digest, pending_push_target,
               push_status, check_failures, push_failures, next_push_at, last_error, allow_latest_push,
               source_credential_id, target_credential_id, template_id, notification_policy_id,
               push_window_id, retention_policy_id, governance_status, governance_note, updated_at
        FROM mirrors
        WHERE source = ?
        """,
        (source,),
    )


def short_digest(value: str | None) -> str:
    digest = value or ""
    return (digest[7:19] + "...") if len(digest) > 19 and digest.startswith("sha256:") else digest


def public_mirror_rule(row: dict, index: int = 0) -> dict:
    digest = row.get("last_source_digest") or row.get("last_digest") or ""
    last_item = db_one(
        """
        SELECT status, step, error, ended_at
        FROM sync_run_items
        WHERE source = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (row["source"],),
    )
    last_error = row.get("last_error") or (last_item.get("error") if last_item else "") or ""
    return {
        "index": index,
        "id": row["source"],
        "source": row["source"],
        "target": row["target"],
        "enabled": bool(row.get("enabled")),
        "registry": row.get("registry") or "local",
        "group": row.get("mirror_group") or "default",
        "project": row.get("project") or "default",
        "environment": row.get("environment") or "local",
        "namespace": row.get("namespace") or "library",
        "mode": normalize_mode(row.get("mode")),
        "check_interval_minutes": int(row.get("check_interval_minutes") or 30),
        "next_check_at": row.get("next_check_at") or "",
        "last_checked_at": row.get("last_checked_at") or "",
        "last_source_digest": row.get("last_source_digest") or "",
        "last_target_digest": row.get("last_target_digest") or "",
        "last_change_at": row.get("last_change_at") or "",
        "last_push_at": row.get("last_push_at") or "",
        "pending_push_digest": row.get("pending_push_digest") or "",
        "pending_push_target": row.get("pending_push_target") or "",
        "push_status": normalize_push_status(row.get("push_status")),
        "check_failures": int(row.get("check_failures") or 0),
        "push_failures": int(row.get("push_failures") or 0),
        "next_push_at": row.get("next_push_at") or "",
        "last_error": last_error,
        "allow_latest_push": bool(row.get("allow_latest_push")),
        "source_credential_id": row.get("source_credential_id") or "",
        "target_credential_id": row.get("target_credential_id") or "",
        "template_id": row.get("template_id") or "",
        "notification_policy_id": row.get("notification_policy_id") or "",
        "push_window_id": row.get("push_window_id") or "",
        "retention_policy_id": row.get("retention_policy_id") or "",
        "governance_status": row.get("governance_status") or "active",
        "governance_note": row.get("governance_note") or "",
        "digest": short_digest(digest),
        "synced": bool(digest),
        "last_status": last_item.get("status") if last_item else "",
        "last_step": last_item.get("step") if last_item else "",
        "last_finished_at": last_item.get("ended_at") if last_item else "",
        "updated_at": row.get("updated_at") or "",
    }


def mirror_rule_export(row: dict) -> dict:
    public = public_mirror_rule(row)
    return {
        "source": public["source"],
        "target": public["target"],
        "registry": public["registry"],
        "group": public["group"],
        "project": public["project"],
        "environment": public["environment"],
        "namespace": public["namespace"],
        "mode": public["mode"],
        "check_interval_minutes": public["check_interval_minutes"],
        "allow_latest_push": public["allow_latest_push"],
        "source_credential_id": public["source_credential_id"],
        "target_credential_id": public["target_credential_id"],
    }


def seed_mirror_rules_from_config() -> None:
    config = load_config()
    for mirror in valid_mirrors(config):
        upsert_mirror_db(mirror["source"], mirror["target"], mirror=mirror)


def sync_config_from_db() -> None:
    config = load_config()
    config["mirrors"] = [mirror_rule_export(row) for row in mirror_rule_rows()]
    save_config(config)


def enqueue_rule_task(task_type: str, row: dict, digest: str = "", force: bool = False) -> dict:
    reason = f"mirror-{task_type}"
    return enqueue_sync_task(
        reason,
        source=row["source"],
        priority=40 if task_type == "push" else 60,
        actor="panel",
        force=force,
        task_type=task_type,
        mirror_source=row["source"],
        mirror_target=row["target"],
        digest=digest,
    )


def record_mirror_event(mirror_id: str, event_type: str, status: str, old_digest: str = "", new_digest: str = "", message: str = "", detail: dict | None = None) -> None:
    db_execute(
        """
        INSERT INTO mirror_events(mirror_id, type, status, old_digest, new_digest, message, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mirror_id, event_type, status, old_digest, new_digest, message, json.dumps(detail or {}, ensure_ascii=False), now_iso()),
    )


def validate_discovery_mode(value: str) -> str:
    mode = (value or "missing_only").strip().lower()
    if mode not in {"missing_only", "merge", "replace"}:
        raise HTTPException(400, "mode 必须是 missing_only、merge 或 replace")
    return mode


def validate_discovery_source_type(value: str) -> str:
    source_type = (value or "auto").strip().lower()
    if source_type not in {"auto", "compose", "kubernetes", "text"}:
        raise HTTPException(400, "source_type 必须是 auto、compose、kubernetes 或 text")
    return source_type


def yaml_documents(content: str) -> list[object]:
    try:
        return [doc for doc in yaml.safe_load_all(content) if doc is not None]
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"YAML 无法解析: {exc}") from exc


def extract_compose_images(doc: object) -> list[dict]:
    if not isinstance(doc, dict):
        return []
    services = doc.get("services")
    if not isinstance(services, dict):
        return []
    entries = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        image = service.get("image")
        if isinstance(image, str) and image.strip():
            entries.append(
                {
                    "image": image.strip(),
                    "source_type": "compose",
                    "location": f"services.{service_name}.image",
                }
            )
    return entries


def extract_kubernetes_images_from_spec(spec: object, prefix: str) -> list[dict]:
    if not isinstance(spec, dict):
        return []
    entries = []
    for key in ["containers", "initContainers", "ephemeralContainers"]:
        containers = spec.get(key)
        if not isinstance(containers, list):
            continue
        for index, container in enumerate(containers):
            if not isinstance(container, dict):
                continue
            image = container.get("image")
            name = str(container.get("name") or index)
            if isinstance(image, str) and image.strip():
                entries.append(
                    {
                        "image": image.strip(),
                        "source_type": "kubernetes",
                        "location": f"{prefix}.{key}.{name}.image",
                    }
                )
    return entries


def extract_kubernetes_images(doc: object) -> list[dict]:
    if not isinstance(doc, dict):
        return []
    kind = str(doc.get("kind") or "Object")
    name = str(doc.get("metadata", {}).get("name") or "unnamed") if isinstance(doc.get("metadata"), dict) else "unnamed"
    spec = doc.get("spec")
    entries = extract_kubernetes_images_from_spec(spec, f"{kind}/{name}.spec")
    if isinstance(spec, dict):
        template = spec.get("template")
        if isinstance(template, dict):
            entries.extend(extract_kubernetes_images_from_spec(template.get("spec"), f"{kind}/{name}.spec.template.spec"))
        job_template = spec.get("jobTemplate")
        if isinstance(job_template, dict):
            job_spec = job_template.get("spec")
            if isinstance(job_spec, dict):
                job_template_spec = job_spec.get("template")
                if isinstance(job_template_spec, dict):
                    entries.extend(
                        extract_kubernetes_images_from_spec(
                            job_template_spec.get("spec"),
                            f"{kind}/{name}.spec.jobTemplate.spec.template.spec",
                        )
                    )
    return entries


def extract_text_images(content: str) -> list[dict]:
    entries = []
    for index, line in enumerate(content.splitlines(), start=1):
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        if " #" in clean:
            clean = clean.split(" #", 1)[0].strip()
        clean = clean.strip("- ").strip("'\"")
        if ": " in clean:
            _, maybe_image = clean.split(": ", 1)
            clean = maybe_image.strip().strip("'\"")
        if clean:
            entries.append({"image": clean, "source_type": "text", "location": f"line {index}"})
    return entries


def extract_discovery_entries(content: str, source_type: str) -> list[dict]:
    clean_source_type = validate_discovery_source_type(source_type)
    if clean_source_type == "text":
        return extract_text_images(content)
    docs = yaml_documents(content)
    compose_entries = []
    kubernetes_entries = []
    if clean_source_type in {"auto", "compose"}:
        for doc in docs:
            compose_entries.extend(extract_compose_images(doc))
    if clean_source_type in {"auto", "kubernetes"}:
        for doc in docs:
            kubernetes_entries.extend(extract_kubernetes_images(doc))
    if clean_source_type == "compose":
        return compose_entries
    if clean_source_type == "kubernetes":
        return kubernetes_entries
    return compose_entries + kubernetes_entries if compose_entries or kubernetes_entries else extract_text_images(content)


def discovery_importable(action: str, mode: str) -> bool:
    if action == "new":
        return True
    if action == "existing_source":
        return mode in {"merge", "replace"}
    if action == "existing_target_conflict":
        return mode == "replace"
    return False


def build_discovery_preview(body: MirrorDiscoveryIn) -> dict:
    mode = validate_discovery_mode(body.mode)
    target_registry = normalize_target_registry(body.target_registry)
    config = load_config()
    existing_mirrors = valid_mirrors(config)
    existing_by_source = {mirror["source"]: mirror for mirror in existing_mirrors}
    existing_by_target = {mirror["target"]: mirror for mirror in existing_mirrors}
    groups = group_map(config)
    entries = extract_discovery_entries(body.content, body.source_type)
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    items = []
    problems = []

    for entry in entries[:500]:
        raw_image = str(entry.get("image") or "").strip()
        item = {
            "raw": raw_image,
            "source": "",
            "target": "",
            "source_type": entry.get("source_type") or validate_discovery_source_type(body.source_type),
            "location": entry.get("location") or "",
            "action": "invalid",
            "reason": "",
            "importable": False,
        }
        try:
            source = canonical_discovered_source(raw_image)
            target = discovery_target_for_source(source, target_registry)
            mirror = normalize_mirror(
                {
                    "source": source,
                    "target": target,
                    "registry": body.registry,
                    "group": body.group,
                    "project": body.project,
                    "environment": body.environment,
                    "namespace": body.namespace,
                    "source_credential_id": body.source_credential_id,
                    "target_credential_id": body.target_credential_id,
                },
                groups,
            )
            action = "new"
            reason = "will add mirror"
            if source in seen_sources:
                action = "duplicate_source"
                reason = "same source appears more than once in discovery input"
            elif target in seen_targets:
                action = "duplicate_target"
                reason = "same target appears more than once in discovery input"
            elif source in existing_by_source:
                action = "existing_source"
                reason = "source already exists"
            elif target in existing_by_target and mode != "replace":
                action = "existing_target_conflict"
                reason = "target already belongs to another source"
            seen_sources.add(source)
            seen_targets.add(target)
            item.update(
                {
                    "source": source,
                    "target": target,
                    "registry": mirror["registry"],
                    "group": mirror["group"],
                    "project": mirror["project"],
                    "environment": mirror["environment"],
                    "namespace": mirror["namespace"],
                    "source_credential_id": mirror["source_credential_id"],
                    "target_credential_id": mirror["target_credential_id"],
                    "action": action,
                    "reason": reason,
                    "importable": discovery_importable(action, mode),
                }
            )
        except HTTPException as exc:
            missing_tag = ":" not in raw_image.rsplit("/", 1)[-1]
            item["action"] = "missing_tag" if missing_tag else "invalid"
            item["reason"] = str(exc.detail)
        items.append(item)
        if not item["importable"]:
            problems.append(item)

    truncated = len(entries) > 500
    summary = {
        "extracted": len(entries),
        "returned": len(items),
        "importable": sum(1 for item in items if item["importable"]),
        "new": sum(1 for item in items if item["action"] == "new"),
        "existing_source": sum(1 for item in items if item["action"] == "existing_source"),
        "target_conflicts": sum(1 for item in items if item["action"] in {"existing_target_conflict", "duplicate_target"}),
        "invalid": sum(1 for item in items if item["action"] in {"invalid", "missing_tag"}),
        "truncated": truncated,
    }
    return {
        "mode": mode,
        "source_type": validate_discovery_source_type(body.source_type),
        "target_registry": target_registry,
        "items": items,
        "problems": problems,
        "summary": summary,
    }


def preflight_check(name: str, status: str, message: str, suggestion: str = "", details: dict | None = None) -> dict:
    return {
        "name": name,
        "status": status,
        "message": message,
        "suggestion": suggestion,
        "details": details or {},
    }


def summarize_preflight_checks(checks: list[dict]) -> dict:
    status = "ok"
    if any(item["status"] == "error" for item in checks):
        status = "error"
    elif any(item["status"] == "warn" for item in checks):
        status = "warn"
    return {
        "status": status,
        "ok": sum(1 for item in checks if item["status"] == "ok"),
        "warn": sum(1 for item in checks if item["status"] == "warn"),
        "error": sum(1 for item in checks if item["status"] == "error"),
    }


def credential_rows() -> list[dict]:
    return db_rows(
        """
        SELECT id, name, registry_host, username, encrypted_secret, scope, created_at, updated_at
        FROM credentials
        ORDER BY registry_host, name
        """
    )


def credential_allows(row: dict, purpose: str) -> bool:
    return str(row.get("scope") or "both").lower() in {"both", purpose}


def select_preflight_credential(image: str, purpose: str, explicit_id: str = "", credentials: list[dict] | None = None) -> tuple[dict | None, dict]:
    rows = credentials if credentials is not None else credential_rows()
    host = image_registry_host(image).lower()
    explicit = (explicit_id or "").strip()
    if explicit:
        row = next((item for item in rows if item.get("id") == explicit), None)
        if not row:
            return None, preflight_check(f"{purpose} 凭据", "error", f"显式凭据 {explicit} 不存在", "检查镜像配置中的 credential id")
        if not credential_allows(row, purpose):
            return None, preflight_check(f"{purpose} 凭据", "error", f"凭据 {explicit} 不允许用于 {purpose}", "调整凭据 scope 或镜像配置")
        try:
            decrypt_secret(row["encrypted_secret"])
        except HTTPException as exc:
            return None, preflight_check(f"{purpose} 凭据", "error", f"凭据 {explicit} 无法解密: {exc.detail}", "在仓库凭据页面重新输入 token/password 并保存一次")
        return row, preflight_check(f"{purpose} 凭据", "ok", f"使用显式凭据 {explicit}", details={"credential_id": explicit, "host": row["registry_host"]})

    row = next((item for item in rows if str(item.get("registry_host") or "").lower() == host and credential_allows(item, purpose)), None)
    if not row:
        return None, preflight_check(f"{purpose} 凭据", "warn", f"{host} 未配置 {purpose} 凭据，将尝试匿名访问", "私有仓库需要先在仓库凭据页保存凭据")
    try:
        decrypt_secret(row["encrypted_secret"])
    except HTTPException as exc:
        return None, preflight_check(f"{purpose} 凭据", "error", f"host 默认凭据 {row['id']} 无法解密: {exc.detail}", "在仓库凭据页面重新输入 token/password 并保存一次")
    return row, preflight_check(f"{purpose} 凭据", "ok", f"匹配 host 默认凭据 {row['id']}", details={"credential_id": row["id"], "host": row["registry_host"]})


def preflight_auth(row: dict | None) -> tuple[str, str] | None:
    if not row:
        return None
    return row["username"], decrypt_secret(row["encrypted_secret"])


def registry_scheme_for_host(host: str) -> str:
    lower = host.lower()
    if lower.startswith("localhost") or lower.startswith("127.") or lower.startswith("registry:"):
        return "http"
    return "https"


def source_manifest_endpoint(source: str) -> tuple[str, str, str]:
    registry, repo, tag = split_image_ref(source)
    host = registry or "docker.io"
    if host == "docker.io":
        return "https://registry-1.docker.io", repo, tag
    return f"{registry_scheme_for_host(host)}://{host}", repo, tag


async def probe_registry_v2(client: httpx.AsyncClient, registry_url: str, credential: dict | None = None) -> dict:
    try:
        response = await client.get(f"{registry_url.rstrip('/')}/v2/", auth=preflight_auth(credential))
    except httpx.TimeoutException:
        return preflight_check("目标 Registry", "error", "目标 Registry /v2/ 访问超时", "检查网络、反向代理和 Registry 服务")
    except httpx.HTTPError as exc:
        return preflight_check("目标 Registry", "error", f"目标 Registry 不可达: {exc.__class__.__name__}", "检查 registry_url 和容器网络")
    if response.status_code in {200, 401}:
        status = "ok" if response.status_code == 200 or not credential else "error"
        message = "目标 Registry /v2/ 可访问" if status == "ok" else "目标 Registry 拒绝当前目标凭据"
        return preflight_check("目标 Registry", status, message, details={"http_status": response.status_code})
    if response.status_code == 403:
        return preflight_check("目标 Registry", "error", "目标 Registry 返回 403，目标凭据权限不足", "检查 push 权限和反向代理认证", {"http_status": 403})
    return preflight_check("目标 Registry", "error", f"目标 Registry 返回 HTTP {response.status_code}", "检查 Registry 服务状态", {"http_status": response.status_code})


async def probe_source_manifest(client: httpx.AsyncClient, source: str, credential: dict | None = None) -> dict:
    registry_url, repo, tag = source_manifest_endpoint(source)
    try:
        response = await client.get(
            f"{registry_url}/v2/{repo}/manifests/{tag}",
            headers={"Accept": MANIFEST_ACCEPT},
            auth=preflight_auth(credential),
        )
    except httpx.TimeoutException:
        return preflight_check("上游镜像", "error", "上游 manifest 访问超时", "检查网络或代理")
    except httpx.HTTPError as exc:
        return preflight_check("上游镜像", "error", f"上游 manifest 不可达: {exc.__class__.__name__}", "检查源 Registry、DNS 和代理")
    if response.status_code == 200:
        return preflight_check(
            "上游镜像",
            "ok",
            "上游 manifest 可读取",
            details={"registry": registry_url, "repo": repo, "tag": tag, "digest": response.headers.get("Docker-Content-Digest", "")},
        )
    if response.status_code == 401:
        return preflight_check("上游镜像", "error", "上游 Registry 要求认证或凭据错误", "检查 source 凭据")
    if response.status_code == 403:
        return preflight_check("上游镜像", "error", "上游 Registry 返回 403，凭据权限不足", "检查 source 凭据权限")
    if response.status_code == 404:
        return preflight_check("上游镜像", "error", "上游镜像或 tag 不存在", "检查 image ref 和 tag")
    return preflight_check("上游镜像", "error", f"上游 Registry 返回 HTTP {response.status_code}", "检查上游 Registry 状态")


async def build_mirror_preflight(mirror_input: dict, check_remote: bool = False) -> dict:
    started = datetime.now(timezone.utc)
    checks: list[dict] = [
        preflight_check("边界", "ok", "预检只读执行，不触发 skopeo copy，不写 digest 缓存或本地 Registry"),
    ]
    try:
        mirror = normalize_mirror(mirror_input)
    except HTTPException as exc:
        checks.append(preflight_check("镜像配置", "error", str(exc.detail), "检查 source、target、group、registry 和凭据字段"))
        summary = summarize_preflight_checks(checks)
        return {"source": str(mirror_input.get("source") or ""), "target": str(mirror_input.get("target") or ""), "summary": summary, "checks": checks, "check_remote": check_remote}

    checks.append(preflight_check("镜像配置", "ok", "source/target/tag 和分组字段格式合法"))
    target_repo, target_tag = image_repo_tag(mirror["target"])
    protection = protection_result(target_repo, target_tag, mirror.get("environment", ""))
    if protection["protected"]:
        checks.append(preflight_check("保护规则", "error", f"目标 tag 受保护: {', '.join(protection['reasons'])}", "同步会在 copy 前被阻断", {"protection": protection}))
    else:
        checks.append(preflight_check("保护规则", "ok", "目标 tag 未命中保护规则", details={"protection": protection}))
    if target_tag == "latest":
        checks.append(preflight_check("latest 策略", "warn", "目标 tag 是 latest；计划推送默认会阻断 latest，手动同步仍需谨慎", "如需计划推送 latest，必须显式 allow_latest"))

    registry_url = get_registry_url()
    target_host = image_registry_host(mirror["target"])
    registry_host = urlparse(registry_url).netloc
    if registry_host and target_host not in {registry_host, "localhost:5000", "registry:5000"}:
        checks.append(preflight_check("目标地址", "warn", f"target host {target_host} 与 registry_url {registry_host} 不一致", "确认 sync 容器能解析目标 host"))
    else:
        checks.append(preflight_check("目标地址", "ok", f"目标地址将写入 {target_host}", details={"registry_url": registry_url}))

    credentials = credential_rows()
    source_credential, source_check = select_preflight_credential(mirror["source"], "source", mirror.get("source_credential_id", ""), credentials)
    target_credential, target_check = select_preflight_credential(mirror["target"], "target", mirror.get("target_credential_id", ""), credentials)
    checks.extend([source_check, target_check])

    if check_remote:
        async with httpx.AsyncClient(timeout=8) as client:
            checks.append(await probe_source_manifest(client, mirror["source"], source_credential))
            checks.append(await probe_registry_v2(client, registry_url, target_credential))
    else:
        checks.append(preflight_check("远程探测", "warn", "未启用 check_remote，已跳过上游 manifest 和目标 /v2/ 网络探测", "需要真实连通性检查时设置 check_remote=true"))

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    summary = summarize_preflight_checks(checks)
    return {
        "source": mirror["source"],
        "target": mirror["target"],
        "environment": mirror["environment"],
        "check_remote": check_remote,
        "duration_ms": elapsed_ms,
        "summary": summary,
        "checks": checks,
    }


def summarize_preflight_results(items: list[dict]) -> dict:
    return {
        "total": len(items),
        "ok": sum(1 for item in items if item["summary"]["status"] == "ok"),
        "warn": sum(1 for item in items if item["summary"]["status"] == "warn"),
        "error": sum(1 for item in items if item["summary"]["status"] == "error"),
    }


def latest_run() -> dict | None:
    return db_one(
        """
        SELECT id, reason, status, started_at, ended_at, total, updated, skipped, failed, message
        FROM sync_runs
        ORDER BY id DESC
        LIMIT 1
        """
    )


def explain_operational_error(message: str | None) -> dict:
    text_value = (message or "").strip()
    lower = text_value.lower()
    if not text_value:
        return {"category": "none", "reason": "", "suggestion": ""}
    rules = [
        (["unauthorized", "authentication", "denied", "401", "403", "credentials"], "auth", "认证或权限失败", "检查源/目标仓库凭据、scope 和 token 权限。"),
        (["x509", "certificate", "tls", "ssl"], "tls", "TLS 或证书失败", "检查 Registry HTTPS 证书、反向代理和容器信任链。"),
        (["timeout", "timed out", "deadline"], "timeout", "网络超时", "检查网络连通性、代理、上游 Registry 状态和超时配置。"),
        (["connection refused", "connecterror", "no route", "network is unreachable"], "network", "网络不可达", "确认 Registry 服务、DNS、端口和容器网络。"),
        (["no such host", "name resolution", "dns"], "dns", "DNS 解析失败", "检查 registry host、容器 DNS 和代理配置。"),
        (["manifest unknown", "not found", "404"], "manifest", "镜像或 tag 不存在", "确认 source image ref 和 tag 是否存在且带显式 tag。"),
        (["no space", "disk", "filesystem"], "storage", "磁盘或存储问题", "检查磁盘余量、删除标记和 Registry garbage-collect 流程。"),
        (["skopeo", "inspect", "copy"], "skopeo", "skopeo 执行失败", "查看任务阶段、copy target、authfile 凭据匹配和 sync 镜像版本。"),
    ]
    for needles, category, reason, suggestion in rules:
        if any(needle in lower for needle in needles):
            return {"category": category, "reason": reason, "suggestion": suggestion}
    return {"category": "unknown", "reason": "未分类错误", "suggestion": "保留原始错误，结合诊断包、任务明细和 sync 日志继续定位。"}


def recent_failed_items(limit: int = 5) -> list[dict]:
    rows = db_rows(
        """
        SELECT id, run_id, source, target, copy_target, status, step, error, started_at, ended_at, duration_ms
        FROM sync_run_items
        WHERE status = 'failed'
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, min(limit, 50)),),
    )
    for row in rows:
        row["explanation"] = explain_operational_error(row.get("error") or row.get("step") or "")
    return rows


def deletion_mark_count() -> int:
    row = db_one("SELECT COUNT(*) AS count FROM deletion_marks")
    return int(row.get("count") or 0) if row else 0


def parse_observability_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_iso(str(value))
    if parsed == datetime.min.replace(tzinfo=timezone.utc):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def sync_run_rows_since(hours: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0)
    return db_rows(
        """
        SELECT id, reason, status, only_source, started_at, ended_at, total, updated, skipped, failed, message
        FROM sync_runs
        WHERE started_at >= ?
        ORDER BY started_at ASC, id ASC
        """,
        (cutoff.isoformat(),),
    )


def build_sync_window_stats(hours: int) -> dict:
    rows = sync_run_rows_since(hours)
    terminal_rows = [row for row in rows if row.get("status") in {"completed", "failed"}]
    completed = sum(1 for row in rows if row.get("status") == "completed")
    failed = sum(1 for row in rows if row.get("status") == "failed")
    running = sum(1 for row in rows if row.get("status") == "running")
    terminal_count = len(terminal_rows)
    return {
        "window_hours": hours,
        "total_runs": len(rows),
        "completed_runs": completed,
        "failed_runs": failed,
        "running_runs": running,
        "success_rate": round(completed / terminal_count, 4) if terminal_count else None,
        "total_items": sum(int(row.get("total") or 0) for row in rows),
        "updated_items": sum(int(row.get("updated") or 0) for row in rows),
        "skipped_items": sum(int(row.get("skipped") or 0) for row in rows),
        "failed_items": sum(int(row.get("failed") or 0) for row in rows),
        "last_started_at": rows[-1].get("started_at") if rows else "",
    }


def bucket_start_iso(value: str | None, bucket_hours: int) -> str | None:
    parsed = parse_observability_time(value)
    if not parsed:
        return None
    bucket_seconds = max(1, bucket_hours) * 3600
    bucket_epoch = int(parsed.timestamp()) // bucket_seconds * bucket_seconds
    return datetime.fromtimestamp(bucket_epoch, timezone.utc).replace(microsecond=0).isoformat()


def build_sync_trend(hours: int = 24, bucket_hours: int = 1) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in sync_run_rows_since(hours):
        bucket = bucket_start_iso(row.get("started_at"), bucket_hours)
        if not bucket:
            continue
        entry = buckets.setdefault(
            bucket,
            {"bucket_start": bucket, "total_runs": 0, "completed_runs": 0, "failed_runs": 0, "failed_items": 0},
        )
        entry["total_runs"] += 1
        if row.get("status") == "completed":
            entry["completed_runs"] += 1
        if row.get("status") == "failed":
            entry["failed_runs"] += 1
        entry["failed_items"] += int(row.get("failed") or 0)
    return [buckets[key] for key in sorted(buckets)]


def build_disk_trend() -> list[dict]:
    runtime = runtime_state()
    disk_free = runtime.get("disk_free_bytes", {})
    disk_total = runtime.get("disk_total_bytes", {})
    if not disk_free.get("value"):
        return []
    return [
        {
            "recorded_at": disk_free.get("updated_at"),
            "disk_free_bytes": disk_free.get("value"),
            "disk_total_bytes": disk_total.get("value"),
        }
    ]


def build_failure_breakdown(hours: int = 168, limit: int = 20) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0)
    mirror_by_source = {mirror["source"]: mirror for mirror in valid_mirrors(load_config())}
    counters: dict[tuple[str, str, str, str], dict] = {}
    rows = db_rows(
        """
        SELECT id, run_id, source, target, copy_target, status, step, error, started_at, ended_at, duration_ms
        FROM sync_run_items
        WHERE status = 'failed' AND started_at >= ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (cutoff.isoformat(), max(1, min(limit * 10, 500))),
    )
    for row in rows:
        explanation = explain_operational_error(row.get("error") or row.get("step") or "")
        mirror = mirror_by_source.get(row.get("source") or "", {})
        source_registry = image_registry_host(row.get("source") or "")
        target_registry = image_registry_host(row.get("target") or row.get("copy_target") or "")
        group = mirror.get("group") or "default"
        key = (explanation["category"], source_registry, target_registry, group)
        entry = counters.setdefault(
            key,
            {
                "category": explanation["category"],
                "reason": explanation["reason"],
                "suggestion": explanation["suggestion"],
                "source_registry": source_registry,
                "target_registry": target_registry,
                "group": group,
                "project": mirror.get("project") or "default",
                "environment": mirror.get("environment") or "local",
                "count": 0,
                "latest_at": "",
                "sample_sources": [],
            },
        )
        entry["count"] += 1
        latest_at = row.get("ended_at") or row.get("started_at") or ""
        if latest_at > entry["latest_at"]:
            entry["latest_at"] = latest_at
        source = row.get("source") or ""
        if source and source not in entry["sample_sources"] and len(entry["sample_sources"]) < 3:
            entry["sample_sources"].append(source)
    return sorted(counters.values(), key=lambda item: (-item["count"], item["category"]))[:limit]


def count_consecutive_failed_runs(limit: int = 10) -> int:
    rows = db_rows(
        """
        SELECT status
        FROM sync_runs
        WHERE status IN ('completed', 'failed')
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, min(limit, 50)),),
    )
    count = 0
    for row in rows:
        if row.get("status") != "failed":
            break
        count += 1
    return count


def seconds_between(started_at: str | None, ended_at: str | None) -> float | None:
    start = parse_observability_time(started_at)
    end = parse_observability_time(ended_at)
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds())


def build_queue_health() -> dict:
    rows = db_rows("SELECT status, COUNT(*) AS count FROM sync_queue GROUP BY status")
    by_status = {row["status"]: int(row.get("count") or 0) for row in rows}
    active_statuses = {"queued", "running", "paused", "cancel_requested"}
    oldest = db_one(
        """
        SELECT id, status, reason, created_at, scheduled_at
        FROM sync_queue
        WHERE status IN ('queued', 'running', 'paused', 'cancel_requested')
        ORDER BY scheduled_at ASC, id ASC
        LIMIT 1
        """
    )
    oldest_age = None
    if oldest:
        created_at = parse_observability_time(oldest.get("created_at") or oldest.get("scheduled_at"))
        oldest_age = int((datetime.now(timezone.utc) - created_at).total_seconds()) if created_at else None
    return {
        "by_status": by_status,
        "active": sum(by_status.get(status, 0) for status in active_statuses),
        "queued": by_status.get("queued", 0),
        "running": by_status.get("running", 0),
        "paused": by_status.get("paused", 0),
        "cancel_requested": by_status.get("cancel_requested", 0),
        "oldest_active": dict(oldest) if oldest else None,
        "oldest_active_age_seconds": oldest_age,
    }


def build_worker_health() -> dict:
    rows = db_rows(
        """
        SELECT worker_id, name, environment, status, last_heartbeat, version, message
        FROM workers
        ORDER BY last_heartbeat DESC
        """
    )
    now = datetime.now(timezone.utc)
    workers = []
    max_age = None
    stale_count = 0
    for row in rows:
        heartbeat_at = parse_observability_time(row.get("last_heartbeat"))
        age = int((now - heartbeat_at).total_seconds()) if heartbeat_at else None
        if age is not None:
            max_age = age if max_age is None else max(max_age, age)
        is_stale = age is None or age > 600
        if is_stale:
            stale_count += 1
        workers.append({
            "worker_id": row["worker_id"],
            "name": row["name"],
            "environment": row["environment"],
            "status": "stale" if is_stale else row["status"],
            "last_heartbeat": row["last_heartbeat"],
            "heartbeat_age_seconds": age,
            "version": row.get("version") or "",
            "message": row.get("message") or "",
        })
    return {
        "total": len(workers),
        "online": sum(1 for item in workers if item["status"] in {"online", "active", "idle"}),
        "stale": stale_count,
        "max_heartbeat_age_seconds": max_age,
        "workers": workers[:20],
    }


def build_sync_duration_stats(hours: int = 24) -> dict:
    durations = []
    for row in sync_run_rows_since(hours):
        seconds = seconds_between(row.get("started_at"), row.get("ended_at"))
        if seconds is not None and row.get("status") in {"completed", "failed"}:
            durations.append(seconds)
    if not durations:
        return {"window_hours": hours, "count": 0, "avg_seconds": None, "max_seconds": None}
    return {
        "window_hours": hours,
        "count": len(durations),
        "avg_seconds": round(sum(durations) / len(durations), 2),
        "max_seconds": round(max(durations), 2),
    }


def build_top_failed_mirrors(hours: int = 24 * 7, limit: int = 10) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0)
    rows = db_rows(
        """
        SELECT source, target, COUNT(*) AS failed_count, MAX(ended_at) AS latest_failed_at
        FROM sync_run_items
        WHERE status = 'failed' AND started_at >= ?
        GROUP BY source, target
        ORDER BY failed_count DESC, latest_failed_at DESC
        LIMIT ?
        """,
        (cutoff.isoformat(), max(1, min(limit, 50))),
    )
    return [
        {
            "source": row["source"],
            "target": row.get("target") or "",
            "failed_count": int(row.get("failed_count") or 0),
            "latest_failed_at": row.get("latest_failed_at") or "",
        }
        for row in rows
    ]


def build_webhook_status(runtime: dict | None = None) -> dict:
    state = runtime or runtime_state()
    last_sent_at = state.get("notify_last_sent_at", {}).get("value")
    last_suppressed_at = state.get("notify_last_suppressed_at", {}).get("value")
    last_error_at = state.get("notify_last_error_at", {}).get("value")
    last_error = state.get("notify_last_error", {}).get("value")
    latest_status = "not_configured"
    if settings_with_defaults().get("notify_webhook_configured"):
        latest_status = "configured"
    if last_sent_at:
        latest_status = "sent"
    if last_suppressed_at and (not last_sent_at or last_suppressed_at > last_sent_at):
        latest_status = "suppressed"
    if last_error_at and (not last_sent_at or last_error_at > last_sent_at):
        latest_status = "error"
    return {
        "configured": bool(settings_with_defaults().get("notify_webhook_configured")),
        "latest_status": latest_status,
        "last_sent_at": last_sent_at,
        "last_sent_event": state.get("notify_last_sent_event", {}).get("value"),
        "last_suppressed_at": last_suppressed_at,
        "last_suppressed_event": state.get("notify_last_suppressed_event", {}).get("value"),
        "last_error_at": last_error_at,
        "last_error": last_error,
    }


def observability_alert(alert_id: str, severity: str, title: str, message: str, suggestion: str, details: dict | None = None) -> dict:
    fingerprint_source = json.dumps(
        {"id": alert_id, "severity": severity, "message": message, "details": details or {}},
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "id": alert_id,
        "severity": severity,
        "title": title,
        "message": message,
        "suggestion": suggestion,
        "details": details or {},
        "fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:16],
        "debounce_seconds": 1800,
        "active": True,
    }


def build_observability_alerts(status: dict, windows: dict, failure_breakdown: list[dict]) -> list[dict]:
    alerts = []
    latest = status.get("latest_run") or {}
    runtime = runtime_state()
    deletion_count = deletion_mark_count()
    if status.get("disk_low"):
        alerts.append(
            observability_alert(
                "disk_low",
                "error",
                "磁盘空间不足",
                "Registry 数据盘低于磁盘阈值。",
                "先清理无用 tag、执行 Registry garbage-collect，或扩容数据卷。",
                {"disk_free_bytes": status.get("disk_free_bytes")},
            )
        )
    consecutive_failures = count_consecutive_failed_runs()
    if consecutive_failures >= 2:
        alerts.append(
            observability_alert(
                "consecutive_sync_failures",
                "error",
                "连续同步失败",
                f"最近 {consecutive_failures} 次同步任务连续失败。",
                "优先查看失败聚合中的最高频分类，并运行同步预检或诊断包。",
                {"consecutive_failures": consecutive_failures},
            )
        )
    elif latest.get("status") == "failed" or int(latest.get("failed") or 0) > 0:
        alerts.append(
            observability_alert(
                "latest_run_failed",
                "warning",
                "最近同步失败",
                "最近一轮同步存在失败项。",
                "打开同步任务详情，按失败阶段和错误分类定位。",
                {"run_id": latest.get("id"), "failed": latest.get("failed")},
            )
        )
    last_heartbeat = status.get("last_heartbeat") or runtime.get("last_heartbeat", {}).get("value")
    heartbeat_at = parse_observability_time(last_heartbeat)
    heartbeat_threshold = max(3600, int(status.get("interval") or 30) * 180)
    if not heartbeat_at:
        alerts.append(
            observability_alert(
                "missing_sync_heartbeat",
                "warning",
                "同步心跳缺失",
                "sync worker 尚未写入心跳。",
                "确认 sync 容器已启动，并检查数据库连接和运行日志。",
            )
        )
    else:
        heartbeat_age = int((datetime.now(timezone.utc) - heartbeat_at).total_seconds())
        if heartbeat_age > heartbeat_threshold:
            alerts.append(
                observability_alert(
                    "stale_sync_heartbeat",
                    "error",
                    "同步心跳过期",
                    f"sync worker 心跳已超过 {heartbeat_age} 秒未更新。",
                    "检查 sync 容器状态、调度器和数据库写入权限。",
                    {"last_heartbeat": last_heartbeat, "age_seconds": heartbeat_age, "threshold_seconds": heartbeat_threshold},
                )
            )
    if deletion_count:
        severity = "error" if deletion_count >= 20 else "warning"
        alerts.append(
            observability_alert(
                "pending_deletion_marks",
                severity,
                "删除标记积压",
                f"当前存在 {deletion_count} 个删除标记等待处理。",
                "确认受保护 tag 后，按维护窗口执行 Registry garbage-collect。",
                {"deletion_marks": deletion_count},
            )
        )
    top_failure = failure_breakdown[0] if failure_breakdown else None
    if top_failure and int(top_failure.get("count") or 0) >= 3:
        alerts.append(
            observability_alert(
                "repeated_failure_category",
                "warning",
                "失败类型集中",
                f"{top_failure['category']} 类型在最近 7 天出现 {top_failure['count']} 次。",
                top_failure.get("suggestion") or "查看失败聚合和任务明细。",
                {"category": top_failure["category"], "count": top_failure["count"]},
            )
        )
    return alerts


def build_observability_summary() -> dict:
    status = get_status()
    windows = {
        "24h": build_sync_window_stats(24),
        "7d": build_sync_window_stats(24 * 7),
    }
    trend = build_sync_trend(hours=24, bucket_hours=1)
    failure_breakdown = build_failure_breakdown(hours=24 * 7)
    alerts = build_observability_alerts(status, windows, failure_breakdown)
    runtime = runtime_state()
    queue_health = build_queue_health()
    worker_health = build_worker_health()
    duration_24h = build_sync_duration_stats(24)
    top_failed_mirrors = build_top_failed_mirrors(24 * 7)
    webhook_status = build_webhook_status(runtime)
    last_heartbeat = status.get("last_heartbeat") or runtime.get("last_heartbeat", {}).get("value")
    heartbeat_at = parse_observability_time(last_heartbeat)
    heartbeat_age = int((datetime.now(timezone.utc) - heartbeat_at).total_seconds()) if heartbeat_at else None
    health = "error" if any(item["severity"] == "error" for item in alerts) else ("warn" if alerts else "ok")
    return {
        "generated_at": now_iso(),
        "health": health,
        "version": {"app_version": APP_VERSION, "image_tag": IMAGE_TAG},
        "windows": windows,
        "trend": trend,
        "disk_trend": build_disk_trend(),
        "failure_breakdown": failure_breakdown,
        "top_failed_mirrors": top_failed_mirrors,
        "alerts": alerts,
        "queue": queue_health,
        "workers": worker_health,
        "storage": {
            "disk_low": bool(status.get("disk_low")),
            "disk_free_bytes": status.get("disk_free_bytes"),
            "deletion_marks": deletion_mark_count(),
        },
        "runtime": {
            "last_heartbeat": last_heartbeat,
            "heartbeat_age_seconds": heartbeat_age,
            "sync_running": bool(status.get("is_syncing")),
            "next_run_at": status.get("next_run_at"),
            "avg_sync_duration_seconds_24h": duration_24h["avg_seconds"],
            "max_sync_duration_seconds_24h": duration_24h["max_seconds"],
        },
        "notifications": {
            "webhook_configured": webhook_status["configured"],
            "webhook_latest_status": webhook_status["latest_status"],
            "dedupe_seconds": 1800,
            "delivery": "sync worker webhook",
            "last_sent_at": webhook_status["last_sent_at"],
            "last_sent_event": webhook_status["last_sent_event"],
            "last_suppressed_at": webhook_status["last_suppressed_at"],
            "last_suppressed_event": webhook_status["last_suppressed_event"],
            "last_error_at": webhook_status["last_error_at"],
            "last_error": webhook_status["last_error"],
        },
        "metrics": {
            "sync_success_rate_24h": windows["24h"]["success_rate"],
            "sync_success_rate_7d": windows["7d"]["success_rate"],
            "sync_failed_runs_24h": windows["24h"]["failed_runs"],
            "sync_failed_items_24h": windows["24h"]["failed_items"],
            "sync_queue_active": queue_health["active"],
            "sync_queue_queued": queue_health["queued"],
            "sync_avg_duration_seconds_24h": duration_24h["avg_seconds"],
            "observability_active_alerts": len(alerts),
            "storage_deletion_marks": deletion_mark_count(),
            "storage_disk_free_bytes": status.get("disk_free_bytes"),
            "sync_consecutive_failures": count_consecutive_failed_runs(),
            "sync_heartbeat_age_seconds": heartbeat_age,
            "worker_max_heartbeat_age_seconds": worker_health["max_heartbeat_age_seconds"],
            "worker_stale_count": worker_health["stale"],
            "webhook_latest_status": webhook_status["latest_status"],
        },
    }


def build_ops_summary() -> dict:
    status = get_status()
    latest = status.get("latest_run")
    failures = recent_failed_items(5)
    deletion_count = deletion_mark_count()
    health = "ok"
    reasons = []
    if status.get("disk_low"):
        health = "error"
        reasons.append("disk_low")
    if latest and int(latest.get("failed") or 0) > 0:
        health = "error"
        reasons.append("latest_run_failed")
    if status.get("is_syncing"):
        reasons.append("sync_active")
    if deletion_count:
        reasons.append("pending_deletion_marks")
        if health == "ok":
            health = "warn"
    return {
        "health": health,
        "reasons": reasons,
        "generated_at": now_iso(),
        "version": {"app_version": APP_VERSION, "image_tag": IMAGE_TAG},
        "sync": {
            "running": bool(status.get("is_syncing")),
            "latest_run": latest,
            "recent_failures": failures,
        },
        "storage": {
            "disk_low": bool(status.get("disk_low")),
            "disk_free_bytes": status.get("disk_free_bytes"),
            "deletion_marks": deletion_count,
        },
        "config": {
            "mirrors": status["total"],
            "synced": status["synced"],
            "pending": status["pending"],
            "registries": status["registries"],
            "mirror_groups": status["mirror_groups"],
            "database_backend": status["database_backend"],
        },
        "security": {
            "auth_required": status["auth_required"],
            "admin_initialized": status["admin_initialized"],
        },
    }


SENSITIVE_EXPORT_KEYS = {"secret", "token", "password", "cookie", "authfile", "encrypted_secret", "authorization"}


def sanitize_for_export(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in SENSITIVE_EXPORT_KEYS):
                clean["<redacted>"] = "<redacted>"
            else:
                clean[key] = sanitize_for_export(item)
        return clean
    if isinstance(value, list):
        return [sanitize_for_export(item) for item in value]
    if isinstance(value, str):
        redacted = value
        redacted = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", redacted)
        redacted = re.sub(r"(?i)(password|token|secret)=([^\s&]+)", r"\1=<redacted>", redacted)
        redacted = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@:\s]+:[^/@\s]+@", r"\1<redacted>@", redacted)
        return redacted
    return value


async def build_diagnostic_bundle() -> dict:
    config = load_config()
    diagnostics = await run_diagnostics()
    recent_runs = list_sync_runs(limit=10)
    events = list_events(limit=50)
    bundle = {
        "generated_at": now_iso(),
        "version": {"app_version": APP_VERSION, "image_tag": IMAGE_TAG},
        "summary": build_ops_summary(),
        "config_summary": {
            "mirrors": len(valid_mirrors(config)),
            "registries": len(registry_map(config)),
            "mirror_groups": len(group_map(config)),
            "settings": settings_with_defaults(),
        },
        "diagnostics": diagnostics,
        "recent_runs": recent_runs,
        "recent_failures": recent_failed_items(20),
        "events": events,
        "deletion_marks": deletion_mark_count(),
        "upgrade_guide": build_upgrade_guide(),
        "install_upgrade": build_install_upgrade_guide(include_preflight=False),
    }
    return sanitize_for_export(bundle)


def build_upgrade_guide() -> dict:
    install_upgrade = build_install_upgrade_guide(include_preflight=False)
    return {
        "principles": [
            "升级前先备份 config/、data/registry/、data/mirror-registry.db、.env 和 CREDENTIALS_SECRET_KEY。",
            "先运行只读恢复演练和生产 smoke，再启动 sync 写入。",
            "正式 release 仍由 v* tag 触发，latest 表示最新正式版本。",
        ],
        "environment_variables": [
            "MIRROR_REGISTRY_IMAGE_TAG",
            "ADMIN_USERNAME",
            "ADMIN_PASSWORD",
            "CREDENTIALS_SECRET_KEY",
            "SESSION_COOKIE_SECURE",
            "DATABASE_URL",
        ],
        "volumes": [
            "mirror-registry-config:/config",
            "mirror-registry-data:/data",
            "mirror-registry-storage:/var/lib/registry",
        ],
        "commands": [
            "python scripts\\verify.py",
            "powershell -ExecutionPolicy Bypass -File .\\scripts\\restore-drill.ps1",
            "powershell -ExecutionPolicy Bypass -File .\\scripts\\prod-smoke.ps1 -AllowInsecureLocal",
            "docker compose pull",
            "docker compose up -d",
        ],
        "compatibility": [
            "SQLite remains the default database path.",
            "Panel API access uses account login with a session cookie.",
            "sync continues to use skopeo and does not require host Docker socket.",
        ],
        "install_upgrade": install_upgrade,
    }


def command_safe_tag(value: str | None, fallback: str) -> str:
    raw = str(value or "").strip()
    return raw if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", raw) else fallback


def build_upgrade_command_set(expected_tag: str | None = None, previous_tag: str | None = None) -> dict:
    target_tag = command_safe_tag(expected_tag, command_safe_tag(IMAGE_TAG, "latest"))
    rollback_tag = command_safe_tag(previous_tag, "<previous-tag>")
    return {
        "first_install": "Copy .env.example to .env, edit secrets, then docker compose pull && docker compose up -d",
        "host_preflight": f"powershell -ExecutionPolicy Bypass -File .\\{UPGRADE_CHECK_SCRIPT_LABEL} -ExpectedTag {target_tag}",
        "backup": "powershell -ExecutionPolicy Bypass -File .\\scripts\\migration-report.ps1 -ReportPath .\\migration-report.json",
        "upgrade": "docker compose pull && docker compose up -d",
        "rollback": f"Set MIRROR_REGISTRY_IMAGE_TAG={rollback_tag} in .env, then docker compose pull && docker compose up -d",
        "verify": "powershell -ExecutionPolicy Bypass -File .\\scripts\\prod-smoke.ps1 -AllowInsecureLocal",
        "status": "curl http://localhost:8080/api/status",
    }


def build_upgrade_runtime_summary() -> dict:
    settings = settings_with_defaults()
    return {
        "app_version": APP_VERSION,
        "image_tag": IMAGE_TAG,
        "database_backend": settings["database_backend"],
        "registry_url": settings["registry_url"],
        "admin_initialized": admin_user_exists(),
        "admin_password_configured": bool(ADMIN_PASSWORD.strip()),
        "credentials_secret_configured": bool(CREDENTIALS_SECRET_KEY.strip()),
        "worker_token_configured": bool(WORKER_TOKEN.strip()),
        "active_queue": queue_count_by_status(["queued", "running", "paused", "cancel_requested"]),
    }


def build_upgrade_preflight(body: InstallUpgradePreflightIn | None = None) -> dict:
    body = body or InstallUpgradePreflightIn()
    expected_tag = str(body.expected_tag or "").strip()
    checks: list[dict] = []

    checks.append(
        drill_check(
            "image_tag",
            "warn" if expected_tag and IMAGE_TAG != expected_tag else "ok",
            f"running image tag: {IMAGE_TAG}" + (f", expected: {expected_tag}" if expected_tag else ""),
            "确认 .env 中 MIRROR_REGISTRY_IMAGE_TAG 与准备部署的 release tag 一致",
            {"image_tag": IMAGE_TAG, "expected_tag": expected_tag},
        )
    )

    checks.append(
        drill_check(
            "admin_account",
            "ok" if admin_user_exists() else "error",
            "管理员账号已初始化" if admin_user_exists() else "管理员账号未初始化",
            "首次部署必须设置 ADMIN_USERNAME 和 ADMIN_PASSWORD 后重启 panel",
            {"admin_password_configured": bool(ADMIN_PASSWORD.strip())},
        )
    )
    if admin_user_exists() and not ADMIN_PASSWORD.strip():
        checks.append(
            drill_check(
                "admin_password_env",
                "warn",
                "ADMIN_PASSWORD 当前进程未配置",
                "已有数据卷可继续使用旧管理员密码；新部署必须在 .env 中设置 ADMIN_PASSWORD",
            )
        )

    credential_count = len(credential_rows())
    if CREDENTIALS_SECRET_KEY.strip():
        credential_status = "ok"
        credential_message = "CREDENTIALS_SECRET_KEY 已配置且未输出内容"
    elif credential_count:
        credential_status = "error"
        credential_message = f"已有 {credential_count} 条仓库凭据，但缺少 CREDENTIALS_SECRET_KEY"
    else:
        credential_status = "warn"
        credential_message = "CREDENTIALS_SECRET_KEY 未配置"
    checks.append(
        drill_check(
            "credentials_secret",
            credential_status,
            credential_message,
            "保存或恢复加密仓库凭据前必须使用原始 CREDENTIALS_SECRET_KEY",
            {"credential_count": credential_count},
        )
    )

    for name, path, required, write_check in [
        ("config_file", CONFIG_PATH, True, False),
        ("config_volume", CONFIG_PATH.parent, True, True),
        ("data_volume", LOG_PATH.parent, True, True),
        ("registry_storage", REGISTRY_STORAGE_PATH, False, False),
    ]:
        exists = path.exists()
        writable = os.access(path, os.W_OK) if write_check and exists else True
        status = "ok" if exists and writable else ("error" if required else "warn")
        message = f"{path} {'存在' if exists else '缺失'}"
        if exists and write_check:
            message += "，可写" if writable else "，不可写"
        checks.append(
            drill_check(
                name,
                status,
                message,
                "确认 Docker volume 挂载正确；生产默认使用 named volumes",
                {"path": str(path), "exists": exists, "writable": writable},
            )
        )

    try:
        db_rows("SELECT 1")
        checks.append(drill_check("database", "ok", f"数据库可读: {database_backend(DATABASE_URL)}", details={"backend": database_backend(DATABASE_URL)}))
    except Exception as exc:
        checks.append(drill_check("database", "error", f"数据库不可读: {exc}", "升级前先恢复数据库可读状态"))

    try:
        base_path = REGISTRY_STORAGE_PATH if REGISTRY_STORAGE_PATH.exists() else LOG_PATH.parent
        usage = shutil.disk_usage(base_path)
        threshold = env_int("DISK_LOW_BYTES", 2147483648, 1048576, 1024 * 1024 * 1024 * 1024)
        checks.append(
            drill_check(
                "disk_space",
                "warn" if usage.free < threshold else "ok",
                f"可用 {usage.free} bytes / 总计 {usage.total} bytes",
                "升级前保留足够空间给 Registry 数据、SQLite 和回滚备份",
                {"free_bytes": usage.free, "total_bytes": usage.total, "threshold_bytes": threshold},
            )
        )
    except OSError as exc:
        checks.append(drill_check("disk_space", "warn", f"无法读取磁盘空间: {exc}", f"在部署宿主机运行 {UPGRADE_CHECK_SCRIPT_LABEL} 复核"))

    active_queue = queue_count_by_status(["queued", "running", "paused", "cancel_requested"])
    checks.append(
        drill_check(
            "sync_queue",
            "warn" if active_queue else "ok",
            f"活动同步队列任务 {active_queue} 个",
            "升级前建议等待队列清空，或记录需要重放的任务",
            {"active": active_queue},
        )
    )

    for name, path in [("compose_file", Path("docker-compose.yml")), ("upgrade_script", Path("scripts/upgrade-check.ps1"))]:
        exists = path.exists()
        checks.append(
            drill_check(
                name,
                "ok" if exists else "warn",
                f"{path} {'存在' if exists else '当前容器内不可见'}",
                f"在部署宿主机保留 docker-compose.yml、.env 和 {UPGRADE_CHECK_SCRIPT_LABEL}",
                {"path": str(path), "exists": exists},
            )
        )

    docker_path = shutil.which("docker")
    checks.append(
        drill_check(
            "docker_cli",
            "ok" if docker_path else "warn",
            f"docker: {docker_path}" if docker_path else "当前运行环境未发现 docker 命令",
            "在部署宿主机执行升级命令前确认 Docker 和 Compose 可用",
        )
    )

    summary = summarize_drill_checks(checks)
    return {
        "ok": summary["error"] == 0,
        "readonly": True,
        "checked_at": now_iso(),
        "summary": summary,
        "runtime": build_upgrade_runtime_summary(),
        "checks": checks,
        "commands": build_upgrade_command_set(expected_tag=body.expected_tag, previous_tag=body.previous_tag),
    }


def build_install_upgrade_guide(include_preflight: bool = True) -> dict:
    guide = {
        "title": "Mirror Registry install and upgrade guide",
        "readonly": True,
        "generated_at": now_iso(),
        "runtime": build_upgrade_runtime_summary(),
        "required_items": [
            "docker-compose.yml",
            ".env",
            "ADMIN_USERNAME",
            "ADMIN_PASSWORD",
            "CREDENTIALS_SECRET_KEY",
            "MIRROR_REGISTRY_IMAGE_TAG",
            "mirror-registry-config",
            "mirror-registry-data",
            "mirror-registry-storage",
        ],
        "stages": [
            {"name": "first_install", "goal": "复制 .env.example，设置强随机密钥，拉取镜像并启动 compose。"},
            {"name": "preflight", "goal": f"运行 /api/install-upgrade/preflight 或 {UPGRADE_CHECK_SCRIPT_LABEL}，确认版本、密钥、数据卷和队列状态。"},
            {"name": "backup", "goal": "生成迁移报告或备份包，保留 .env、SQLite、config 和 Registry 数据。"},
            {"name": "upgrade", "goal": "设置目标 MIRROR_REGISTRY_IMAGE_TAG，执行 docker compose pull && docker compose up -d。"},
            {"name": "verify", "goal": "查看 /api/status、运行 prod-smoke，并确认 sync 队列和最近任务正常。"},
            {"name": "rollback", "goal": "把 MIRROR_REGISTRY_IMAGE_TAG 改回上一版本，重新 pull/up，并用 smoke 复核。"},
        ],
        "commands": build_upgrade_command_set(),
        "host_script": f"powershell -ExecutionPolicy Bypass -File .\\{UPGRADE_CHECK_SCRIPT_LABEL} -ExpectedTag <target-tag> -ReportPath .\\upgrade-check.json",
        "compatibility": [
            "默认仍是单机 Docker Compose，不要求外部数据库或远程 worker。",
            "SQLite 数据路径保持 data/mirror-registry.db；面板 API 使用账号登录后的 session cookie。",
            "升级和回滚命令只生成清单，不由面板自动执行 destructive 操作。",
            "离线或内网环境可只使用本地脚本和已固定的 MIRROR_REGISTRY_IMAGE_TAG。",
        ],
    }
    if include_preflight:
        guide["preflight"] = build_upgrade_preflight()
    return guide


def summarize_platform(config: dict) -> dict:
    mirrors = valid_mirrors(config)
    registries = registry_map(config)
    groups = group_map(config)
    projects = sorted({mirror["project"] for mirror in mirrors})
    environments = sorted({mirror["environment"] for mirror in mirrors})
    namespaces = sorted({mirror["namespace"] for mirror in mirrors})
    return {
        "registries": list(registries.values()),
        "mirror_groups": list(groups.values()),
        "projects": projects,
        "environments": environments,
        "namespaces": namespaces,
        "mirror_count": len(mirrors),
        "external_database": external_database_guide(),
        "deployment_modes": deployment_modes(),
    }


def grouped_mirror_summary(config: dict) -> list[dict]:
    mirrors = valid_mirrors(config)
    groups = group_map(config)
    counters: dict[tuple[str, str, str, str, str], dict] = {}
    for mirror in mirrors:
        key = (
            mirror["project"],
            mirror["environment"],
            mirror["namespace"],
            mirror["registry"],
            mirror["group"],
        )
        item = counters.setdefault(
            key,
            {
                "project": mirror["project"],
                "environment": mirror["environment"],
                "namespace": mirror["namespace"],
                "registry": mirror["registry"],
                "group": mirror["group"],
                "group_name": groups.get(mirror["group"], {}).get("name", mirror["group"]),
                "mirror_count": 0,
            },
        )
        item["mirror_count"] += 1
    return sorted(counters.values(), key=lambda item: (item["project"], item["environment"], item["namespace"], item["group"]))


def deployment_modes() -> list[dict]:
    return [
        {
            "id": "single-node",
            "status": "default",
            "description": "默认单机部署：registry、panel、sync 通过 docker compose 在同一台服务器运行。",
        },
        {
            "id": "multi-instance",
            "status": "planned",
            "description": "多实例部署可共享外部数据库和配置卷，但需要避免多个 sync 同时处理同一镜像组。",
        },
        {
            "id": "remote-worker",
            "status": "planned",
            "description": "远程 worker 可按镜像组消费任务，适合跨网络或多环境同步。",
        },
        {
            "id": "queued-sync",
            "status": "planned",
            "description": "队列化同步可将触发、调度和执行解耦，但不作为默认部署路径。",
        },
    ]


@app.get("/api/status")
def get_status():
    config = load_config()
    settings = settings_with_defaults()
    state = load_state()
    seed_mirror_rules_from_config()
    mirrors = mirror_rule_rows()
    synced = sum(1 for mirror in mirrors if mirror.get("last_source_digest") or mirror.get("last_digest") or state.get(mirror["source"]))
    runtime = runtime_state()
    running = runtime.get("sync_running", {}).get("value") == "true"
    return {
        "total": len(mirrors),
        "registries": len(registry_map(config)),
        "mirror_groups": len(group_map(config)),
        "synced": synced,
        "pending": len(mirrors) - synced,
        "interval": settings["check_interval_minutes"],
        "sync_concurrency": settings["sync_concurrency"],
        "sync_retry_count": settings["sync_retry_count"],
        "is_syncing": running or TRIGGER_PATH.exists(),
        "auth_required": True,
        "auth_mode": "session",
        "admin_initialized": admin_user_exists(),
        "session_cookie_secure": SESSION_COOKIE_SECURE,
        "session_cookie_samesite": SESSION_COOKIE_SAMESITE,
        "app_version": APP_VERSION,
        "image_tag": IMAGE_TAG,
        "last_started_at": runtime.get("last_started_at", {}).get("value"),
        "last_finished_at": runtime.get("last_finished_at", {}).get("value"),
        "last_heartbeat": runtime.get("last_heartbeat", {}).get("value"),
        "next_run_at": runtime.get("next_run_at", {}).get("value"),
        "sync_engine": runtime.get("sync_engine", {}).get("value", "skopeo"),
        "database_backend": settings["database_backend"],
        "disk_free_bytes": runtime.get("disk_free_bytes", {}).get("value"),
        "disk_low": runtime.get("disk_low", {}).get("value") == "true",
        "latest_run": latest_run(),
    }


@app.get("/api/ops/summary")
def get_ops_summary():
    return build_ops_summary()


@app.get("/api/ops/upgrade-guide")
def get_ops_upgrade_guide():
    return build_upgrade_guide()


@app.get("/api/install-upgrade/guide")
def get_install_upgrade_guide():
    return build_install_upgrade_guide()


@app.get("/api/setup/checklist")
def get_setup_checklist():
    return build_upgrade_preflight()


@app.post("/api/install-upgrade/preflight", dependencies=[Depends(require_write_token)])
def run_install_upgrade_preflight(body: InstallUpgradePreflightIn):
    result = build_upgrade_preflight(body)
    audit_log("preflight", "install_upgrade", "readonly", {"ok": result["ok"], "summary": result["summary"]})
    return result


@app.get("/api/ops/diagnostic-bundle")
async def get_ops_diagnostic_bundle():
    return await build_diagnostic_bundle()


@app.get("/api/observability/summary")
def get_observability_summary():
    return build_observability_summary()


@app.get("/api/observability/metrics")
def get_observability_metrics():
    summary = build_observability_summary()
    return {
        "generated_at": summary["generated_at"],
        "health": summary["health"],
        "version": summary["version"],
        "metrics": summary["metrics"],
        "alerts": summary["alerts"],
    }


@app.get("/api/mirrors")
def list_mirrors():
    seed_mirror_rules_from_config()
    return [public_mirror_rule(row, index) for index, row in enumerate(mirror_rule_rows())]


@app.get("/api/mirrors/export")
def export_mirrors():
    seed_mirror_rules_from_config()
    config = load_config()
    settings = settings_with_defaults()
    safe_settings = {
        "check_interval_minutes": settings["check_interval_minutes"],
        "registry_url": settings["registry_url"],
        "sync_concurrency": settings["sync_concurrency"],
        "sync_retry_count": settings["sync_retry_count"],
    }
    return {
        "version": 2,
        "exported_at": now_iso(),
        "registries": list(registry_map(config).values()),
        "mirror_groups": list(group_map(config).values()),
        "mirrors": [mirror_rule_export(row) for row in mirror_rule_rows()],
        "settings": safe_settings,
    }


@app.post("/api/mirrors/import", dependencies=[Depends(require_write_token)])
def import_mirrors(body: MirrorImportIn):
    imported: list[dict] = []
    seen_sources: set[str] = set()
    for item in body.mirrors:
        source = validate_image_ref(item.source, "source")
        if source in seen_sources:
            continue
        seen_sources.add(source)
        imported.append(normalize_mirror(item.model_dump()))
    if not imported:
        raise HTTPException(400, "导入内容没有有效镜像")

    config = load_config()
    if body.registries:
        registry_by_id = registry_map(config)
        for item in body.registries:
            normalized = normalize_registry(item)
            if normalized["id"] != "local":
                registry_by_id[normalized["id"]] = normalized
        config["registries"] = [item for key, item in registry_by_id.items() if key != "local"]
    if body.mirror_groups:
        groups_by_id = group_map(config)
        for item in body.mirror_groups:
            normalized = normalize_group(item)
            if normalized["id"] != "default":
                groups_by_id[normalized["id"]] = normalized
        config["mirror_groups"] = [item for key, item in groups_by_id.items() if key != "default"]
    if body.replace:
        mirrors = imported
    else:
        mirrors_by_source = {mirror["source"]: mirror for mirror in valid_mirrors(config)}
        for mirror in imported:
            mirrors_by_source[mirror["source"]] = mirror
        mirrors = list(mirrors_by_source.values())
    config["mirrors"] = mirrors
    save_config(config)
    for mirror in imported:
        upsert_mirror_db(mirror["source"], mirror["target"], mirror=mirror)
    audit_log("import", "mirrors", "bulk", {"imported": len(imported), "replace": body.replace})
    return {"ok": True, "imported": len(imported), "total": len(mirrors), "replace": body.replace}


@app.post("/api/mirrors/discover")
def discover_mirrors(body: MirrorDiscoveryIn):
    return build_discovery_preview(body)


@app.post("/api/mirrors/discover/import", dependencies=[Depends(require_write_token)])
def import_discovered_mirrors(body: MirrorDiscoveryIn):
    preview = build_discovery_preview(body)
    mode = preview["mode"]
    imported = [
        normalize_mirror(item)
        for item in preview["items"]
        if item.get("importable")
    ]
    if not imported:
        raise HTTPException(400, "发现结果没有可导入镜像")

    config = load_config()
    if mode == "replace":
        mirrors = imported
    else:
        mirrors_by_source = {mirror["source"]: mirror for mirror in valid_mirrors(config)}
        for mirror in imported:
            if mode == "missing_only" and mirror["source"] in mirrors_by_source:
                continue
            mirrors_by_source[mirror["source"]] = mirror
        mirrors = list(mirrors_by_source.values())
    config["mirrors"] = mirrors
    save_config(config)
    for mirror in imported:
        upsert_mirror_db(mirror["source"], mirror["target"], mirror=mirror)
    queue_task = None
    if body.trigger_sync:
        queue_task = write_trigger("discover-import", sources=[mirror["source"] for mirror in imported])
    audit_log(
        "discover_import",
        "mirrors",
        "bulk",
        {
            "mode": mode,
            "imported": len(imported),
            "total": len(mirrors),
            "trigger_sync": bool(body.trigger_sync),
            "source_type": preview["source_type"],
        },
    )
    return {
        "ok": True,
        "mode": mode,
        "imported": len(imported),
        "total": len(mirrors),
        "trigger_sync": bool(body.trigger_sync),
        "queue": queue_task,
        "summary": preview["summary"],
    }


@app.post("/api/mirrors/preflight", dependencies=[Depends(require_write_token)])
async def preflight_mirror(body: MirrorPreflightIn):
    result = await build_mirror_preflight(body.model_dump(), body.check_remote)
    audit_log(
        "preflight",
        "mirror",
        result.get("source") or "unknown",
        {"status": result["summary"]["status"], "check_remote": body.check_remote},
    )
    return result


@app.post("/api/mirrors/preflight/batch", dependencies=[Depends(require_write_token)])
async def preflight_mirrors_batch(body: MirrorPreflightBatchIn):
    requested = [item.model_dump() for item in body.mirrors]
    seed_mirror_rules_from_config()
    mirrors = requested if requested else [mirror_rule_export(row) for row in mirror_rule_rows()]
    mirrors = mirrors[:500]
    items = []
    for mirror in mirrors:
        items.append(await build_mirror_preflight(mirror, body.check_remote))
    summary = summarize_preflight_results(items)
    audit_log("preflight", "mirrors", "batch", {"summary": summary, "check_remote": body.check_remote})
    return {"ok": summary["error"] == 0, "check_remote": body.check_remote, "summary": summary, "items": items}


@app.post("/api/mirrors", dependencies=[Depends(require_write_token)])
def add_mirror(body: MirrorIn):
    mirror = normalize_mirror(body.model_dump())
    source = mirror["source"]
    target = mirror["target"]
    seed_mirror_rules_from_config()
    if mirror_rule_by_source(source):
        raise HTTPException(400, "该 source 已存在")
    config = load_config()
    mirrors = valid_mirrors(config)
    mirrors.append(mirror)
    config["mirrors"] = mirrors
    save_config(config)
    upsert_mirror_db(source, target, mirror=mirror)
    audit_log("create", "mirror", source, mirror)
    return {"ok": True, "mirror": public_mirror_rule(mirror_rule_by_source(source))}


@app.put("/api/mirrors/{index}", dependencies=[Depends(require_write_token)])
def update_mirror(index: int, body: MirrorIn):
    existing = mirror_rule_by_index(index)
    mirror = normalize_mirror(body.model_dump())
    if mirror["source"] != existing["source"] and mirror_rule_by_source(mirror["source"]):
        raise HTTPException(400, "该 source 已存在")
    if mirror["source"] != existing["source"]:
        delete_mirror_db(existing["source"])
    upsert_mirror_db(mirror["source"], mirror["target"], mirror=mirror)
    sync_config_from_db()
    rows = mirror_rule_rows()
    current_index = next((i for i, row in enumerate(rows) if row["source"] == mirror["source"]), index)
    audit_log("update", "mirror", mirror["source"], mirror)
    return {"ok": True, "mirror": public_mirror_rule(rows[current_index], current_index)}


@app.delete("/api/mirrors/{index}", dependencies=[Depends(require_write_token)])
def delete_mirror(index: int):
    removed = mirror_rule_by_index(index)
    state = load_state()
    state.pop(removed["source"], None)
    save_state(state)
    delete_mirror_db(removed["source"])
    sync_config_from_db()
    audit_log("delete", "mirror", removed["source"], removed)
    return {"ok": True}


@app.post("/api/mirrors/{index}/reset", dependencies=[Depends(require_write_token)])
def reset_mirror_digest(index: int):
    mirror = mirror_rule_by_index(index)
    state = load_state()
    state.pop(mirror["source"], None)
    save_state(state)
    db_execute(
        """
        UPDATE mirrors
        SET last_digest = NULL, last_source_digest = NULL, last_target_digest = NULL,
            pending_push_digest = NULL, pending_push_target = NULL, push_status = 'idle',
            check_failures = 0, push_failures = 0, last_error = NULL, updated_at = ?
        WHERE source = ?
        """,
        (now_iso(), mirror["source"]),
    )
    audit_log("reset_digest", "mirror", mirror["source"], mirror)
    return {"ok": True}


@app.post("/api/mirrors/{index}/sync", dependencies=[Depends(require_write_token)])
def trigger_mirror_sync(index: int):
    mirror = mirror_rule_by_index(index)
    task = write_trigger("manual-single", source=mirror["source"])
    audit_log("trigger_sync", "mirror", mirror["source"], {**mirror, "queue_id": task["id"]})
    return {"ok": True, "message": "单镜像同步任务已入队，请稍后查看队列和任务历史", "queue": task}


@app.post("/api/mirrors/{index}/check", dependencies=[Depends(require_write_token)])
def trigger_mirror_check(index: int):
    mirror = mirror_rule_by_index(index)
    task = enqueue_rule_task("check", mirror, force=True)
    audit_log("trigger_check", "mirror", mirror["source"], {"queue_id": task["id"]})
    return {"ok": True, "message": "检查任务已入队", "queue": task}


@app.post("/api/mirrors/{index}/push", dependencies=[Depends(require_write_token)])
def trigger_mirror_push(index: int, body: MirrorPushIn | None = None):
    mirror = mirror_rule_by_index(index)
    digest = (body.digest if body else None) or mirror.get("pending_push_digest") or mirror.get("last_source_digest") or mirror.get("last_digest") or ""
    if not digest:
        raise HTTPException(409, "该规则还没有可推送的 digest，请先执行检查")
    task = enqueue_rule_task("push", mirror, digest=digest, force=True)
    db_execute(
        """
        UPDATE mirrors
        SET pending_push_digest = ?, pending_push_target = ?, push_status = 'pending', updated_at = ?
        WHERE source = ?
        """,
        (digest, mirror["target"], now_iso(), mirror["source"]),
    )
    audit_log("trigger_push", "mirror", mirror["source"], {"queue_id": task["id"], "digest": digest})
    return {"ok": True, "message": "推送任务已入队", "queue": task}


@app.post("/api/mirrors/{index}/pause", dependencies=[Depends(require_write_token)])
def pause_mirror_rule(index: int):
    mirror = mirror_rule_by_index(index)
    db_execute("UPDATE mirrors SET enabled = 0, updated_at = ? WHERE source = ?", (now_iso(), mirror["source"]))
    audit_log("pause", "mirror", mirror["source"], {})
    return {"ok": True, "mirror": public_mirror_rule(mirror_rule_by_index(index), index)}


@app.post("/api/mirrors/{index}/resume", dependencies=[Depends(require_write_token)])
def resume_mirror_rule(index: int):
    mirror = mirror_rule_by_index(index)
    now = now_iso()
    db_execute("UPDATE mirrors SET enabled = 1, next_check_at = COALESCE(next_check_at, ?), updated_at = ? WHERE source = ?", (now, now, mirror["source"]))
    audit_log("resume", "mirror", mirror["source"], {})
    return {"ok": True, "mirror": public_mirror_rule(mirror_rule_by_index(index), index)}


@app.post("/api/mirrors/{index}/skip-pending-push", dependencies=[Depends(require_write_token)])
def skip_pending_mirror_push(index: int):
    mirror = mirror_rule_by_index(index)
    digest = mirror.get("pending_push_digest") or ""
    db_execute(
        """
        UPDATE mirrors
        SET pending_push_digest = NULL, pending_push_target = NULL, push_status = 'skipped', last_error = NULL, updated_at = ?
        WHERE source = ?
        """,
        (now_iso(), mirror["source"]),
    )
    record_mirror_event(mirror["source"], "push", "skipped", new_digest=digest, message="pending push skipped by panel")
    audit_log("skip_pending_push", "mirror", mirror["source"], {"digest": digest})
    return {"ok": True, "mirror": public_mirror_rule(mirror_rule_by_index(index), index)}


@app.get("/api/mirror-events")
def list_mirror_events(mirror_id: str = "", limit: int = 100):
    bounded_limit = max(1, min(limit, 500))
    if mirror_id:
        rows = db_rows(
            """
            SELECT id, mirror_id, type, status, old_digest, new_digest, message, detail_json, created_at
            FROM mirror_events
            WHERE mirror_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (mirror_id, bounded_limit),
        )
    else:
        rows = db_rows(
            """
            SELECT id, mirror_id, type, status, old_digest, new_digest, message, detail_json, created_at
            FROM mirror_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        )
    for row in rows:
        try:
            row["detail"] = json.loads(row.pop("detail_json") or "{}")
        except json.JSONDecodeError:
            row["detail"] = {}
    return rows


@app.get("/api/mirrors/{index}/artifact")
def download_mirror_artifact(index: int):
    mirror = mirror_rule_by_index(index)
    archive_path, filename = build_local_artifact_archive(mirror["target"])
    audit_log("download", "artifact", mirror["target"], {"source": mirror["source"], "format": "registry-storage-artifact-v1"})
    return FileResponse(
        archive_path,
        media_type="application/x-tar",
        filename=filename,
        headers={"X-Mirror-Registry-Artifact-Format": "registry-storage-artifact-v1"},
        background=BackgroundTask(lambda: archive_path.unlink(missing_ok=True)),
    )


@app.get("/api/settings")
def get_settings():
    return settings_with_defaults()


def get_registry_url() -> str:
    return settings_with_defaults()["registry_url"]


def config_database_url(config: dict | None = None) -> str:
    config = config or load_config()
    settings = config.get("settings", {})
    return str(settings.get("database_url") or DATABASE_URL)


def external_database_guide() -> dict:
    return {
        "default_backend": "sqlite",
        "supported_backends": ["sqlite", "postgresql", "mysql"],
        "current_backend": database_backend(config_database_url()),
        "current_configured": not config_database_url().startswith("sqlite:"),
        "notes": [
            "默认单机部署继续使用 sqlite:////data/mirror-registry.db。",
            "PostgreSQL/MySQL 通过 DATABASE_URL 或 settings.database_url 预留正式配置入口。",
            "切换外部数据库前先备份 /data/mirror-registry.db 和 config/mirrors.yml。",
            "多实例部署时建议把 panel 和 sync 指向同一个外部数据库，并避免多个 sync 同时执行相同镜像组。",
        ],
        "examples": {
            "postgresql": "postgresql://mirror:password@postgres:5432/mirror_registry",
            "mysql": "mysql://mirror:password@mysql:3306/mirror_registry",
        },
    }


def persist_setting(key: str, value: object) -> None:
    db_execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, str(value), now_iso()),
    )


@app.put("/api/settings", dependencies=[Depends(require_write_token)])
def update_settings(body: SettingsIn):
    config = load_config()
    settings = config.setdefault("settings", {})
    changed: dict[str, object] = {}
    for key in ["check_interval_minutes", "sync_concurrency", "sync_retry_count"]:
        value = getattr(body, key)
        if value is not None:
            settings[key] = value
            changed[key] = value
    if body.database_url is not None:
        database_url = body.database_url.strip()
        if database_url:
            backend = database_backend(database_url)
            if backend == "unknown":
                raise HTTPException(400, "database_url 仅支持 sqlite/postgresql/mysql")
            settings["database_url"] = database_url
            changed["database_url"] = f"{backend}:***"
    if body.clear_notify_webhook_url:
        settings.pop("notify_webhook_url", None)
        changed["notify_webhook_url"] = ""
    elif body.notify_webhook_url is not None:
        url = body.notify_webhook_url.strip()
        if url:
            if not (url.startswith("http://") or url.startswith("https://")):
                raise HTTPException(400, "webhook URL 必须以 http:// 或 https:// 开头")
            settings["notify_webhook_url"] = url
            changed["notify_webhook_url"] = mask_url(url)
    save_config(config)
    for key, value in changed.items():
        persist_setting(key, value)
    audit_log("update", "settings", "sync", changed)
    return {"ok": True, "message": "设置已保存，sync 服务下次读取配置后生效", "changed": changed}


@app.get("/api/logs")
def get_logs(lines: int = 150):
    bounded_lines = max(1, min(lines, 1000))
    if not LOG_PATH.exists():
        return {"lines": ["（暂无日志）"]}
    all_lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": all_lines[-bounded_lines:]}


@app.get("/api/events")
def list_events(limit: int = 100):
    bounded_limit = max(1, min(limit, 500))
    return db_rows(
        """
        SELECT id, created_at, level, run_id, source, target, message
        FROM log_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (bounded_limit,),
    )


@app.get("/api/sync-runs")
def list_sync_runs(limit: int = 30):
    bounded_limit = max(1, min(limit, 100))
    return db_rows(
        """
        SELECT id, reason, status, only_source, started_at, ended_at, total, updated, skipped, failed, message
        FROM sync_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (bounded_limit,),
    )


@app.get("/api/sync-runs/{run_id}")
def get_sync_run(run_id: int):
    run = db_one(
        """
        SELECT id, reason, status, only_source, started_at, ended_at, total, updated, skipped, failed, message
        FROM sync_runs
        WHERE id = ?
        """,
        (run_id,),
    )
    if not run:
        raise HTTPException(404, "同步任务不存在")
    items = db_rows(
        """
        SELECT id, source, target, copy_target, status, old_digest, new_digest, step, error, started_at, ended_at, duration_ms
        FROM sync_run_items
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    )
    run["items"] = items
    return run


@app.post("/api/sync-runs/{run_id}/retry", dependencies=[Depends(require_write_token)])
def retry_sync_run(run_id: int):
    run = db_one("SELECT id FROM sync_runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(404, "同步任务不存在")
    rows = db_rows(
        """
        SELECT source
        FROM sync_run_items
        WHERE run_id = ? AND status = 'failed'
        ORDER BY id ASC
        """,
        (run_id,),
    )
    sources = list(dict.fromkeys(row["source"] for row in rows))
    if not sources:
        raise HTTPException(400, "该任务没有失败镜像可重试")
    task = write_trigger("retry-run", sources=sources)
    audit_log("retry", "sync_run", str(run_id), {"sources": sources, "queue_id": task["id"]})
    return {"ok": True, "sources": sources, "count": len(sources), "queue": task}


@app.post("/api/sync-run-items/{item_id}/retry", dependencies=[Depends(require_write_token)])
def retry_sync_run_item(item_id: int):
    item = db_one("SELECT source FROM sync_run_items WHERE id = ?", (item_id,))
    if not item:
        raise HTTPException(404, "同步任务明细不存在")
    task = write_trigger("retry-item", source=item["source"])
    audit_log("retry", "sync_run_item", str(item_id), {"source": item["source"], "queue_id": task["id"]})
    return {"ok": True, "source": item["source"], "queue": task}


@app.get("/api/diagnostics")
async def get_diagnostics():
    return await run_diagnostics()


@app.post("/api/diagnostics/run")
async def post_diagnostics():
    return await run_diagnostics()


def diagnostic_item(name: str, status: str, message: str, suggestion: str = "", details: dict | None = None) -> dict:
    return {
        "name": name,
        "status": status,
        "message": message,
        "suggestion": suggestion,
        "details": details or {},
    }


async def run_diagnostics() -> dict:
    checks = []
    registry_url = get_registry_url()

    started = datetime.now(timezone.utc)
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{registry_url}/v2/", timeout=5)
            if response.status_code in {200, 401}:
                checks.append(diagnostic_item("Registry API", "ok", f"{registry_url}/v2/ 可访问"))
            else:
                checks.append(
                    diagnostic_item(
                        "Registry API",
                        "error",
                        f"Registry 返回 HTTP {response.status_code}",
                        "检查 registry 服务和 config/mirrors.yml 中的 registry_url",
                    )
                )
        except httpx.HTTPError as exc:
            checks.append(
                diagnostic_item(
                    "Registry API",
                    "error",
                    f"无法连接 Registry: {exc}",
                    "确认 registry 容器正在运行，且 panel 能访问 registry:5000",
                )
            )

    try:
        config = load_config()
        checks.append(
            diagnostic_item(
                "配置文件",
                "ok",
                f"已加载 {len(valid_mirrors(config))} 条镜像配置",
                details={"path": str(CONFIG_PATH)},
            )
        )
    except Exception as exc:
        checks.append(diagnostic_item("配置文件", "error", f"读取配置失败: {exc}", "检查 config/mirrors.yml 格式"))

    config_parent = CONFIG_PATH.parent
    checks.append(
        diagnostic_item(
            "配置目录写入",
            "ok" if config_parent.exists() and os.access(config_parent, os.W_OK) else "error",
            f"{config_parent} {'可写' if config_parent.exists() and os.access(config_parent, os.W_OK) else '不可写'}",
            "确认 panel 容器挂载了 mirror-registry-config:/config",
        )
    )

    data_parent = LOG_PATH.parent
    checks.append(
        diagnostic_item(
            "数据目录写入",
            "ok" if data_parent.exists() and os.access(data_parent, os.W_OK) else "error",
            f"{data_parent} {'可写' if data_parent.exists() and os.access(data_parent, os.W_OK) else '不可写'}",
            "确认 panel 和 sync 容器都挂载了 mirror-registry-data:/data",
        )
    )

    try:
        usage = shutil.disk_usage(data_parent)
        checks.append(
            diagnostic_item(
                "磁盘空间",
                "warn" if usage.free < 2 * 1024 * 1024 * 1024 else "ok",
                f"剩余 {usage.free} / 总计 {usage.total} bytes",
                "磁盘不足时先清理删除标记镜像并执行 Registry garbage-collect",
                {"free_bytes": usage.free, "total_bytes": usage.total},
            )
        )
    except OSError as exc:
        checks.append(diagnostic_item("磁盘空间", "warn", f"无法读取磁盘空间: {exc}"))

    try:
        with connect_db() as conn:
            conn.execute("SELECT 1")
        checks.append(diagnostic_item("SQLite", "ok", f"数据库可用: {DB_PATH}", details={"path": str(DB_PATH)}))
    except sqlite3.Error as exc:
        checks.append(diagnostic_item("SQLite", "error", f"数据库不可用: {exc}", "检查 /data 是否可写"))

    settings = settings_with_defaults()
    checks.append(
        diagnostic_item(
            "同步策略",
            "ok",
            f"并发 {settings['sync_concurrency']}，最大重试 {settings['sync_retry_count']}，webhook {'已配置' if settings['notify_webhook_configured'] else '未配置'}",
            details={"notify_webhook_configured": settings["notify_webhook_configured"]},
        )
    )
    checks.append(
        diagnostic_item(
            "仓库凭据密钥",
            "ok" if CREDENTIALS_SECRET_KEY.strip() else "warn",
            "CREDENTIALS_SECRET_KEY 已配置" if CREDENTIALS_SECRET_KEY.strip() else "未配置 CREDENTIALS_SECRET_KEY，不能保存加密仓库凭据",
            "生产环境保存仓库凭据前必须设置 CREDENTIALS_SECRET_KEY",
        )
    )
    admin_ready = admin_user_exists()
    admin_env_ready = bool(ADMIN_USERNAME.strip() and ADMIN_PASSWORD.strip())
    checks.append(
        diagnostic_item(
            "面板登录",
            "ok" if admin_ready else "error",
            "管理员账号已初始化" if admin_ready else "管理员账号未初始化",
            "生产环境必须设置 ADMIN_USERNAME 和 ADMIN_PASSWORD 后重启 panel",
            {"admin_username_configured": bool(ADMIN_USERNAME.strip()), "admin_password_configured": bool(ADMIN_PASSWORD.strip()), "admin_env_ready": admin_env_ready},
        )
    )

    external_db = external_database_guide()
    checks.append(
        diagnostic_item(
            "数据库后端",
            "ok" if external_db["current_backend"] in {"sqlite", "postgresql", "mysql"} else "warn",
            f"当前后端: {external_db['current_backend']}",
            "默认使用 SQLite；多实例部署可配置 PostgreSQL/MySQL DATABASE_URL",
            {"external_database_configured": external_db["current_configured"]},
        )
    )

    platform = summarize_platform(load_config())
    checks.append(
        diagnostic_item(
            "平台化配置",
            "ok",
            f"{len(platform['registries'])} 个 Registry，{len(platform['mirror_groups'])} 个镜像组",
            "单机部署仍可只使用默认 local/default 配置",
            {"projects": platform["projects"], "environments": platform["environments"]},
        )
    )

    checks.append(
        diagnostic_item(
            "版本信息",
            "ok",
            f"app={APP_VERSION}, image_tag={IMAGE_TAG}",
            "如果这里不是预期版本，执行 docker compose pull 后重新创建服务",
            {"app_version": APP_VERSION, "image_tag": IMAGE_TAG},
        )
    )

    runtime = runtime_state()
    skopeo_available = runtime.get("skopeo_available", {}).get("value")
    last_heartbeat = runtime.get("last_heartbeat", {}).get("value")
    if skopeo_available == "true":
        checks.append(
            diagnostic_item(
                "Sync 依赖",
                "ok",
                f"sync 已上报 skopeo: {runtime.get('skopeo_path', {}).get('value', '')}",
                details={"last_heartbeat": last_heartbeat},
            )
        )
    elif last_heartbeat:
        checks.append(diagnostic_item("Sync 依赖", "error", "sync 已运行但未找到 skopeo", "重新构建并拉取最新 sync 镜像"))
    else:
        checks.append(diagnostic_item("Sync 依赖", "warn", "尚未收到 sync 心跳", "检查 sync 容器是否启动"))

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    summary_status = "ok"
    if any(item["status"] == "error" for item in checks):
        summary_status = "error"
    elif any(item["status"] == "warn" for item in checks):
        summary_status = "warn"

    return {
        "status": summary_status,
        "checked_at": now_iso(),
        "duration_ms": elapsed_ms,
        "checks": checks,
        "runtime": {key: value["value"] for key, value in runtime.items()},
    }


async def list_registry_images() -> list[dict]:
    registry_url = get_registry_url()
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{registry_url}/v2/_catalog", timeout=5)
            response.raise_for_status()
            repos = response.json().get("repositories", [])
            tasks = [_get_tags(client, registry_url, repo) for repo in repos]
            return await asyncio.gather(*tasks)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"无法连接到 Registry: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(502, f"Registry 返回了无效 JSON: {exc}") from exc


@app.get("/api/images")
async def list_images():
    return await list_registry_images()


async def _get_tags(client: httpx.AsyncClient, registry_url: str, repo: str) -> dict:
    try:
        response = await client.get(f"{registry_url}/v2/{repo}/tags/list", timeout=5)
        response.raise_for_status()
        tags = response.json().get("tags") or []
    except (httpx.HTTPError, ValueError):
        tags = []
    return {"repo": repo, "tags": tags}


def directory_size(path: Path) -> int | None:
    if not path.exists():
        return None
    total = 0
    try:
        for file_path in path.rglob("*"):
            if file_path.is_file():
                total += file_path.stat().st_size
    except OSError:
        return None
    return total


def registry_blob_physical_bytes() -> int | None:
    return directory_size(registry_storage_root() / "blobs" / "sha256")


def descriptor_blob(descriptor: dict) -> dict | None:
    digest = descriptor.get("digest")
    size = descriptor.get("size", 0)
    if not digest:
        return None
    try:
        size_int = int(size or 0)
    except (TypeError, ValueError):
        size_int = 0
    return {"digest": digest, "size": size_int}


def compute_manifest_stats(manifest: dict, child_manifests: dict[str, dict] | None = None) -> dict:
    media_type = manifest.get("mediaType") or ""
    child_manifests = child_manifests or {}
    blob_counts: dict[str, int] = {}
    blobs: list[dict] = []
    platforms = []

    def add_blob(blob: dict | None) -> None:
        if not blob:
            return
        blobs.append(blob)
        blob_counts[blob["digest"]] = blob_counts.get(blob["digest"], 0) + 1

    if "manifests" in manifest:
        total = 0
        for descriptor in manifest.get("manifests") or []:
            digest = descriptor.get("digest", "")
            child = child_manifests.get(digest)
            if child:
                child_stats = compute_manifest_stats(child, child_manifests)
                child_size = child_stats["logical_size_bytes"]
                for blob in child_stats["blobs"]:
                    add_blob(blob)
            else:
                child_blob = descriptor_blob(descriptor)
                child_size = child_blob["size"] if child_blob else 0
                add_blob(child_blob)
            total += child_size
            platforms.append(
                {
                    "digest": digest,
                    "platform": descriptor.get("platform") or {},
                    "logical_size_bytes": child_size,
                    "media_type": descriptor.get("mediaType", ""),
                }
            )
        unique = {blob["digest"]: blob["size"] for blob in blobs}
        return {
            "media_type": media_type,
            "logical_size_bytes": total,
            "deduplicated_size_bytes": sum(unique.values()),
            "shared_blob_count": sum(1 for count in blob_counts.values() if count > 1),
            "platforms": platforms,
            "blobs": blobs,
        }

    add_blob(descriptor_blob(manifest.get("config") or {}))
    for layer in manifest.get("layers") or []:
        add_blob(descriptor_blob(layer))
    unique = {blob["digest"]: blob["size"] for blob in blobs}
    return {
        "media_type": media_type,
        "logical_size_bytes": sum(blob["size"] for blob in blobs),
        "deduplicated_size_bytes": sum(unique.values()),
        "shared_blob_count": sum(1 for count in blob_counts.values() if count > 1),
        "platforms": platforms,
        "blobs": blobs,
    }


def cached_storage_stats() -> dict[str, dict]:
    rows = db_rows(
        """
        SELECT repo, tag, manifest_digest, logical_size_bytes, deduplicated_size_bytes, shared_blob_count, platforms, blobs, updated_at
        FROM storage_stats
        """
    )
    result = {}
    for row in rows:
        result[f"{row['repo']}:{row['tag']}"] = {
            **row,
            "platforms": json.loads(row.get("platforms") or "[]"),
            "blobs": json.loads(row.get("blobs") or "[]"),
        }
    return result


def upsert_storage_stat(repo: str, tag: str, manifest_digest: str, stats: dict) -> None:
    db_execute("DELETE FROM storage_stats WHERE repo = ? AND tag = ?", (repo, tag))
    db_execute(
        """
        INSERT INTO storage_stats(
            repo, tag, manifest_digest, logical_size_bytes, deduplicated_size_bytes,
            shared_blob_count, platforms, blobs, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repo,
            tag,
            manifest_digest,
            int(stats["logical_size_bytes"]),
            int(stats["deduplicated_size_bytes"]),
            int(stats["shared_blob_count"]),
            json.dumps(stats["platforms"], ensure_ascii=False),
            json.dumps(stats["blobs"], ensure_ascii=False),
            now_iso(),
        ),
    )


async def fetch_manifest(client: httpx.AsyncClient, registry_url: str, repo: str, reference: str) -> tuple[dict, str]:
    response = await client.get(
        f"{registry_url}/v2/{repo}/manifests/{reference}",
        headers={"Accept": MANIFEST_ACCEPT},
        timeout=15,
    )
    response.raise_for_status()
    digest = response.headers.get("Docker-Content-Digest", reference)
    return response.json(), digest


async def registry_manifest_digest_for_delete(client: httpx.AsyncClient, registry_url: str, repo: str, tag: str) -> str:
    try:
        response = await client.get(
            f"{registry_url}/v2/{repo}/manifests/{tag}",
            headers={"Accept": MANIFEST_ACCEPT},
            timeout=15,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"Registry manifest 读取超时: {repo}:{tag}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Registry manifest 读取失败: {exc.__class__.__name__}") from exc

    if response.status_code == 404:
        raise HTTPException(404, f"Registry manifest 不存在: {repo}:{tag}")
    if response.status_code >= 400:
        raise HTTPException(response.status_code if response.status_code < 500 else 502, f"Registry manifest 读取失败: HTTP {response.status_code}")

    digest = (response.headers.get("Docker-Content-Digest") or "").strip().lower()
    if not digest:
        raise HTTPException(502, "Registry 未返回 Docker-Content-Digest，无法删除 manifest")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise HTTPException(502, "Registry 返回的 manifest digest 格式不合法")
    return digest


async def delete_registry_manifest(client: httpx.AsyncClient, registry_url: str, repo: str, digest: str) -> int:
    try:
        response = await client.delete(f"{registry_url}/v2/{repo}/manifests/{digest}", timeout=15)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"Registry manifest 删除超时: {repo}@{digest}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Registry manifest 删除失败: {exc.__class__.__name__}") from exc

    if response.status_code in {200, 202}:
        return response.status_code
    if response.status_code == 404:
        raise HTTPException(404, f"Registry manifest 不存在或已被删除: {repo}@{digest}")
    raise HTTPException(response.status_code if response.status_code < 500 else 502, f"Registry manifest 删除失败: HTTP {response.status_code}")


async def recalculate_storage_stats() -> dict:
    registry_url = get_registry_url()
    images = await list_registry_images()
    updated = 0
    errors = []
    async with httpx.AsyncClient() as client:
        for image in images:
            repo = image["repo"]
            for tag in image.get("tags", []):
                try:
                    manifest, digest = await fetch_manifest(client, registry_url, repo, tag)
                    child_manifests = {}
                    for descriptor in manifest.get("manifests") or []:
                        child_digest = descriptor.get("digest")
                        if child_digest:
                            child_manifests[child_digest] = (await fetch_manifest(client, registry_url, repo, child_digest))[0]
                    stats = compute_manifest_stats(manifest, child_manifests)
                    upsert_storage_stat(repo, tag, digest, stats)
                    updated += 1
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append({"repo": repo, "tag": tag, "error": str(exc)})
    return {"updated": updated, "errors": errors}


def recalculate_storage_stats_sync() -> None:
    try:
        result = asyncio.run(recalculate_storage_stats())
        audit_log("recalculate", "storage_stats", "all", result)
    except Exception as exc:
        audit_log("recalculate_failed", "storage_stats", "all", {"error": str(exc)})


def estimate_repo_size(repo: str) -> int | None:
    return directory_size(registry_storage_root() / "repositories" / repo)


GC_ACTIVE_STATUSES = {"requested", "running"}
GC_ALLOWED_STATUSES = {"idle", "requested", "running", "completed", "failed"}


def runtime_state_value(state: dict, key: str, default: str = "") -> str:
    item = state.get(key) or {}
    return str(item.get("value") or default)


def storage_gc_status(state: dict | None = None) -> dict:
    state = state or runtime_state()
    status = runtime_state_value(state, "storage_gc_status", "idle").strip().lower() or "idle"
    if status not in GC_ALLOWED_STATUSES:
        status = "idle"
    request_id = runtime_state_value(state, "storage_gc_request_id")
    updated_values = [
        (state.get(key) or {}).get("updated_at")
        for key in (
            "storage_gc_status",
            "storage_gc_request_id",
            "storage_gc_requested_at",
            "storage_gc_started_at",
            "storage_gc_finished_at",
            "storage_gc_message",
            "storage_gc_log_tail",
        )
    ]
    return {
        "status": status,
        "request_id": request_id,
        "requested_at": runtime_state_value(state, "storage_gc_requested_at"),
        "started_at": runtime_state_value(state, "storage_gc_started_at"),
        "finished_at": runtime_state_value(state, "storage_gc_finished_at"),
        "message": runtime_state_value(state, "storage_gc_message"),
        "log_tail": runtime_state_value(state, "storage_gc_log_tail"),
        "active": status in GC_ACTIVE_STATUSES,
        "can_request": status not in GC_ACTIVE_STATUSES,
        "updated_at": max([value for value in updated_values if value], default=""),
    }


def set_storage_gc_values(values: dict[str, str]) -> None:
    for key, value in values.items():
        set_runtime_state(key, value)


def gc_guide() -> dict:
    return {
        "summary": "manifest 删除后，真正释放磁盘空间仍需执行 Registry garbage-collect。",
        "commands": [
            "powershell -ExecutionPolicy Bypass -File .\\scripts\\run-registry-gc.ps1",
            "docker compose stop sync",
            "docker compose stop registry",
            "docker compose run --rm registry registry garbage-collect /etc/docker/registry/config.yml",
            "docker compose up -d registry sync",
        ],
        "request": storage_gc_status(),
    }


@app.get("/api/tag-protection")
def list_tag_protection_rules():
    return [public_protection_rule(row) for row in protection_rule_rows()]


@app.post("/api/tag-protection", dependencies=[Depends(require_write_token)])
def upsert_tag_protection_rule(body: TagProtectionRuleIn):
    rule_id = validate_slug(body.id or slug_candidate(body.name, "protect"), "rule_id")
    now = now_iso()
    existing = db_one("SELECT id, created_at FROM tag_protection_rules WHERE id = ?", (rule_id,))
    params = (
        rule_id,
        body.name.strip(),
        body.repo_pattern.strip(),
        body.tag_pattern.strip(),
        body.environment.strip() or "*",
        1 if body.enabled else 0,
        body.reason or "",
        existing["created_at"] if existing else now,
        now,
    )
    if existing:
        db_execute(
            """
            UPDATE tag_protection_rules
            SET name = ?, repo_pattern = ?, tag_pattern = ?, environment = ?, enabled = ?, reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (params[1], params[2], params[3], params[4], params[5], params[6], params[8], rule_id),
        )
    else:
        db_execute(
            """
            INSERT INTO tag_protection_rules(id, name, repo_pattern, tag_pattern, environment, enabled, reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
    audit_log("upsert", "tag_protection_rule", rule_id, {"repo_pattern": body.repo_pattern, "tag_pattern": body.tag_pattern, "environment": body.environment, "enabled": body.enabled})
    return {"ok": True, "rule": public_protection_rule(db_one("SELECT * FROM tag_protection_rules WHERE id = ?", (rule_id,)))}


@app.delete("/api/tag-protection/{rule_id}", dependencies=[Depends(require_write_token)])
def delete_tag_protection_rule(rule_id: str):
    clean_id = validate_slug(rule_id, "rule_id")
    db_execute("DELETE FROM tag_protection_rules WHERE id = ?", (clean_id,))
    audit_log("delete", "tag_protection_rule", clean_id, {})
    return {"ok": True}


@app.get("/api/tag-protection/check")
def check_tag_protection(repo: str, tag: str, environment: str = ""):
    return protection_result(repo, tag, environment)


@app.get("/api/retention-policies")
def list_retention_policies():
    rows = db_rows(
        """
        SELECT id, name, repo_pattern, environment, keep_last, max_age_days, enabled, created_at, updated_at
        FROM retention_policies
        ORDER BY id
        """
    )
    return [public_retention_policy(row) for row in rows]


@app.post("/api/retention-policies", dependencies=[Depends(require_write_token)])
def upsert_retention_policy(body: RetentionPolicyIn):
    policy_id = validate_slug(body.id or slug_candidate(body.name, "retention"), "policy_id")
    now = now_iso()
    existing = db_one("SELECT id, created_at FROM retention_policies WHERE id = ?", (policy_id,))
    params = (
        policy_id,
        body.name.strip(),
        body.repo_pattern.strip(),
        body.environment.strip() or "*",
        body.keep_last,
        body.max_age_days,
        1 if body.enabled else 0,
        existing["created_at"] if existing else now,
        now,
    )
    if existing:
        db_execute(
            """
            UPDATE retention_policies
            SET name = ?, repo_pattern = ?, environment = ?, keep_last = ?, max_age_days = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (params[1], params[2], params[3], params[4], params[5], params[6], params[8], policy_id),
        )
    else:
        db_execute(
            """
            INSERT INTO retention_policies(id, name, repo_pattern, environment, keep_last, max_age_days, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
    audit_log("upsert", "retention_policy", policy_id, {"repo_pattern": body.repo_pattern, "environment": body.environment, "enabled": body.enabled})
    return {"ok": True, "policy": public_retention_policy(retention_policy_row(policy_id))}


@app.delete("/api/retention-policies/{policy_id}", dependencies=[Depends(require_write_token)])
def delete_retention_policy(policy_id: str):
    row = retention_policy_row(policy_id)
    db_execute("DELETE FROM retention_policies WHERE id = ?", (row["id"],))
    audit_log("delete", "retention_policy", row["id"], {})
    return {"ok": True}


@app.post("/api/retention-policies/{policy_id}/dry-run", dependencies=[Depends(require_write_token)])
def dry_run_retention_policy(policy_id: str):
    row = retention_policy_row(policy_id)
    result = retention_dry_run(row)
    audit_log("dry_run", "retention_policy", row["id"], {"candidates": len(result["candidates"]), "skipped_protected": len(result["skipped_protected"])})
    return result


@app.post("/api/retention-policies/{policy_id}/apply", dependencies=[Depends(require_write_token)])
def apply_retention_policy(policy_id: str):
    row = retention_policy_row(policy_id)
    result = retention_dry_run(row)
    created = []
    for candidate in result["candidates"]:
        db_execute(
            """
            INSERT INTO deletion_marks(repo, tag, reason, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repo, tag) DO UPDATE SET reason = excluded.reason, created_at = excluded.created_at
            """,
            (candidate["repo"], candidate["tag"], f"retention:{row['id']}:{','.join(candidate['reasons'])}", now_iso()),
        )
        created.append(f"{candidate['repo']}:{candidate['tag']}")
    audit_log("apply", "retention_policy", row["id"], {"marked": created, "skipped_protected": len(result["skipped_protected"])})
    return {"ok": True, "marked": created, "dry_run": result}


@app.get("/api/schedules")
def list_schedules():
    rows = db_rows(
        """
        SELECT id, name, source, target, cron, enabled, allow_latest, source_credential_id, target_credential_id,
               last_run_at, next_run_at, last_error, created_at, updated_at
        FROM scheduled_push_policies
        ORDER BY id
        """
    )
    return [public_scheduled_policy(row) for row in rows]


@app.post("/api/schedules", dependencies=[Depends(require_write_token)])
def upsert_schedule(body: ScheduledPushPolicyIn):
    if not cron_is_supported(body.cron):
        raise HTTPException(400, "cron 仅支持标准 5 段 UTC 表达式，字段支持 *、*/n、数字和逗号列表")
    if body.enabled:
        assert_scheduled_policy_allowed(body.source, body.target, body.allow_latest)
    else:
        validate_image_ref(body.source, "source")
        validate_image_ref(body.target, "target")
    schedule_id = validate_slug(body.id or slug_candidate(body.name, "schedule"), "schedule_id")
    now = now_iso()
    existing = db_one("SELECT id, created_at, last_run_at, last_error FROM scheduled_push_policies WHERE id = ?", (schedule_id,))
    params = (
        schedule_id,
        body.name.strip(),
        body.source.strip(),
        body.target.strip(),
        body.cron.strip(),
        1 if body.enabled else 0,
        1 if body.allow_latest else 0,
        optional_slug(body.source_credential_id, "source_credential_id"),
        optional_slug(body.target_credential_id, "target_credential_id"),
        existing.get("last_run_at") if existing else "",
        next_run_from_cron(body.cron) if body.enabled else "",
        existing.get("last_error") if existing else "",
        existing["created_at"] if existing else now,
        now,
    )
    if existing:
        db_execute(
            """
            UPDATE scheduled_push_policies
            SET name = ?, source = ?, target = ?, cron = ?, enabled = ?, allow_latest = ?,
                source_credential_id = ?, target_credential_id = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (params[1], params[2], params[3], params[4], params[5], params[6], params[7], params[8], params[10], params[13], schedule_id),
        )
    else:
        db_execute(
            """
            INSERT INTO scheduled_push_policies(
                id, name, source, target, cron, enabled, allow_latest, source_credential_id, target_credential_id,
                last_run_at, next_run_at, last_error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
    audit_log("upsert", "scheduled_push_policy", schedule_id, {"source": body.source, "target": body.target, "cron": body.cron, "enabled": body.enabled, "allow_latest": body.allow_latest})
    return {"ok": True, "schedule": public_scheduled_policy(scheduled_policy_row(schedule_id))}


@app.delete("/api/schedules/{schedule_id}", dependencies=[Depends(require_write_token)])
def delete_schedule(schedule_id: str):
    row = scheduled_policy_row(schedule_id)
    db_execute("DELETE FROM scheduled_push_policies WHERE id = ?", (row["id"],))
    audit_log("delete", "scheduled_push_policy", row["id"], {})
    return {"ok": True}


@app.post("/api/schedules/{schedule_id}/run", dependencies=[Depends(require_write_token)])
def run_schedule(schedule_id: str):
    row = scheduled_policy_row(schedule_id)
    assert_scheduled_policy_allowed(row["source"], row["target"], bool(row["allow_latest"]))
    db_execute(
        "UPDATE scheduled_push_policies SET next_run_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "", now_iso(), row["id"]),
    )
    task = write_trigger(f"scheduled-policy:{row['id']}", source=row["source"])
    audit_log("run", "scheduled_push_policy", row["id"], {"source": row["source"], "target": row["target"], "queue_id": task["id"]})
    return {"ok": True, "message": "计划推送已入队", "schedule": public_scheduled_policy(scheduled_policy_row(row["id"])), "queue": task}


@app.get("/api/storage")
async def get_storage():
    registry_error = ""
    try:
        images = await list_registry_images()
    except HTTPException as exc:
        registry_error = str(exc.detail)
        images = []
    marks = db_rows(
        """
        SELECT id, repo, tag, reason, created_at
        FROM deletion_marks
        ORDER BY id DESC
        """
    )
    mark_by_ref = {f"{mark['repo']}:{mark['tag']}": mark for mark in marks}
    stats_by_ref = cached_storage_stats()
    enriched = []
    for image in images:
        repo = image["repo"]
        repo_size = estimate_repo_size(repo)
        repo_blobs = {}
        for ref, stat in stats_by_ref.items():
            if not ref.startswith(f"{repo}:"):
                continue
            for blob in stat.get("blobs", []):
                repo_blobs[blob["digest"]] = blob["size"]
        enriched.append(
            {
                "repo": repo,
                "estimated_size_bytes": repo_size,
                "deduplicated_size_bytes": sum(repo_blobs.values()) if repo_blobs else None,
                "tags": [
                    {
                        "name": tag,
                        "marked_for_deletion": f"{repo}:{tag}" in mark_by_ref,
                        "deletion_mark": mark_by_ref.get(f"{repo}:{tag}"),
                        "stats": stats_by_ref.get(f"{repo}:{tag}"),
                    }
                    for tag in image.get("tags", [])
                ],
            }
        )
    total_storage = directory_size(REGISTRY_STORAGE_PATH)
    physical_blobs = registry_blob_physical_bytes()
    return {
        "images": enriched,
        "deletion_marks": marks,
        "registry_storage_path": str(REGISTRY_STORAGE_PATH),
        "estimated_total_bytes": total_storage,
        "physical_blob_bytes": physical_blobs,
        "stats_cached": bool(stats_by_ref),
        "registry_error": registry_error,
        "garbage_collection": gc_guide(),
    }


@app.get("/api/storage/stats")
def list_storage_stats():
    return list(cached_storage_stats().values())


@app.post("/api/storage/stats/recalculate", dependencies=[Depends(require_write_token)])
def queue_storage_stats_recalculate(background_tasks: BackgroundTasks):
    background_tasks.add_task(recalculate_storage_stats_sync)
    audit_log("queue_recalculate", "storage_stats", "all", {})
    return {"ok": True, "status": "queued"}


@app.get("/api/storage/gc/status")
def get_storage_gc_status():
    return storage_gc_status()


@app.post("/api/storage/gc/request", dependencies=[Depends(require_write_token)])
def request_storage_gc():
    current = storage_gc_status()
    if current["status"] in GC_ACTIVE_STATUSES:
        raise HTTPException(409, f"垃圾回收请求已存在: {current['status']}")
    requested_at = now_iso()
    request_id = hashlib.sha256(f"{requested_at}:{os.urandom(8).hex()}".encode("utf-8")).hexdigest()[:16]
    set_storage_gc_values(
        {
            "storage_gc_request_id": request_id,
            "storage_gc_status": "requested",
            "storage_gc_requested_at": requested_at,
            "storage_gc_started_at": "",
            "storage_gc_finished_at": "",
            "storage_gc_message": "等待宿主机脚本执行 Registry garbage-collect",
            "storage_gc_log_tail": "",
        }
    )
    audit_log("request_gc", "storage", request_id, {"status": "requested"})
    return {"ok": True, "request": storage_gc_status()}


@app.post("/api/storage/gc/status", dependencies=[Depends(require_write_token)])
def update_storage_gc_status(body: StorageGcStatusIn):
    status = body.status.strip().lower()
    if status not in {"requested", "running", "completed", "failed"}:
        raise HTTPException(422, "垃圾回收状态只能是 requested/running/completed/failed")

    current = storage_gc_status()
    request_id = (body.request_id or current.get("request_id") or "").strip()
    if current.get("request_id") and request_id and request_id != current["request_id"]:
        raise HTTPException(409, "垃圾回收请求 ID 不匹配")
    if not request_id:
        request_id = hashlib.sha256(f"{now_iso()}:{os.urandom(8).hex()}".encode("utf-8")).hexdigest()[:16]

    now = now_iso()
    values = {
        "storage_gc_request_id": request_id,
        "storage_gc_status": status,
        "storage_gc_message": (body.message or "").strip(),
        "storage_gc_log_tail": (body.log_tail or "")[-20000:],
    }
    if status == "requested":
        values["storage_gc_requested_at"] = body.requested_at or current.get("requested_at") or now
    elif status == "running":
        values["storage_gc_requested_at"] = current.get("requested_at") or body.requested_at or now
        values["storage_gc_started_at"] = body.started_at or current.get("started_at") or now
        values["storage_gc_finished_at"] = ""
    else:
        values["storage_gc_requested_at"] = current.get("requested_at") or body.requested_at or now
        values["storage_gc_started_at"] = body.started_at or current.get("started_at") or now
        values["storage_gc_finished_at"] = body.finished_at or now
    set_storage_gc_values(values)
    audit_log("update_gc", "storage", request_id, {"status": status, "message": values["storage_gc_message"]})
    return {"ok": True, "request": storage_gc_status()}


@app.get("/api/storage/search")
async def search_storage(q: str = "", status: str = "", limit: int = 100):
    storage = await get_storage()
    term = q.strip().lower()
    status_filter = status.strip().lower()
    results = []
    for image in storage["images"]:
        for tag in image.get("tags", []):
            ref = f"{image['repo']}:{tag['name']}"
            protection = protection_result(image["repo"], tag["name"])
            item_status = "marked" if tag.get("marked_for_deletion") else ("protected" if protection["protected"] else "active")
            if term and term not in ref.lower() and term not in json.dumps(protection, ensure_ascii=False).lower():
                continue
            if status_filter and status_filter != item_status:
                continue
            results.append(
                {
                    "repo": image["repo"],
                    "tag": tag["name"],
                    "ref": ref,
                    "status": item_status,
                    "deletion_mark": tag.get("deletion_mark"),
                    "protection": protection,
                }
            )
            if len(results) >= min(limit, 500):
                return {"items": results, "registry_error": storage["registry_error"]}
    return {"items": results, "registry_error": storage["registry_error"]}


@app.get("/api/storage/images/{repo:path}/tags/{tag}")
def get_storage_image_detail(repo: str, tag: str):
    clean_repo, clean_tag = validate_repo_tag(repo, tag)
    stats = cached_storage_stats().get(f"{clean_repo}:{clean_tag}")
    mark = db_one("SELECT id, repo, tag, reason, created_at FROM deletion_marks WHERE repo = ? AND tag = ?", (clean_repo, clean_tag))
    latest_item = db_one(
        """
        SELECT id, run_id, source, target, copy_target, status, old_digest, new_digest, step, error, started_at, ended_at
        FROM sync_run_items
        WHERE target LIKE ?
        ORDER BY id DESC
        """,
        (f"%/{clean_repo}:{clean_tag}",),
    )
    context = mirror_context_for_tag(clean_repo, clean_tag)
    return {
        "repo": clean_repo,
        "tag": clean_tag,
        "digest": latest_item.get("new_digest") if latest_item else "",
        "source": context.get("source", latest_item.get("source") if latest_item else ""),
        "target": context.get("target", latest_item.get("target") if latest_item else ""),
        "environment": context.get("environment", ""),
        "deletion_mark": mark,
        "protection": protection_result(clean_repo, clean_tag, context.get("environment", "")),
        "latest_sync_item": latest_item,
        "stats": stats,
        "estimated_size_bytes": estimate_repo_size(clean_repo),
    }


def backup_path_item(name: str, path: Path, required: bool = True, secret: bool = False) -> dict:
    exists = path.exists()
    size = None
    if exists and path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            size = None
    return {
        "name": name,
        "path": str(path),
        "required": required,
        "exists": exists,
        "kind": "directory" if exists and path.is_dir() else "file",
        "size_bytes": size,
        "secret": secret,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_inventory(path: Path, include_checksums: bool = False, max_checksum_files: int = 5000) -> dict:
    result = {
        "path": str(path),
        "exists": path.exists(),
        "kind": "directory",
        "file_count": 0,
        "size_bytes": 0,
        "sha256": "",
        "checksum_warning": "",
    }
    if not path.exists() or not path.is_dir():
        return result
    files = [item for item in path.rglob("*") if item.is_file()]
    result["file_count"] = len(files)
    total_size = 0
    for item in files:
        try:
            total_size += item.stat().st_size
        except OSError:
            continue
    result["size_bytes"] = total_size
    if include_checksums:
        if len(files) > max_checksum_files:
            result["checksum_warning"] = f"文件数超过 {max_checksum_files}，请使用 scripts\\migration-report.ps1 生成离线校验清单"
        else:
            aggregate = hashlib.sha256()
            for item in sorted(files):
                rel = item.relative_to(path).as_posix()
                aggregate.update(rel.encode("utf-8"))
                aggregate.update(b"\0")
                aggregate.update(file_sha256(item).encode("ascii"))
                aggregate.update(b"\0")
            result["sha256"] = aggregate.hexdigest()
    return result


def migration_manifest_item(name: str, path: Path, required: bool = True, secret: bool = False) -> dict:
    item = backup_path_item(name, path, required=required, secret=secret)
    item["sha256"] = ""
    if path.exists() and path.is_file() and not secret:
        try:
            item["sha256"] = file_sha256(path)
        except OSError:
            item["sha256"] = ""
    return item


def build_backup_package_manifest() -> dict:
    registry_root = REGISTRY_STORAGE_PATH / "docker" / "registry" / "v2"
    return {
        "version": 1,
        "generated_at": now_iso(),
        "format": "zip",
        "required_items": [
            backup_path_item("config", CONFIG_PATH.parent),
            backup_path_item("registry_storage", REGISTRY_STORAGE_PATH),
            backup_path_item("database", DB_PATH),
            {"name": "env_file", "path": ".env", "required": True, "exists": Path(".env").exists(), "kind": "file", "size_bytes": Path(".env").stat().st_size if Path(".env").exists() else None, "secret": True},
            {"name": "credentials_secret", "path": "CREDENTIALS_SECRET_KEY", "required": True, "exists": bool(CREDENTIALS_SECRET_KEY.strip()), "kind": "environment", "size_bytes": None, "secret": True},
        ],
        "optional_items": [
            backup_path_item("sync_log", LOG_PATH, required=False),
            backup_path_item("sync_state", STATE_PATH, required=False),
            backup_path_item("registry_v2_root", registry_root, required=False),
        ],
        "commands": {
            "create": "Compress-Archive -Path config,data\\registry,data\\mirror-registry.db,.env -DestinationPath mirror-registry-backup.zip -Force",
            "drill": "powershell -ExecutionPolicy Bypass -File .\\scripts\\restore-drill.ps1",
        },
        "notes": [
            "备份包不得导出明文仓库凭据；凭据恢复依赖原始 CREDENTIALS_SECRET_KEY。",
            "恢复演练默认只读，不启动 sync，不创建同步触发文件。",
        ],
    }


def build_migration_package_manifest(include_registry_checksums: bool = False) -> dict:
    env_path = Path(".env")
    return {
        "version": 1,
        "generated_at": now_iso(),
        "readonly": True,
        "source": {
            "app_version": APP_VERSION,
            "image_tag": IMAGE_TAG,
            "database_backend": database_backend(DATABASE_URL),
        },
        "required_items": [
            migration_manifest_item("config_file", CONFIG_PATH),
            migration_manifest_item("config_dir", CONFIG_PATH.parent),
            migration_manifest_item("database", DB_PATH),
            migration_manifest_item("env_file", env_path, secret=True),
            {"name": "credentials_secret", "path": "CREDENTIALS_SECRET_KEY", "required": True, "exists": bool(CREDENTIALS_SECRET_KEY.strip()), "kind": "environment", "size_bytes": None, "secret": True, "sha256": ""},
            {**directory_inventory(REGISTRY_STORAGE_PATH, include_checksums=include_registry_checksums), "name": "registry_storage", "required": True, "secret": False},
        ],
        "queue": {
            "active": queue_count_by_status(["queued", "running", "paused", "cancel_requested"]),
            "terminal": queue_count_by_status(["completed", "failed", "canceled"]),
        },
        "commands": {
            "manifest": "powershell -ExecutionPolicy Bypass -File .\\scripts\\migration-report.ps1",
            "backup": "Compress-Archive -Path config,data\\registry,data\\mirror-registry.db,.env -DestinationPath mirror-registry-backup.zip -Force",
            "restore_drill": "powershell -ExecutionPolicy Bypass -File .\\scripts\\restore-drill.ps1",
        },
    }


def migration_check(name: str, status: str, message: str, suggestion: str = "", details: dict | None = None) -> dict:
    return drill_check(name, status, message, suggestion, details)


def queue_count_by_status(statuses: list[str]) -> int:
    if not statuses:
        return 0
    placeholders = ",".join("?" for _ in statuses)
    row = db_one(f"SELECT COUNT(*) AS count FROM sync_queue WHERE status IN ({placeholders})", tuple(statuses))
    return int(row.get("count") or 0) if row else 0


def build_migration_preflight() -> dict:
    checks: list[dict] = []
    docker_path = shutil.which("docker")
    checks.append(migration_check("Docker CLI", "ok" if docker_path else "warn", f"docker: {docker_path}" if docker_path else "当前环境未发现 docker 命令", "目标机器恢复前需要 Docker 和 Compose"))

    for name, path in [("配置文件", CONFIG_PATH), ("SQLite 数据库", DB_PATH), ("Registry 数据", REGISTRY_STORAGE_PATH)]:
        checks.append(migration_check(name, "ok" if path.exists() else "error", f"{path} {'存在' if path.exists() else '缺失'}", "迁移包必须包含该项", {"path": str(path)}))

    try:
        base_path = REGISTRY_STORAGE_PATH if REGISTRY_STORAGE_PATH.exists() else DB_PATH.parent
        usage = shutil.disk_usage(base_path)
        inventory = directory_inventory(REGISTRY_STORAGE_PATH)
        estimated = int(inventory.get("size_bytes") or 0) + (DB_PATH.stat().st_size if DB_PATH.exists() else 0)
        status = "ok" if usage.free > estimated else "warn"
        checks.append(migration_check("磁盘空间", status, f"当前可用 {usage.free} bytes，备份估算 {estimated} bytes", "目标机器可用空间应大于 registry 数据和数据库体积", {"free_bytes": usage.free, "estimated_backup_bytes": estimated}))
    except OSError as exc:
        checks.append(migration_check("磁盘空间", "warn", f"无法读取磁盘空间: {exc}", "在目标机器上运行 scripts\\migration-report.ps1 复核"))

    if CREDENTIALS_SECRET_KEY.strip():
        checks.append(migration_check("凭据主密钥", "ok", "CREDENTIALS_SECRET_KEY 已配置且未输出内容"))
    else:
        checks.append(migration_check("凭据主密钥", "error", "缺少 CREDENTIALS_SECRET_KEY", "恢复含加密凭据的数据前必须使用原始主密钥"))

    active_queue = queue_count_by_status(["queued", "running", "paused", "cancel_requested"])
    checks.append(migration_check("同步队列", "ok" if active_queue == 0 else "warn", f"活动队列任务 {active_queue} 个", "迁移前建议等待队列清空或显式取消/重放", {"active": active_queue}))
    checks.append(migration_check("只读边界", "ok" if not TRIGGER_PATH.exists() else "warn", "未发现同步触发文件" if not TRIGGER_PATH.exists() else "存在同步触发文件", "迁移前确认不会自动触发 sync"))

    summary = summarize_drill_checks(checks)
    return {
        "ok": summary["error"] == 0,
        "readonly": True,
        "checked_at": now_iso(),
        "summary": summary,
        "checks": checks,
        "manifest": build_migration_package_manifest(),
    }


def build_migration_restore_plan() -> dict:
    return {
        "title": "Mirror Registry migration and restore plan",
        "readonly": True,
        "preflight": [
            "在源机器运行 /api/migration/preflight 或 scripts\\migration-report.ps1，确认配置、SQLite、Registry 数据和密钥完整。",
            "等待 /api/sync-queue 没有 queued/running/paused/cancel_requested 任务，或先取消并记录可重放任务。",
            "停止 registry 后打包 config、data\\registry、data\\mirror-registry.db 和 .env。",
        ],
        "restore_steps": [
            "在目标机器安装 Docker 和 Compose，创建同名 named volumes 或使用开发 compose 的本地目录。",
            "还原 config/、data/registry/、data/mirror-registry.db 和 .env，并设置原始 CREDENTIALS_SECRET_KEY。",
            "只启动 registry 与 panel，运行 /api/backup-restore/verify 和 /api/backup-restore/drill。",
            "确认 Registry /v2/ 和至少一个样本 manifest 可读后，再启动 sync。",
        ],
        "rollback": [
            "恢复前保留目标机器原 data/ 与 .env 快照。",
            "如果密钥不一致，立即停止 panel/sync，恢复原始 CREDENTIALS_SECRET_KEY 后重跑只读演练。",
            "如果磁盘不足，不要启动 sync；扩容或更换数据盘后再恢复 registry 数据。",
        ],
        "commands": {
            "source_report": "powershell -ExecutionPolicy Bypass -File .\\scripts\\migration-report.ps1 -ReportPath .\\migration-report.json",
            "restore_drill": "powershell -ExecutionPolicy Bypass -File .\\scripts\\restore-drill.ps1",
            "queue": "curl http://localhost:8080/api/sync-queue",
        },
        "manifest": build_migration_package_manifest(),
    }


def drill_check(name: str, status: str, message: str, suggestion: str = "", details: dict | None = None) -> dict:
    return {
        "name": name,
        "status": status,
        "ok": status != "error",
        "message": message,
        "suggestion": suggestion,
        "details": details or {},
    }


def summarize_drill_checks(checks: list[dict]) -> dict:
    status = "ok"
    if any(item["status"] == "error" for item in checks):
        status = "error"
    elif any(item["status"] == "warn" for item in checks):
        status = "warn"
    return {
        "status": status,
        "ok": sum(1 for item in checks if item["status"] == "ok"),
        "warn": sum(1 for item in checks if item["status"] == "warn"),
        "error": sum(1 for item in checks if item["status"] == "error"),
    }


def verify_credentials_decryptable(require_secret: bool) -> dict:
    rows = credential_rows()
    if require_secret and not CREDENTIALS_SECRET_KEY.strip():
        return drill_check("凭据主密钥", "error", "缺少 CREDENTIALS_SECRET_KEY", "恢复含加密凭据的数据前必须设置原始主密钥")
    failed = []
    for row in rows:
        try:
            decrypt_secret(row["encrypted_secret"])
        except HTTPException:
            failed.append(row["id"])
    if failed:
        return drill_check("凭据解密", "error", f"{len(failed)} 个凭据无法解密", "确认使用备份来源的 CREDENTIALS_SECRET_KEY", {"failed_ids": failed[:20]})
    if rows:
        return drill_check("凭据解密", "ok", f"{len(rows)} 个凭据可用原始主密钥解密", details={"credential_count": len(rows)})
    return drill_check("凭据解密", "ok", "没有已保存凭据需要解密")


def latest_successful_tag() -> dict | None:
    return db_one(
        """
        SELECT source, target, new_digest, ended_at
        FROM sync_run_items
        WHERE status = 'success' AND target != ''
        ORDER BY COALESCE(ended_at, started_at) DESC
        LIMIT 1
        """
    )


async def verify_registry_sample_manifest(enabled: bool) -> dict:
    sample = latest_successful_tag()
    if not enabled:
        return drill_check("Registry 样本", "warn", "未启用 Registry 样本 manifest 探测", "需要端到端恢复演练时设置 verify_registry_sample=true")
    if not sample:
        return drill_check("Registry 样本", "warn", "没有成功同步记录可作为 manifest 样本", "先完成至少一次同步，再执行样本验证")
    try:
        repo, tag = image_repo_tag(sample["target"])
    except HTTPException as exc:
        return drill_check("Registry 样本", "error", f"最近成功记录的 target 无法解析: {exc.detail}", "检查同步任务历史")
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            manifest, digest = await fetch_manifest(client, get_registry_url(), repo, tag)
        except (httpx.HTTPError, ValueError) as exc:
            return drill_check("Registry 样本", "error", f"样本 manifest 不可读: {exc}", "确认 registry 数据已还原且 /v2/ 可访问", {"repo": repo, "tag": tag})
    return drill_check(
        "Registry 样本",
        "ok",
        f"样本 manifest 可读: {repo}:{tag}",
        details={"repo": repo, "tag": tag, "digest": digest, "media_type": manifest.get("mediaType", "")},
    )


async def run_backup_restore_drill(body: BackupRestoreDrillIn) -> dict:
    checks: list[dict] = []
    try:
        config = load_config()
        checks.append(drill_check("配置文件", "ok", f"配置可读取，镜像 {len(valid_mirrors(config))} 条", details={"path": str(CONFIG_PATH)}))
    except Exception as exc:
        checks.append(drill_check("配置文件", "error", f"配置无法读取: {exc}", "检查 config/mirrors.yml 是否在备份包内"))

    try:
        db_rows("SELECT 1")
        table_rows = db_rows("SELECT name FROM sqlite_master WHERE type = 'table'") if database_backend(DATABASE_URL) == "sqlite" else []
        checks.append(drill_check("数据库", "ok", "数据库可读取", details={"path": str(DB_PATH), "tables": [row["name"] for row in table_rows]}))
    except HTTPException as exc:
        checks.append(drill_check("数据库", "error", f"数据库不可读: {exc.detail}", "检查 data/mirror-registry.db 是否还原"))

    registry_root = REGISTRY_STORAGE_PATH / "docker" / "registry" / "v2"
    if REGISTRY_STORAGE_PATH.exists():
        status = "ok" if registry_root.exists() else "warn"
        checks.append(drill_check("Registry 数据", status, f"Registry 数据目录存在: {REGISTRY_STORAGE_PATH}", "空仓库或新实例可能还没有 docker/registry/v2 子目录", {"path": str(REGISTRY_STORAGE_PATH)}))
    else:
        checks.append(drill_check("Registry 数据", "error", f"Registry 数据目录不存在: {REGISTRY_STORAGE_PATH}", "检查 data/registry 是否还原"))

    checks.append(verify_credentials_decryptable(body.require_credentials_secret))
    checks.append(await verify_registry_sample_manifest(body.verify_registry_sample))
    trigger_absent = not TRIGGER_PATH.exists()
    checks.append(drill_check("只读边界", "ok" if trigger_absent else "warn", "未发现同步触发文件" if trigger_absent else "存在同步触发文件，演练未创建但恢复前应确认来源", "恢复演练不会启动 sync 或写入 Registry"))

    summary = summarize_drill_checks(checks)
    return {
        "ok": summary["error"] == 0,
        "summary": summary,
        "checked_at": now_iso(),
        "readonly": True,
        "package_manifest": build_backup_package_manifest(),
        "checks": checks,
        "report": {
            "title": "Mirror Registry restore drill report",
            "status": summary["status"],
            "failed": [item["name"] for item in checks if item["status"] == "error"],
            "warnings": [item["name"] for item in checks if item["status"] == "warn"],
        },
    }


@app.get("/api/backup-restore-guide")
def get_backup_restore_guide():
    return {
        "required_items": [
            "config/",
            "data/registry/",
            "data/mirror-registry.db",
            ".env",
            "CREDENTIALS_SECRET_KEY",
        ],
        "backup_commands": [
            "docker compose stop registry",
            "Compress-Archive -Path config,data\\registry,data\\mirror-registry.db,.env -DestinationPath mirror-registry-backup.zip -Force",
            "docker compose start registry",
        ],
        "package_manifest": build_backup_package_manifest(),
        "restore_steps": [
            "还原 config/、data/registry/、SQLite 数据库和 .env。",
            "先设置原始 CREDENTIALS_SECRET_KEY，再启动 panel 进行只读验证。",
            "通过 /api/backup-restore/verify 确认配置、数据库和 registry 数据可读。",
            "通过 /api/backup-restore/drill 生成只读恢复演练报告。",
            "确认至少一个已同步 tag 可通过 Registry API 读取后，再启动 sync 写入。",
        ],
        "readonly_verification": [
            "docker compose up -d registry panel",
            "curl http://localhost:8080/api/status",
            "curl http://localhost:5000/v2/_catalog",
            "powershell -ExecutionPolicy Bypass -File .\\scripts\\restore-drill.ps1",
        ],
        "tls_entry": {
            "panel": "面板入口建议只暴露 HTTPS，并叠加 Basic Auth 或 SSO。",
            "registry": "Registry /v2/ 入口必须单独配置 HTTPS，跨机器长期使用时不要裸 HTTP 暴露。",
        },
    }


@app.post("/api/backup-restore/verify", dependencies=[Depends(require_write_token)])
def verify_backup_restore_readiness(body: BackupRestoreVerifyIn):
    try:
        db_rows("SELECT 1")
        database_ok = True
    except HTTPException:
        database_ok = False
    checks = [
        {"name": "config", "ok": CONFIG_PATH.exists(), "path": str(CONFIG_PATH)},
        {"name": "database", "ok": database_ok, "path": str(DB_PATH)},
        {"name": "registry_storage", "ok": REGISTRY_STORAGE_PATH.exists(), "path": str(REGISTRY_STORAGE_PATH)},
        {"name": "credentials_secret", "ok": bool(CREDENTIALS_SECRET_KEY.strip()) or not body.require_credentials_secret, "path": "CREDENTIALS_SECRET_KEY"},
    ]
    ok_status = all(item["ok"] for item in checks)
    audit_log("verify", "backup_restore", "readiness", {"ok": ok_status, "failed": [item["name"] for item in checks if not item["ok"]]})
    return {"ok": ok_status, "checks": checks, "guide": get_backup_restore_guide()}


@app.get("/api/backup-restore/package-manifest")
def backup_restore_package_manifest():
    return build_backup_package_manifest()


@app.post("/api/backup-restore/drill", dependencies=[Depends(require_write_token)])
async def backup_restore_drill(body: BackupRestoreDrillIn):
    result = await run_backup_restore_drill(body)
    audit_log("drill", "backup_restore", "readonly", {"ok": result["ok"], "summary": result["summary"]})
    return result


@app.get("/api/migration/plan")
def get_migration_restore_plan():
    return build_migration_restore_plan()


@app.get("/api/migration/package-manifest")
def get_migration_package_manifest(include_registry_checksums: bool = False):
    return build_migration_package_manifest(include_registry_checksums=include_registry_checksums)


@app.post("/api/migration/preflight", dependencies=[Depends(require_write_token)])
def run_migration_preflight():
    result = build_migration_preflight()
    audit_log("preflight", "migration", "readonly", {"ok": result["ok"], "summary": result["summary"]})
    return result


@app.post("/api/storage/delete-mark", dependencies=[Depends(require_write_token)])
def mark_image_for_delete(body: StorageDeleteMarkIn):
    repo, tag = validate_repo_tag(body.repo, body.tag)
    protection = assert_tag_mutation_allowed(repo, tag, "delete-mark")
    mark_id = db_execute(
        """
        INSERT INTO deletion_marks(repo, tag, reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(repo, tag) DO UPDATE SET reason = excluded.reason, created_at = excluded.created_at
        """,
        (repo, tag, body.reason or "", now_iso()),
    )
    audit_log("mark_delete", "image", f"{repo}:{tag}", {"reason": body.reason or "", "protection": protection})
    return {"ok": True, "id": mark_id, "repo": repo, "tag": tag, "protection": protection}


@app.delete("/api/storage/delete-mark/{mark_id}", dependencies=[Depends(require_write_token)])
def unmark_image_for_delete(mark_id: int):
    db_execute("DELETE FROM deletion_marks WHERE id = ?", (mark_id,))
    audit_log("unmark_delete", "deletion_mark", str(mark_id), {})
    return {"ok": True}


@app.post("/api/storage/delete-mark/{mark_id}/apply", dependencies=[Depends(require_write_token)])
async def apply_image_delete_mark(mark_id: int):
    mark = db_one("SELECT id, repo, tag, reason, created_at FROM deletion_marks WHERE id = ?", (mark_id,))
    if not mark:
        raise HTTPException(404, "删除标记不存在，无法执行清理")

    repo, tag = validate_repo_tag(mark["repo"], mark["tag"])
    protection = assert_tag_mutation_allowed(repo, tag, "manifest-delete")
    registry_url = get_registry_url().rstrip("/")
    async with httpx.AsyncClient() as client:
        manifest_digest = await registry_manifest_digest_for_delete(client, registry_url, repo, tag)
        http_status = await delete_registry_manifest(client, registry_url, repo, manifest_digest)

    db_execute("DELETE FROM deletion_marks WHERE id = ?", (mark_id,))
    db_execute("DELETE FROM storage_stats WHERE repo = ? AND tag = ?", (repo, tag))
    audit_log(
        "apply_delete",
        "deletion_mark",
        str(mark_id),
        {
            "repo": repo,
            "tag": tag,
            "manifest_digest": manifest_digest,
            "registry_url": mask_url(registry_url),
            "http_status": http_status,
            "protection": protection,
        },
    )
    return {
        "ok": True,
        "repo": repo,
        "tag": tag,
        "manifest_digest": manifest_digest,
        "deleted_mark_id": mark_id,
        "message": "manifest 已删除；释放磁盘空间仍需执行 Registry garbage-collect",
    }


def security_check_item(name: str, status: str, message: str, suggestion: str = "", details: dict | None = None) -> dict:
    return {"name": name, "status": status, "message": message, "suggestion": suggestion, "details": details or {}}


def weak_or_placeholder(value: str, placeholders: set[str] | None = None) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized or normalized.startswith("replace-with-"):
        return True
    return normalized in (placeholders or set())


def summarize_security_checks(checks: list[dict]) -> dict:
    counts = {"ok": 0, "warn": 0, "error": 0}
    for check in checks:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    status = "error" if counts["error"] else ("warn" if counts["warn"] else "ok")
    return {"status": status, **counts}


def build_security_checks() -> dict:
    production_mode = os.getenv("APP_ENV", "").lower() in {"prod", "production"} or os.getenv("MIRROR_REGISTRY_ENV", "").lower() in {"prod", "production"}
    checks: list[dict] = []
    password_weak = weak_or_placeholder(ADMIN_PASSWORD, {"change-me", "changeme", "password", "admin", "admin-password"})
    checks.append(security_check_item(
        "管理员密码",
        "error" if password_weak and production_mode else ("warn" if password_weak else "ok"),
        "ADMIN_PASSWORD 为空、弱口令或占位值" if password_weak else "ADMIN_PASSWORD 已配置",
        "首次启动生产环境前设置强随机管理员密码；已初始化后请在访问控制页轮换。",
    ))
    secret_missing = not CREDENTIALS_SECRET_KEY.strip()
    credential_count = int((db_one("SELECT COUNT(*) AS count FROM credentials") or {}).get("count") or 0)
    checks.append(security_check_item(
        "凭据主密钥",
        "error" if secret_missing and credential_count else ("warn" if secret_missing else "ok"),
        "CREDENTIALS_SECRET_KEY 未配置" if secret_missing else "CREDENTIALS_SECRET_KEY 已配置且不会回显",
        "保存或恢复仓库凭据前必须设置并备份原始 CREDENTIALS_SECRET_KEY。",
        {"credential_count": credential_count},
    ))
    checks.append(security_check_item(
        "Session Cookie",
        "warn" if not SESSION_COOKIE_SECURE else "ok",
        f"Secure={SESSION_COOKIE_SECURE}, SameSite={SESSION_COOKIE_SAMESITE}",
        "HTTPS 入口应设置 SESSION_COOKIE_SECURE=true；SameSite 建议保持 lax 或 strict。",
    ))
    checks.append(security_check_item(
        "敏感导出脱敏",
        "ok",
        "诊断包、配置导出和迁移清单不会输出 password/token/secret/encrypted_secret 等敏感字段",
        "发布前继续保留相关自动化测试。",
    ))
    checks.append(security_check_item(
        "凭据删除引用检查",
        "ok",
        "删除凭据前会检查镜像引用关系",
        "如需删除被引用凭据，先调整相关镜像配置。",
    ))
    checks.append(security_check_item(
        "Artifact 路径边界",
        "ok",
        "本地 artifact 导出通过 repo/tag 校验和 registry 根目录 resolve 检查防止 traversal",
        "仅从本地 Registry storage 读取 manifest/blob。",
    ))
    return {
        "generated_at": now_iso(),
        "production_mode": production_mode,
        "summary": summarize_security_checks(checks),
        "checks": checks,
    }


@app.get("/api/security-checks")
def get_security_checks():
    return build_security_checks()


@app.get("/api/security-guide")
def get_security_guide():
    return {
        "public_exposure_boundary": "不要把 8080 面板端口直接暴露到公网。面板后台 API 只接受账号登录后的 session cookie。",
        "panel_auth": {
            "mode": "single_admin_session",
            "admin_initialized": admin_user_exists(),
            "admin_username_configured": bool(ADMIN_USERNAME.strip()),
            "admin_password_configured": bool(ADMIN_PASSWORD.strip()),
            "session_cookie": SESSION_COOKIE_NAME,
            "session_cookie_secure": SESSION_COOKIE_SECURE,
            "session_cookie_samesite": SESSION_COOKIE_SAMESITE,
            "session_ttl_seconds": SESSION_TTL_SECONDS,
        },
        "security_checks": build_security_checks(),
        "recommended": [
            "通过 Nginx、Caddy 或 Traefik 放在内网入口后面。",
            "生产环境必须设置 ADMIN_USERNAME 和 ADMIN_PASSWORD 初始化管理员。",
            "反向代理层 Basic Auth、SSO 或可信 IP 限制可作为额外保护层。",
        ],
        "nginx_basic_auth": [
            "location / {",
            "  auth_basic \"Mirror Registry\";",
            "  auth_basic_user_file /etc/nginx/.htpasswd;",
            "  proxy_pass http://127.0.0.1:8080;",
            "}",
        ],
        "tls_reverse_proxy": [
            "管理面板入口和 Registry /v2/ 入口分开配置 server_name 和证书。",
            "Registry /v2/ 长期跨机器使用必须走 HTTPS；仅本机 compose 内部流量可以保留 HTTP。",
            "不要把 sync 服务端口暴露到公网；sync 不需要入站流量。",
        ],
    }


@app.get("/api/platform")
def get_platform():
    config = load_config()
    return summarize_platform(config)


@app.get("/api/platform/groups")
def list_grouped_mirrors():
    return grouped_mirror_summary(load_config())


@app.get("/api/registries")
def list_registries():
    return list(registry_map(load_config()).values())


@app.get("/api/credentials")
def list_credentials():
    rows = db_rows(
        """
        SELECT id, name, registry_host, username, encrypted_secret, scope, created_at, updated_at
        FROM credentials
        ORDER BY registry_host, name
        """
    )
    return [public_credential(row) for row in rows]


@app.post("/api/credentials", dependencies=[Depends(require_write_token)])
def create_credential(body: CredentialIn):
    if not body.secret:
        raise HTTPException(400, "创建凭据时必须填写 secret")
    registry_host = normalize_registry_host(body.registry_host)
    credential_id = validate_slug(body.id or slug_candidate(f"{registry_host.replace(':', '-')}-{body.name}"), "credential_id")
    existing = db_one("SELECT id FROM credentials WHERE id = ?", (credential_id,))
    if existing:
        raise HTTPException(409, "凭据 id 已存在")
    now = now_iso()
    encrypted = encrypt_secret(body.secret)
    scope = validate_credential_scope(body.scope)
    db_execute(
        """
        INSERT INTO credentials(id, name, registry_host, username, encrypted_secret, scope, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (credential_id, body.name.strip(), registry_host, body.username.strip(), encrypted, scope, now, now),
    )
    row = credential_row(credential_id)
    audit_log("create", "credential", credential_id, {"registry_host": registry_host, "scope": scope})
    return {"ok": True, "credential": public_credential(row)}


@app.put("/api/credentials/{credential_id}", dependencies=[Depends(require_write_token)])
def update_credential(credential_id: str, body: CredentialIn):
    current = credential_row(credential_id)
    registry_host = normalize_registry_host(body.registry_host)
    scope = validate_credential_scope(body.scope)
    encrypted = encrypt_secret(body.secret) if body.secret else current["encrypted_secret"]
    db_execute(
        """
        UPDATE credentials
        SET name = ?, registry_host = ?, username = ?, encrypted_secret = ?, scope = ?, updated_at = ?
        WHERE id = ?
        """,
        (body.name.strip(), registry_host, body.username.strip(), encrypted, scope, now_iso(), current["id"]),
    )
    row = credential_row(current["id"])
    audit_log("update", "credential", current["id"], {"registry_host": registry_host, "scope": scope, "secret_updated": bool(body.secret)})
    return {"ok": True, "credential": public_credential(row)}


@app.delete("/api/credentials/{credential_id}", dependencies=[Depends(require_write_token)])
def delete_credential(credential_id: str):
    row = credential_row(credential_id)
    refs = mirror_credential_references(load_config(), row["id"])
    if refs:
        raise HTTPException(400, f"凭据仍被镜像引用: {', '.join(refs[:5])}")
    db_execute("DELETE FROM credentials WHERE id = ?", (row["id"],))
    audit_log("delete", "credential", row["id"], {"registry_host": row["registry_host"], "scope": row["scope"]})
    return {"ok": True}


def credential_test_registry_url(row: dict, override: str | None = None) -> tuple[str, str]:
    raw = (override or row["registry_host"] or "").strip().rstrip("/")
    if not raw:
        raise HTTPException(400, "registry_url 不能为空")
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(400, "registry_url 必须是 http:// 或 https:// 地址")
        base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return base_url, "manual"
    host = normalize_registry_host(raw)
    if host == "docker.io":
        return "https://registry-1.docker.io", "docker-hub"
    return f"{registry_scheme_for_host(host)}://{host}", "auto"




def parse_www_authenticate_challenge(value: str) -> dict:
    if not value:
        return {}
    scheme, _, rest = value.partition(" ")
    if scheme.lower() != "bearer":
        return {"scheme": scheme.lower()}
    result = {"scheme": "bearer"}
    for match in re.finditer(r'(realm|service|scope)="([^"]*)"', rest):
        result[match.group(1)] = match.group(2)
    return result


async def registry_bearer_token(
    client: httpx.AsyncClient,
    challenge: dict,
    username: str,
    secret: str,
) -> tuple[str | None, dict | None, str | None]:
    realm = str(challenge.get("realm") or "").strip()
    if not realm:
        return None, None, "Registry 返回 Bearer challenge 但缺少 realm"
    params = {}
    if challenge.get("service"):
        params["service"] = challenge["service"]
    if challenge.get("scope"):
        params["scope"] = challenge["scope"]
    response = await client.get(realm, params=params, auth=(username, secret))
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200:
        return None, {"http_status": response.status_code, "payload": payload}, "Bearer token 获取失败"
    token = payload.get("token") or payload.get("access_token")
    if not token:
        return None, {"http_status": response.status_code, "payload": payload}, "Bearer token 响应缺少 token"
    return str(token), {"http_status": response.status_code}, None


@app.post("/api/credentials/{credential_id}/test", dependencies=[Depends(require_write_token)])
async def test_credential(credential_id: str, body: CredentialTestIn | None = None):
    row = credential_row(credential_id)
    secret = decrypt_secret(row["encrypted_secret"])
    registry_url, url_source = credential_test_registry_url(row, body.registry_url if body else None)
    check_url = f"{registry_url}/v2/"
    detail = {"registry_host": row["registry_host"], "registry_url": registry_url, "url_source": url_source}
    auth_mode = "basic"
    token_detail = None
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            response = await client.get(check_url, auth=(row["username"], secret))
            challenge = parse_www_authenticate_challenge(response.headers.get("www-authenticate", ""))
            if response.status_code == 401 and challenge.get("scheme") == "bearer":
                token, token_detail, token_error = await registry_bearer_token(client, challenge, row["username"], secret)
                auth_mode = "bearer"
                if token:
                    response = await client.get(check_url, headers={"Authorization": f"Bearer {token}"})
                else:
                    audit_log("test_failed", "credential", row["id"], {**detail, "reason": "token", "token_detail": token_detail})
                    return {"ok": False, "status": "authentication_failed", "auth_mode": auth_mode, "http_status": response.status_code, "registry_url": registry_url, "check_url": check_url, "message": token_error or "Bearer token 获取失败"}
    except httpx.ConnectError as exc:
        audit_log("test_failed", "credential", row["id"], {**detail, "reason": "network"})
        return {"ok": False, "status": "network_error", "auth_mode": auth_mode, "registry_url": registry_url, "check_url": check_url, "message": f"无法连接 Registry: {exc.__class__.__name__}"}
    except httpx.ConnectTimeout:
        audit_log("test_failed", "credential", row["id"], {**detail, "reason": "timeout"})
        return {"ok": False, "status": "timeout", "auth_mode": auth_mode, "registry_url": registry_url, "check_url": check_url, "message": "连接 Registry 超时"}
    except httpx.ReadTimeout:
        audit_log("test_failed", "credential", row["id"], {**detail, "reason": "timeout"})
        return {"ok": False, "status": "timeout", "auth_mode": auth_mode, "registry_url": registry_url, "check_url": check_url, "message": "Registry 响应超时"}
    except httpx.HTTPError as exc:
        reason = "tls_or_protocol" if isinstance(exc, (httpx.ConnectError, httpx.ProtocolError)) else "http_error"
        audit_log("test_failed", "credential", row["id"], {**detail, "reason": reason, "error": exc.__class__.__name__})
        return {"ok": False, "status": reason, "auth_mode": auth_mode, "registry_url": registry_url, "check_url": check_url, "message": f"请求 Registry 失败: {exc.__class__.__name__}"}
    if response.status_code in {200, 401, 403}:
        ok = response.status_code == 200
        status = "ok" if ok else ("authentication_failed" if response.status_code == 401 else "permission_denied")
        message = "凭据可用" if ok else ("认证失败：用户名、密码/token 或 Registry 认证流程不匹配" if response.status_code == 401 else "权限不足：凭据有效但没有访问该 Registry 的权限")
        audit_log("test_ok" if ok else "test_failed", "credential", row["id"], {**detail, "status": status, "auth_mode": auth_mode, "http_status": response.status_code, "token_detail": token_detail})
        return {"ok": ok, "status": status, "auth_mode": auth_mode, "http_status": response.status_code, "registry_url": registry_url, "check_url": check_url, "message": message}
    audit_log("test_failed", "credential", row["id"], {**detail, "status": "registry_error", "http_status": response.status_code})
    return {"ok": False, "status": "registry_error", "auth_mode": auth_mode, "http_status": response.status_code, "registry_url": registry_url, "check_url": check_url, "message": f"Registry 返回非预期状态码: {response.status_code}"}


@app.post("/api/registries", dependencies=[Depends(require_write_token)])
def upsert_registry(body: RegistryIn):
    normalized = normalize_registry(body.model_dump())
    if normalized["id"] == "local":
        raise HTTPException(400, "local Registry 是内置默认项，不能覆盖")
    config = load_config()
    registries = registry_map(config)
    registries[normalized["id"]] = normalized
    config["registries"] = [item for key, item in registries.items() if key != "local"]
    save_config(config)
    audit_log("upsert", "registry", normalized["id"], normalized)
    return {"ok": True, "registry": normalized}


@app.delete("/api/registries/{registry_id}", dependencies=[Depends(require_write_token)])
def delete_registry(registry_id: str):
    clean_id = validate_slug(registry_id, "registry")
    if clean_id == "local":
        raise HTTPException(400, "local Registry 是内置默认项，不能删除")
    config = load_config()
    mirrors = valid_mirrors(config)
    if any(mirror["registry"] == clean_id for mirror in mirrors):
        raise HTTPException(400, "该 Registry 仍被镜像引用")
    registries = registry_map(config)
    registries.pop(clean_id, None)
    config["registries"] = [item for key, item in registries.items() if key != "local"]
    save_config(config)
    audit_log("delete", "registry", clean_id, {})
    return {"ok": True}


@app.get("/api/mirror-groups")
def list_mirror_groups():
    return list(group_map(load_config()).values())


@app.post("/api/mirror-groups", dependencies=[Depends(require_write_token)])
def upsert_mirror_group(body: MirrorGroupIn):
    normalized = normalize_group(body.model_dump())
    if normalized["id"] == "default":
        raise HTTPException(400, "default 镜像组是内置默认项，不能覆盖")
    registries = registry_map(load_config())
    if normalized["registry"] not in registries:
        raise HTTPException(400, "镜像组引用的 Registry 不存在")
    config = load_config()
    groups = group_map(config)
    groups[normalized["id"]] = normalized
    config["mirror_groups"] = [item for key, item in groups.items() if key != "default"]
    save_config(config)
    audit_log("upsert", "mirror_group", normalized["id"], normalized)
    return {"ok": True, "group": normalized}


@app.delete("/api/mirror-groups/{group_id}", dependencies=[Depends(require_write_token)])
def delete_mirror_group(group_id: str):
    clean_id = validate_slug(group_id, "group")
    if clean_id == "default":
        raise HTTPException(400, "default 镜像组是内置默认项，不能删除")
    config = load_config()
    mirrors = valid_mirrors(config)
    if any(mirror["group"] == clean_id for mirror in mirrors):
        raise HTTPException(400, "该镜像组仍被镜像引用")
    groups = group_map(config)
    groups.pop(clean_id, None)
    config["mirror_groups"] = [item for key, item in groups.items() if key != "default"]
    save_config(config)
    audit_log("delete", "mirror_group", clean_id, {})
    return {"ok": True}


@app.get("/api/audit-logs")
def list_audit_logs(limit: int = 100):
    bounded_limit = max(1, min(limit, 500))
    rows = db_rows(
        """
        SELECT id, created_at, actor, action, resource_type, resource_id, detail
        FROM audit_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (bounded_limit,),
    )
    for row in rows:
        try:
            row["detail"] = json.loads(row.get("detail") or "{}")
        except json.JSONDecodeError:
            row["detail"] = {}
    return rows


@app.get("/api/deployment-modes")
def list_deployment_modes():
    return deployment_modes()


@app.get("/api/database-guide")
def get_database_guide():
    return external_database_guide()


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
