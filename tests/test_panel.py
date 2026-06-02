import importlib
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def panel_app(tmp_path, monkeypatch):
    return make_panel_client(tmp_path, monkeypatch)


def make_panel_client(
    tmp_path,
    monkeypatch,
    credentials_secret_key: str | None = "unit-secret-key",
    seed_config: bool = True,
    login: bool = True,
):
    config_path = tmp_path / "config" / "mirrors.yml"
    state_path = tmp_path / "data" / "sync-state.json"
    log_path = tmp_path / "data" / "sync.log"
    trigger_path = tmp_path / "data" / ".trigger"
    db_path = tmp_path / "data" / "mirror-registry.db"
    registry_storage_path = tmp_path / "data" / "registry"
    static_dir = tmp_path / "static"

    static_dir.mkdir(parents=True)
    registry_storage_path.mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if seed_config:
        config_path.write_text(
            "mirrors: []\nsettings:\n  check_interval_minutes: 30\n",
            encoding="utf-8",
        )

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("STATE_PATH", str(state_path))
    monkeypatch.setenv("LOG_PATH", str(log_path))
    monkeypatch.setenv("TRIGGER_PATH", str(trigger_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("REGISTRY_STORAGE_PATH", str(registry_storage_path))
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("WORKER_TOKEN", "worker-token")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-password")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "604800")
    if credentials_secret_key is None:
        monkeypatch.delenv("CREDENTIALS_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("CREDENTIALS_SECRET_KEY", credentials_secret_key)

    import panel.main as panel_main

    importlib.reload(panel_main)
    panel_main.ensure_admin_user()
    client = TestClient(panel_main.app)
    if login:
        response = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
        assert response.status_code == 200
    return client, config_path, state_path, trigger_path


def audit_rows():
    import panel.main as panel_main

    rows = panel_main.db_rows("SELECT actor, action, resource_type, resource_id, detail FROM audit_logs ORDER BY id")
    for row in rows:
        row["detail"] = json.loads(row.get("detail") or "{}")
    return rows


def test_panel_auth_login_logout_and_me(tmp_path, monkeypatch):
    client, _, _, _ = make_panel_client(tmp_path, monkeypatch, login=False)

    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"}).status_code == 401

    login_response = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    assert login_response.status_code == 200
    assert login_response.json()["user"]["username"] == "admin"
    assert client.get("/api/auth/me").json()["authenticated"] is True
    assert client.get("/api/status").status_code == 200

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/status").status_code == 401


def test_api_auth_rejects_anonymous_and_bearer_tokens(tmp_path, monkeypatch):
    client, _, _, _ = make_panel_client(tmp_path, monkeypatch, login=False)

    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer test-token"}).status_code == 401
    response = client.post(
        "/api/mirrors",
        json={"source": "docker.io/library/busybox:latest", "target": "localhost:5000/library/busybox:latest"},
    )
    assert response.status_code == 401


def test_login_audit_redacts_secret_values(tmp_path, monkeypatch):
    client, _, _, _ = make_panel_client(tmp_path, monkeypatch, login=False)

    client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200

    audit_text = json.dumps(audit_rows(), ensure_ascii=False)
    assert "login_failed" in audit_text
    assert "logout" in audit_text
    assert "wrong-password" not in audit_text
    assert "admin-password" not in audit_text
    assert "mirror_registry_session" not in audit_text


def test_non_core_api_routes_are_not_exposed(panel_app):
    client, _, _, _ = panel_app
    hidden_routes = [
        "/api/access/users",
        "/api/audit-logs",
        "/api/workers",
        "/api/workers/guide",
        "/api/tag-protection",
        "/api/retention-policies",
        "/api/backup-restore-guide",
        "/api/migration/plan",
        "/api/observability/summary",
        "/api/ops/summary",
        "/api/ops/diagnostic-bundle",
        "/api/install-upgrade/guide",
        "/api/setup/checklist",
        "/api/security-guide",
        "/api/platform",
        "/api/database-guide",
        "/api/registries",
        "/api/mirror-groups",
        "/api/schedules",
    ]

    for route in hidden_routes:
        assert client.get(route).status_code == 404, route


def test_status_and_mirror_crud(panel_app):
    client, config_path, state_path, _ = panel_app

    assert client.get("/api/status").json()["total"] == 0
    response = client.post(
        "/api/mirrors",
        json={"source": "docker.io/library/busybox:latest", "target": "localhost:5000/library/busybox:latest"},
    )
    assert response.status_code == 200

    mirrors = client.get("/api/mirrors").json()
    assert mirrors[0]["source"] == "docker.io/library/busybox:latest"
    assert "docker.io/library/busybox:latest" in config_path.read_text(encoding="utf-8")

    state_path.write_text(json.dumps({"docker.io/library/busybox:latest": "sha256:abc"}), encoding="utf-8")
    assert client.post("/api/mirrors/0/reset").status_code == 200
    assert json.loads(state_path.read_text(encoding="utf-8")) == {}

    assert client.delete("/api/mirrors/0").status_code == 200
    assert client.get("/api/status").json()["total"] == 0


def test_missing_config_file_bootstraps_default_config(tmp_path, monkeypatch):
    client, config_path, _, _ = make_panel_client(tmp_path, monkeypatch)
    config_path.unlink()

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    content = config_path.read_text(encoding="utf-8")
    assert "docker.io/library/busybox:latest" in content
    assert "registry_url: http://registry:5000" in content


def test_trigger_sync_and_queue_control_flow(panel_app):
    client, _, _, trigger_path = panel_app

    response = client.post("/api/sync")

    assert response.status_code == 200
    queue = response.json()["queue"]
    assert queue["reason"] == "manual"
    assert queue["status"] == "queued"
    assert trigger_path.exists()
    trigger = json.loads(trigger_path.read_text(encoding="utf-8"))
    assert trigger["reason"] == "manual"
    assert trigger["queued"] is True

    queue_id = queue["id"]
    assert client.post(f"/api/sync-queue/{queue_id}/pause").json()["queue"]["status"] == "paused"
    assert client.post(f"/api/sync-queue/{queue_id}/resume").json()["queue"]["status"] == "queued"
    assert client.post(f"/api/sync-queue/{queue_id}/cancel").json()["queue"]["status"] == "canceled"
    replayed = client.post(f"/api/sync-queue/{queue_id}/replay").json()["queue"]
    assert replayed["reason"] == "replay:manual"
    assert replayed["status"] == "queued"


def test_single_mirror_sync_trigger_writes_source(panel_app):
    client, _, _, trigger_path = panel_app
    client.post(
        "/api/mirrors",
        json={"source": "docker.io/library/busybox:latest", "target": "localhost:5000/library/busybox:latest"},
    )

    response = client.post("/api/mirrors/0/sync")

    assert response.status_code == 200
    assert response.json()["queue"]["sources"] == ["docker.io/library/busybox:latest"]
    assert "docker.io/library/busybox:latest" in trigger_path.read_text(encoding="utf-8")


def test_settings_logs_and_events_are_core(panel_app):
    client, config_path, _, _ = panel_app

    response = client.put(
        "/api/settings",
        json={
            "check_interval_minutes": 15,
            "sync_concurrency": 3,
            "sync_retry_count": 4,
            "notify_webhook_url": "https://example.com/hook",
        },
    )

    assert response.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["check_interval_minutes"] == 15
    assert settings["sync_concurrency"] == 3
    assert settings["sync_retry_count"] == 4
    assert "notify_webhook_url: https://example.com/hook" in config_path.read_text(encoding="utf-8")

    assert client.get("/api/logs").status_code == 200
    assert client.get("/api/events").status_code == 200


def test_mirror_export_import_and_discovery(panel_app):
    client, config_path, _, trigger_path = panel_app

    response = client.post(
        "/api/mirrors/import",
        json={
            "replace": True,
            "mirrors": [{"source": "docker.io/library/busybox:latest", "target": "localhost:5000/library/busybox:latest"}],
        },
    )
    assert response.status_code == 200
    exported = client.get("/api/mirrors/export").json()
    assert exported["version"] == 2
    assert exported["mirrors"][0]["source"] == "docker.io/library/busybox:latest"

    payload = {
        "source_type": "compose",
        "target_registry": "localhost:5000",
        "mode": "missing_only",
        "trigger_sync": True,
        "content": """
services:
  web:
    image: nginx:1.27
  api:
    image: ghcr.io/example/api:v2
  bad:
    image: redis
""",
    }
    result = client.post("/api/mirrors/discover/import", json=payload).json()

    assert result["imported"] == 2
    content = config_path.read_text(encoding="utf-8")
    assert "docker.io/library/nginx:1.27" in content
    assert "ghcr.io/example/api:v2" in content
    trigger = json.loads(trigger_path.read_text(encoding="utf-8"))
    assert trigger["reason"] == "discover-import"
    assert sorted(trigger["sources"]) == ["docker.io/library/nginx:1.27", "ghcr.io/example/api:v2"]
    assert any(item["action"] == "discover_import" for item in audit_rows())


def test_mirror_preflight_uses_explicit_credentials_without_secret_leak(panel_app, monkeypatch):
    client, _, state_path, trigger_path = panel_app
    create = client.post(
        "/api/credentials",
        json={
            "id": "dockerhub",
            "name": "Docker Hub",
            "registry_host": "docker.io",
            "username": "alice",
            "secret": "top-secret",
            "scope": "source",
        },
    )
    assert create.status_code == 200

    import panel.main as panel_main

    class FakeResponse:
        def __init__(self, status_code=200, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

    class FakeAsyncClient:
        def __init__(self, timeout, **kwargs):
            self.timeout = timeout
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, auth=None):
            if "/manifests/" in url:
                assert headers["Accept"] == panel_main.MANIFEST_ACCEPT
                assert auth == ("alice", "top-secret")
                return FakeResponse(200, {"Docker-Content-Digest": "sha256:new"})
            assert url.endswith("/v2/")
            return FakeResponse(200)

    monkeypatch.setattr(panel_main.httpx, "AsyncClient", FakeAsyncClient)

    ok = client.post(
        "/api/mirrors/preflight",
        json={
            "source": "docker.io/library/busybox:latest",
            "target": "localhost:5000/library/busybox:nightly",
            "source_credential_id": "dockerhub",
            "check_remote": True,
        },
    ).json()

    assert ok["summary"]["status"] == "warn"
    assert any(item["name"] == "source 凭据" and item["status"] == "ok" for item in ok["checks"])
    assert any(item["name"] == "上游镜像" and item["status"] == "ok" for item in ok["checks"])
    assert "top-secret" not in json.dumps(ok, ensure_ascii=False)
    assert "top-secret" not in json.dumps(audit_rows(), ensure_ascii=False)
    assert not state_path.exists()
    assert not trigger_path.exists()


def write_registry_blob(root: Path, digest_hex: str, content: bytes) -> None:
    path = root / "docker" / "registry" / "v2" / "blobs" / "sha256" / digest_hex[:2] / digest_hex / "data"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_download_mirror_artifact_from_local_registry_storage(panel_app, tmp_path):
    client, config_path, _, _ = panel_app
    assert client.post(
        "/api/mirrors/import",
        json={
            "replace": True,
            "mirrors": [{"source": "docker.io/library/busybox:latest", "target": "localhost:5000/library/busybox:latest"}],
        },
    ).status_code == 200

    registry_root = config_path.parents[1] / "data" / "registry"
    config_digest = "a" * 64
    layer_digest = "b" * 64
    manifest_digest = "c" * 64
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": f"sha256:{config_digest}", "size": 2},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar", "digest": f"sha256:{layer_digest}", "size": 5}],
    }
    write_registry_blob(registry_root, config_digest, b"{}")
    write_registry_blob(registry_root, layer_digest, b"layer")
    write_registry_blob(registry_root, manifest_digest, json.dumps(manifest).encode("utf-8"))
    link = registry_root / "docker" / "registry" / "v2" / "repositories" / "library" / "busybox" / "_manifests" / "tags" / "latest" / "current" / "link"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.write_text(f"sha256:{manifest_digest}", encoding="utf-8")

    response = client.get("/api/mirrors/0/artifact")

    assert response.status_code == 200
    assert response.headers["x-mirror-registry-artifact-format"] == "registry-storage-artifact-v1"
    archive_path = tmp_path / "artifact.tar"
    archive_path.write_bytes(response.content)
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
        assert "metadata.json" in names
        assert f"manifests/sha256_{manifest_digest}.json" in names
        assert f"blobs/sha256/{config_digest}" in names
        assert f"blobs/sha256/{layer_digest}" in names


