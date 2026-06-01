import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_write_token
from .db import db_execute, db_one, db_rows
from .schemas import WorkerClaimIn, WorkerCompleteIn, WorkerHeartbeatIn

def trigger_path() -> Path:
    return Path(os.getenv("TRIGGER_PATH", "/data/.trigger"))


QUEUE_ACTIVE_STATUSES = {"queued", "running", "paused", "cancel_requested"}
QUEUE_TERMINAL_STATUSES = {"completed", "failed", "canceled"}

router = APIRouter(prefix="/api")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_slug(value: str, field_name: str) -> str:
    slug = value.strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", slug):
        raise HTTPException(400, f"{field_name} 只能包含字母、数字、点、下划线和短横线")
    return slug


def audit_log(action: str, resource_type: str, resource_id: str, detail: dict | None = None, actor: str = "panel") -> None:
    db_execute(
        """
        INSERT INTO audit_logs(created_at, actor, action, resource_type, resource_id, detail)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now_iso(), actor, action, resource_type, resource_id, json.dumps(detail or {}, ensure_ascii=False)),
    )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    ensure_parent(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)


def clean_queue_sources(source: str | None = None, sources: list[str] | None = None) -> list[str]:
    clean_sources = [item.strip() for item in (sources or []) if item.strip()]
    if not clean_sources and source and source.strip():
        clean_sources = [source.strip()]
    return list(dict.fromkeys(clean_sources))


def queue_dedupe_key(reason: str, sources: list[str]) -> str:
    raw = json.dumps({"reason": reason, "sources": sorted(sources)}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_queue_sources(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item).strip() for item in decoded if str(item).strip()]


def public_sync_queue_task(row: dict) -> dict:
    return {
        "id": row["id"],
        "reason": row["reason"],
        "sources": parse_queue_sources(row.get("sources")),
        "priority": int(row.get("priority") or 100),
        "status": row["status"],
        "dedupe_key": row.get("dedupe_key") or "",
        "scheduled_at": row.get("scheduled_at") or "",
        "attempts": int(row.get("attempts") or 0),
        "run_id": row.get("run_id"),
        "message": row.get("message") or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row.get("started_at") or "",
        "finished_at": row.get("finished_at") or "",
    }


def sync_queue_row(queue_id: int) -> dict:
    row = db_one(
        """
        SELECT id, reason, sources, priority, status, dedupe_key, scheduled_at, attempts, run_id,
               message, created_at, updated_at, started_at, finished_at
        FROM sync_queue
        WHERE id = ?
        """,
        (queue_id,),
    )
    if not row:
        raise HTTPException(404, "同步队列任务不存在")
    return row


def list_sync_queue(limit: int = 50, status: str = "") -> list[dict]:
    bounded_limit = max(1, min(limit, 200))
    clean_status = status.strip().lower()
    if clean_status:
        rows = db_rows(
            """
            SELECT id, reason, sources, priority, status, dedupe_key, scheduled_at, attempts, run_id,
                   message, created_at, updated_at, started_at, finished_at
            FROM sync_queue
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (clean_status, bounded_limit),
        )
    else:
        rows = db_rows(
            """
            SELECT id, reason, sources, priority, status, dedupe_key, scheduled_at, attempts, run_id,
                   message, created_at, updated_at, started_at, finished_at
            FROM sync_queue
            ORDER BY id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        )
    return [public_sync_queue_task(row) for row in rows]


def wake_sync_worker(payload: dict) -> None:
    path = trigger_path()
    ensure_parent(path)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))


def enqueue_sync_task(
    reason: str,
    source: str | None = None,
    sources: list[str] | None = None,
    priority: int = 100,
    scheduled_at: str | None = None,
    actor: str = "panel",
    force: bool = False,
) -> dict:
    clean_reason = reason.strip() or "manual"
    clean_sources = clean_queue_sources(source=source, sources=sources)
    dedupe_key = queue_dedupe_key(clean_reason, clean_sources)
    if not force:
        existing = db_one(
            """
            SELECT id, reason, sources, priority, status, dedupe_key, scheduled_at, attempts, run_id,
                   message, created_at, updated_at, started_at, finished_at
            FROM sync_queue
            WHERE dedupe_key = ? AND status IN ('queued', 'running', 'paused', 'cancel_requested')
            ORDER BY id DESC
            LIMIT 1
            """,
            (dedupe_key,),
        )
        if existing:
            task = public_sync_queue_task(existing)
            task["duplicate"] = True
            wake_sync_worker({"reason": clean_reason, "sources": clean_sources, "queue_id": task["id"], "queued": True})
            return task
    now = now_iso()
    run_after = scheduled_at or now
    queue_id = db_execute(
        """
        INSERT INTO sync_queue(reason, sources, priority, status, dedupe_key, scheduled_at, attempts, created_at, updated_at, message)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            clean_reason,
            json.dumps(clean_sources, ensure_ascii=False),
            max(0, min(int(priority), 1000)),
            "queued",
            dedupe_key,
            run_after,
            now,
            now,
            "queued",
        ),
    )
    audit_log("enqueue", "sync_queue", str(queue_id), {"reason": clean_reason, "sources": clean_sources, "priority": priority}, actor=actor)
    wake_sync_worker({"reason": clean_reason, "sources": clean_sources, "queue_id": queue_id, "queued": True})
    task = public_sync_queue_task(sync_queue_row(queue_id))
    task["duplicate"] = False
    return task


