# Mirror RegistryX Project Brief

## Positioning

Mirror RegistryX is a single-node private Docker image mirror for personal servers and small teams.

The highest priority is reliable local image availability: a user should be able to deploy the service with Docker Compose, configure upstream image rules, store registry credentials, sync images into a local Registry, understand failures, clean storage, and upgrade safely.

It is not a general container platform, a Harbor replacement, or a full enterprise supply-chain governance system.

## Default User

The default user can SSH into a server, run Docker Compose, read basic logs, and maintain a personal or small-team service.

The product can expose operational concepts such as Compose, sync queues, Registry storage, garbage collection, and agent heartbeat. It should not require enterprise administration concepts such as multi-tenant organizations, complex RBAC, approval workflows, or policy-as-code to complete the core mirror workflow.

## Core Capabilities

- Administrator login and session management.
- Mirror rule configuration.
- Source and target registry credentials.
- Manual sync and scheduled sync.
- Sync queue, run history, failed task retry, and event logs.
- Local Registry status.
- Storage usage, deletion marks, and garbage-collection guidance.
- Single-node Docker Compose deployment.
- Published-image upgrade and rollback workflow.

## Enhanced Capabilities

These capabilities may exist, but they must not obscure the core mirror workflow:

- Image discovery.
- Rule templates.
- Push windows.
- Notification policies.
- Bulk operations.
- Ops-agent controlled operational tasks.
- Backup checks and diagnostic bundles.
- Release trust scan, promotion, rollback, and restore drills.

## Product Rules

- The core sync workflow must not depend on ops-agent.
- Ops-agent may be included in the default Compose deployment, but it is an enhanced operational capability because it has Docker socket access.
- Trust, scan, promotion, rollback, and restore drills are advanced operational capabilities, not the main product story.
- Navigation and documentation should be organized around daily user tasks, not internal technical modules.
- The next product phase should improve deployment, first configuration, sync reliability, failure handling, storage cleanup, and upgrade confidence before adding new major modules.
