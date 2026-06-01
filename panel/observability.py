"""Observability summary and metrics routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

build_observability_summary = legacy.build_observability_summary
router = legacy_router("observability", lambda path: path_in_prefixes(path, ["/api/observability"]))
