"""Scheduled push routes kept as part of the personal-use sync workflow."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

protection_result = legacy.protection_result
assert_tag_mutation_allowed = legacy.assert_tag_mutation_allowed
retention_dry_run = legacy.retention_dry_run
next_run_from_cron = legacy.next_run_from_cron

router = legacy_router(
    "governance",
    lambda path: path_in_prefixes(path, ["/api/schedules"]),
)
