"""Mirror governance, discovery, policy, and window routes."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from mirror_registry_core.governance import (
    evaluate_push_window,
    json_dumps,
    json_loads,
    matching_template,
    notification_decision,
    render_target,
)
from mirror_registry_core.mirror_rules import bool_int, normalize_mode

from . import legacy
from .auth import require_write_token
from .route_utils import legacy_router, path_in_prefixes
from .schemas import (
    BulkOperationIn,
    DiscoveryCandidateBatchIn,
    DiscoverySourceIn,
    MirrorManualPushIn,
    MirrorRuleTemplateIn,
    NotificationPolicyIn,
    NotificationPolicyTestIn,
    PushWindowIn,
    TemplateApplyIn,
    TemplatePreviewIn,
)


router = APIRouter()
router.include_router(legacy_router("governance_legacy", lambda path: path_in_prefixes(path, ["/api/schedules"])))

protection_result = legacy.protection_result
assert_tag_mutation_allowed = legacy.assert_tag_mutation_allowed
retention_dry_run = legacy.retention_dry_run
next_run_from_cron = legacy.next_run_from_cron


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def actor_from_request(request: Request) -> str:
    user = getattr(request.state, "auth_user", None) or {}
    return str(user.get("username") or "panel")


def slug(value: str | None, fallback: str) -> str:
    text = (value or fallback).strip().lower().replace(" ", "-")
    clean = "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_"})
    return clean[:64] or fallback


def fetch_rows(sql: str, params: tuple = ()) -> list[dict]:
    return legacy.db_rows(sql, params)


def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    return legacy.db_one(sql, params)


def execute(sql: str, params: tuple = ()) -> int:
    return legacy.db_execute(sql, params)


def public_template(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "source_registry_pattern": row["source_registry_pattern"],
        "source_namespace_pattern": row["source_namespace_pattern"],
        "source_repo_pattern": row["source_repo_pattern"],
        "target_registry": row["target_registry"],
        "target_namespace_template": row.get("target_namespace_template") or "",
        "mode": row["mode"],
        "check_interval_minutes": row["check_interval_minutes"],
        "allow_latest_push": bool(row.get("allow_latest_push")),
        "source_credential_id": row.get("source_credential_id") or "",
        "target_credential_id": row.get("target_credential_id") or "",
        "notification_policy_id": row.get("notification_policy_id") or "",
        "push_window_id": row.get("push_window_id") or "",
        "retention_policy_id": row.get("retention_policy_id") or "",
        "priority": row.get("priority") or 100,
        "enabled": bool(row.get("enabled")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def template_rows() -> list[dict]:
    return fetch_rows("SELECT * FROM mirror_rule_templates ORDER BY priority, id")


def template_row(template_id: str) -> dict:
    row = fetch_one("SELECT * FROM mirror_rule_templates WHERE id = ?", (template_id,))
    if not row:
        raise HTTPException(404, "模板不存在")
    return row


def validate_template_target(row: dict, source: str) -> str:
    target = render_target(row, source)
    return legacy.validate_image_ref(target, "target")


def mirror_policy_update_sql() -> str:
    return """
        UPDATE mirrors
        SET template_id = ?, notification_policy_id = ?, push_window_id = ?, retention_policy_id = ?,
            mode = ?, check_interval_minutes = ?, allow_latest_push = ?, source_credential_id = ?,
            target_credential_id = ?, updated_at = ?
        WHERE source = ?
    """


def sync_config_from_db() -> None:
    legacy.sync_config_from_db()


@router.get("/api/mirror-rule-templates")
def list_mirror_rule_templates():
    return [public_template(row) for row in template_rows()]


@router.post("/api/mirror-rule-templates", dependencies=[Depends(require_write_token)])
def upsert_mirror_rule_template(body: MirrorRuleTemplateIn, request: Request):
    template_id = slug(body.id, body.name)
    mode = normalize_mode(body.mode)
    now = now_iso()
    row = fetch_one("SELECT id FROM mirror_rule_templates WHERE id = ?", (template_id,))
    params = (
        template_id,
        body.name.strip(),
        body.source_registry_pattern.strip() or "*",
        body.source_namespace_pattern.strip() or "*",
        body.source_repo_pattern.strip() or "*",
        body.target_registry.strip().rstrip("/"),
        (body.target_namespace_template or "{namespace}").strip(),
        mode,
        body.check_interval_minutes,
        bool_int(body.allow_latest_push),
        body.source_credential_id or "",
        body.target_credential_id or "",
        body.notification_policy_id or "",
        body.push_window_id or "",
        body.retention_policy_id or "",
        body.priority,
        bool_int(body.enabled),
        now,
        now,
    )
    if row:
        execute(
            """
            UPDATE mirror_rule_templates
            SET name = ?, source_registry_pattern = ?, source_namespace_pattern = ?, source_repo_pattern = ?,
                target_registry = ?, target_namespace_template = ?, mode = ?, check_interval_minutes = ?,
                allow_latest_push = ?, source_credential_id = ?, target_credential_id = ?,
                notification_policy_id = ?, push_window_id = ?, retention_policy_id = ?, priority = ?,
                enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                body.name.strip(),
                body.source_registry_pattern.strip() or "*",
                body.source_namespace_pattern.strip() or "*",
                body.source_repo_pattern.strip() or "*",
                body.target_registry.strip().rstrip("/"),
                (body.target_namespace_template or "{namespace}").strip(),
                mode,
                body.check_interval_minutes,
                bool_int(body.allow_latest_push),
                body.source_credential_id or "",
                body.target_credential_id or "",
                body.notification_policy_id or "",
                body.push_window_id or "",
                body.retention_policy_id or "",
                body.priority,
                bool_int(body.enabled),
                now,
                template_id,
            ),
        )
    else:
        execute(
            """
            INSERT INTO mirror_rule_templates(
                id, name, source_registry_pattern, source_namespace_pattern, source_repo_pattern,
                target_registry, target_namespace_template, mode, check_interval_minutes,
                allow_latest_push, source_credential_id, target_credential_id, notification_policy_id,
                push_window_id, retention_policy_id, priority, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
    template = template_row(template_id)
    legacy.audit_log("upsert", "mirror_rule_template", template_id, {"name": body.name}, actor=actor_from_request(request))
    return {"ok": True, "template": public_template(template)}


@router.put("/api/mirror-rule-templates/{template_id}", dependencies=[Depends(require_write_token)])
def update_mirror_rule_template(template_id: str, body: MirrorRuleTemplateIn, request: Request):
    return upsert_mirror_rule_template(body.model_copy(update={"id": template_id}), request)


@router.delete("/api/mirror-rule-templates/{template_id}", dependencies=[Depends(require_write_token)])
def delete_mirror_rule_template(template_id: str, request: Request):
    template_row(template_id)
    execute("DELETE FROM mirror_rule_templates WHERE id = ?", (template_id,))
    legacy.audit_log("delete", "mirror_rule_template", template_id, {}, actor=actor_from_request(request))
    return {"ok": True}


@router.post("/api/mirror-rule-templates/{template_id}/preview")
def preview_mirror_rule_template(template_id: str, body: TemplatePreviewIn):
    row = template_row(template_id)
    source = legacy.validate_image_ref(body.source, "source")
    target = validate_template_target(row, source)
    match = matching_template(template_rows(), source)
    return {"source": source, "target": target, "template": public_template(row), "matches": bool(match and match["id"] == template_id)}


@router.post("/api/mirror-rule-templates/{template_id}/apply", dependencies=[Depends(require_write_token)])
def apply_mirror_rule_template(template_id: str, body: TemplateApplyIn, request: Request):
    row = template_row(template_id)
    sources = body.sources or [item["source"] for item in legacy.mirror_rule_rows()]
    op_id = create_bulk_operation("apply_template", {"template_id": template_id, "sources": sources, "apply_target": body.apply_target, "apply_policy": body.apply_policy}, actor_from_request(request), len(sources))
    succeeded = 0
    failed = 0
    for source in sources:
        mirror = legacy.mirror_rule_by_source(source)
        if not mirror:
            failed += 1
            create_bulk_item(op_id, source, "failed", "mirror rule not found")
            continue
        try:
            target = validate_template_target(row, source) if body.apply_target else mirror["target"]
            updates = {
                "template_id": row["id"],
                "notification_policy_id": row.get("notification_policy_id") or "",
                "push_window_id": row.get("push_window_id") or "",
                "retention_policy_id": row.get("retention_policy_id") or "",
                "mode": row["mode"],
                "check_interval_minutes": row["check_interval_minutes"],
                "allow_latest_push": bool_int(row.get("allow_latest_push")),
                "source_credential_id": row.get("source_credential_id") or "",
                "target_credential_id": row.get("target_credential_id") or "",
            }
            if body.apply_target:
                execute("UPDATE mirrors SET target = ?, updated_at = ? WHERE source = ?", (target, now_iso(), source))
            if body.apply_policy:
                execute(
                    mirror_policy_update_sql(),
                    (
                        updates["template_id"],
                        updates["notification_policy_id"],
                        updates["push_window_id"],
                        updates["retention_policy_id"],
                        updates["mode"],
                        updates["check_interval_minutes"],
                        updates["allow_latest_push"],
                        updates["source_credential_id"],
                        updates["target_credential_id"],
                        now_iso(),
                        source,
                    ),
                )
            succeeded += 1
            create_bulk_item(op_id, source, "succeeded", "template applied", {"target": target})
        except Exception as exc:
            failed += 1
            create_bulk_item(op_id, source, "failed", str(exc))
    finish_bulk_operation(op_id, succeeded, failed)
    sync_config_from_db()
    legacy.audit_log("apply", "mirror_rule_template", template_id, {"operation_id": op_id, "succeeded": succeeded, "failed": failed}, actor=actor_from_request(request))
    return get_bulk_operation(op_id)


def public_discovery_source(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "source_type": row["source_type"],
        "location": row.get("location") or "",
        "content": row.get("content") or "",
        "scan_interval_minutes": row.get("scan_interval_minutes") or 60,
        "enabled": bool(row.get("enabled")),
        "last_scanned_at": row.get("last_scanned_at"),
        "next_scan_at": row.get("next_scan_at"),
        "last_error": row.get("last_error") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def discovery_source_row(source_id: str) -> dict:
    row = fetch_one("SELECT * FROM discovery_sources WHERE id = ?", (source_id,))
    if not row:
        raise HTTPException(404, "发现源不存在")
    return row


@router.get("/api/discovery-sources")
def list_discovery_sources():
    return [public_discovery_source(row) for row in fetch_rows("SELECT * FROM discovery_sources ORDER BY id")]


@router.post("/api/discovery-sources", dependencies=[Depends(require_write_token)])
def upsert_discovery_source(body: DiscoverySourceIn, request: Request):
    source_id = slug(body.id, body.name)
    source_type = body.source_type.strip().lower()
    if source_type not in {"inline", "plain_text", "url"}:
        raise HTTPException(422, "发现源类型仅支持 inline、plain_text、url")
    now = now_iso()
    if fetch_one("SELECT id FROM discovery_sources WHERE id = ?", (source_id,)):
        execute(
            """
            UPDATE discovery_sources
            SET name = ?, source_type = ?, location = ?, content = ?, scan_interval_minutes = ?,
                enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (body.name, source_type, body.location or "", body.content or "", body.scan_interval_minutes, bool_int(body.enabled), now, source_id),
        )
    else:
        execute(
            """
            INSERT INTO discovery_sources(
                id, name, source_type, location, content, scan_interval_minutes, enabled,
                next_scan_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_id, body.name, source_type, body.location or "", body.content or "", body.scan_interval_minutes, bool_int(body.enabled), now, now, now),
        )
    legacy.audit_log("upsert", "discovery_source", source_id, {"source_type": source_type}, actor=actor_from_request(request))
    return {"ok": True, "source": public_discovery_source(discovery_source_row(source_id))}


@router.put("/api/discovery-sources/{source_id}", dependencies=[Depends(require_write_token)])
def update_discovery_source(source_id: str, body: DiscoverySourceIn, request: Request):
    return upsert_discovery_source(body.model_copy(update={"id": source_id}), request)


@router.delete("/api/discovery-sources/{source_id}", dependencies=[Depends(require_write_token)])
def delete_discovery_source(source_id: str, request: Request):
    discovery_source_row(source_id)
    execute("DELETE FROM discovery_sources WHERE id = ?", (source_id,))
    legacy.audit_log("delete", "discovery_source", source_id, {}, actor=actor_from_request(request))
    return {"ok": True}


def discovery_content(row: dict) -> tuple[str, str]:
    source_type = row["source_type"]
    if source_type == "url":
        url = str(row.get("location") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(422, "URL 发现源必须使用 http:// 或 https://")
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        return response.text, "auto"
    return str(row.get("content") or ""), "auto"


def candidate_action(source_image: str, target: str) -> tuple[str, str, str]:
    existing_source = legacy.mirror_rule_by_source(source_image)
    if existing_source:
        return "already_exists", "new", existing_source["source"]
    existing_target = fetch_one("SELECT source FROM mirrors WHERE target = ?", (target,))
    if existing_target:
        return "conflict", "conflict", existing_target["source"]
    return "create_rule", "new", ""


@router.post("/api/discovery-sources/{source_id}/scan", dependencies=[Depends(require_write_token)])
def scan_discovery_source(source_id: str, request: Request):
    row = discovery_source_row(source_id)
    templates = template_rows()
    now = now_iso()
    try:
        content, source_type = discovery_content(row)
        entries = legacy.extract_discovery_entries(content, source_type)
        seen = 0
        created = 0
        updated = 0
        for entry in entries[:500]:
            raw = str(entry.get("image") or "").strip()
            source_image = legacy.canonical_discovered_source(raw)
            template = matching_template(templates, source_image)
            if template:
                target = validate_template_target(template, source_image)
                template_id = template["id"]
            else:
                target = legacy.discovery_target_for_source(source_image, "localhost:5000")
                template_id = ""
            action, status, existing = candidate_action(source_image, target)
            detail = {"raw": raw, "source_type": entry.get("source_type") or source_type}
            current = fetch_one("SELECT * FROM discovery_candidates WHERE source_id = ? AND source_image = ?", (source_id, source_image))
            if current:
                execute(
                    """
                    UPDATE discovery_candidates
                    SET location = ?, recommended_target = ?, recommended_template_id = ?, action = ?,
                        status = CASE WHEN status = 'ignored' THEN status ELSE ? END,
                        existing_rule_source = ?, detail_json = ?, last_seen_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (entry.get("location") or "", target, template_id, action, status, existing, json_dumps(detail), now, now, current["id"]),
                )
                updated += 1
            else:
                execute(
                    """
                    INSERT INTO discovery_candidates(
                        source_id, source_image, location, recommended_target, recommended_template_id,
                        action, status, existing_rule_source, detail_json, first_seen_at, last_seen_at,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source_id, source_image, entry.get("location") or "", target, template_id, action, status, existing, json_dumps(detail), now, now, now, now),
                )
                created += 1
            seen += 1
        execute("UPDATE discovery_sources SET last_scanned_at = ?, next_scan_at = ?, last_error = '', updated_at = ? WHERE id = ?", (now, legacy.add_minutes(now, int(row.get("scan_interval_minutes") or 60)), now, source_id))
        legacy.audit_log("scan", "discovery_source", source_id, {"seen": seen, "created": created, "updated": updated}, actor=actor_from_request(request))
        return {"ok": True, "seen": seen, "created": created, "updated": updated, "candidates": list_discovery_candidates(source_id=source_id, limit=500)}
    except Exception as exc:
        message = str(exc)
        execute("UPDATE discovery_sources SET last_scanned_at = ?, last_error = ?, updated_at = ? WHERE id = ?", (now, message, now, source_id))
        raise


def public_candidate(row: dict) -> dict:
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "source_image": row["source_image"],
        "location": row.get("location") or "",
        "recommended_target": row.get("recommended_target") or "",
        "recommended_template_id": row.get("recommended_template_id") or "",
        "action": row["action"],
        "status": row["status"],
        "existing_rule_source": row.get("existing_rule_source") or "",
        "ignored_reason": row.get("ignored_reason") or "",
        "detail": json_loads(row.get("detail_json"), {}),
        "first_seen_at": row.get("first_seen_at"),
        "last_seen_at": row.get("last_seen_at"),
        "decided_at": row.get("decided_at"),
        "decided_by": row.get("decided_by"),
    }


@router.get("/api/discovery-candidates")
def list_discovery_candidates(status: str | None = None, source_id: str | None = None, limit: int = 100):
    clean_limit = max(1, min(int(limit or 100), 500))
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if source_id:
        clauses.append("source_id = ?")
        params.append(source_id)
    if clauses:
        rows = fetch_rows("SELECT * FROM discovery_candidates WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?", tuple(params + [clean_limit]))
    else:
        rows = fetch_rows("SELECT * FROM discovery_candidates ORDER BY id DESC LIMIT ?", (clean_limit,))
    return [public_candidate(row) for row in rows]


def candidate_rows_by_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return fetch_rows("SELECT * FROM discovery_candidates WHERE status IN ('new', 'conflict') ORDER BY id")
    rows = []
    for candidate_id in ids:
        row = fetch_one("SELECT * FROM discovery_candidates WHERE id = ?", (candidate_id,))
        if row:
            rows.append(row)
    return rows


@router.post("/api/discovery-candidates/batch/import", dependencies=[Depends(require_write_token)])
def import_discovery_candidates(body: DiscoveryCandidateBatchIn, request: Request):
    candidates = candidate_rows_by_ids(body.ids)
    op_id = create_bulk_operation("import_candidates", {"ids": [item["id"] for item in candidates]}, actor_from_request(request), len(candidates))
    config = legacy.load_config()
    mirrors = {mirror["source"]: mirror for mirror in legacy.valid_mirrors(config)}
    imported = 0
    failed = 0
    for row in candidates:
        if row["action"] not in {"create_rule", "conflict", "already_exists"} or row["status"] == "ignored":
            failed += 1
            create_bulk_item(op_id, row["source_image"], "failed", f"candidate not importable: {row['status']}/{row['action']}")
            continue
        mirror = {
            "source": row["source_image"],
            "target": row["recommended_target"],
            "target_override": row["recommended_target"],
        }
        template = fetch_one("SELECT * FROM mirror_rule_templates WHERE id = ?", (row.get("recommended_template_id") or "",))
        if template:
            mirror.update(
                {
                    "mode": template["mode"],
                    "check_interval_minutes": template["check_interval_minutes"],
                    "allow_latest_push": bool(template["allow_latest_push"]),
                    "source_credential_id": template.get("source_credential_id") or "",
                    "target_credential_id": template.get("target_credential_id") or "",
                }
            )
        normalized = legacy.normalize_mirror(mirror)
        mirrors[normalized["source"]] = normalized
        legacy.upsert_mirror_db(normalized["source"], normalized["target"], mirror=normalized)
        if template:
            execute(
                """
                UPDATE mirrors
                SET template_id = ?, notification_policy_id = ?, push_window_id = ?, retention_policy_id = ?, updated_at = ?
                WHERE source = ?
                """,
                (template["id"], template.get("notification_policy_id") or "", template.get("push_window_id") or "", template.get("retention_policy_id") or "", now_iso(), normalized["source"]),
            )
        execute("UPDATE discovery_candidates SET status = 'imported', decided_at = ?, decided_by = ?, updated_at = ? WHERE id = ?", (now_iso(), actor_from_request(request), now_iso(), row["id"]))
        imported += 1
        create_bulk_item(op_id, normalized["source"], "succeeded", "candidate imported", {"target": normalized["target"]})
    config["mirrors"] = list(mirrors.values())
    legacy.save_config(config)
    queue_task = None
    if body.trigger_sync and imported:
        queue_task = legacy.write_trigger("governance-discovery-import", sources=[row["source_image"] for row in candidates])
    finish_bulk_operation(op_id, imported, failed)
    legacy.audit_log("import", "discovery_candidates", "bulk", {"operation_id": op_id, "imported": imported, "failed": failed}, actor=actor_from_request(request))
    return {"ok": True, "imported": imported, "failed": failed, "operation": get_bulk_operation(op_id), "queue": queue_task}


@router.post("/api/discovery-candidates/batch/ignore", dependencies=[Depends(require_write_token)])
def ignore_discovery_candidates(body: DiscoveryCandidateBatchIn, request: Request):
    candidates = candidate_rows_by_ids(body.ids)
    now = now_iso()
    for row in candidates:
        execute(
            "UPDATE discovery_candidates SET status = 'ignored', ignored_reason = ?, decided_at = ?, decided_by = ?, updated_at = ? WHERE id = ?",
            (body.reason or "ignored by operator", now, actor_from_request(request), now, row["id"]),
        )
    legacy.audit_log("ignore", "discovery_candidates", "bulk", {"ids": [row["id"] for row in candidates]}, actor=actor_from_request(request))
    return {"ok": True, "ignored": len(candidates)}


def public_notification_policy(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "webhook_configured": bool(row.get("webhook_url_encrypted")),
        "events": json_loads(row.get("events_json"), {}),
        "min_severity": row.get("min_severity") or "warning",
        "dedupe_seconds": row.get("dedupe_seconds") or 1800,
        "quiet_hours": json_loads(row.get("quiet_hours_json"), {}),
        "enabled": bool(row.get("enabled")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/api/notification-policies")
def list_notification_policies():
    return [public_notification_policy(row) for row in fetch_rows("SELECT * FROM notification_policies ORDER BY id")]


@router.post("/api/notification-policies", dependencies=[Depends(require_write_token)])
def upsert_notification_policy(body: NotificationPolicyIn, request: Request):
    policy_id = slug(body.id, body.name)
    now = now_iso()
    encrypted = legacy.encrypt_secret(body.webhook_url.strip()) if body.webhook_url else None
    current = fetch_one("SELECT * FROM notification_policies WHERE id = ?", (policy_id,))
    if current:
        execute(
            """
            UPDATE notification_policies
            SET name = ?, webhook_url_encrypted = COALESCE(?, webhook_url_encrypted), events_json = ?,
                min_severity = ?, dedupe_seconds = ?, quiet_hours_json = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (body.name, encrypted, json_dumps(body.events), body.min_severity, body.dedupe_seconds, json_dumps(body.quiet_hours), bool_int(body.enabled), now, policy_id),
        )
    else:
        execute(
            """
            INSERT INTO notification_policies(
                id, name, webhook_url_encrypted, events_json, min_severity, dedupe_seconds,
                quiet_hours_json, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (policy_id, body.name, encrypted, json_dumps(body.events), body.min_severity, body.dedupe_seconds, json_dumps(body.quiet_hours), bool_int(body.enabled), now, now),
        )
    legacy.audit_log("upsert", "notification_policy", policy_id, {"name": body.name, "webhook_configured": bool(encrypted)}, actor=actor_from_request(request))
    return {"ok": True, "policy": public_notification_policy(fetch_one("SELECT * FROM notification_policies WHERE id = ?", (policy_id,)) or {})}


@router.put("/api/notification-policies/{policy_id}", dependencies=[Depends(require_write_token)])
def update_notification_policy(policy_id: str, body: NotificationPolicyIn, request: Request):
    return upsert_notification_policy(body.model_copy(update={"id": policy_id}), request)


@router.delete("/api/notification-policies/{policy_id}", dependencies=[Depends(require_write_token)])
def delete_notification_policy(policy_id: str, request: Request):
    execute("DELETE FROM notification_policies WHERE id = ?", (policy_id,))
    legacy.audit_log("delete", "notification_policy", policy_id, {}, actor=actor_from_request(request))
    return {"ok": True}


@router.post("/api/notification-policies/{policy_id}/test", dependencies=[Depends(require_write_token)])
def test_notification_policy(policy_id: str, body: NotificationPolicyTestIn, request: Request):
    row = fetch_one("SELECT * FROM notification_policies WHERE id = ?", (policy_id,))
    if not row:
        raise HTTPException(404, "通知策略不存在")
    decision = notification_decision(row, body.event_type, body.severity)
    status = "would_send" if decision["send"] else "suppressed"
    dedupe_key = hashlib.sha256(json_dumps({"policy": policy_id, "event": body.event_type, "payload": body.payload}).encode("utf-8")).hexdigest()
    execute(
        """
        INSERT INTO notification_events(policy_id, event_type, severity, status, reason, dedupe_key, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (policy_id, body.event_type, body.severity, status, decision["reason"], dedupe_key, json_dumps(body.payload), now_iso()),
    )
    legacy.audit_log("test", "notification_policy", policy_id, {"event_type": body.event_type, "status": status}, actor=actor_from_request(request))
    return {"ok": True, "decision": decision, "status": status}


def public_push_window(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "timezone": row.get("timezone") or "Asia/Shanghai",
        "allow_windows": json_loads(row.get("allow_windows_json"), []),
        "freeze_windows": json_loads(row.get("freeze_windows_json"), []),
        "enabled": bool(row.get("enabled")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/api/push-windows")
def list_push_windows():
    return [public_push_window(row) for row in fetch_rows("SELECT * FROM push_windows ORDER BY id")]


@router.post("/api/push-windows", dependencies=[Depends(require_write_token)])
def upsert_push_window(body: PushWindowIn, request: Request):
    window_id = slug(body.id, body.name)
    now = now_iso()
    if fetch_one("SELECT id FROM push_windows WHERE id = ?", (window_id,)):
        execute(
            """
            UPDATE push_windows
            SET name = ?, timezone = ?, allow_windows_json = ?, freeze_windows_json = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (body.name, body.timezone, json_dumps(body.allow_windows), json_dumps(body.freeze_windows), bool_int(body.enabled), now, window_id),
        )
    else:
        execute(
            """
            INSERT INTO push_windows(id, name, timezone, allow_windows_json, freeze_windows_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (window_id, body.name, body.timezone, json_dumps(body.allow_windows), json_dumps(body.freeze_windows), bool_int(body.enabled), now, now),
        )
    legacy.audit_log("upsert", "push_window", window_id, {"name": body.name}, actor=actor_from_request(request))
    return {"ok": True, "window": public_push_window(fetch_one("SELECT * FROM push_windows WHERE id = ?", (window_id,)) or {})}


@router.put("/api/push-windows/{window_id}", dependencies=[Depends(require_write_token)])
def update_push_window(window_id: str, body: PushWindowIn, request: Request):
    return upsert_push_window(body.model_copy(update={"id": window_id}), request)


@router.delete("/api/push-windows/{window_id}", dependencies=[Depends(require_write_token)])
def delete_push_window(window_id: str, request: Request):
    execute("DELETE FROM push_windows WHERE id = ?", (window_id,))
    legacy.audit_log("delete", "push_window", window_id, {}, actor=actor_from_request(request))
    return {"ok": True}


@router.post("/api/push-windows/{window_id}/preview")
def preview_push_window(window_id: str):
    row = fetch_one("SELECT * FROM push_windows WHERE id = ?", (window_id,))
    if not row:
        raise HTTPException(404, "推送窗口不存在")
    return {"window": public_push_window(row), "evaluation": evaluate_push_window(row)}


@router.post("/api/mirrors/{index}/push", dependencies=[Depends(require_write_token)])
def trigger_governed_mirror_push(index: int, request: Request, body: MirrorManualPushIn | None = None):
    mirror = legacy.mirror_rule_by_index(index)
    digest = (body.digest if body else None) or mirror.get("pending_push_digest") or mirror.get("last_source_digest") or mirror.get("last_digest") or ""
    if not digest:
        raise HTTPException(409, "该规则还没有可推送的 digest，请先执行检查")

    window = fetch_one("SELECT * FROM push_windows WHERE id = ?", (mirror.get("push_window_id") or "",))
    evaluation = evaluate_push_window(window)
    if not evaluation["allowed"] and not (body and body.confirm_bypass_window):
        raise HTTPException(
            409,
            {
                "message": "当前不在允许推送窗口内，手工绕过需要显式确认",
                "window": public_push_window(window) if window else None,
                "evaluation": evaluation,
            },
        )

    task = legacy.enqueue_rule_task("push", mirror, digest=digest, force=True)
    execute(
        """
        UPDATE mirrors
        SET pending_push_digest = ?, pending_push_target = ?, push_status = 'pending',
            next_push_at = NULL, last_error = NULL, updated_at = ?
        WHERE source = ?
        """,
        (digest, mirror["target"], now_iso(), mirror["source"]),
    )
    detail = {"queue_id": task["id"], "digest": digest}
    if not evaluation["allowed"]:
        detail.update({"bypass_window": True, "reason": body.bypass_reason if body else "", "evaluation": evaluation})
        legacy.record_mirror_event(mirror["source"], "push_window_bypassed", "pending", new_digest=digest, message=body.bypass_reason if body else "")
    legacy.audit_log("trigger_push", "mirror", mirror["source"], detail, actor=actor_from_request(request) if request else "panel")
    return {"ok": True, "message": "推送任务已入队", "queue": task, "window": evaluation}


def create_bulk_operation(operation_type: str, params: dict, requested_by: str, total: int) -> int:
    now = now_iso()
    return execute(
        """
        INSERT INTO bulk_operations(operation_type, status, params_json, requested_by, total, created_at, updated_at)
        VALUES (?, 'running', ?, ?, ?, ?, ?)
        """,
        (operation_type, json_dumps(params), requested_by, total, now, now),
    )


def create_bulk_item(operation_id: int, mirror_source: str, status: str, message: str, detail: dict | None = None) -> None:
    now = now_iso()
    execute(
        """
        INSERT INTO bulk_operation_items(operation_id, mirror_source, status, message, detail_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (operation_id, mirror_source, status, message, json_dumps(detail or {}), now, now),
    )


def finish_bulk_operation(operation_id: int, succeeded: int, failed: int) -> None:
    now = now_iso()
    status = "failed" if failed and not succeeded else "completed"
    execute(
        """
        UPDATE bulk_operations
        SET status = ?, succeeded = ?, failed = ?, finished_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, succeeded, failed, now, now, operation_id),
    )


@router.get("/api/bulk-operations")
def list_bulk_operations(status: str | None = None, type: str | None = None, limit: int = 50):
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if type:
        clauses.append("operation_type = ?")
        params.append(type)
    if clauses:
        rows = fetch_rows("SELECT * FROM bulk_operations WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?", tuple(params + [max(1, min(limit, 200))]))
    else:
        rows = fetch_rows("SELECT * FROM bulk_operations ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),))
    return [public_bulk_operation(row, include_items=False) for row in rows]


@router.post("/api/bulk-operations", dependencies=[Depends(require_write_token)])
def create_bulk_operation_route(body: BulkOperationIn, request: Request):
    sources = body.sources or [row["source"] for row in legacy.mirror_rule_rows()]
    op_id = create_bulk_operation(body.operation_type, {"sources": sources, "params": body.params}, actor_from_request(request), len(sources))
    succeeded = 0
    failed = 0
    for source in sources:
        row = legacy.mirror_rule_by_source(source)
        if not row:
            failed += 1
            create_bulk_item(op_id, source, "failed", "mirror not found")
            continue
        if body.operation_type == "pause_rules":
            execute("UPDATE mirrors SET enabled = 0, updated_at = ? WHERE source = ?", (now_iso(), source))
        elif body.operation_type == "resume_rules":
            execute("UPDATE mirrors SET enabled = 1, updated_at = ? WHERE source = ?", (now_iso(), source))
        elif body.operation_type == "check_rules":
            legacy.enqueue_rule_task("check", row, force=True)
        elif body.operation_type == "push_pending":
            digest = row.get("pending_push_digest") or row.get("last_source_digest") or row.get("last_digest") or ""
            if not digest:
                failed += 1
                create_bulk_item(op_id, source, "failed", "no digest to push")
                continue
            legacy.enqueue_rule_task("push", row, digest=digest, force=True)
        elif body.operation_type == "update_interval":
            execute("UPDATE mirrors SET check_interval_minutes = ?, updated_at = ? WHERE source = ?", (int(body.params.get("check_interval_minutes") or 30), now_iso(), source))
        elif body.operation_type == "update_mode":
            execute("UPDATE mirrors SET mode = ?, updated_at = ? WHERE source = ?", (normalize_mode(body.params.get("mode")), now_iso(), source))
        else:
            failed += 1
            create_bulk_item(op_id, source, "failed", f"unsupported operation: {body.operation_type}")
            continue
        succeeded += 1
        create_bulk_item(op_id, source, "succeeded", "ok")
    finish_bulk_operation(op_id, succeeded, failed)
    sync_config_from_db()
    return get_bulk_operation(op_id)


@router.get("/api/bulk-operations/{operation_id}")
def get_bulk_operation(operation_id: int):
    row = fetch_one("SELECT * FROM bulk_operations WHERE id = ?", (operation_id,))
    if not row:
        raise HTTPException(404, "批量操作不存在")
    return public_bulk_operation(row, include_items=True)


def public_bulk_operation(row: dict, include_items: bool = True) -> dict:
    payload = {
        "id": row["id"],
        "operation_type": row["operation_type"],
        "status": row["status"],
        "params": json_loads(row.get("params_json"), {}),
        "requested_by": row.get("requested_by") or "panel",
        "total": row.get("total") or 0,
        "succeeded": row.get("succeeded") or 0,
        "failed": row.get("failed") or 0,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "finished_at": row.get("finished_at"),
    }
    if include_items:
        payload["items"] = [
            {
                "id": item["id"],
                "mirror_source": item["mirror_source"],
                "status": item["status"],
                "message": item.get("message") or "",
                "detail": json_loads(item.get("detail_json"), {}),
                "created_at": item.get("created_at"),
            }
            for item in fetch_rows("SELECT * FROM bulk_operation_items WHERE operation_id = ? ORDER BY id", (row["id"],))
        ]
    return payload


@router.post("/api/bulk-operations/{operation_id}/retry-failed", dependencies=[Depends(require_write_token)])
def retry_failed_bulk_operation(operation_id: int, request: Request):
    operation = get_bulk_operation(operation_id)
    failed_sources = [item["mirror_source"] for item in operation.get("items", []) if item["status"] == "failed"]
    if not failed_sources:
        return {"ok": True, "operation": operation, "retry": None}
    return create_bulk_operation_route(BulkOperationIn(operation_type=operation["operation_type"], sources=failed_sources, params=operation.get("params") or {}), request)


@router.get("/api/governance/summary")
def governance_summary():
    pending_pushes = fetch_rows("SELECT source, target, push_status, pending_push_digest, next_push_at FROM mirrors WHERE push_status IN ('pending', 'pending_window', 'failed', 'degraded') ORDER BY updated_at DESC LIMIT 100")
    candidates = fetch_rows("SELECT status, COUNT(*) AS count FROM discovery_candidates GROUP BY status")
    failed_rules = [row for row in pending_pushes if row["push_status"] in {"failed", "degraded"}]
    return {
        "pending_pushes": len([row for row in pending_pushes if row["push_status"] in {"pending", "pending_window"}]),
        "failed_rules": len(failed_rules),
        "degraded_rules": len([row for row in pending_pushes if row["push_status"] == "degraded"]),
        "stale_rules": len(fetch_rows("SELECT source FROM mirrors WHERE governance_status = 'stale'")),
        "new_discovery_candidates": sum(int(row["count"]) for row in candidates if row["status"] == "new"),
        "notification_failures": len(fetch_rows("SELECT id FROM notification_events WHERE status = 'failed'")),
        "storage_cleanup_candidates": legacy.deletion_mark_count(),
        "latest_bulk_operation": public_bulk_operation(fetch_rows("SELECT * FROM bulk_operations ORDER BY id DESC LIMIT 1")[0], include_items=False) if fetch_rows("SELECT * FROM bulk_operations ORDER BY id DESC LIMIT 1") else None,
    }


@router.get("/api/governance/issues")
def governance_issues(limit: int = 100):
    issues = []
    for row in fetch_rows("SELECT source, target, push_status, last_error, next_push_at FROM mirrors WHERE push_status IN ('pending_window', 'failed', 'degraded') ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)):
        issues.append({"type": row["push_status"], "severity": "error" if row["push_status"] in {"failed", "degraded"} else "warning", "source": row["source"], "target": row["target"], "message": row.get("last_error") or row.get("next_push_at") or ""})
    for row in fetch_rows("SELECT id, source_image, status, action FROM discovery_candidates WHERE status IN ('new', 'conflict') ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)):
        issues.append({"type": "discovery_candidate", "severity": "warning", "source": row["source_image"], "target": "", "message": f"{row['status']}/{row['action']}"})
    return issues[: max(1, min(limit, 500))]


@router.get("/api/governance/stale-rules")
def governance_stale_rules():
    return [legacy.public_mirror_rule(row, index) for index, row in enumerate(fetch_rows("SELECT * FROM mirrors WHERE governance_status = 'stale' ORDER BY source"))]


@router.get("/api/governance/pending-pushes")
def governance_pending_pushes():
    return [legacy.public_mirror_rule(row, index) for index, row in enumerate(fetch_rows("SELECT * FROM mirrors WHERE push_status IN ('pending', 'pending_window') ORDER BY updated_at DESC"))]


@router.get("/api/governance/discovery-summary")
def governance_discovery_summary():
    rows = fetch_rows("SELECT status, COUNT(*) AS count FROM discovery_candidates GROUP BY status")
    return {row["status"]: row["count"] for row in rows}
