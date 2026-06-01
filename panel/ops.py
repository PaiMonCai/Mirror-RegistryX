"""Operational, diagnostics, setup, backup, migration, and platform routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

build_ops_summary = legacy.build_ops_summary
build_diagnostic_bundle = legacy.build_diagnostic_bundle

_OPS_PREFIXES = [
    "/api/ops",
    "/api/diagnostics",
    "/api/sync-runs",
    "/api/sync-run-items",
]
_OPS_EXACT = {
    "/api/status",
    "/api/settings",
    "/api/logs",
    "/api/events",
    "/api/images",
    "/api/security-guide",
    "/api/security-checks",
    "/api/platform",
    "/api/platform/groups",
    "/api/deployment-modes",
    "/api/database-guide",
}

router = legacy_router("ops", lambda path: path in _OPS_EXACT or path_in_prefixes(path, _OPS_PREFIXES))
