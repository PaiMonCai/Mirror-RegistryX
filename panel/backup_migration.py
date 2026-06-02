"""Backup, restore, and cross-machine migration routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

build_backup_package_manifest = legacy.build_backup_package_manifest
build_migration_package_manifest = legacy.build_migration_package_manifest
build_migration_preflight = legacy.build_migration_preflight
build_migration_restore_plan = legacy.build_migration_restore_plan
get_backup_restore_guide = legacy.get_backup_restore_guide
run_backup_restore_drill = legacy.run_backup_restore_drill
run_migration_preflight = legacy.run_migration_preflight
verify_backup_restore_readiness = legacy.verify_backup_restore_readiness

_BACKUP_MIGRATION_PREFIXES = [
    "/api/backup-restore",
    "/api/migration",
]
_BACKUP_MIGRATION_EXACT = {
    "/api/backup-restore-guide",
}

router = legacy_router(
    "backup_migration",
    lambda path: path in _BACKUP_MIGRATION_EXACT or path_in_prefixes(path, _BACKUP_MIGRATION_PREFIXES),
)
