"""Qualification-only helpers. Not research, Core, or runtime authority.

These modules exist so staging/VDS qualification scripts can run from an
installed release without the tests tree. They do not authorize work,
dispatch Workers, or create Findings.
"""

from research_os.qualification.staging_spine import (
    DEFAULT_STAGING_NOW,
    TRUNCATE_GUARD,
    StagingTruncateDenied,
    require_explicit_spine_truncate,
    seed_authorized_spine,
    truncate_spine,
)

__all__ = [
    "DEFAULT_STAGING_NOW",
    "TRUNCATE_GUARD",
    "StagingTruncateDenied",
    "require_explicit_spine_truncate",
    "seed_authorized_spine",
    "truncate_spine",
]
