"""Narrow, normalized Strategy 1 commit authority (pre-publication contract).

The writer must verify every typed detail family before publishing these rows.
No runtime may treat either row alone as a durable fence: recovery requires the
unique commit, its complete child-family set, and the detail-row readback.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Mapping, Sequence
from uuid import UUID

from src.trading_runtime.journal_contract import canonical_json


def _family_set_hash(rows: Sequence[Mapping]) -> str:
    return sha256(canonical_json(sorted(
        (row["family_name"], row["row_count"], row["row_hash"])
        for row in rows
    )).encode()).hexdigest()


def prepare_commit_v4(
    *, run_id: str, run_month, attempt_id: str, batch_id: str,
    prior_batch_id: str, first_sequence: int, last_sequence: int,
    source_cursor: str, status: str,
    sealed_families: Sequence[tuple[str, Sequence[Mapping]]],
    committed_at: datetime,
) -> tuple[dict, tuple[dict, ...]]:
    """Describe one complete commit without inserting or accepting opaque data."""
    if (not run_id or run_month.day != 1 or not source_cursor
            or status not in {"running", "completed", "stopped", "failed"}
            or type(first_sequence) is not int or first_sequence < 1
            or type(last_sequence) is not int or last_sequence < first_sequence
            or committed_at.tzinfo is None):
        raise ValueError("V4 commit identity or completed cursor is invalid")
    for identity in (attempt_id, batch_id, prior_batch_id):
        UUID(identity)
    family_rows = []
    seen = set()
    event_count = last_sequence - first_sequence + 1
    for name, rows in sealed_families:
        if (name in seen or not re.fullmatch(r"trading_[a-z0-9_]+_v\d+", name)):
            raise ValueError("V4 commit has duplicate or invalid family identity")
        seen.add(name)
        if not rows:
            continue
        identities = []
        for row in rows:
            record_id = str(UUID(str(row["record_id"])))
            content_hash = str(row["content_hash"])
            if (row["run_id"] != run_id or str(UUID(str(row["batch_id"]))) != batch_id
                    or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None):
                raise ValueError("V4 family row differs from its batch authority")
            identities.append((record_id, content_hash))
        if len({record_id for record_id, _ in identities}) != len(identities):
            raise ValueError("V4 family repeated a typed row identity")
        family_rows.append({
            "run_id": run_id, "run_month": run_month.isoformat(),
            "batch_id": batch_id, "family_name": name,
            "row_count": len(rows),
            "row_hash": sha256(canonical_json(sorted(identities)).encode()).hexdigest(),
        })
        if name == "trading_event_v1" and len(rows) != event_count:
            raise ValueError("V4 event family differs from the sequence span")
    if ("trading_event_v1" not in {row["family_name"] for row in family_rows}
            or len(family_rows) > 65_535):
        raise ValueError("V4 commit requires one nonempty event family")
    family_rows.sort(key=lambda row: row["family_name"])
    family_set_hash = _family_set_hash(family_rows)
    commit = {
        "run_id": run_id, "run_month": run_month.isoformat(),
        "attempt_id": attempt_id, "batch_id": batch_id,
        "prior_batch_id": prior_batch_id,
        "first_sequence": first_sequence, "last_sequence": last_sequence,
        "event_count": event_count, "family_count": len(family_rows),
        "family_set_hash": family_set_hash,
        "source_cursor": source_cursor, "status": status,
        "committed_at": committed_at.astimezone(timezone.utc).isoformat(),
    }
    return commit, tuple(family_rows)


def verify_commit_v4(
    commit: Mapping, family_rows: Sequence[Mapping],
    detail_identities: Mapping[str, Sequence[tuple[str, str]]],
) -> None:
    """Verify complete family identities after every detail row hash is checked.

    `detail_identities` must come from a bounded readback that has independently
    recomputed each typed row's content hash; identity seals alone cannot prove
    that a row's non-key scalar columns are intact.
    """
    try:
        run_id = str(commit["run_id"])
        batch_id = str(UUID(str(commit["batch_id"])))
        month = str(commit["run_month"])
        count = int(commit["family_count"])
        events = int(commit["event_count"])
        span = int(commit["last_sequence"]) - int(commit["first_sequence"]) + 1
        if (not run_id or count < 1 or events < 1 or events != span
                or count != len(family_rows)):
            raise ValueError("V4 commit count or sequence span differs")
        names = []
        for row in family_rows:
            name = str(row["family_name"])
            if (not re.fullmatch(r"trading_[a-z0-9_]+_v\d+", name)
                    or row["run_id"] != run_id
                    or str(UUID(str(row["batch_id"]))) != batch_id
                    or str(row["run_month"]) != month
                    or type(row["row_count"]) is not int
                    or row["row_count"] < 1
                    or re.fullmatch(r"[0-9a-f]{64}", str(row["row_hash"])) is None):
                raise ValueError("V4 family row differs from its commit")
            names.append(name)
            identities = detail_identities[name]
            normalized = [(str(UUID(str(record_id))), str(digest))
                          for record_id, digest in identities]
            if (len(normalized) != row["row_count"]
                    or len({record_id for record_id, _ in normalized}) != len(normalized)
                    or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None
                           for _, digest in normalized)
                    or sha256(canonical_json(sorted(normalized)).encode()).hexdigest()
                    != row["row_hash"]):
                raise ValueError("V4 detail identities differ from family seal")
        if (len(set(names)) != count
                or set(detail_identities) != set(names)
                or "trading_event_v1" not in names
                or next(row["row_count"] for row in family_rows
                        if row["family_name"] == "trading_event_v1") != events
                or _family_set_hash(family_rows) != commit["family_set_hash"]):
            raise ValueError("V4 family set differs from commit seal")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("V4 readback lacks complete normalized families") from exc
