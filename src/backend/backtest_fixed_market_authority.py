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
    expected = {"source_key": "fixed_market_data", **execution_plan.payload(),
                "parent_market_plan_token": parent_plan.token,
                "scanner_ticker_count": len(parent_plan.tickers),
                "execution_ticker_count": len(execution_plan.tickers), **_STATIC}
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
    return {"source_key": "fixed_market_data", **execution_plan.payload(),
            "parent_market_plan_token": parent_plan.token,
            "scanner_ticker_count": len(parent_plan.tickers),
            "execution_ticker_count": len(execution_plan.tickers), **_STATIC}
