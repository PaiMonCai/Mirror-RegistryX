# Mirror Registry

[English](README.en.md)

单机私有 Docker 镜像缓存服务，包含本地 Registry、管理面板和定时同步 worker。这个项目按个人服务器和小团队使用场景收敛：核心目标是让本地镜像站可靠运行，能登录、能配置镜像、能保存仓库凭据、能同步、能查看任务和日志，并能处理存储清理与升级恢复。

项目定位见 [PROJECT_BRIEF.md](PROJECT_BRIEF.md)。

## 功能分层

核心功能优先保证本地镜像可用性：

- 管理员登录和会话。
- 镜像规则配置。
- 源仓库和目标 Registry 凭据。
- 手动同步、定时同步、队列、历史和失败重试。
- 日志、事件、本地 Registry 状态。
- 存储占用、删除标记和 GC 指引。
- Docker Compose 单机部署和镜像升级流程。

增强功能用于辅助运维和治理，但不应遮挡核心同步流程：

- 镜像发现、规则模板、推送窗口、通知策略和批量操作。
- `ops-agent` 运维任务、服务重启、更新、诊断和备份检查。
- 可信发布扫描、促发、回滚和恢复演练。

## 服务组成

- `registry`：官方 `registry:2`，保存本地镜像层数据。
- `panel`：FastAPI + React 管理面板，默认监听 `8080`。
- `sync`：Python worker，使用 `skopeo copy` 把上游镜像同步到本地 Registry。
- `ops-agent`：可选增强运维代理，用于受控执行服务状态、重启、更新、诊断和备份检查；核心同步流程不依赖它。

## 生产部署

生产服务器直接拉取已发布镜像，不在服务器上构建：

```powershell
docker compose pull
docker compose up -d
docker compose ps
```

也可以合并执行：

```powershell
docker compose pull && docker compose up -d
```

启动后访问：

```text
http://localhost:8080
```

生产 compose 使用 Docker named volumes 保存运行数据：

- `mirror-registry-config`：面板生成的 `mirrors.yml`。
- `mirror-registry-data`：SQLite、日志、触发文件和同步状态。
- `mirror-registry-storage`：Registry 镜像层数据。

## .env 示例

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-strong-admin-password
SESSION_TTL_SECONDS=604800
SESSION_COOKIE_NAME=mirror_registry_session
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
APP_ENV=development
MIRROR_REGISTRY_IMAGE_TAG=latest
APP_VERSION=v4
DATABASE_URL=sqlite:////data/mirror-registry.db
SYNC_CONCURRENCY=2
SYNC_RETRY_COUNT=2
SYNC_RETRY_BACKOFF_SECONDS=2
DISK_LOW_BYTES=2147483648
NOTIFY_WEBHOOK_URL=
NOTIFY_DEDUPE_SECONDS=1800
SKOPEO_COPY_ALL=1
SKOPEO_DEST_TLS_VERIFY=false
```

`SESSION_COOKIE_SECURE=false` 适合 HTTP 内网访问；如果面板放到 HTTPS 后面，再改成 `true`。如果登录接口返回 200，但随后 `/api/auth/me` 仍是 401，优先检查这里是否在 HTTP 场景误设成了 `true`。

首次启动时，如果数据卷里没有管理员，面板会用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 初始化账号。

## 终端重置登录密码

忘记面板密码时，在部署主机执行：

```powershell
docker compose exec panel python -m panel.password_reset admin
```

如果 panel 容器已经停止：

```powershell
docker compose run --rm --no-deps panel python -m panel.password_reset admin
```

源码工作区也可以用包装脚本：

```powershell
.\.venv\Scripts\python.exe scripts\reset-admin-password.py admin
```

默认只重置已存在用户。确实需要救援创建账号时再加：

```powershell
docker compose run --rm --no-deps panel python -m panel.password_reset admin --create-if-missing
```

外部数据库或临时 SQLite 文件可以用 `--database-url` 覆盖。命令会隐藏输入并二次确认密码，重置后删除该用户所有 session，并写入不含明文密码的审计记录。

## 仓库凭据

面板「仓库凭据」页保存源仓库或目标 Registry 的用户名和 token/password。

如果是旧版本保存过的凭据，同步日志提示无法读取时，打开「仓库凭据」，编辑对应 GHCR/Docker Hub 凭据，重新输入 token/password 并保存一次即可。

## 常用操作

```powershell
docker compose logs -f panel
docker compose logs -f sync
docker compose restart panel sync
docker compose pull && docker compose up -d
```

手动同步可以在面板点「立即同步」，也可以调用：

```powershell
curl -X POST http://localhost:8080/api/sync
```

## 生产 smoke

smoke 只检查核心链路：环境、compose、登录、`/api/status`、Registry `/v2/`，以及在显式启动服务时可选触发一次同步。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prod-smoke.ps1
```

Linux/macOS：

```bash
scripts/prod-smoke.sh
```

如果要让脚本拉镜像并启动服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prod-smoke.ps1 -StartServices
```

## 本地开发

```powershell
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml ps
```

前端修改后构建：

```powershell
npm.cmd --prefix panel run typecheck
npm.cmd --prefix panel run build
```

Python 验证：

```powershell
python scripts\verify.py
python -m pytest --basetemp .pytest-basetemp
```

完整本地检查：

```powershell
.\scripts\check-runtime.ps1
```

## 配置文件

生产环境优先通过面板维护镜像配置。开发时也可以直接编辑 `config/mirrors.yml`：

```yaml
mirrors:
  - source: docker.io/library/busybox:latest
    target: localhost:5000/library/busybox:latest
    registry: local
    group: default
    project: default
    environment: local
    namespace: library

settings:
  check_interval_minutes: 30
  registry_url: http://registry:5000
  database_url: sqlite:////data/mirror-registry.db
  sync_concurrency: 2
  sync_retry_count: 2
```

修改 `check_interval_minutes` 后，如需立即生效，重启 sync：

```powershell
docker compose restart sync
```

## 存储清理

面板里的删除标记只是记录清理意图。真正释放 Registry 空间仍需要按 Docker Registry 的规则删除 manifest 后运行 garbage-collect。个人部署建议先备份数据卷，再做清理。
