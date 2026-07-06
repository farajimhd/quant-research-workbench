"""Shared gateway infrastructure contracts.

Domain services should import stable shared behavior from this package instead
of copying policy logic into service-specific modules.
"""

__all__ = [
    "audit",
    "backfill",
    "config",
    "coverage",
    "dashboard",
    "errors",
    "health",
    "lifecycle",
    "logging",
    "market_calendar",
    "preflight",
    "provider",
    "reconciliation",
    "rich_renderer",
    "schedule",
    "storage",
    "types",
]
