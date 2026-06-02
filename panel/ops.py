"""Core panel status, settings, log, and sync history routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

_OPS_PREFIXES = [
    "/api/sync-runs",
    "/api/sync-run-items",
]
_OPS_EXACT = {
    "/api/status",
    "/api/settings",
    "/api/logs",
    "/api/events",
}

router = legacy_router("ops", lambda path: path in _OPS_EXACT or path_in_prefixes(path, _OPS_PREFIXES))