def write_trigger(reason: str, source: str | None = None, sources: list[str] | None = None) -> dict:
    return enqueue_sync_task(reason, source=source, sources=sources)


def update_sync_queue_status(queue_id: int, status: str, message: str) -> dict:
    row = sync_queue_row(queue_id)
    now = now_iso()
    db_execute(
        "UPDATE sync_queue SET status = ?, message = ?, updated_at = ?, finished_at = CASE WHEN ? IN ('canceled') THEN ? ELSE finished_at END WHERE id = ?",
        (status, message, now, status, now, row["id"]),
    )
    audit_log(status, "sync_queue", str(row["id"]), {"previous_status": row["status"], "message": message})
    return public_sync_queue_task(sync_queue_row(row["id"]))


def replay_sync_queue_task(queue_id: int) -> dict:
    row = sync_queue_row(queue_id)
    if row["status"] not in QUEUE_TERMINAL_STATUSES:
        raise HTTPException(409, "只有已完成、失败或已取消的队列任务可重放")
    sources = parse_queue_sources(row.get("sources"))
    task = enqueue_sync_task(f"replay:{row['reason']}", sources=sources, priority=int(row.get("priority") or 100), actor="panel", force=True)
    audit_log("replay", "sync_queue", str(row["id"]), {"new_queue_id": task["id"], "sources": sources})
    return task


def clean_worker_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))[:32]


def parse_worker_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return clean_worker_list([str(item) for item in decoded])


def worker_row(worker_id: str) -> dict | None:
    return db_one(
        """
        SELECT worker_id, name, labels, environment, capabilities, status, last_heartbeat, version, message, created_at, updated_at
        FROM workers
        WHERE worker_id = ?
        """,
        (validate_slug(worker_id, "worker_id"),),
    )


