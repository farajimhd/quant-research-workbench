"""Closed fixed-Watchlist correctness scaffold over attested scanner sidecars.

This is deliberately not wired to the fixed Backtest launch path: each 1s
boundary currently needs a cold attested read. A bounded multi-boundary reader
and measured full-session throughput are required before activation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Mapping

from src.backend.fixed_watchlist_scanner_publisher import load_attested_scanner_boundary
from src.backend.fixed_watchlist_transitions import (
    CompletedMembership, MembershipCandidate, MembershipTransition,
    reduce_completed_memberships, universe_fingerprint,
)


_RULE = "strategy-squeeze-volume-spread-quality"
_CONDITIONS = (
    ("squeeze-price-floor", "market.last_price", "greater_or_equal", Decimal("2")),
    ("squeeze-price-ceiling", "market.last_price", "less_or_equal", Decimal("50")),
    ("squeeze-session-dollar-volume", "market.session_dollar_volume", "greater_or_equal", Decimal("1000000")),
    ("squeeze-session-share-volume", "market.volume", "greater_or_equal", Decimal("100000")),
    ("squeeze-trade-rate", "market.trade_rate_10s", "greater_or_equal", Decimal("1")),
    ("squeeze-sustained-trade-rate", "market.trade_rate_60s", "greater_or_equal", Decimal("0.5")),
    ("squeeze-spread-quality", "market.spread_bps", "less_or_equal", Decimal("60")),
)
_SOURCES = sorted({source for _, source, _, _ in _CONDITIONS} | {"market.liquidity_score"})
_RUNTIME_FIELDS = {
    "market.last_price": "last_price",
    "market.liquidity_score": "liquidity_score",
    "market.session_dollar_volume": "session_dollar_volume",
    "market.spread_bps": "spread_bps",
    "market.trade_rate_10s": "trade_rate_10s",
    "market.trade_rate_60s": "trade_rate_60s",
    "market.volume": "volume",
}


@dataclass(frozen=True, slots=True)
class ScannerBoundaryRef:
    boundary_id: str
    source_revision_token: str
    boundary_at: datetime


def _pin_plan(plan: Mapping[str, Any]) -> str:
    if not isinstance(plan, Mapping):
        raise ValueError("fixed squeeze Watchlist needs an approved plan")
    raw_hash = plan.get("plan_hash")
    body = {key: value for key, value in plan.items() if key != "plan_hash"}
    digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True).encode()).hexdigest()
    if raw_hash != f"sha256:{digest}":
        raise ValueError("fixed squeeze Watchlist plan hash differs")
    fields = {
        "schema_version": 4,
        "watchlist_id": "squeeze-tradable-candidates", "source_scan_id": "qmd-core-scan",
        "cadence_ms": 1000, "inclusion_operator": "all",
        "inclusion_rule_sets": [_RULE], "exclusion_rule_sets": [],
        "ranking_field": "market.liquidity_score", "ranking_direction": "descending",
        "maximum_size": 10, "membership_expiry": "end_of_trading_day",
        "membership_ttl_ms": 300000, "manual_inclusions": [], "manual_exclusions": [],
        "external_features": [], "qmd_sources": _SOURCES,
        "qmd_source_specs": [
            {"instance_id": source, "source_id": source,
             "runtime_field": _RUNTIME_FIELDS[source], "interval": "", "aggregation": ""}
            for source in _SOURCES
        ],
        "output_mode": "initial_membership_then_transition_deltas",
        "state_carry_required": True, "focused_seed_multiplier": 5,
    }
    if any(plan.get(key) != value for key, value in fields.items()):
        raise ValueError("fixed squeeze Watchlist plan surface is unsupported")
    rules = plan.get("rule_sets")
    if (not isinstance(rules, list) or len(rules) != 1
            or rules[0].get("rule_set_id") != _RULE
            or rules[0].get("operator") != "all"
            or rules[0].get("enabled") is not True):
        raise ValueError("fixed squeeze Watchlist rule is unsupported")
    conditions = rules[0].get("conditions")
    if not isinstance(conditions, list) or len(conditions) != len(_CONDITIONS):
        raise ValueError("fixed squeeze Watchlist conditions differ")
    actual = []
    for row in conditions:
        if (not isinstance(row, Mapping) or row.get("enabled") is not True
                or row.get("right_source_id") != ""
                or row.get("left_instance_id") != row.get("left_source_id")
                or any(key in row for key in ("left_interval", "right_interval",
                                              "left_aggregation", "right_aggregation"))):
            raise ValueError("fixed squeeze Watchlist condition is unsupported")
        actual.append((row.get("condition_id"), row.get("left_source_id"),
                       row.get("comparator"), Decimal(str(row.get("value")))))
    if tuple(actual) != _CONDITIONS:
        raise ValueError("fixed squeeze Watchlist seven-filter contract differs")
    return digest


def _matches(row: Mapping[str, Any]) -> bool:
    price = Decimal(str(row["last_price"]))
    bid = Decimal(str(row["bid"]))
    ask = Decimal(str(row["ask"]))
    if price <= 0 or bid <= 0 or ask < bid:
        return False  # QMD does not project spread_bps without an executable quote.
    # QMD projects this operand with binary f64 arithmetic at the scanner
    # clock; preserve its threshold behavior at exactly 60 bps.
    spread_bps = (float(ask) - float(bid)) / float(price) * 10000.0
    values = (price, price, Decimal(str(row["day_dollar_volume"])),
              Decimal(str(row["day_volume"])), Decimal(str(row["trade_rate_10s"])),
              Decimal(str(row["trade_rate_60s"])), Decimal(str(spread_bps)))
    return all(value >= threshold if comparator == "greater_or_equal" else value <= threshold
               for value, (_, _, comparator, threshold) in zip(values, _CONDITIONS))


def evaluate_attested_squeeze_watchlist(
    client: Any, keeper: Any, plan: Mapping[str, Any],
    refs: tuple[ScannerBoundaryRef, ...], *, expected_tickers: tuple[str, ...],
    market_plan_token: str, query_sha256: str,
) -> tuple[tuple[CompletedMembership, ...], tuple[MembershipTransition, ...]]:
    """Require every pinned full-universe 1s boundary; never build or infer one.

    Diagnostic correctness path only until batched cold reads exist.
    """
    plan_hash = _pin_plan(plan)
    if not refs or not expected_tickers:
        raise ValueError("fixed squeeze Watchlist needs full-scope boundaries")
    try:
        start = datetime.fromisoformat(plan["start"])
        end = datetime.fromisoformat(plan["end"])
        windows = tuple((datetime.fromisoformat(window["start"]),
                         datetime.fromisoformat(window["end"]))
                        for window in plan["evaluation_windows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("fixed squeeze Watchlist evaluation windows are invalid") from exc
    if (start.tzinfo is None or end.tzinfo is None or start >= end
            or not windows or any(left.tzinfo is None or right.tzinfo is None
                                  or left >= right for left, right in windows)):
        raise ValueError("fixed squeeze Watchlist evaluation windows are invalid")
    fingerprint = universe_fingerprint(expected_tickers)
    snapshots = []
    previous_at = None
    for ref in refs:
        if not isinstance(ref, ScannerBoundaryRef) or ref.boundary_at.tzinfo is None:
            raise ValueError("fixed squeeze Watchlist boundary reference is invalid")
        at = ref.boundary_at.astimezone(timezone.utc)
        if (at.microsecond or (previous_at is not None
                               and at - previous_at != timedelta(seconds=1))):
            raise ValueError("fixed squeeze Watchlist requires contiguous completed 1s clocks")
        if not (start <= at < end and any(left <= at < right for left, right in windows)):
            raise ValueError("fixed squeeze Watchlist boundary is outside pinned session")
        previous_at = at
        boundary, rows = load_attested_scanner_boundary(
            client, keeper, ref.boundary_id, market_plan_token=market_plan_token,
            source_revision_token=ref.source_revision_token, boundary_at=at,
        )
        tickers = tuple(sorted(row["ticker"] for row in rows))
        ranks = {int(row["liquidity_rank"]) for row in rows}
        if (tickers != expected_tickers or len(rows) != boundary["market_row_count"]
                or ranks != set(range(1, len(rows) + 1))):
            raise ValueError("fixed squeeze Watchlist scanner universe is incomplete")
        matched = [row for row in rows if _matches(row)]
        # QMD Watchlist ranking is score DESC, then ticker ASC. The global
        # scanner rank has additional tie-breaks and is not this plan's rank.
        matched.sort(key=lambda row: (-Decimal(str(row["liquidity_score"])), row["ticker"]))
        candidates = tuple(MembershipCandidate(
            row["ticker"], rank, Decimal(str(row["liquidity_score"])), "rules passed",
        ) for rank, row in enumerate(matched[:10], 1))
        snapshots.append(CompletedMembership(
            "squeeze-tradable-candidates", plan_hash, market_plan_token,
            query_sha256, fingerprint, expected_tickers, at, candidates,
        ))
    completed = tuple(snapshots)
    return completed, reduce_completed_memberships(completed, expected_tickers=expected_tickers)
