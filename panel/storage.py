"""Registry storage statistics, tag details, and delete-mark routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

registry_storage_root = legacy.registry_storage_root
cached_storage_stats = legacy.cached_storage_stats
recalculate_storage_stats = legacy.recalculate_storage_stats
recalculate_storage_stats_sync = legacy.recalculate_storage_stats_sync

router = legacy_router("storage", lambda path: path_in_prefixes(path, ["/api/storage"]))
