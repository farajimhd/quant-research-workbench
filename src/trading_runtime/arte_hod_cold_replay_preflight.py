"""Fail-closed, read-only authority preflight for an inactive HOD replay pin."""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path
from typing import Any, Mapping

from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayLedger
from src.trading_runtime.arte_hod_cold_replay import MANIFEST_TABLE, _hash, _hex


def require_hod_readonly_access(*, ledger_path: Path | None = None) -> None:
    """Never substitute generic/writer ClickHouse credentials or an absent ledger."""
    if not os.environ.get("BACKTEST_CLICKHOUSE_URL", "").strip() or not os.environ.get(
        "BACKTEST_CLICKHOUSE_USER", ""
    ).strip():
        raise ValueError("HOD replay requires dedicated BACKTEST_CLICKHOUSE_URL/USER read-only authority")
    ledger = ledger_path or MarketDayLedger().path
    if not ledger.is_file():
        raise ValueError(f"HOD replay certified market-day ledger is unavailable: {ledger}")


def preflight_hod_replay_pins(
    manifest: Mapping[str, Any], *, market_plan: CertifiedMarketDayPlan,
    seed_coverage: Mapping[str, Any], seed_plan_token: str,
) -> None:
    """Match one sealed replay pin to independently certified source identities.

    The caller must still run the existing read-only market-plan and seed
    coverage queries, then fetch the exact pinned rows before parity replay.
    """
    if not isinstance(manifest, Mapping) or set(manifest) != {key for key, _ in MANIFEST_TABLE.columns}:
        raise ValueError("incomplete HOD replay pin")
    if manifest["content_hash"] != _hash({key: value for key, value in manifest.items()
                                           if key != "content_hash"}):
        raise ValueError("tampered HOD replay pin")
    if not isinstance(market_plan, CertifiedMarketDayPlan):
        raise ValueError("certified market-day plan is required")
    session, ticker = manifest["session"], manifest["ticker"]
    if (market_plan.build_id != manifest["market_build_id"]
            or market_plan.definition_hash != manifest["market_source_plan_hash"]
            or market_plan.token != manifest["market_coverage_hash"]
            or market_plan.sessions != (session,) or market_plan.tickers != (ticker,)
            or not {1000, 5000}.issubset(market_plan.required_resolutions_ms)):
        raise ValueError("HOD replay market plan identity/resolution mismatch")
    units = [unit for unit in market_plan.units
             if unit.session_date == session and unit.ticker == ticker]
    if len(units) != len(market_plan.units) or len(units) != 3:
        raise ValueError("HOD replay market units are incomplete or out of scope")
    by_stage = {unit.stage: unit for unit in units}
    if set(by_stage) != {"bars", "technical", "broker_100ms"}:
        raise ValueError("HOD replay market stages are missing or duplicated")
    for stage, name in (("bars", "bars_attempt_id"),
                        ("technical", "indicators_attempt_id"),
                        ("broker_100ms", "liquidity_attempt_id")):
        unit = by_stage[stage]
        if unit.build_id != market_plan.build_id or unit.attempt_id != manifest[name]:
            raise ValueError("HOD replay market attempt changed")
    if not isinstance(seed_coverage, Mapping) or not all(key in seed_coverage for key in (
        "ticker", "session_date", "available_at", "source_checkpoint_hash",
        "source_plan_hash", "level_count", "observation_count",
    )):
        raise ValueError("complete V7 seed coverage is required")
    _hex(seed_plan_token, "seed_plan_token")
    if (seed_plan_token != manifest["seed_coverage_hash"]
            or seed_coverage["ticker"] != ticker
            or seed_coverage.get("state") not in {"complete", "empty"}
            or seed_coverage["source_checkpoint_hash"] != manifest["seed_checkpoint_hash"]
            or seed_coverage["source_plan_hash"] != manifest["seed_source_plan_hash"]
            or str(seed_coverage["session_date"]) >= session
            or int(seed_coverage["level_count"]) < 0
            or int(seed_coverage["observation_count"]) < 0):
        raise ValueError("HOD replay V7 seed coverage changed")
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo
    opening = datetime.combine(date.fromisoformat(session), time(4), ZoneInfo("America/New_York"))
    # ClickHouse DateTime64(9, 'UTC') JSONEachRow renders a timezone-less UTC
    # string. The typed column, not the string itself, supplies this timezone.
    available = datetime.fromisoformat(str(seed_coverage["available_at"]).replace("Z", "+00:00"))
    if available.tzinfo is None:
        available = available.replace(tzinfo=timezone.utc)
    if available.astimezone(timezone.utc) > opening.astimezone(timezone.utc):
        raise ValueError("V7 seed was unavailable at the replay session opening")
