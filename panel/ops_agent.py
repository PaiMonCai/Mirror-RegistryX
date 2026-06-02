"""Ops-agent task APIs and panel-facing operation controls."""

from __future__ import annotations

import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from mirror_registry_core.ops_agent import (
    ACTIVE_TASK_STATUSES,
    ALLOWED_ACTIONS,
    HIGH_RISK_ACTIONS,
    IDEMPOTENT_ACTIONS,
    TERMINAL_TASK_STATUSES,
    json_dumps,
    json_loads_object,
    normalize_capabilities,
    redact_data,
    redact_text,
    validate_action,
)

from . import legacy
from .auth import audit_log, require_write_token
from .db import db_execute, db_one, db_rows
from .schemas import (
    OpsAgentClaimIn,
    OpsAgentHeartbeatIn,
    OpsAgentTaskControlIn,
    OpsTaskCompleteIn,
    OpsTaskCreateIn,
    OpsTaskEventIn,
)


router = APIRouter(prefix="/api")

AGENT_STATUSES = {"online", "offline", "degraded"}
CLAIMABLE_STATUSES = {"queued"}
AGENT_ENDPOINT_PREFIX = "/api/ops-agent/"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def actor_from_request(request: Request) -> str:
    user = getattr(request.state, "auth_user", None) or {}
    return str(user.get("username") or "panel")


def require_ops_agent_token(request: Request) -> None:
    expected = os.getenv("OPS_AGENT_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "OPS_AGENT_TOKEN 未配置，ops-agent 接口不可用")
    authorization = request.headers.get("authorization", "")
    token = request.headers.get("x-ops-agent-token", "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(401, "ops-agent token 无效")


def task_is_high_risk(row: dict) -> bool:
    action = str(row.get("action") or "")
    if action in HIGH_RISK_ACTIONS:
        return True
    if action == "restart_service":
        params = json_loads_object(row.get("params_json"))
        return params.get("service") in {"registry", "ops-agent"}
    return False


def insert_task_event(task_id: int, event_type: str, message: str = "", detail: dict[str, Any] | None = None) -> None:
    db_execute(
        """
        INSERT INTO ops_task_events(task_id, type, message, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            task_id,
            event_type,
            redact_text(message, max_length=20000),
            json_dumps(redact_data(detail or {})),
            now_iso(),
        ),
    )


def public_task(row: dict | None) -> dict | None:
    if not row:
        return None
    params = json_loads_object(row.get("params_json"))
    return {
        "id": row["id"],
        "action": row["action"],
        "params": params,
        "status": row["status"],
        "agent_id": row.get("agent_id"),
        "requested_by": row.get("requested_by") or "panel",
        "requires_confirmation": bool(row.get("confirm_token") and not row.get("confirmed_at")),
        "confirmed_at": row.get("confirmed_at"),
        "lease_expires_at": row.get("lease_expires_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "timeout_seconds": row.get("timeout_seconds"),
        "exit_code": row.get("exit_code"),
        "log_tail": row.get("log_tail") or "",
        "error": row.get("error") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "high_risk": task_is_high_risk(row),
    }


def public_agent(row: dict) -> dict:
    capabilities = normalize_capabilities(json_loads_object(row.get("capabilities_json"), {"items": []}).get("items", []))
    if not capabilities:
        import json

        try:
            raw = json.loads(row.get("capabilities_json") or "[]")
        except Exception:
            raw = []
        capabilities = normalize_capabilities(raw if isinstance(raw, list) else [])
    status = str(row.get("status") or "offline")
    last_heartbeat = parse_iso(row.get("last_heartbeat_at"))
    if status == "online" and last_heartbeat and datetime.now(timezone.utc) - last_heartbeat > timedelta(seconds=180):
        status = "offline"
    return {
        "agent_id": row["agent_id"],
        "host_label": row.get("host_label") or row["agent_id"],
        "environment": row.get("environment") or "prod",
        "capabilities": capabilities,
        "status": status,
        "last_heartbeat_at": row.get("last_heartbeat_at"),
        "version": row.get("version"),
        "message": row.get("message") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def active_high_risk_task(agent_id: str | None = None, exclude_task_id: int | None = None) -> dict | None:
    rows = db_rows(
        """
        SELECT * FROM ops_tasks
        WHERE status IN ('queued', 'claimed', 'running')
        ORDER BY id
        """
    )
    for row in rows:
        if exclude_task_id and int(row["id"]) == int(exclude_task_id):
            continue
        if agent_id and row.get("agent_id") and row.get("agent_id") != agent_id:
            continue
        if task_is_high_risk(row):
            return row
    return None


def ensure_no_active_high_risk(agent_id: str | None, action_row: dict | None = None) -> None:
    if action_row and not task_is_high_risk(action_row):
        return
    existing = active_high_risk_task(agent_id)
    if existing:
        raise HTTPException(409, f"已有高风险运维任务未完成: #{existing['id']} {existing['action']}")


def create_ops_task(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    agent_id: str | None = None,
    requested_by: str = "panel",
    actor: str = "panel",
    confirmed: bool = False,
) -> dict:
    try:
        validated = validate_action(action, params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    pending_row = {
        "action": validated.action,
        "params_json": json_dumps(validated.params),
    }
    if task_is_high_risk(pending_row):
        ensure_no_active_high_risk(agent_id, pending_row)

    created_at = now_iso()
    confirm_token = None
    confirmed_at = None
    if validated.requires_confirmation and not confirmed:
        confirm_token = secrets.token_urlsafe(24)
    elif validated.requires_confirmation or confirmed:
        confirmed_at = created_at

    task_id = db_execute(
        """
        INSERT INTO ops_tasks(
            action, params_json, status, agent_id, requested_by, confirm_token,
            confirmed_at, timeout_seconds, created_at, updated_at
        )
        VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            validated.action,
            json_dumps(validated.params),
            agent_id.strip() if agent_id else None,
            requested_by,
            confirm_token,
            confirmed_at,
            validated.timeout_seconds,
            created_at,
            created_at,
        ),
    )
    insert_task_event(task_id, "created", f"queued {validated.action}", {"params": validated.params, "requested_by": requested_by})
    audit_log(
        "create_ops_task",
        "ops_task",
        str(task_id),
        redact_data({"action": validated.action, "params": validated.params, "agent_id": agent_id, "confirmed": bool(confirmed_at)}),
        actor=actor,
    )
    return public_task(db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))) or {}


