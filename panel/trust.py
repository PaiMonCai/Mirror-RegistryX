"""Release trust, scan, promotion, rollback, and restore-drill APIs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from mirror_registry_core.mirror_rules import bool_int
from mirror_registry_core.trust import (
    TRUST_ALLOWED_FOR_PROMOTION,
    compute_trust_status,
    image_ref_for_digest,
    json_loads,
    safe_release_artifact_dir,
)

from . import legacy
from .auth import require_write_token
from .db import db_execute, db_one, db_rows
from .schemas import ReleaseBypassIn, ReleasePromoteIn, ReleaseRollbackIn, RestoreDrillIn


router = APIRouter(prefix="/api")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def actor_from_request(request: Request) -> str:
    user = getattr(request.state, "auth_user", None) or {}
    return str(user.get("username") or "panel")


def artifact_root() -> Path:
    return Path(os.getenv("ARTIFACT_ROOT", "/data/artifacts"))


def public_release(row: dict) -> dict:
    return {
        "id": row["id"],
        "mirror_source": row["mirror_source"],
        "source_image": row["source_image"],
        "target_image": row["target_image"],
        "source_digest": row["source_digest"],
        "target_digest": row["target_digest"],
        "target_repo": row["target_repo"],
        "target_tag": row["target_tag"],
        "release_type": row["release_type"],
        "parent_release_id": row.get("parent_release_id") or "",
        "trust_status": row.get("trust_status") or "unknown",
        "scan_status": row.get("scan_status") or "not_scanned",
        "scanner": row.get("scanner") or "",
        "scanner_version": row.get("scanner_version") or "",
        "severity": {
            "critical": int(row.get("severity_critical") or 0),
            "high": int(row.get("severity_high") or 0),
            "medium": int(row.get("severity_medium") or 0),
            "low": int(row.get("severity_low") or 0),
            "unknown": int(row.get("severity_unknown") or 0),
        },
        "scan_report_path": row.get("scan_report_path") or "",
        "sbom_path": row.get("sbom_path") or "",
        "metadata_path": row.get("metadata_path") or "",
        "signature_status": row.get("signature_status") or "skipped",
        "bypass_reason": row.get("bypass_reason") or "",
        "bypassed_by": row.get("bypassed_by") or "",
        "bypassed_at": row.get("bypassed_at") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def release_row(release_id: str) -> dict:
    row = db_one("SELECT * FROM mirror_releases WHERE id = ?", (release_id,))
    if not row:
        raise HTTPException(404, "release 不存在")
    return row


def record_release_event(release_id: str, event_type: str, status: str = "", message: str = "", detail: dict | None = None) -> None:
    db_execute(
        """
        INSERT INTO release_events(release_id, type, status, message, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (release_id, event_type, status, message, json.dumps(detail or {}, ensure_ascii=False), now_iso()),
    )


def enqueue_release_task(release: dict, task_type: str, reason: str, target_image: str = "") -> dict:
    now = now_iso()
    dedupe = f"{task_type}:{reason}:{release['id']}:{target_image}"
    queue_id = db_execute(
        """
        INSERT INTO sync_queue(reason, sources, priority, status, dedupe_key, scheduled_at, attempts,
                               created_at, updated_at, message, task_type, mirror_source, mirror_target, digest)
        VALUES (?, ?, ?, 'queued', ?, ?, 0, ?, ?, 'queued', ?, ?, ?, ?)
        """,
        (
            reason,
            json.dumps([release["mirror_source"]], ensure_ascii=False),
            55 if task_type in {"promote", "rollback"} else 70,
            dedupe,
            now,
            now,
            now,
            task_type,
            release["mirror_source"],
            target_image,
            release["id"],
        ),
    )
    return db_one("SELECT * FROM sync_queue WHERE id = ?", (queue_id,)) or {"id": queue_id}


@router.get("/releases")
def list_releases(mirror_source: str = "", target_image: str = "", trust_status: str = "", limit: int = 100):
    clauses = []
    params: list[Any] = []
    if mirror_source:
        clauses.append("mirror_source = ?")
        params.append(mirror_source)
    if target_image:
        clauses.append("target_image = ?")
        params.append(target_image)
    if trust_status:
        clauses.append("trust_status = ?")
        params.append(trust_status)
    sql = "SELECT * FROM mirror_releases"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    rows = db_rows(sql, tuple(params + [max(1, min(int(limit or 100), 500))]))
    return [public_release(row) for row in rows]


@router.get("/releases/{release_id}")
def get_release(release_id: str):
    row = release_row(release_id)
    payload = public_release(row)
    payload["rule_snapshot"] = json_loads(row.get("rule_snapshot_json"), {})
    payload["policy_snapshot"] = json_loads(row.get("policy_snapshot_json"), {})
    return payload