def test_storage_delete_mark_and_stats(panel_app):
    client, _, _, _ = panel_app

    response = client.post("/api/storage/delete-mark", json={"repo": "library/busybox", "tag": "latest", "reason": "cleanup"})

    assert response.status_code == 200
    storage = client.get("/api/storage").json()
    assert storage["deletion_marks"][0]["repo"] == "library/busybox"
    assert "garbage-collect" in "\n".join(storage["garbage_collection"]["commands"])
    assert client.post("/api/storage/stats/recalculate").status_code == 200


def test_storage_delete_mark_apply_deletes_registry_manifest(panel_app, monkeypatch):
    client, _, _, _ = panel_app
    mark = client.post("/api/storage/delete-mark", json={"repo": "library/busybox", "tag": "latest", "reason": "cleanup"}).json()

    import panel.main as panel_main

    manifest_digest = "sha256:" + "d" * 64
    calls = []

    class FakeResponse:
        def __init__(self, status_code=200, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, timeout=None):
            calls.append(("GET", url, headers, timeout))
            assert headers["Accept"] == panel_main.MANIFEST_ACCEPT
            return FakeResponse(200, {"Docker-Content-Digest": manifest_digest})

        async def delete(self, url, timeout=None):
            calls.append(("DELETE", url, None, timeout))
            return FakeResponse(202)

    monkeypatch.setattr(panel_main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(f"/api/storage/delete-mark/{mark['id']}/apply")

    assert response.status_code == 200
    result = response.json()
    assert result["repo"] == "library/busybox"
    assert result["tag"] == "latest"
    assert result["manifest_digest"] == manifest_digest
    assert calls[0][1] == "http://registry:5000/v2/library/busybox/manifests/latest"
    assert calls[1][1] == f"http://registry:5000/v2/library/busybox/manifests/{manifest_digest}"
    assert panel_main.db_one("SELECT id FROM deletion_marks WHERE id = ?", (mark["id"],)) is None
    assert panel_main.db_one("SELECT id FROM audit_logs WHERE action = 'apply_delete' AND resource_id = ?", (str(mark["id"]),)) is not None


def test_storage_delete_mark_apply_rejects_invalid_or_unsafe_cleanup(panel_app, monkeypatch):
    client, _, _, _ = panel_app

    assert client.post("/api/storage/delete-mark/999/apply").status_code == 404

    import panel.main as panel_main

    protected_mark_id = panel_main.db_execute(
        "INSERT INTO deletion_marks(repo, tag, reason, created_at) VALUES (?, ?, ?, ?)",
        ("library/busybox", "v1.0.0", "cleanup", panel_main.now_iso()),
    )
    protected = client.post(f"/api/storage/delete-mark/{protected_mark_id}/apply")
    assert protected.status_code == 409
    assert "受保护 tag" in protected.json()["message"]

    class FakeResponse:
        def __init__(self, status_code=200, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

    class MissingManifestClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, timeout=None):
            return FakeResponse(404)

    missing_mark = client.post("/api/storage/delete-mark", json={"repo": "library/alpine", "tag": "latest", "reason": "cleanup"}).json()
    monkeypatch.setattr(panel_main.httpx, "AsyncClient", MissingManifestClient)
    missing = client.post(f"/api/storage/delete-mark/{missing_mark['id']}/apply")
    assert missing.status_code == 404
    assert "manifest 不存在" in missing.json()["message"]
    assert panel_main.db_one("SELECT id FROM deletion_marks WHERE id = ?", (missing_mark["id"],)) is not None

    class DeleteFailedClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, timeout=None):
            return FakeResponse(200, {"Docker-Content-Digest": "sha256:" + "e" * 64})

        async def delete(self, url, timeout=None):
            return FakeResponse(500)

    failed_mark = client.post("/api/storage/delete-mark", json={"repo": "library/redis", "tag": "latest", "reason": "cleanup"}).json()
    monkeypatch.setattr(panel_main.httpx, "AsyncClient", DeleteFailedClient)
    failed = client.post(f"/api/storage/delete-mark/{failed_mark['id']}/apply")
    assert failed.status_code == 502
    assert "删除失败" in failed.json()["message"]
    assert panel_main.db_one("SELECT id FROM deletion_marks WHERE id = ?", (failed_mark["id"],)) is not None


