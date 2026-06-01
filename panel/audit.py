"""Audit log API routes and helpers."""

from . import legacy
from .route_utils import legacy_router

audit_log = legacy.audit_log
router = legacy_router("audit", lambda path: path == "/api/audit-logs")