@router.get("/releases/{release_id}/events")
def list_release_events(release_id: str):
    release_row(release_id)
    rows = db_rows("SELECT * FROM release_events WHERE release_id = ? ORDER BY id", (release_id,))
    return [
        {
            "id": row["id"],
            "release_id": row["release_id"],
            "type": row["type"],
            "status": row.get("status") or "",
            "message": row.get("message") or "",
            "detail": json_loads(row.get("detail_json"), {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@router.post("/releases/{release_id}/scan", dependencies=[Depends(require_write_token)])
def enqueue_release_scan(release_id: str, request: Request):
    release = release_row(release_id)
    queue = enqueue_release_task(release, "scan", f"release-scan:{release_id}")
    now = now_iso()
    db_execute(
        """
        INSERT INTO image_scan_tasks(release_id, image_ref, scanner, status, scheduled_at, created_at, updated_at)
        VALUES (?, ?, 'trivy', 'queued', ?, ?, ?)
        """,
        (release_id, image_ref_for_digest(release["target_image"], release["target_digest"]), now, now, now),
    )
    db_execute("UPDATE mirror_releases SET scan_status = 'queued', trust_status = 'scanning', scanner = 'trivy', updated_at = ? WHERE id = ?", (now, release_id))
    record_release_event(release_id, "scan_queued", "queued", "manual scan queued", {"queue_id": queue.get("id")})
    legacy.audit_log("scan_release", "release", release_id, {"queue_id": queue.get("id")}, actor=actor_from_request(request))
    return {"ok": True, "queue": queue, "release": public_release(release_row(release_id))}


@router.post("/releases/{release_id}/bypass", dependencies=[Depends(require_write_token)])
def bypass_release(release_id: str, body: ReleaseBypassIn, request: Request):
    release = release_row(release_id)
    now = now_iso()
    db_execute(
        """
        UPDATE mirror_releases
        SET trust_status = 'bypassed', bypass_reason = ?, bypassed_by = ?, bypassed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (body.reason, actor_from_request(request), now, now, release_id),
    )
    record_release_event(release_id, "bypassed", "bypassed", body.reason, {"previous_trust_status": release.get("trust_status")})
    legacy.audit_log("bypass_release", "release", release_id, {"reason": body.reason}, actor=actor_from_request(request))
    return {"ok": True, "release": public_release(release_row(release_id))}


def ensure_release_promotable(release: dict, confirm: bool) -> None:
    status = release.get("trust_status") or "unknown"
    if status not in TRUST_ALLOWED_FOR_PROMOTION:
        raise HTTPException(409, f"release trust_status={status} 不允许提升或回滚")
    if status in {"warning", "bypassed"} and not confirm:
        raise HTTPException(409, f"release trust_status={status} 需要显式确认")


@router.post("/releases/{release_id}/promote", dependencies=[Depends(require_write_token)])
def promote_release(release_id: str, body: ReleasePromoteIn, request: Request):
    release = release_row(release_id)
    ensure_release_promotable(release, body.confirm)
    target = legacy.validate_image_ref(body.target_image, "target_image")
    queue = enqueue_release_task(release, "promote", f"release-promote:{release_id}", target)
    record_release_event(release_id, "promoted", "queued", body.reason or "promotion queued", {"queue_id": queue.get("id"), "target_image": target})
    legacy.audit_log("promote_release", "release", release_id, {"target_image": target, "queue_id": queue.get("id"), "reason": body.reason or ""}, actor=actor_from_request(request))
    return {"ok": True, "queue": queue}


@router.post("/releases/{release_id}/rollback", dependencies=[Depends(require_write_token)])
def rollback_release(release_id: str, body: ReleaseRollbackIn, request: Request):
    release = release_row(release_id)
    ensure_release_promotable(release, body.confirm)
    queue = enqueue_release_task(release, "rollback", f"release-rollback:{release_id}", release["target_image"])
    record_release_event(release_id, "rollback_started", "queued", body.reason or "rollback queued", {"queue_id": queue.get("id")})
    legacy.audit_log("rollback_release", "release", release_id, {"queue_id": queue.get("id"), "reason": body.reason or ""}, actor=actor_from_request(request))
    return {"ok": True, "queue": queue}


@router.get("/releases/{release_id}/artifacts/{artifact_kind}")
def get_release_artifact(release_id: str, artifact_kind: str):
    release = release_row(release_id)
    if artifact_kind not in {"metadata", "scan", "sbom"}:
        raise HTTPException(404, "artifact 不存在")
    field = {"metadata": "metadata_path", "scan": "scan_report_path", "sbom": "sbom_path"}[artifact_kind]
    rel_path = str(release.get(field) or "")
    if not rel_path:
        raise HTTPException(404, "artifact 尚未生成")
    root = artifact_root().resolve()
    path = (root / rel_path).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(400, "artifact path 越界")
    if not path.is_file():
        raise HTTPException(404, "artifact 文件不存在")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"artifact JSON 无法解析: {exc}") from exc


@router.get("/scan-tasks")
def list_scan_tasks(status: str = "", release_id: str = "", limit: int = 100):
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if release_id:
        clauses.append("release_id = ?")
        params.append(release_id)
    sql = "SELECT * FROM image_scan_tasks"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    return db_rows(sql, tuple(params + [max(1, min(int(limit or 100), 500))]))


@router.post("/scan-tasks/{task_id}/cancel", dependencies=[Depends(require_write_token)])
def cancel_scan_task(task_id: int, request: Request):
    row = db_one("SELECT * FROM image_scan_tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, "scan task 不存在")
    if row["status"] not in {"queued", "running"}:
        return {"ok": True, "task": row}
    now = now_iso()
    db_execute("UPDATE image_scan_tasks SET status = 'canceled', finished_at = ?, updated_at = ? WHERE id = ?", (now, now, task_id))
    record_release_event(row["release_id"], "scan_canceled", "canceled", "scan canceled by panel")
    legacy.audit_log("cancel_scan_task", "scan_task", str(task_id), {}, actor=actor_from_request(request))
    return {"ok": True, "task": db_one("SELECT * FROM image_scan_tasks WHERE id = ?", (task_id,))}


@router.get("/trust/summary")
def trust_summary():
    rows = db_rows("SELECT trust_status, COUNT(*) AS count FROM mirror_releases GROUP BY trust_status")
    scans = db_rows("SELECT scan_status, COUNT(*) AS count FROM mirror_releases GROUP BY scan_status")
    return {
        "trust": {row["trust_status"]: row["count"] for row in rows},
        "scans": {row["scan_status"]: row["count"] for row in scans},
        "blocked": int(next((row["count"] for row in rows if row["trust_status"] == "blocked"), 0) or 0),
        "scan_failed": int(next((row["count"] for row in scans if row["scan_status"] == "failed"), 0) or 0),
    }


@router.get("/trust/issues")
def trust_issues(severity: str = "", status: str = "", limit: int = 100):
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("trust_status = ?")
        params.append(status)
    else:
        clauses.append("trust_status IN ('blocked', 'warning', 'scan_failed', 'unknown')")
    if severity == "critical":
        clauses.append("severity_critical > 0")
    elif severity == "high":
        clauses.append("severity_high > 0")
    rows = db_rows("SELECT * FROM mirror_releases WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT ?", tuple(params + [max(1, min(int(limit or 100), 500))]))
    return [public_release(row) for row in rows]


@router.post("/restore-drills", dependencies=[Depends(require_write_token)])
def create_restore_drill(body: RestoreDrillIn, request: Request):
    scope = body.model_dump()
    now = now_iso()
    ops_task_id = None
    if body.use_ops_agent:
        from .ops_agent import create_ops_task

        task = create_ops_task(
            "restore_drill",
            {"backup_package": body.backup_package or "", "compose_project": body.compose_project, "cleanup": bool_int(body.cleanup)},
            requested_by="restore_drill",
            actor=actor_from_request(request),
        )
        ops_task_id = int(task.get("id") or 0) or None
    drill_id = db_execute(
        """
        INSERT INTO restore_drills(status, scope_json, report_json, ops_task_id, requested_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("queued" if ops_task_id else "completed", json.dumps(scope, ensure_ascii=False), "{}", ops_task_id, actor_from_request(request), now, now),
    )
    legacy.audit_log("create_restore_drill", "restore_drill", str(drill_id), {"ops_task_id": ops_task_id}, actor=actor_from_request(request))
    return {"ok": True, "drill": get_restore_drill(drill_id)}


@router.get("/restore-drills/{drill_id}")
def get_restore_drill(drill_id: int):
    row = db_one("SELECT * FROM restore_drills WHERE id = ?", (drill_id,))
    if not row:
        raise HTTPException(404, "restore drill 不存在")
    return {
        "id": row["id"],
        "status": row["status"],
        "scope": json_loads(row.get("scope_json"), {}),
        "report": json_loads(row.get("report_json"), {}),
        "ops_task_id": row.get("ops_task_id"),
        "requested_by": row.get("requested_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "finished_at": row.get("finished_at"),
    }
