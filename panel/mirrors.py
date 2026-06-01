"""Mirror CRUD, discovery, preflight, and artifact routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

normalize_mirror = legacy.normalize_mirror
valid_mirrors = legacy.valid_mirrors
upsert_mirror_db = legacy.upsert_mirror_db
delete_mirror_db = legacy.delete_mirror_db
build_discovery_preview = legacy.build_discovery_preview
build_mirror_preflight = legacy.build_mirror_preflight
build_local_artifact_archive = legacy.build_local_artifact_archive

router = legacy_router("mirrors", lambda path: path_in_prefixes(path, ["/api/mirrors"]))
