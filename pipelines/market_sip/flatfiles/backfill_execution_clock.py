"""Retired historical execution-clock recovery entry point.

Historical market consumers and repair jobs must read the certified imported
event authority. They may not reopen retained SIP flatfiles to recover fields
that the canonical import did not preserve. New source days materialize the
execution-clock sidecar inside ``download_update_events.py`` during import.
Older incomplete days require an explicit versioned re-import design.
"""

from __future__ import annotations


RETIREMENT_MESSAGE = (
    "backfill_execution_clock.py is retired: downstream repair tools may not "
    "read retained SIP flatfiles. Use market_sip_compact.events_YYYY and "
    "ingestion-owned certified sidecars; incomplete historical clock coverage "
    "requires an explicit versioned canonical re-import."
)


def main() -> None:
    raise SystemExit(RETIREMENT_MESSAGE)


if __name__ == "__main__":
    main()