def update_gc_state_from_task(row: dict, status: str, message: str = "", log_tail: str = "") -> None:
    if row.get("action") != "registry_gc":
        return
    params = json_loads_object(row.get("params_json"))
    request_id = str(params.get("request_id") or f"ops-{row['id']}")
    now = now_iso()
    current = legacy.storage_gc_status()
    values = {
        "storage_gc_request_id": request_id,
        "storage_gc_status": status,
        "storage_gc_message": redact_text(message or current.get("message") or ""),
        "storage_gc_log_tail": redact_text(log_tail or current.get("log_tail") or ""),
        "storage_gc_requested_at": current.get("requested_at") or row.get("created_at") or now,
    }
    if status == "running":
        values["storage_gc_started_at"] = current.get("started_at") or now
        values["storage_gc_finished_at"] = ""
    elif status in {"completed", "failed"}:
        values["storage_gc_started_at"] = current.get("started_at") or row.get("started_at") or now
        values["storage_gc_finished_at"] = now
    legacy.set_storage_gc_values(values)


def recover_stale_ops_tasks() -> None:
    now = datetime.now(timezone.utc)
    rows = db_rows(
        """
        SELECT * FROM ops_tasks
        WHERE status IN ('claimed', 'running') AND lease_expires_at IS NOT NULL
        ORDER BY id
        """
    )
    for row in rows:
        lease_expires = parse_iso(row.get("lease_expires_at"))
        if not lease_expires or lease_expires >= now:
            continue
        if row["status"] == "claimed" and row["action"] in IDEMPOTENT_ACTIONS:
            db_execute(
                """
                UPDATE ops_tasks
                SET status = 'queued', agent_id = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now_iso(), row["id"]),
            )
            insert_task_event(row["id"], "requeued", "lease expired; task requeued")
            continue
        finished_at = now_iso()
        db_execute(
            """
            UPDATE ops_tasks
            SET status = 'timed_out', finished_at = ?, lease_expires_at = NULL, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (finished_at, "ops-agent lease expired", finished_at, row["id"]),
        )
        insert_task_event(row["id"], "timed_out", "lease expired")
        update_gc_state_from_task(row, "failed", "ops-agent lease expired")


