"""Governance helpers for mirror templates, discovery, windows, and notifications."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from mirror_registry_core.mirror_rules import image_repo_tag


WEEKDAY_ALIASES = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
ALLOWED_EVENTS = {
    "monitor_only_change",
    "push_failed",
    "rule_degraded",
    "check_failed_threshold",
    "source_missing",
    "retention_warning",
    "discovery_new_candidate",
    "pending_window_expired",
    "sync_failed",
    "sync_recovered",
    "disk_low",
}
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


@dataclass(frozen=True)
class ImageParts:
    source: str
    registry: str
    namespace: str
    repo: str
    image: str
    tag: str


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def image_parts(source: str) -> ImageParts:
    repo_path, tag = image_repo_tag(source)
    first, rest = (source.rsplit(":", 1)[0].split("/", 1) + [""])[:2]
    has_registry = bool(rest) and ("." in first or ":" in first or first == "localhost")
    registry = first if has_registry else "docker.io"
    repo_path = rest if has_registry else source.rsplit(":", 1)[0]
    bits = repo_path.split("/")
    image = bits[-1]
    namespace = "/".join(bits[:-1]) or "library"
    return ImageParts(source=source, registry=registry, namespace=namespace, repo=repo_path, image=image, tag=tag)


def template_matches_source(template: dict, source: str) -> bool:
    parts = image_parts(source)
    return (
        fnmatch.fnmatch(parts.registry, str(template.get("source_registry_pattern") or "*"))
        and fnmatch.fnmatch(parts.namespace, str(template.get("source_namespace_pattern") or "*"))
        and fnmatch.fnmatch(parts.repo, str(template.get("source_repo_pattern") or "*"))
    )


def render_target(template: dict, source: str) -> str:
    parts = image_parts(source)
    target_registry = str(template.get("target_registry") or "").strip().rstrip("/")
    namespace_template = str(template.get("target_namespace_template") or "{namespace}").strip().strip("/")
    variables = {
        "registry": parts.registry,
        "namespace": parts.namespace,
        "repo": parts.repo,
        "image": parts.image,
        "tag": parts.tag,
    }
    target_namespace = namespace_template.format(**variables).strip("/")
    if target_namespace:
        return f"{target_registry}/{target_namespace}/{parts.image}:{parts.tag}"
    return f"{target_registry}/{parts.image}:{parts.tag}"


def matching_template(templates: list[dict], source: str) -> dict | None:
    enabled = [item for item in templates if bool(int(item.get("enabled", 1)))]
    matches = [item for item in enabled if template_matches_source(item, source)]
    if not matches:
        return None
    return sorted(matches, key=lambda item: (int(item.get("priority") or 100), str(item.get("id") or "")))[0]


def normalize_window_day(value: str) -> int | None:
    text = str(value or "").strip().lower()[:3]
    return WEEKDAY_ALIASES.get(text)


def parse_hhmm(value: str) -> time:
    hour, minute = str(value or "00:00").split(":", 1)
    return time(hour=max(0, min(int(hour), 23)), minute=max(0, min(int(minute), 59)))


def window_applies(window: dict, current: datetime) -> bool:
    days = window.get("days") or []
    normalized_days = {normalize_window_day(day) for day in days}
    if normalized_days and current.weekday() not in normalized_days:
        return False
    start = parse_hhmm(str(window.get("start") or "00:00"))
    end = parse_hhmm(str(window.get("end") or "23:59"))
    current_time = current.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current_time <= end
    return current_time >= start or current_time <= end


def coerce_zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def evaluate_push_window(window: dict | None, now: datetime | None = None) -> dict:
    if not window or not bool(int(window.get("enabled", 1))):
        return {"allowed": True, "reason": "no_window", "next_allowed_at": None}
    zone = coerce_zone(str(window.get("timezone") or "Asia/Shanghai"))
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    allow_windows = json_loads(window.get("allow_windows_json"), [])
    freeze_windows = json_loads(window.get("freeze_windows_json"), [])
    in_freeze = any(window_applies(item, current) for item in freeze_windows if isinstance(item, dict))
    if in_freeze:
        return {"allowed": False, "reason": "freeze_window", "next_allowed_at": next_allowed_time(window, current).astimezone(timezone.utc).isoformat()}
    if not allow_windows:
        return {"allowed": True, "reason": "allowed", "next_allowed_at": None}
    in_allow = any(window_applies(item, current) for item in allow_windows if isinstance(item, dict))
    if in_allow:
        return {"allowed": True, "reason": "allowed", "next_allowed_at": None}
    return {"allowed": False, "reason": "outside_allow_window", "next_allowed_at": next_allowed_time(window, current).astimezone(timezone.utc).isoformat()}


def next_allowed_time(window: dict, current: datetime) -> datetime:
    allow_windows = [item for item in json_loads(window.get("allow_windows_json"), []) if isinstance(item, dict)]
    if not allow_windows:
        allow_windows = [{"days": list(WEEKDAY_ALIASES), "start": "00:00", "end": "23:59"}]
    freeze_windows = [item for item in json_loads(window.get("freeze_windows_json"), []) if isinstance(item, dict)]
    cursor = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for minute_offset in range(0, 14 * 24 * 60):
        candidate = cursor + timedelta(minutes=minute_offset)
        if any(window_applies(item, candidate) for item in allow_windows) and not any(window_applies(item, candidate) for item in freeze_windows):
            return candidate
    return cursor + timedelta(hours=1)


def severity_allows(event_severity: str, minimum: str) -> bool:
    return SEVERITY_ORDER.get(event_severity, 1) >= SEVERITY_ORDER.get(minimum, 1)


def event_enabled(policy: dict, event_type: str) -> bool:
    events = json_loads(policy.get("events_json"), {})
    value = events.get(event_type)
    return True if value is None else bool(value)


def quiet_hours_active(policy: dict, now: datetime | None = None) -> bool:
    quiet = json_loads(policy.get("quiet_hours_json"), {})
    if not isinstance(quiet, dict) or not quiet.get("enabled"):
        return False
    zone = coerce_zone(str(quiet.get("timezone") or "Asia/Shanghai"))
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    return window_applies({"days": quiet.get("days") or [], "start": quiet.get("start") or "00:00", "end": quiet.get("end") or "23:59"}, current)


def notification_decision(policy: dict | None, event_type: str, severity: str, now: datetime | None = None) -> dict:
    if not policy or not bool(int(policy.get("enabled", 1))):
        return {"send": False, "reason": "policy_disabled"}
    if not event_enabled(policy, event_type):
        return {"send": False, "reason": "event_disabled"}
    if not severity_allows(severity, str(policy.get("min_severity") or "warning")):
        return {"send": False, "reason": "below_min_severity"}
    if quiet_hours_active(policy, now) and severity != "critical":
        return {"send": False, "reason": "quiet_hours"}
    return {"send": True, "reason": "send"}