def test_credentials_crud_test_and_secret_redaction(panel_app, monkeypatch):
    client, config_path, _, _ = panel_app
    create = client.post(
        "/api/credentials",
        json={
            "id": "dockerhub",
            "name": "Docker Hub",
            "registry_host": "https://index.docker.io",
            "username": "alice",
            "secret": "top-secret",
            "scope": "both",
        },
    )

    assert create.status_code == 200
    listed = client.get("/api/credentials").json()
    assert listed[0]["id"] == "dockerhub"
    assert listed[0]["registry_host"] == "index.docker.io"
    assert listed[0]["configured"] is True
    assert "secret" not in json.dumps(listed)

    import panel.main as panel_main

    status_holder = {"code": 200}

    class FakeAsyncClient:
        def __init__(self, timeout, **kwargs):
            self.timeout = timeout
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, auth=None, headers=None, params=None):
            assert url == "https://index.docker.io/v2/"
            assert auth == ("alice", "top-secret")
            return type("Response", (), {"status_code": status_holder["code"], "headers": {}})()

    monkeypatch.setattr(panel_main.httpx, "AsyncClient", FakeAsyncClient)
    assert client.post("/api/credentials/dockerhub/test", json={}).json()["status"] == "ok"
    status_holder["code"] = 401
    assert client.post("/api/credentials/dockerhub/test", json={}).json()["status"] == "authentication_failed"
    status_holder["code"] = 403
    assert client.post("/api/credentials/dockerhub/test", json={}).json()["status"] == "permission_denied"

    update = client.put(
        "/api/credentials/dockerhub",
        json={"name": "Docker Hub Read", "registry_host": "index.docker.io", "username": "alice", "scope": "source"},
    )
    assert update.status_code == 200
    assert update.json()["credential"]["scope"] == "source"

    audit_text = json.dumps(audit_rows())
    assert "top-secret" not in audit_text
    assert "alice" not in audit_text

    client.post(
        "/api/mirrors",
        json={
            "source": "docker.io/library/busybox:latest",
            "target": "localhost:5000/library/busybox:latest",
            "source_credential_id": "dockerhub",
        },
    )
    assert "source_credential_id: dockerhub" in config_path.read_text(encoding="utf-8")
    assert client.delete("/api/credentials/dockerhub").status_code == 400