def upsert_agent(
    *,
    agent_id: str,
    host_label: str | None,
    environment: str,
    capabilities: list[str],
    status: str,
    version: str | None = None,
    message: str | None = "",
) -> dict:
    clean_status = status if status in AGENT_STATUSES else "online"
    now = now_iso()
    capabilities_json = json_dumps(normalize_capabilities(capabilities))
    row = db_one("SELECT agent_id FROM ops_agents WHERE agent_id = ?", (agent_id,))
    if row:
        db_execute(
            """
            UPDATE ops_agents
            SET host_label = ?, environment = ?, capabilities_json = ?, status = ?,
                last_heartbeat_at = ?, version = ?, message = ?, updated_at = ?
            WHERE agent_id = ?
            """,
            (
                host_label or agent_id,
                environment or "prod",
                capabilities_json,
                clean_status,
                now,
                version,
                redact_text(message or "", max_length=1000),
                now,
                agent_id,
            ),
        )
    else:
        db_execute(
            """
            INSERT INTO ops_agents(
                agent_id, host_label, environment, capabilities_json, status,
                last_heartbeat_at, version, message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                host_label or agent_id,
                environment or "prod",
                capabilities_json,
                clean_status,
                now,
                version,
                redact_text(message or "", max_length=1000),
                now,
                now,
            ),
        )
    return public_agent(db_one("SELECT * FROM ops_agents WHERE agent_id = ?", (agent_id,)) or {})


@router.get("/ops-agents")
def list_ops_agents():
    recover_stale_ops_tasks()
    return [public_agent(row) for row in db_rows("SELECT * FROM ops_agents ORDER BY agent_id")]


@router.get("/ops-tasks")
def list_ops_tasks(status: str | None = None, action: str | None = None, agent_id: str | None = None, limit: int = 50):
    recover_stale_ops_tasks()
    clean_limit = max(1, min(int(limit or 50), 200))
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status.strip())
    if action:
        clauses.append("action = ?")
        params.append(action.strip())
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id.strip())
    if clauses:
        where = "WHERE " + " AND ".join(clauses)
        sql = "SELECT * FROM ops_tasks " + where + " ORDER BY id DESC LIMIT ?"
        rows = db_rows(sql, tuple(params + [clean_limit]))
    else:
        rows = db_rows("SELECT * FROM ops_tasks ORDER BY id DESC LIMIT ?", (clean_limit,))
    return [public_task(row) for row in rows]


@router.post("/ops-tasks", dependencies=[Depends(require_write_token)])
def create_panel_ops_task(body: OpsTaskCreateIn, request: Request):
    task = create_ops_task(
        body.action,
        body.params,
        agent_id=body.agent_id,
        requested_by="panel",
        actor=actor_from_request(request),
    )
    return {"ok": True, "task": task}


@router.get("/ops-tasks/{task_id}")
def get_ops_task(task_id: int):
    recover_stale_ops_tasks()
    row = db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, "运维任务不存在")
    return public_task(row)


@router.get("/ops-tasks/{task_id}/events")
def list_ops_task_events(task_id: int):
    if not db_one("SELECT id FROM ops_tasks WHERE id = ?", (task_id,)):
        raise HTTPException(404, "运维任务不存在")
    rows = db_rows("SELECT * FROM ops_task_events WHERE task_id = ? ORDER BY id", (task_id,))
    return [
        {
            "id": row["id"],
            "task_id": row["task_id"],
            "type": row["type"],
            "message": row.get("message") or "",
            "detail": json_loads_object(row.get("detail_json")),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@router.post("/ops-tasks/{task_id}/confirm", dependencies=[Depends(require_write_token)])
def confirm_ops_task(task_id: int, request: Request):
    row = db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, "运维任务不存在")
    if row["status"] != "queued":
        raise HTTPException(409, "只有 queued 运维任务可以确认")
    if not row.get("confirm_token"):
        return {"ok": True, "task": public_task(row)}
    if task_is_high_risk(row) and active_high_risk_task(row.get("agent_id"), exclude_task_id=task_id):
        existing = active_high_risk_task(row.get("agent_id"), exclude_task_id=task_id)
        raise HTTPException(409, f"已有高风险运维任务未完成: #{existing['id']} {existing['action']}")
    confirmed_at = now_iso()
    db_execute(
        "UPDATE ops_tasks SET confirmed_at = ?, updated_at = ? WHERE id = ?",
        (confirmed_at, confirmed_at, task_id),
    )
    insert_task_event(task_id, "confirmed", "confirmed by panel")
    audit_log("confirm_ops_task", "ops_task", str(task_id), {}, actor=actor_from_request(request))
    return {"ok": True, "task": public_task(db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,)))}


@router.post("/ops-tasks/{task_id}/cancel", dependencies=[Depends(require_write_token)])
def cancel_ops_task(task_id: int, request: Request):
    row = db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, "运维任务不存在")
    if row["status"] in TERMINAL_TASK_STATUSES:
        return {"ok": True, "task": public_task(row)}
    finished_at = now_iso()
    db_execute(
        """
        UPDATE ops_tasks
        SET status = 'canceled', finished_at = ?, lease_expires_at = NULL, updated_at = ?
        WHERE id = ?
        """,
        (finished_at, finished_at, task_id),
    )
    insert_task_event(task_id, "canceled", "canceled by panel")
    audit_log("cancel_ops_task", "ops_task", str(task_id), {"previous_status": row["status"]}, actor=actor_from_request(request))
    return {"ok": True, "task": public_task(db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,)))}


@router.post("/storage/gc/request", dependencies=[Depends(require_write_token)])
def request_storage_gc_task(request: Request):
    current = legacy.storage_gc_status()
    if current["status"] in {"requested", "running"}:
        raise HTTPException(409, f"垃圾回收请求已存在: {current['status']}")
    active_gc = db_one(
        """
        SELECT * FROM ops_tasks
        WHERE action = 'registry_gc' AND status IN ('queued', 'claimed', 'running')
        ORDER BY id LIMIT 1
        """
    )
    if active_gc:
        raise HTTPException(409, f"Registry GC 运维任务已存在: #{active_gc['id']} {active_gc['status']}")
    request_id = secrets.token_hex(8)
    requested_at = now_iso()
    task = create_ops_task(
        "registry_gc",
        {"request_id": request_id},
        requested_by="storage",
        actor=actor_from_request(request),
        confirmed=True,
    )
    legacy.set_storage_gc_values(
        {
            "storage_gc_request_id": request_id,
            "storage_gc_status": "requested",
            "storage_gc_requested_at": requested_at,
            "storage_gc_started_at": "",
            "storage_gc_finished_at": "",
            "storage_gc_message": f"已创建 Registry GC 运维任务 #{task['id']}，等待 ops-agent 执行",
            "storage_gc_log_tail": "",
        }
    )
    audit_log("request_gc", "storage", request_id, {"task_id": task["id"], "status": "requested"}, actor=actor_from_request(request))
    return {"ok": True, "request": legacy.storage_gc_status(), "task": task}


@router.post("/ops-agent/heartbeat", dependencies=[Depends(require_ops_agent_token)])
def ops_agent_heartbeat(body: OpsAgentHeartbeatIn):
    agent = upsert_agent(
        agent_id=body.agent_id.strip(),
        host_label=body.host_label,
        environment=body.environment,
        capabilities=body.capabilities,
        status=body.status,
        version=body.version,
        message=body.message,
    )
    return {"ok": True, "agent": agent}


@router.post("/ops-agent/claim", dependencies=[Depends(require_ops_agent_token)])
def ops_agent_claim(body: OpsAgentClaimIn):
    recover_stale_ops_tasks()
    capabilities = normalize_capabilities(body.capabilities)
    upsert_agent(
        agent_id=body.agent_id.strip(),
        host_label=body.agent_id.strip(),
        environment="prod",
        capabilities=capabilities,
        status="online",
        message="claim",
    )
    rows = db_rows("SELECT * FROM ops_tasks WHERE status = 'queued' ORDER BY id")
    for row in rows:
        if row["action"] not in capabilities:
            continue
        if row.get("agent_id") and row["agent_id"] != body.agent_id:
            continue
        if row.get("confirm_token") and not row.get("confirmed_at"):
            continue
        if task_is_high_risk(row) and active_high_risk_task(body.agent_id, exclude_task_id=int(row["id"])):
            continue
        lease_expires_at = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=body.lease_seconds)).isoformat()
        updated_at = now_iso()
        db_execute(
            """
            UPDATE ops_tasks
            SET status = 'claimed', agent_id = ?, lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (body.agent_id, lease_expires_at, updated_at, row["id"]),
        )
        claimed = db_one("SELECT * FROM ops_tasks WHERE id = ?", (row["id"],))
        insert_task_event(row["id"], "claimed", f"claimed by {body.agent_id}", {"agent_id": body.agent_id})
        audit_log("claim_ops_task", "ops_task", str(row["id"]), {"agent_id": body.agent_id}, actor=f"ops-agent:{body.agent_id}")
        return {"ok": True, "task": public_task(claimed)}
    return {"ok": True, "task": None}


