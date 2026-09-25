"""Inactive, closed V3 seal for fixed completed-bar squeeze occurrences.

This module does not publish or accept generic Signal Stream occurrences. A
whole-run V3 chain reader is required before these rows can serve the UI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.backend.backtest_squeeze_episode_projection import project_fixed_squeeze_episode
from src.backend.backtest_squeeze_episode_schema import SQUEEZE_COMMIT_V3, SQUEEZE_EPISODE
from src.backend.fixed_bar_signal import CONTRACT, STREAM_ID
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROW_COLUMNS = {name for name, _ in SQUEEZE_EPISODE.columns}
_COMMIT_COLUMNS = {name for name, _ in SQUEEZE_COMMIT_V3.columns}


def _canonical_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if set(row) != _ROW_COLUMNS - {"content_hash"}:
        raise ValueError("Squeeze row columns differ from V3 contract")
    result: dict[str, Any] = {}
    for name, kind in SQUEEZE_EPISODE.columns:
        if name == "content_hash":
            continue
        value = row[name]
        if kind == "UUID":
            result[name] = str(UUID(str(value)))
        elif kind == "FixedString(64)":
            if not isinstance(value, str) or not _HEX.fullmatch(value):
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = value
        elif kind.startswith("DateTime64"):
            at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        elif kind.startswith("Decimal"):
            try:
                number = Decimal(str(value))
                scaled = number.quantize(Decimal("0.000000000000000001"))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Invalid squeeze {name}") from exc
            if not number.is_finite() or number != scaled or abs(number) >= Decimal(10) ** 20:
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = format(scaled, "f")
        elif kind == "UInt64":
            if type(value) is not int or not 0 <= value < 2**64:
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = value
        elif kind == "Date":
            result[name] = str(value)
            datetime.fromisoformat(result[name])
        else:
            if not isinstance(value, str):
                raise ValueError(f"Invalid squeeze {name}")
            result[name] = value
    return result


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def project_squeeze_row_v3(
    record: JournalRecord, *, batch_id: str, expected_market_plan_token: str,
    expected_query_sha256: str,
) -> dict[str, Any]:
    """Project only a pinned source-native start into its immutable batch."""
    row = project_fixed_squeeze_episode(
        record, expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256)
    row["batch_id"] = str(UUID(batch_id))
    row["content_hash"] = _digest(_canonical_row(row))
    return row


def seal_squeeze_family_v3(
    v2_commit: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    parent_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Produce a replacement V3 seal after exact parent/child verification.

    Parent events must already be verified by the ordinary V2 event seal.
    This pure function never upgrades an existing V2 commit in storage.
    """
    if set(v2_commit) != _COMMIT_COLUMNS - {
        "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash"}:
        raise ValueError("V2 commit columns differ from V3 base")
    batch = str(UUID(str(v2_commit["batch_id"])))
    run = str(v2_commit["run_id"])
    event_by_id = {str(UUID(str(e["record_id"]))): e for e in parent_events}
    if len(event_by_id) != len(parent_events):
        raise ValueError("Duplicate parent event")
    seen: set[str] = set()
    identities: list[tuple[str, str]] = []
    for row in rows:
        if set(row) != _ROW_COLUMNS:
            raise ValueError("Squeeze row columns differ from V3 contract")
        canonical = _canonical_row({k: v for k, v in row.items() if k != "content_hash"})
        identity = canonical["record_id"]
        if identity in seen or row["content_hash"] != _digest(canonical):
            raise ValueError("Duplicate or corrupt squeeze row")
        seen.add(identity)
        parent = event_by_id.get(identity)
        if parent is None or str(parent.get("category")) != "market_discovery_signal" or str(parent.get("entity_type")) != "signal_occurrence":
            raise ValueError("Squeeze row lacks typed parent occurrence")
        if (canonical["run_id"] != run or canonical["batch_id"] != batch
                or str(parent.get("run_id")) != run
                or str(UUID(str(parent.get("batch_id")))) != batch
                or str(parent.get("entity_id")) != canonical["episode_id"]
                or str(parent.get("account_id")) != canonical["account_id"]):
            raise ValueError("Squeeze parent identity differs")
        event_at = datetime.fromisoformat(str(parent["event_time"]).replace("Z", "+00:00"))
        start_at = datetime.fromisoformat(canonical["episode_started_at"]).replace(tzinfo=timezone.utc)
        expires_at = datetime.fromisoformat(canonical["expires_at"]).replace(tzinfo=timezone.utc)
        if (event_at.tzinfo is None or event_at.astimezone(timezone.utc) != start_at
                or expires_at != start_at + timedelta(seconds=300)
                or canonical["event_month"] != start_at.date().replace(day=1).isoformat()
                or canonical["signal_stream_id"] != STREAM_ID
                or canonical["source_authority"] != CONTRACT
                or not _HEX.fullmatch(canonical["market_plan_token"])
                or not _HEX.fullmatch(canonical["query_sha256"])):
            raise ValueError("Squeeze completed-boundary causality differs")
        identities.append((identity, str(row["content_hash"])))
    sealed = dict(v2_commit)
    sealed["backtest_squeeze_episode_count"] = len(rows)
    sealed["backtest_squeeze_episode_hash"] = _digest(sorted(identities))
    return sealed


def verify_squeeze_family_v3(
    commit: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    parent_events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Verify V3 family seal before exposing a bounded typed occurrence page."""
    if set(commit) != _COMMIT_COLUMNS:
        raise ValueError("Not an exact V3 commit")
    base = {k: v for k, v in commit.items() if k not in {
        "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash"}}
    expected = seal_squeeze_family_v3(base, rows, parent_events)
    if (type(commit["backtest_squeeze_episode_count"]) is not int
            or expected != dict(commit)):
        raise ValueError("V3 squeeze count/hash or base commit differs")
    return tuple(dict(row) for row in rows)