def test_credentials_do_not_require_secret_key(tmp_path, monkeypatch):
    client, _, _, _ = make_panel_client(tmp_path, monkeypatch, credentials_secret_key=None)
    response = client.post(
        "/api/credentials",
        json={
            "id": "missing-key",
            "name": "Missing Key",
            "registry_host": "ghcr.io",
            "username": "alice",
            "secret": "top-secret",
            "scope": "both",
        },
    )

    assert response.status_code == 200
    listed = client.get("/api/credentials").json()
    assert listed[0]["configured"] is True
    assert "plain:" not in json.dumps(listed, ensure_ascii=False)
    assert "top-secret" not in json.dumps(listed, ensure_ascii=False)


def test_retry_failed_run_writes_sources_trigger(panel_app):
    client, _, _, trigger_path = panel_app

    import panel.main as panel_main

    run_id = panel_main.db_execute(
        "INSERT INTO sync_runs(reason, status, only_source, started_at, failed) VALUES (?, ?, ?, ?, ?)",
        ("manual", "failed", None, panel_main.now_iso(), 1),
    )
    panel_main.db_execute(
        "INSERT INTO sync_run_items(run_id, source, target, status, started_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, "docker.io/library/busybox:latest", "localhost:5000/library/busybox:latest", "failed", panel_main.now_iso()),
    )

    response = client.post(f"/api/sync-runs/{run_id}/retry")

    assert response.status_code == 200
    assert response.json()["queue"]["sources"] == ["docker.io/library/busybox:latest"]
    trigger = json.loads(trigger_path.read_text(encoding="utf-8"))
    assert trigger["reason"] == "retry-run"
    assert trigger["sources"] == ["docker.io/library/busybox:latest"]


