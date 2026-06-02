from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


class AccessUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=8, max_length=512)
    role: str = Field(default="viewer", max_length=32)


class PasswordChangeIn(BaseModel):
    current_password: str | None = Field(default=None, min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=512)


class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=512)


class MirrorIn(BaseModel):
    source: str = Field(min_length=1, max_length=255)
    target: str | None = Field(default=None, max_length=255)
    target_registry: str | None = Field(default=None, max_length=255)
    target_namespace: str | None = Field(default=None, max_length=128)
    target_override: str | None = Field(default=None, max_length=255)
    registry: str = Field(default="local", min_length=1, max_length=64)
    group: str = Field(default="default", min_length=1, max_length=64)
    project: str = Field(default="default", min_length=1, max_length=64)
    environment: str = Field(default="local", min_length=1, max_length=64)
    namespace: str = Field(default="library", min_length=1, max_length=128)
    mode: str = Field(default="auto_push", max_length=32)
    check_interval_minutes: int = Field(default=30, ge=1, le=1440)
    allow_latest_push: bool = False
    source_credential_id: str | None = Field(default=None, max_length=64)
    target_credential_id: str | None = Field(default=None, max_length=64)


class MirrorImportIn(BaseModel):
    mirrors: list[MirrorIn] = Field(default_factory=list, max_length=500)
    replace: bool = False
    registries: list[dict] = Field(default_factory=list, max_length=50)
    mirror_groups: list[dict] = Field(default_factory=list, max_length=100)


class MirrorDiscoveryIn(BaseModel):
    content: str = Field(min_length=1, max_length=200000)
    source_type: str = Field(default="auto", max_length=16)
    target_registry: str = Field(default="localhost:5000", min_length=1, max_length=255)
    registry: str = Field(default="local", min_length=1, max_length=64)
    group: str = Field(default="default", min_length=1, max_length=64)
    project: str = Field(default="default", min_length=1, max_length=64)
    environment: str = Field(default="local", min_length=1, max_length=64)
    namespace: str = Field(default="library", min_length=1, max_length=128)
    source_credential_id: str | None = Field(default=None, max_length=64)
    target_credential_id: str | None = Field(default=None, max_length=64)
    mode: str = Field(default="missing_only", max_length=16)
    trigger_sync: bool = False


class MirrorPreflightIn(MirrorIn):
    check_remote: bool = False


class MirrorPreflightBatchIn(BaseModel):
    mirrors: list[MirrorIn] = Field(default_factory=list, max_length=500)
    check_remote: bool = False


class MirrorPushIn(BaseModel):
    digest: str | None = Field(default=None, max_length=255)


class RegistryIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=500)
    copy_host: str | None = Field(default=None, max_length=255)
    storage_path: str | None = Field(default=None, max_length=500)


class CredentialIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    registry_host: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    secret: str | None = Field(default=None, max_length=2000)
    scope: str = Field(default="both", max_length=16)


class CredentialTestIn(BaseModel):
    registry_url: str | None = Field(default=None, max_length=500)


class MirrorGroupIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    project: str = Field(default="default", min_length=1, max_length=64)
    environment: str = Field(default="local", min_length=1, max_length=64)
    namespace: str = Field(default="library", min_length=1, max_length=128)
    registry: str = Field(default="local", min_length=1, max_length=64)


class SettingsIn(BaseModel):
    check_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    sync_concurrency: int | None = Field(default=None, ge=1, le=16)
    sync_retry_count: int | None = Field(default=None, ge=0, le=10)
    notify_webhook_url: str | None = Field(default=None, max_length=1000)
    database_url: str | None = Field(default=None, max_length=1000)
    clear_notify_webhook_url: bool = False


class StorageDeleteMarkIn(BaseModel):
    repo: str = Field(min_length=1, max_length=255)
    tag: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default="", max_length=500)


class StorageGcStatusIn(BaseModel):
    status: str = Field(min_length=1, max_length=32)
    request_id: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default="", max_length=2000)
    log_tail: str | None = Field(default="", max_length=20000)
    requested_at: str | None = Field(default=None, max_length=64)
    started_at: str | None = Field(default=None, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)


class OpsTaskCreateIn(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    agent_id: str | None = Field(default=None, max_length=64)
    params: dict = Field(default_factory=dict)


class OpsTaskEventIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=64)
    message: str | None = Field(default="", max_length=20000)
    detail: dict = Field(default_factory=dict)
    log_tail: str | None = Field(default="", max_length=20000)


class OpsTaskCompleteIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=32)
    exit_code: int | None = None
    log_tail: str | None = Field(default="", max_length=20000)
    error: str | None = Field(default="", max_length=20000)
    result: dict = Field(default_factory=dict)


class OpsAgentHeartbeatIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    host_label: str | None = Field(default=None, max_length=120)
    environment: str = Field(default="prod", max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    status: str = Field(default="online", max_length=32)
    version: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default="", max_length=1000)


class OpsAgentClaimIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    lease_seconds: int = Field(default=120, ge=30, le=1800)


class OpsAgentTaskControlIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)


class TagProtectionRuleIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    repo_pattern: str = Field(default="*", min_length=1, max_length=255)
    tag_pattern: str = Field(default="*", min_length=1, max_length=128)
    environment: str = Field(default="*", min_length=1, max_length=64)
    enabled: bool = True
    reason: str | None = Field(default="", max_length=500)


class RetentionPolicyIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    repo_pattern: str = Field(default="*", min_length=1, max_length=255)
    environment: str = Field(default="*", min_length=1, max_length=64)
    keep_last: int = Field(default=5, ge=1, le=200)
    max_age_days: int | None = Field(default=None, ge=1, le=3650)
    enabled: bool = False


class BackupRestoreVerifyIn(BaseModel):
    require_credentials_secret: bool = True


class BackupRestoreDrillIn(BaseModel):
    require_credentials_secret: bool = True
    verify_registry_sample: bool = False


class InstallUpgradePreflightIn(BaseModel):
    expected_tag: str | None = Field(default=None, max_length=128)
    previous_tag: str | None = Field(default=None, max_length=128)


class WorkerHeartbeatIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    labels: list[str] = Field(default_factory=list, max_length=32)
    environment: str = Field(default="local", max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    version: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default="", max_length=500)


class WorkerClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    labels: list[str] = Field(default_factory=list, max_length=32)
    environment: str | None = Field(default=None, max_length=64)


class WorkerCompleteIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    queue_id: int = Field(ge=1)
    status: str = Field(min_length=1, max_length=32)
    run_id: int | None = Field(default=None, ge=1)
    message: str | None = Field(default="", max_length=1000)


class ScheduledPushPolicyIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1, max_length=255)
    cron: str = Field(default="0 18 * * *", min_length=1, max_length=120)
    enabled: bool = False
    allow_latest: bool = False
    source_credential_id: str | None = Field(default=None, max_length=64)
    target_credential_id: str | None = Field(default=None, max_length=64)


class MirrorRuleTemplateIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    source_registry_pattern: str = Field(default="*", min_length=1, max_length=255)
    source_namespace_pattern: str = Field(default="*", min_length=1, max_length=255)
    source_repo_pattern: str = Field(default="*", min_length=1, max_length=255)
    target_registry: str = Field(min_length=1, max_length=255)
    target_namespace_template: str | None = Field(default="{namespace}", max_length=255)
    mode: str = Field(default="auto_push", max_length=32)
    check_interval_minutes: int = Field(default=30, ge=1, le=1440)
    allow_latest_push: bool = False
    source_credential_id: str | None = Field(default=None, max_length=64)
    target_credential_id: str | None = Field(default=None, max_length=64)
    notification_policy_id: str | None = Field(default=None, max_length=64)
    push_window_id: str | None = Field(default=None, max_length=64)
    retention_policy_id: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=100, ge=1, le=10000)
    enabled: bool = True


class TemplatePreviewIn(BaseModel):
    source: str = Field(min_length=1, max_length=255)


class TemplateApplyIn(BaseModel):
    sources: list[str] = Field(default_factory=list, max_length=500)
    apply_target: bool = False
    apply_policy: bool = True


class DiscoverySourceIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    source_type: str = Field(default="inline", max_length=32)
    location: str | None = Field(default=None, max_length=1000)
    content: str | None = Field(default="", max_length=200000)
    scan_interval_minutes: int = Field(default=60, ge=1, le=1440)
    enabled: bool = True


class DiscoveryCandidateBatchIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)
    trigger_sync: bool = False
    reason: str | None = Field(default="", max_length=500)


class NotificationPolicyIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    webhook_url: str | None = Field(default=None, max_length=1000)
    events: dict = Field(default_factory=dict)
    min_severity: str = Field(default="warning", max_length=32)
    dedupe_seconds: int = Field(default=1800, ge=0, le=86400)
    quiet_hours: dict = Field(default_factory=dict)
    enabled: bool = True


class NotificationPolicyTestIn(BaseModel):
    event_type: str = Field(default="push_failed", max_length=64)
    severity: str = Field(default="warning", max_length=32)
    payload: dict = Field(default_factory=dict)


class PushWindowIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    allow_windows: list[dict] = Field(default_factory=list, max_length=32)
    freeze_windows: list[dict] = Field(default_factory=list, max_length=32)
    enabled: bool = True


class BulkOperationIn(BaseModel):
    operation_type: str = Field(min_length=1, max_length=64)
    sources: list[str] = Field(default_factory=list, max_length=500)
    params: dict = Field(default_factory=dict)


class MirrorManualPushIn(MirrorPushIn):
    confirm_bypass_window: bool = False
    bypass_reason: str | None = Field(default="", max_length=500)
