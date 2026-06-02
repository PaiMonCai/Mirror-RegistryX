"""Polling ops-agent runner.

The panel stores structured tasks. This agent maps those tasks to fixed command
arrays or internal checks; it never executes panel-provided shell strings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from mirror_registry_core.ops_agent import (
    AGENT_CAPABILITIES,
    build_action_commands,
    redact_data,
    redact_text,
    validate_action,
)


VERSION = "phase2"


@dataclass(frozen=True)
class AgentConfig:
    panel_url: str
    agent_id: str
    token: str
    host_label: str
    environment: str
    compose_file: str
    poll_interval_seconds: float
    command_timeout_seconds: int


def env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def load_config() -> AgentConfig:
    return AgentConfig(
        panel_url=os.getenv("PANEL_URL", "http://panel:8080").rstrip("/"),
        agent_id=os.getenv("OPS_AGENT_ID", "local").strip() or "local",
        token=os.getenv("OPS_AGENT_TOKEN", "").strip(),
        host_label=os.getenv("OPS_AGENT_HOST_LABEL", "Local host").strip() or "Local host",
        environment=os.getenv("OPS_AGENT_ENVIRONMENT", "prod").strip() or "prod",
        compose_file=os.getenv("COMPOSE_FILE", "/workspace/docker-compose.yml").strip() or "/workspace/docker-compose.yml",
        poll_interval_seconds=env_float("OPS_AGENT_POLL_INTERVAL_SECONDS", 5, 1, 120),
        command_timeout_seconds=env_int("OPS_AGENT_COMMAND_TIMEOUT_SECONDS", 900, 30, 3600),
    )


def auth_headers(config: AgentConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {config.token}"}


def log_tail(text: str, max_length: int = 20000) -> str:
    return redact_text(text, max_length=max_length)


def run_command(command: list[str], timeout_seconds: int) -> tuple[int, str]:
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        return int(completed.returncode), log_tail(output)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        elapsed = int(time.time() - started)
        return 124, log_tail(f"{output}\ncommand timed out after {elapsed}s")


def backup_verify_result(config: AgentConfig) -> dict[str, Any]:
    compose_file = Path(config.compose_file)
    workspace = compose_file.parent
    checks = [
        {"name": "compose_file", "status": "ok" if compose_file.exists() else "warn", "path": str(compose_file)},
        {"name": "docker_cli", "status": "ok" if shutil.which("docker") else "warn"},
        {"name": "workspace", "status": "ok" if workspace.exists() else "warn", "path": str(workspace)},
        {"name": "data_path", "status": "ok" if Path("/data").exists() else "warn", "path": "/data"},
        {"name": "registry_path", "status": "ok" if Path("/registry").exists() else "warn", "path": "/registry"},
    ]
    return {
        "status": "ok" if all(item["status"] == "ok" for item in checks[:3]) else "warn",
        "checks": checks,
        "message": "backup readiness verified without exporting credentials",
    }


def diagnostic_bundle_result(config: AgentConfig) -> dict[str, Any]:
    compose_file = Path(config.compose_file)
    disk = shutil.disk_usage(str(compose_file.parent if compose_file.parent.exists() else Path("/")))
    return redact_data(
        {
            "agent_id": config.agent_id,
            "host_label": config.host_label,
            "environment": config.environment,
            "compose_file": str(compose_file),
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
            "capabilities": list(AGENT_CAPABILITIES),
            "env": {
                "PANEL_URL": config.panel_url,
                "OPS_AGENT_TOKEN": config.token,
                "OPS_AGENT_ID": config.agent_id,
            },
        }
    )


def execute_task(config: AgentConfig, task: dict[str, Any], client: httpx.Client) -> None:
    task_id = int(task["id"])
    action = str(task["action"])
    params = dict(task.get("params") or {})
    validated = validate_action(action, params)
    timeout_seconds = int(task.get("timeout_seconds") or config.command_timeout_seconds)

    post_event(config, client, task_id, "started", f"starting {validated.action}", {"params": validated.params})
    if validated.action == "backup_verify":
        result = backup_verify_result(config)
        complete_task(config, client, task_id, "succeeded", 0, json.dumps(result, ensure_ascii=False), "", result)
        return
    if validated.action == "diagnostic_bundle":
        result = diagnostic_bundle_result(config)
        complete_task(config, client, task_id, "succeeded", 0, json.dumps(result, ensure_ascii=False), "", result)
        return

    combined_output: list[str] = []
    final_exit_code = 0
    commands = build_action_commands(validated.action, validated.params, config.compose_file)
    for index, command in enumerate(commands, start=1):
        post_event(config, client, task_id, "step", "running fixed command", {"step": index, "command": command})
        exit_code, output = run_command(command, timeout_seconds)
        combined_output.append(f"$ {' '.join(command)}\n{output}".strip())
        post_event(config, client, task_id, "log", output, {"step": index, "exit_code": exit_code}, log_tail_value=output)
        if exit_code != 0:
            final_exit_code = exit_code
            break
        if validated.action == "registry_gc" and index == 1:
            apply_gc_marks(config, client, task_id)

    final_tail = log_tail("\n\n".join(combined_output))
    status = "succeeded" if final_exit_code == 0 else ("timed_out" if final_exit_code == 124 else "failed")
    error = "" if status == "succeeded" else f"{validated.action} failed with exit code {final_exit_code}"
    complete_task(config, client, task_id, status, final_exit_code, final_tail, error, {"commands": len(commands)})


def heartbeat(config: AgentConfig, client: httpx.Client) -> None:
    client.post(
        f"{config.panel_url}/api/ops-agent/heartbeat",
        headers=auth_headers(config),
        json={
            "agent_id": config.agent_id,
            "host_label": config.host_label,
            "environment": config.environment,
            "capabilities": list(AGENT_CAPABILITIES),
            "status": "online",
            "version": VERSION,
            "message": "ready",
        },
        timeout=15,
    ).raise_for_status()


def claim_task(config: AgentConfig, client: httpx.Client) -> dict[str, Any] | None:
    response = client.post(
        f"{config.panel_url}/api/ops-agent/claim",
        headers=auth_headers(config),
        json={"agent_id": config.agent_id, "capabilities": list(AGENT_CAPABILITIES), "lease_seconds": 120},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("task")


def post_event(
    config: AgentConfig,
    client: httpx.Client,
    task_id: int,
    event_type: str,
    message: str,
    detail: dict[str, Any] | None = None,
    *,
    log_tail_value: str = "",
) -> None:
    client.post(
        f"{config.panel_url}/api/ops-agent/tasks/{task_id}/events",
        headers=auth_headers(config),
        json={
            "agent_id": config.agent_id,
            "type": event_type,
            "message": log_tail(message),
            "detail": redact_data(detail or {}),
            "log_tail": log_tail(log_tail_value),
        },
        timeout=30,
    ).raise_for_status()


def apply_gc_marks(config: AgentConfig, client: httpx.Client, task_id: int) -> None:
    response = client.post(
        f"{config.panel_url}/api/ops-agent/tasks/{task_id}/apply-gc-marks",
        headers=auth_headers(config),
        json={"agent_id": config.agent_id},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok", False):
        raise RuntimeError(f"apply-gc-marks failed: {payload.get('errors')}")


def complete_task(
    config: AgentConfig,
    client: httpx.Client,
    task_id: int,
    status: str,
    exit_code: int,
    tail: str,
    error: str,
    result: dict[str, Any],
) -> None:
    client.post(
        f"{config.panel_url}/api/ops-agent/tasks/{task_id}/complete",
        headers=auth_headers(config),
        json={
            "agent_id": config.agent_id,
            "status": status,
            "exit_code": exit_code,
            "log_tail": log_tail(tail),
            "error": log_tail(error),
            "result": redact_data(result),
        },
        timeout=30,
    ).raise_for_status()


def run_once(config: AgentConfig, client: httpx.Client) -> bool:
    heartbeat(config, client)
    task = claim_task(config, client)
    if not task:
        return False
    execute_task(config, task, client)
    return True


def main() -> int:
    config = load_config()
    if not config.token:
        raise RuntimeError("OPS_AGENT_TOKEN is required")
    with httpx.Client() as client:
        while True:
            try:
                run_once(config, client)
            except Exception as exc:
                print(redact_text(f"ops-agent loop error: {exc}"), flush=True)
            time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