def test_terminal_password_reset_updates_user_invalidates_sessions_and_redacts_secret(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "reset.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    import panel.password_reset as password_reset
    import panel.auth as panel_auth
    import panel.db as panel_db

    result = password_reset.reset_panel_password("admin", "new-password", create_if_missing=True)

    assert result.created is True
    row = panel_auth.user_row("admin")
    assert row["role"] == "admin"
    assert panel_auth.verify_password("new-password", row["password_hash"])

    token, _ = panel_auth.create_session("admin")
    assert panel_auth.session_user(token)["username"] == "admin"

    result = password_reset.reset_panel_password("admin", "changed-password")

    assert result.created is False
    assert result.sessions_invalidated is True
    assert panel_auth.verify_password("changed-password", panel_auth.user_row("admin")["password_hash"])
    assert panel_auth.session_user(token) is None

    audit_text = json.dumps(panel_db.db_rows("SELECT actor, action, resource_type, resource_id, detail FROM audit_logs"), ensure_ascii=False)
    assert "password_reset" in audit_text
    assert "terminal" in audit_text
    assert "changed-password" not in audit_text
    assert "new-password" not in audit_text

    assert password_reset.main(["admin", "--password", "cli-password", "--database-url", f"sqlite:///{db_path}"]) == 0
    output = capsys.readouterr().out
    assert "User password reset: admin" in output
    assert "cli-password" not in output
    assert panel_auth.verify_password("cli-password", panel_auth.user_row("admin")["password_hash"])


def test_terminal_password_reset_requires_existing_user_unless_create_requested(tmp_path, monkeypatch):
    db_path = tmp_path / "missing-reset.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    import panel.password_reset as password_reset
    import panel.auth as panel_auth

    with pytest.raises(password_reset.PasswordResetError, match="User not found"):
        password_reset.reset_panel_password("operator", "new-password")

    result = password_reset.reset_panel_password("operator", "new-password", create_if_missing=True, role="operator")

    assert result.created is True
    row = panel_auth.user_row("operator")
    assert row["role"] == "operator"
    assert panel_auth.verify_password("new-password", row["password_hash"])


def test_terminal_password_reset_database_url_override(tmp_path, monkeypatch):
    default_db = tmp_path / "default-reset.db"
    override_db = tmp_path / "override-reset.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{default_db}")

    import panel.password_reset as password_reset
    import panel.auth as panel_auth

    password_reset.reset_panel_password("admin", "default-password", create_if_missing=True)

    assert password_reset.main(["admin", "--password", "override-password", "--database-url", f"sqlite:///{override_db}", "--create-if-missing"]) == 0
    assert panel_auth.verify_password("override-password", panel_auth.user_row("admin")["password_hash"])

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{default_db}")
    assert panel_auth.verify_password("default-password", panel_auth.user_row("admin")["password_hash"])