@router.post("/ops-agent/tasks/{task_id}/events", dependencies=[Depends(require_ops_agent_token)])
def ops_agent_task_event(task_id: int, body: OpsTaskEventIn):
    row = db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, "运维任务不存在")
    if row.get("agent_id") != body.agent_id:
        raise HTTPException(409, "任务不属于该 ops-agent")
    if row["status"] in TERMINAL_TASK_STATUSES:
        raise HTTPException(409, "终态任务不能继续写入事件")
    event_type = body.type.strip().lower()
    message = redact_text(body.message or "")
    log_tail = redact_text(body.log_tail or "")
    updates: list[str] = ["updated_at = ?"]
    params: list[Any] = [now_iso()]
    if event_type == "started":
        updates.extend(["status = 'running'", "started_at = COALESCE(started_at, ?)", "lease_expires_at = ?"])
        started_at = now_iso()
        params.extend([started_at, (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=int(row.get("timeout_seconds") or 900))).isoformat()])
        update_gc_state_from_task(row, "running", message, log_tail)
    elif row["status"] == "claimed" and event_type in {"step", "log"}:
        updates.extend(["status = 'running'", "started_at = COALESCE(started_at, ?)"])
        params.append(now_iso())
        update_gc_state_from_task(row, "running", message, log_tail)
    elif row.get("action") == "registry_gc":
        update_gc_state_from_task(row, "running", message, log_tail)
    if log_tail:
        updates.append("log_tail = ?")
        params.append(log_tail)
    params.append(task_id)
    db_execute(f"UPDATE ops_tasks SET {', '.join(updates)} WHERE id = ?", tuple(params))
    insert_task_event(task_id, event_type, message, {"agent_id": body.agent_id, **redact_data(body.detail)})
    return {"ok": True, "task": public_task(db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,)))}


