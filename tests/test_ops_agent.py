import json

import pytest

from mirror_registry_core.ops_agent import build_action_commands, redact_data, redact_text, validate_action
from ops_agent.agent import AgentConfig, backup_verify_result, diagnostic_bundle_result, restore_drill_result


def test_ops_action_validation_and_fixed_commands():
    with pytest.raises(ValueError):
        validate_action("restart_service", {"service": "postgres"})

    restart = build_action_commands("restart_service", {"service": "sync"}, "/workspace/docker-compose.yml")
    assert restart == [["docker", "compose", "-f", "/workspace/docker-compose.yml", "restart", "sync"]]

    update = build_action_commands("update_services", {"services": ["panel", "sync"]}, "/workspace/docker-compose.yml")
    assert update[0] == ["docker", "compose", "-f", "/workspace/docker-compose.yml", "pull", "panel", "sync"]
    assert update[1] == ["docker", "compose", "-f", "/workspace/docker-compose.yml", "up", "-d", "panel", "sync"]
    assert all(isinstance(command, list) for command in update)
    assert not any(";" in part for command in update for part in command)


def test_ops_redaction_removes_sensitive_values():
    text = redact_text("Authorization: Bearer abc123 token=secret password=hunter2 https://user:pass@example.com")
    assert "abc123" not in text
    assert "secret" not in text
    assert "hunter2" not in text
    assert "user:pass" not in text

    data = redact_data({"nested": {"webhook_url": "https://example.com/hook", "safe": "value"}, "items": [{"cookie": "abc"}]})
    assert data["nested"]["webhook_url"] == "<redacted>"
    assert data["nested"]["safe"] == "value"
    assert data["items"][0]["cookie"] == "<redacted>"


def test_agent_internal_checks_do_not_leak_token(tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    config = AgentConfig(
        panel_url="http://panel:8080",
        agent_id="local",
        token="top-secret-token",
        host_label="Local host",
        environment="test",
        compose_file=str(compose_file),
        poll_interval_seconds=1,
        command_timeout_seconds=60,
    )

    backup = backup_verify_result(config)
    diagnostic = diagnostic_bundle_result(config)

    assert backup["message"]
    assert "top-secret-token" not in json.dumps(backup, ensure_ascii=False)
    assert "top-secret-token" not in json.dumps(diagnostic, ensure_ascii=False)
    assert diagnostic["env"]["OPS_AGENT_TOKEN"] == "<redacted>"


def test_restore_drill_validation_and_report_are_read_only_and_redacted(tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    backup_package = tmp_path / "backup.tar.gz"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    backup_package.write_text("backup", encoding="utf-8")
    validated = validate_action(
        "restore_drill",
        {
            "compose_project": "drill-project",
            "backup_package": str(backup_package),
            "cleanup": False,
        },
    )
    assert validated.params == {
        "compose_project": "drill-project",
        "backup_package": str(backup_package),
        "cleanup": False,
    }
    assert validated.requires_confirmation is False
    assert validated.timeout_seconds == 900

    config = AgentConfig(
        panel_url="http://panel:8080",
        agent_id="local",
        token="top-secret-token",
        host_label="Local host",
        environment="test",
        compose_file=str(compose_file),
        poll_interval_seconds=1,
        command_timeout_seconds=60,
    )
    result = restore_drill_result(config, validated.params)

    assert result["compose_project"] == "drill-project"
    assert result["cleanup"] is False
    assert result["message"] == "restore drill report generated without modifying production data"
    assert any(item["name"] == "backup_package" and item["status"] == "ok" for item in result["checks"])
    assert "top-secret-token" not in json.dumps(result, ensure_ascii=False)