def test_schema_migrations_empty_old_repeat_and_failure(tmp_path, monkeypatch):
    import sqlite3
    import types

    import panel.db as panel_db

    db_path = tmp_path / "migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        panel_db.init_db(conn)
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "schema_migrations" in tables
        assert "mirrors" in tables
        assert "sync_queue" in tables
        assert "api_tokens" not in tables
        versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations")]
        assert versions == ["0001_initial", "0002_drop_api_tokens"]

        panel_db.init_db(conn)
        repeat_versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations")]
        assert repeat_versions == ["0001_initial", "0002_drop_api_tokens"]

    old_db_path = tmp_path / "old.db"
    with sqlite3.connect(old_db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE mirrors (source TEXT PRIMARY KEY, target TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_digest TEXT, updated_at TEXT NOT NULL)")
        conn.execute(
            """
            CREATE TABLE api_tokens (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                scopes TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_used_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO mirrors(source, target, updated_at) VALUES (?, ?, ?)",
            ("docker.io/library/busybox:latest", "localhost:5000/library/busybox:latest", "2024-01-01T00:00:00+00:00"),
        )
        conn.commit()

        panel_db.init_db(conn)
        assert conn.execute("SELECT COUNT(*) FROM mirrors").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = '0001_initial'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = '0002_drop_api_tokens'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'api_tokens'").fetchone()[0] == 0

    failing_db_path = tmp_path / "failing.db"
    with sqlite3.connect(failing_db_path) as conn:
        conn.row_factory = sqlite3.Row

        def fail_upgrade(_conn):
            _conn.execute("CREATE TABLE partial_failure_marker (id INTEGER PRIMARY KEY)")
            raise RuntimeError("boom")

        monkeypatch.setattr(panel_db, "available_migrations", lambda: [("9999_failure", types.SimpleNamespace(upgrade=fail_upgrade))])

        with pytest.raises(RuntimeError, match="boom"):
            panel_db.init_db(conn)

        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'partial_failure_marker'").fetchone()[0] == 0


def test_api_error_response_envelope(tmp_path, monkeypatch):
    client, _, _, _ = make_panel_client(tmp_path, monkeypatch, login=False)

    response = client.get("/api/status")

    assert response.status_code == 401
    assert response.json() == {
        "code": "UNAUTHENTICATED",
        "message": "需要登录",
        "suggestion": "请重新登录。",
        "details": {},
    }


def test_validation_error_response_includes_field_details(panel_app):
    client, _, _, _ = panel_app

    response = client.post("/api/mirrors", json={"source": "", "target": "localhost:5000/library/busybox:latest"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["message"] == "请求参数校验失败"
    assert payload["suggestion"]
    assert any(field["field"] == "source" for field in payload["details"]["fields"])