def latest_worker_claim(worker_id: str) -> dict | None:
    return db_one(
        """
        SELECT id, worker_id, queue_id, status, claimed_at, finished_at, message
        FROM worker_claims
        WHERE worker_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (worker_id,),
    )


def public_worker(row: dict) -> dict:
    latest = latest_worker_claim(row["worker_id"])
    return {
        "worker_id": row["worker_id"],
        "name": row["name"],
        "labels": parse_worker_list(row.get("labels")),
        "environment": row.get("environment") or "local",
        "capabilities": parse_worker_list(row.get("capabilities")),
        "status": row["status"],
        "last_heartbeat": row["last_heartbeat"],
        "version": row.get("version") or "",
        "message": row.get("message") or "",
        "latest_claim": latest,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_worker_heartbeat(body: WorkerHeartbeatIn, status: str = "online") -> dict:
    worker_id = validate_slug(body.worker_id, "worker_id")
    now = now_iso()
    existing = worker_row(worker_id)
    values = (
        body.name or worker_id,
        json.dumps(clean_worker_list(body.labels), ensure_ascii=False),
        (body.environment or "local").strip() or "local",
        json.dumps(clean_worker_list(body.capabilities), ensure_ascii=False),
        status,
        now,
        body.version or "",
        body.message or "",
        now,
        worker_id,
    )
    if existing:
        db_execute(
            """
            UPDATE workers
            SET name = ?, labels = ?, environment = ?, capabilities = ?, status = ?, last_heartbeat = ?,
                version = ?, message = ?, updated_at = ?
            WHERE worker_id = ?
            """,
            values,
        )
    else:
        db_execute(
            """
            INSERT INTO workers(worker_id, name, labels, environment, capabilities, status, last_heartbeat, version, message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (worker_id, *values[:-1], now),
        )
    return public_worker(worker_row(worker_id))


def list_worker_rows() -> list[dict]:
    rows = db_rows(
        """
        SELECT worker_id, name, labels, environment, capabilities, status, last_heartbeat, version, message, created_at, updated_at
        FROM workers
        ORDER BY last_heartbeat DESC, worker_id
        """
    )
    return [public_worker(row) for row in rows]


def next_worker_queue_task() -> dict | None:
    return db_one(
        """
        SELECT id, reason, sources, priority, status, dedupe_key, scheduled_at, attempts, run_id,
               message, created_at, updated_at, started_at, finished_at
        FROM sync_queue
        WHERE status = 'queued' AND scheduled_at <= ?
        ORDER BY priority ASC, id ASC
        LIMIT 1
        """,
        (now_iso(),),
    )


def claim_worker_queue_task(body: WorkerClaimIn) -> dict:
    heartbeat = WorkerHeartbeatIn(worker_id=body.worker_id, labels=body.labels, environment=body.environment or "remote", capabilities=["sync-queue"], message="claim")
    worker = upsert_worker_heartbeat(heartbeat)
    row = next_worker_queue_task()
    if not row:
        return {"ok": True, "worker": worker, "task": None, "claim": None}
    now = now_iso()
    db_execute(
        """
        UPDATE sync_queue
        SET status = 'running', message = ?, updated_at = ?, started_at = COALESCE(started_at, ?), attempts = attempts + 1
        WHERE id = ? AND status = 'queued'
        """,
        (f"claimed by worker {worker['worker_id']}", now, now, row["id"]),
    )
    claim_id = db_execute(
        """
        INSERT INTO worker_claims(worker_id, queue_id, status, claimed_at, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (worker["worker_id"], row["id"], "running", now, "claimed"),
    )
    task = public_sync_queue_task(sync_queue_row(row["id"]))
    claim = db_one("SELECT id, worker_id, queue_id, status, claimed_at, finished_at, message FROM worker_claims WHERE id = ?", (claim_id,))
    audit_log("claim", "sync_queue", str(row["id"]), {"worker_id": worker["worker_id"]}, actor=f"worker:{worker['worker_id']}")
    return {"ok": True, "worker": worker, "task": task, "claim": claim}


def complete_worker_queue_task(body: WorkerCompleteIn) -> dict:
    worker_id = validate_slug(body.worker_id, "worker_id")
    status = body.status.strip().lower()
    if status not in QUEUE_TERMINAL_STATUSES:
        raise HTTPException(400, "worker 完成状态必须是 completed、failed 或 canceled")
    row = sync_queue_row(body.queue_id)
    now = now_iso()
    db_execute(
        """
        UPDATE sync_queue
        SET status = ?, message = ?, run_id = COALESCE(?, run_id), updated_at = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, body.message or status, body.run_id, now, now, row["id"]),
    )
    claim = db_one(
        "SELECT id FROM worker_claims WHERE worker_id = ? AND queue_id = ? ORDER BY id DESC LIMIT 1",
        (worker_id, row["id"]),
    )
    if claim:
        db_execute("UPDATE worker_claims SET status = ?, finished_at = ?, message = ? WHERE id = ?", (status, now, body.message or status, claim["id"]))
    else:
        db_execute(
            "INSERT INTO worker_claims(worker_id, queue_id, status, claimed_at, finished_at, message) VALUES (?, ?, ?, ?, ?, ?)",
            (worker_id, row["id"], status, now, now, body.message or status),
        )
    heartbeat = WorkerHeartbeatIn(worker_id=worker_id, capabilities=["sync-queue"], message=f"completed queue {row['id']}")
    worker = upsert_worker_heartbeat(heartbeat)
    audit_log("complete", "sync_queue", str(row["id"]), {"worker_id": worker_id, "status": status, "run_id": body.run_id}, actor=f"worker:{worker_id}")
    return {"ok": True, "worker": worker, "queue": public_sync_queue_task(sync_queue_row(row["id"]))}


@router.post("/sync", dependencies=[Depends(require_write_token)])
def trigger_sync():
    task = write_trigger("manual")
    audit_log("trigger_sync", "mirrors", "all", {"queue_id": task["id"]})
    return {"ok": True, "message": "同步任务已入队，请稍后查看队列和任务历史", "queue": task}


@router.get("/sync-queue")
def get_sync_queue(limit: int = 50, status: str = ""):
    return list_sync_queue(limit=limit, status=status)


@router.post("/sync-queue/{queue_id}/pause", dependencies=[Depends(require_write_token)])
def pause_sync_queue_task(queue_id: int):
    row = sync_queue_row(queue_id)
    if row["status"] != "queued":
        raise HTTPException(409, "只有排队中的任务可暂停")
    return {"ok": True, "queue": update_sync_queue_status(queue_id, "paused", "paused by panel")}


@router.post("/sync-queue/{queue_id}/resume", dependencies=[Depends(require_write_token)])
def resume_sync_queue_task(queue_id: int):
    row = sync_queue_row(queue_id)
    if row["status"] != "paused":
        raise HTTPException(409, "只有已暂停任务可恢复")
    return {"ok": True, "queue": update_sync_queue_status(queue_id, "queued", "resumed by panel")}


@router.post("/sync-queue/{queue_id}/cancel", dependencies=[Depends(require_write_token)])
def cancel_sync_queue_task(queue_id: int):
    row = sync_queue_row(queue_id)
    if row["status"] in {"completed", "failed", "canceled"}:
        raise HTTPException(409, "终态队列任务不能取消")
    if row["status"] == "running":
        return {"ok": True, "queue": update_sync_queue_status(queue_id, "cancel_requested", "cancel requested by panel")}
    return {"ok": True, "queue": update_sync_queue_status(queue_id, "canceled", "canceled by panel")}


@router.post("/sync-queue/{queue_id}/replay", dependencies=[Depends(require_write_token)])
def replay_sync_queue(queue_id: int):
    return {"ok": True, "queue": replay_sync_queue_task(queue_id)}
