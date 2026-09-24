"""Logical trading-journal envelope shared by live trading and Backtest.

Storage-specific compression and batching must not change these fields or the
meaning of the canonical payload hash.  The hash is over the *logical* JSON,
before a storage adapter replaces large evidence with content references.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any


VERSION = "trading-journal-envelope-v1"


@dataclass(frozen=True, slots=True)
class JournalRecord:
    record_id: str
    run_id: str
    sequence: int
    event_time: datetime
    recorded_at: datetime
    category: str
    entity_type: str
    entity_id: str
    account_id: str
    payload: dict[str, Any]


def canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=_json_default,
                      allow_nan=False)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    try:
        return asdict(value)
    except TypeError:
        return str(value)


def journal_row(record: Any) -> dict[str, Any]:
    payload_json = canonical_json(record.payload)
    return {
        "record_id": record.record_id,
        "run_id": record.run_id,
        "sequence": record.sequence,
        "event_time": record.event_time.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "category": record.category,
        "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "account_id": record.account_id,
        "payload_json": payload_json,
    }


def payload_hash(record: Any) -> str:
    return sha256(canonical_json(record.payload).encode("utf-8")).hexdigest()