@router.post("/ops-agent/tasks/{task_id}/apply-gc-marks", dependencies=[Depends(require_ops_agent_token)])
async def ops_agent_apply_gc_marks(task_id: int, body: OpsAgentTaskControlIn):
    row = db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, "运维任务不存在")
    if row.get("agent_id") != body.agent_id:
        raise HTTPException(409, "任务不属于该 ops-agent")
    if row.get("action") != "registry_gc":
        raise HTTPException(409, "只有 registry_gc 任务可以应用删除标记")
    marks = db_rows("SELECT id, repo, tag FROM deletion_marks ORDER BY id")
    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for mark in marks:
        try:
            result = await legacy.apply_image_delete_mark(int(mark["id"]))
            applied.append({"id": mark["id"], "repo": result.get("repo"), "tag": result.get("tag")})
        except HTTPException as exc:
            errors.append({"id": mark["id"], "repo": mark.get("repo"), "tag": mark.get("tag"), "status": exc.status_code, "message": str(exc.detail)})
    message = f"applied {len(applied)} deletion marks"
    if errors:
        message += f", {len(errors)} failed"
    insert_task_event(task_id, "step", message, {"agent_id": body.agent_id, "applied": applied, "errors": errors})
    update_gc_state_from_task(row, "running", message)
    return {"ok": not errors, "applied": applied, "errors": errors}


@router.post("/ops-agent/tasks/{task_id}/complete", dependencies=[Depends(require_ops_agent_token)])
def ops_agent_task_complete(task_id: int, body: OpsTaskCompleteIn):
    row = db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, "运维任务不存在")
    if row.get("agent_id") != body.agent_id:
        raise HTTPException(409, "任务不属于该 ops-agent")
    if row["status"] in TERMINAL_TASK_STATUSES:
        return {"ok": True, "task": public_task(row)}
    status = body.status.strip().lower()
    if status not in {"succeeded", "failed", "timed_out"}:
        raise HTTPException(422, "完成状态只能是 succeeded/failed/timed_out")
    finished_at = now_iso()
    log_tail = redact_text(body.log_tail or "")
    error = redact_text(body.error or "")
    db_execute(
        """
        UPDATE ops_tasks
        SET status = ?, finished_at = ?, lease_expires_at = NULL, exit_code = ?,
            log_tail = ?, error = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, finished_at, body.exit_code, log_tail, error, finished_at, task_id),
    )
    insert_task_event(task_id, status, error or f"task {status}", {"agent_id": body.agent_id, "result": redact_data(body.result)})
    final_row = db_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,)) or row
    if final_row.get("action") == "registry_gc":
        gc_status = "completed" if status == "succeeded" else "failed"
        update_gc_state_from_task(final_row, gc_status, error or f"Registry GC {status}", log_tail)
        if status == "succeeded":
            legacy.recalculate_storage_stats_sync()
    audit_log(
        "complete_ops_task",
        "ops_task",
        str(task_id),
        redact_data({"agent_id": body.agent_id, "status": status, "exit_code": body.exit_code, "result": body.result}),
        actor=f"ops-agent:{body.agent_id}",
    )
    return {"ok": True, "task": public_task(final_row)}
