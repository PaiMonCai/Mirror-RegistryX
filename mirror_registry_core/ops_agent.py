"""Shared ops-agent action validation, redaction, and command builders."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


ALLOWED_ACTIONS = {
    "service_status",
    "restart_service",
    "update_services",
    "registry_gc",
    "backup_verify",
    "diagnostic_bundle",
}
ALLOWED_SERVICES = ("panel", "sync", "registry", "ops-agent")
DEFAULT_UPDATE_SERVICES = ("panel", "sync", "registry")
HIGH_RISK_ACTIONS = {"update_services", "registry_gc"}
IDEMPOTENT_ACTIONS = {"service_status", "backup_verify", "diagnostic_bundle"}
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "canceled", "timed_out"}
ACTIVE_TASK_STATUSES = {"queued", "claimed", "running"}
AGENT_CAPABILITIES = tuple(sorted(ALLOWED_ACTIONS))

DEFAULT_TIMEOUTS = {
    "service_status": 120,
    "restart_service": 300,
    "update_services": 1200,
    "registry_gc": 1800,
    "backup_verify": 300,
    "diagnostic_bundle": 300,
}

SENSITIVE_KEY_MARKERS = (
    "token",
    "password",
    "secret",
    "webhook",
    "authorization",
    "cookie",
    "credential",
    "authfile",
)

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(token|password|secret|webhook|authorization|cookie|credential)=([^\s&]+)"),
    re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@:\s]+:[^/@\s]+@"),
]


@dataclass(frozen=True)
class ValidatedAction:
    action: str
    params: dict[str, Any]
    requires_confirmation: bool
    timeout_seconds: int
    high_risk: bool


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads_object(value: str | None, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not value:
        return dict(default or {})
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return dict(default or {})
    return parsed if isinstance(parsed, dict) else dict(default or {})


def normalize_capabilities(capabilities: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized = []
    for item in capabilities or []:
        action = str(item or "").strip()
        if action in ALLOWED_ACTIONS and action not in normalized:
            normalized.append(action)
    return normalized


def normalize_services(services: Any, *, default: tuple[str, ...] = ()) -> list[str]:
    if services is None:
        services = list(default)
    if not isinstance(services, list):
        raise ValueError("services 必须是数组")
    cleaned: list[str] = []
    for value in services:
        service = str(value or "").strip()
        if service not in ALLOWED_SERVICES:
            raise ValueError(f"服务不在白名单: {service or '<empty>'}")
        if service not in cleaned:
            cleaned.append(service)
    return cleaned


def validate_action(action: str, params: dict[str, Any] | None = None) -> ValidatedAction:
    clean_action = str(action or "").strip()
    if clean_action not in ALLOWED_ACTIONS:
        raise ValueError(f"不支持的运维动作: {clean_action or '<empty>'}")
    raw_params = dict(params or {})
    normalized: dict[str, Any] = {}
    requires_confirmation = clean_action in HIGH_RISK_ACTIONS

    if clean_action == "service_status":
        normalized = {}
    elif clean_action == "restart_service":
        service = str(raw_params.get("service") or "").strip()
        if service not in ALLOWED_SERVICES:
            raise ValueError(f"服务不在白名单: {service or '<empty>'}")
        normalized = {"service": service}
        requires_confirmation = service in {"registry", "ops-agent"}
    elif clean_action == "update_services":
        normalized = {"services": normalize_services(raw_params.get("services"), default=DEFAULT_UPDATE_SERVICES)}
    elif clean_action == "registry_gc":
        request_id = str(raw_params.get("request_id") or "").strip()
        normalized = {"request_id": request_id} if request_id else {}
    elif clean_action in {"backup_verify", "diagnostic_bundle"}:
        normalized = {}

    return ValidatedAction(
        action=clean_action,
        params=normalized,
        requires_confirmation=requires_confirmation,
        timeout_seconds=DEFAULT_TIMEOUTS[clean_action],
        high_risk=clean_action in HIGH_RISK_ACTIONS or requires_confirmation,
    )


def redact_text(value: Any, *, max_length: int = 20000) -> str:
    text = str(value or "")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.pattern.startswith("([a-zA-Z]"):
            text = pattern.sub(r"\1<redacted>@", text)
        elif "(Bearer)" in pattern.pattern:
            text = pattern.sub(r"\1 <redacted>", text)
        else:
            text = pattern.sub(r"\1=<redacted>", text)
    return text[-max_length:]


def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in SENSITIVE_KEY_MARKERS):
                clean[key] = "<redacted>"
            else:
                clean[key] = redact_data(item)
        return clean
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def build_compose_command(compose_file: str, *args: str) -> list[str]:
    return ["docker", "compose", "-f", compose_file, *args]


def build_action_commands(action: str, params: dict[str, Any] | None, compose_file: str) -> list[list[str]]:
    validated = validate_action(action, params)
    compose_path = str(compose_file)
    if validated.action == "service_status":
        return [build_compose_command(compose_path, "ps")]
    if validated.action == "restart_service":
        return [build_compose_command(compose_path, "restart", str(validated.params["service"]))]
    if validated.action == "update_services":
        services = [str(item) for item in validated.params["services"]]
        return [
            build_compose_command(compose_path, "pull", *services),
            build_compose_command(compose_path, "up", "-d", *services),
            build_compose_command(compose_path, "ps", *services),
        ]
    if validated.action == "registry_gc":
        return [
            build_compose_command(compose_path, "stop", "sync"),
            build_compose_command(
                compose_path,
                "run",
                "--rm",
                "registry",
                "registry",
                "garbage-collect",
                "/etc/docker/registry/config.yml",
            ),
            build_compose_command(compose_path, "up", "-d", "sync"),
            build_compose_command(compose_path, "ps", "registry", "sync"),
        ]
    return []
