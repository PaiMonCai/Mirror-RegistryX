"""Install, upgrade, and first-run setup routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

build_install_upgrade_guide = legacy.build_install_upgrade_guide
build_upgrade_preflight = legacy.build_upgrade_preflight
get_install_upgrade_guide = legacy.get_install_upgrade_guide
get_setup_checklist = legacy.get_setup_checklist
run_install_upgrade_preflight = legacy.run_install_upgrade_preflight

_INSTALL_UPGRADE_PREFIXES = [
    "/api/install-upgrade",
    "/api/setup",
]

router = legacy_router("install_upgrade", lambda path: path_in_prefixes(path, _INSTALL_UPGRADE_PREFIXES))
