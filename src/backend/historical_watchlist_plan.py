from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.application_registry import FIELD_DEFINITIONS
from src.trading_runtime.watchlist_resolver import SOURCE_FIELDS


HISTORICAL_WATCHLIST_PLAN_SCHEMA_VERSION = 2
MAX_EVALUATIONS_PER_CHUNK = 1_800
NEW_YORK = ZoneInfo("America/New_York")
SUPPORTED_COMPARATORS = {
    "above_by_bps",
    "equals",
    "greater_or_equal",
    "greater_than",
    "is_true",
    "less_or_equal",
    "less_than",
}
QMD_SOURCE_IDS = {
    "indicator.vwap.value",
    "liquidity-rank",
    "market.change_pct",
    "market.last_price",
    "market.relative_volume",
    "market.volume",
}


def compile_historical_watchlist_plan(
    configuration: dict[str, Any],
    watchlist_id: str,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("historical Watchlist plan bounds must be timezone-aware")
    if end <= start:
        raise ValueError("historical Watchlist plan end must follow start")
    discovery = dict(configuration.get("market_discovery") or {})
    watchlist = next(
        (
            deepcopy(row)
            for row in discovery.get("watchlists") or []
            if str(row.get("watchlist_id") or "") == watchlist_id
        ),
        None,
    )
    if watchlist is None:
        raise ValueError(f"unknown Watchlist: {watchlist_id}")
    if not bool(watchlist.get("enabled", True)):
        raise ValueError(f"historical Watchlist is disabled: {watchlist_id}")

    rule_by_id = {
        str(row.get("rule_set_id") or ""): deepcopy(row)
        for row in discovery.get("rule_sets") or []
    }
    selected_rule_ids = [
        *[str(value) for value in watchlist.get("inclusion_rule_sets") or []],
        *[str(value) for value in watchlist.get("exclusion_rule_sets") or []],
    ]
    missing_rules = sorted({rule_id for rule_id in selected_rule_ids if rule_id not in rule_by_id})
    if missing_rules:
        raise ValueError(f"historical Watchlist references unknown rules: {', '.join(missing_rules)}")
    selected_rules = [rule_by_id[rule_id] for rule_id in dict.fromkeys(selected_rule_ids)]
    sources = _validated_sources(watchlist, selected_rules)
    field_by_id = {field.field_id: field for field in FIELD_DEFINITIONS}
    external_features: list[dict[str, Any]] = []
    for source_id in sorted(sources - QMD_SOURCE_IDS):
        field = field_by_id.get(source_id)
        if field is None:
            raise ValueError(f"historical Watchlist source is not registered: {source_id}")
        if field.status != "implemented" or field.historical_support != "point_in_time":
            raise ValueError(
                f"historical Watchlist source is not causally available: {source_id} "
                f"(status={field.status}, historical_support={field.historical_support})"
            )
        external_features.append(
            {
                "available_at": field.available_at,
                "event_at": field.event_at,
                "field_id": source_id,
                "identity_join": field.identity_join,
                "owner": field.owner,
                "query_plan_id": field.query_plan_id,
                "schema_version": field.schema_version,
                "source_path": field.source_path,
            }
        )

    cadence_ms = max(1, int(watchlist.get("refresh_interval_ms") or 0))
    evaluation_windows = _evaluation_windows(start, end)
    body = {
        "schema_version": HISTORICAL_WATCHLIST_PLAN_SCHEMA_VERSION,
        "watchlist_id": watchlist_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "evaluation_windows": evaluation_windows,
        "cadence_ms": cadence_ms,
        "chunk_duration_ms": cadence_ms * MAX_EVALUATIONS_PER_CHUNK,
        "max_evaluations_per_chunk": MAX_EVALUATIONS_PER_CHUNK,
        "source_scan_id": str(watchlist.get("source_scan_id") or ""),
        "inclusion_operator": str(watchlist.get("inclusion_operator") or "all"),
        "inclusion_rule_sets": [str(value) for value in watchlist.get("inclusion_rule_sets") or []],
        "exclusion_rule_sets": [str(value) for value in watchlist.get("exclusion_rule_sets") or []],
        "rule_sets": selected_rules,
        "ranking_field": str(watchlist.get("ranking_field") or ""),
        "ranking_direction": str(watchlist.get("ranking_direction") or "descending"),
        "maximum_size": max(1, int(watchlist.get("maximum_size") or 1)),
        "membership_expiry": str(watchlist.get("membership_expiry") or "end_of_trading_day"),
        "membership_ttl_ms": max(0, int(watchlist.get("membership_ttl_ms") or 0)),
        "manual_inclusions": sorted({str(value).strip().upper() for value in watchlist.get("manual_inclusions") or [] if str(value).strip()}),
        "manual_exclusions": sorted({str(value).strip().upper() for value in watchlist.get("manual_exclusions") or [] if str(value).strip()}),
        "qmd_sources": sorted(sources & QMD_SOURCE_IDS),
        "external_features": external_features,
        "output_mode": "initial_membership_then_transition_deltas",
        "state_carry_required": True,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return {**body, "plan_hash": f"sha256:{hashlib.sha256(encoded).hexdigest()}"}


def _evaluation_windows(start: datetime, end: datetime) -> list[dict[str, str]]:
    local_start = start.astimezone(NEW_YORK)
    local_end = end.astimezone(NEW_YORK)
    cursor = local_start.date()
    windows: list[dict[str, str]] = []
    while cursor <= local_end.date():
        if cursor.weekday() < 5:
            session_start = datetime.combine(cursor, time(4, 0), tzinfo=NEW_YORK)
            session_end = datetime.combine(cursor, time(20, 0), tzinfo=NEW_YORK)
            bounded_start = max(session_start, local_start)
            bounded_end = min(session_end, local_end)
            if bounded_end > bounded_start:
                windows.append(
                    {
                        "start": bounded_start.isoformat(),
                        "end": bounded_end.isoformat(),
                    }
                )
        cursor += timedelta(days=1)
    if not windows:
        raise ValueError("historical Watchlist plan has no New York market evaluation window")
    return windows


def _validated_sources(watchlist: dict[str, Any], rules: list[dict[str, Any]]) -> set[str]:
    sources = {str(watchlist.get("ranking_field") or "")}
    for rule in rules:
        operator = str(rule.get("operator") or "all")
        if operator not in {"all", "any", "score"}:
            raise ValueError(f"unsupported historical Watchlist rule operator: {operator}")
        for condition in rule.get("conditions") or []:
            if not bool(condition.get("enabled", True)):
                continue
            comparator = str(condition.get("comparator") or "")
            if comparator not in SUPPORTED_COMPARATORS:
                raise ValueError(f"unsupported historical Watchlist comparator: {comparator}")
            left = str(condition.get("left_source_id") or "")
            right = str(condition.get("right_source_id") or "")
            if not left:
                raise ValueError("historical Watchlist condition requires left_source_id")
            sources.add(left)
            if right:
                sources.add(right)
    sources.discard("")
    unknown = sorted(source for source in sources if source not in SOURCE_FIELDS)
    if unknown:
        raise ValueError(f"historical Watchlist uses unsupported sources: {', '.join(unknown)}")
    return sources
