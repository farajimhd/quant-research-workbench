"""Minimal typed authority for the fixed Backtest persisted-market plan.

The full certified plan remains pinned in the run definition. This projector
stores only the execution-plan hash and its parent hash, never a plan blob or
an arbitrary source-key/value tree. It performs no ClickHouse operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from src.backend.backtest_market_data import CertifiedMarketDayPlan
from src.trading_runtime.journal_contract import JournalRecord


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_STATIC = {
    "database": "arte",
    "tables": ["bars_v1", "indicators_v1", "liquidity_100ms_v1"],
    "access": "select_only",
    "frame_spool": False,
}


@dataclass(frozen=True, slots=True)
class FixedMarketAuthority:
    run_id: str
    record_id: str
    source_event_time: datetime
    execution_plan_token: str
    parent_market_plan_token: str


def _validate_plans(parent: CertifiedMarketDayPlan,
                    execution: CertifiedMarketDayPlan) -> None:
    if (not _HASH.fullmatch(parent.token) or not _HASH.fullmatch(execution.token)
            or parent.build_id != execution.build_id
            or parent.definition_hash != execution.definition_hash
            or parent.sessions != execution.sessions
            or parent.execution_interval != execution.execution_interval
            or parent.required_resolutions_ms != execution.required_resolutions_ms
            or not set(execution.tickers).issubset(parent.tickers)):
        raise ValueError("Fixed market authority plans do not share a pinned source")
    selected = tuple(sorted(set(execution.tickers)))
    projected = sha256(json.dumps({"parent_token": parent.token,
                                  "tickers": selected},
                                 sort_keys=True,
                                 separators=(",", ":")).encode()).hexdigest()
    if (execution.tickers != selected
            or execution.token not in {projected, parent.token}
            or (execution.token == parent.token and execution.tickers != parent.tickers)):
        raise ValueError("Fixed market execution projection lacks its parent hash")


def fixed_market_authority_payload(
    parent: CertifiedMarketDayPlan, execution: CertifiedMarketDayPlan,
) -> dict[str, Any]:
    """One exact payload for the executor and its typed journal projector."""
    _validate_plans(parent, execution)
    return {"source_key": "fixed_market_data", **execution.payload(),
            "parent_market_plan_token": parent.token,
            "scanner_ticker_count": len(parent.tickers),
            "execution_ticker_count": len(execution.tickers), **_STATIC}


def project_fixed_market_authority(
    record: JournalRecord, *, parent_plan: CertifiedMarketDayPlan,
    execution_plan: CertifiedMarketDayPlan, expected_event_time: datetime,
) -> FixedMarketAuthority:
    """Accept exactly the fixed_market_data record bound to certified plans."""
    _validate_plans(parent_plan, execution_plan)
    if ((record.category, record.entity_type, record.entity_id, record.account_id)
            != ("data_authority", "source_revision", "fixed_market_data", "")
            or record.event_time.tzinfo is None
            or expected_event_time.tzinfo is None
            or record.event_time.astimezone(timezone.utc)
            != expected_event_time.astimezone(timezone.utc)):
        raise ValueError("Fixed market authority journal envelope is invalid")
    expected = fixed_market_authority_payload(parent_plan, execution_plan)
    payload = {key: value for key, value in record.payload.items()
               if key not in {"correlation_id", "causation_id"}}
    if payload != expected:
        raise ValueError("Fixed market authority differs from its pinned plans")
    return FixedMarketAuthority(
        record.run_id, record.record_id,
        record.event_time.astimezone(timezone.utc),
        execution_plan.token, parent_plan.token,
    )


def recover_fixed_market_authority(
    row: FixedMarketAuthority, *, parent_plan: CertifiedMarketDayPlan,
    execution_plan: CertifiedMarketDayPlan,
) -> dict[str, Any]:
    """Reconstruct the authoritative source payload from verified run plans."""
    _validate_plans(parent_plan, execution_plan)
    if (row.execution_plan_token != execution_plan.token
            or row.parent_market_plan_token != parent_plan.token):
        raise ValueError("Fixed market authority row differs from pinned run plans")
    return fixed_market_authority_payload(parent_plan, execution_plan)


def load_committed_fixed_market_authority(
    client: Any, prefix: Any, *, parent_plan: CertifiedMarketDayPlan,
    execution_plan: CertifiedMarketDayPlan, expected_start: datetime,
) -> dict[str, Any]:
    """Verify the original normalized authority before a resumed run reuses it."""
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_writer import (
        _CONTRACTS, _canonical_typed_content, _committed_batch_filter,
        _literal, _rows,
    )
    from src.trading_runtime.journal_contract import canonical_json

    _validate_plans(parent_plan, execution_plan)
    if (not isinstance(prefix, V4CommittedPrefix)
            or expected_start.tzinfo is None):
        raise ValueError("Fixed market authority needs a verified V4 run")
    name = "trading_backtest_market_authority_v1"
    columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.{name} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"{_committed_batch_filter(prefix)} "
        "LIMIT 2 FORMAT JSONEachRow")
    if len(rows) != 1:
        raise RuntimeError("Fixed market authority lacks one committed detail")
    detail = rows[0]
    event_name = "trading_event_v1"
    event_columns = ",".join(column for column, _ in _CONTRACTS[event_name].columns)
    events = _rows(client,
        f"SELECT {event_columns} FROM arte.{event_name} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND record_id=toUUID({_literal(detail['record_id'])}) "
        f"{_committed_batch_filter(prefix)} "
        "LIMIT 2 FORMAT JSONEachRow")
    if len(events) != 1:
        raise RuntimeError("Fixed market authority lacks one committed event")
    event = events[0]
    for table, row in ((name, detail), (event_name, event)):
        content = {key: value for key, value in row.items()
                   if key != "content_hash"}
        digest = sha256(canonical_json(_canonical_typed_content(
            table, content, stored_utc=True)).encode()).hexdigest()
        if digest != str(row["content_hash"]):
            raise RuntimeError("Fixed market authority row differs from its hash")
    event_time = datetime.fromisoformat(str(event["event_time"]))
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    if (detail["record_id"] != event["record_id"]
            or detail["batch_id"] != event["batch_id"]
            or detail["account_id"] or event["account_id"]
            or event["category"] != "data_authority"
            or event["entity_type"] != "source_revision"
            or event["entity_id"] != "fixed_market_data"
            or event_time.astimezone(timezone.utc)
               != expected_start.astimezone(timezone.utc)
            or detail["execution_plan_token"] != execution_plan.token
            or detail["parent_market_plan_token"] != parent_plan.token):
        raise RuntimeError("Fixed market authority differs from pinned start")
    payload = fixed_market_authority_payload(parent_plan, execution_plan)
    return {key: value for key, value in payload.items()
            if key != "source_key"}
