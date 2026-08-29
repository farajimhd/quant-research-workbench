from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.application_registry import FIELD_DEFINITIONS, QUERY_PLANS
from src.backend.data_field_contracts import (
    field_instance_ref,
    interval_expression,
    normalize_aggregation_function,
)
from src.trading_runtime.watchlist_resolver import SOURCE_FIELDS


HISTORICAL_WATCHLIST_PLAN_SCHEMA_VERSION = 4
MAX_EVALUATIONS_PER_CHUNK = 1_800
MAX_MEMBERSHIP_SLOTS_PER_CHUNK = 2_000_000
FOCUSED_SEED_MULTIPLIER = 5
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
    "market.liquidity_rank",
    "market.liquidity_score",
    "market.session_dollar_volume",
    "market.trade_rate_10s",
    "market.trade_rate_60s",
    "market.change_pct",
    "market.change_actual",
    "market.last_price",
    "market.relative_volume",
    "market.volume",
    "price_change_1_bar_pct",
    "volume_rate_ratio",
    "market.spread_bps",
    "quote.bid_price",
    "quote.ask_price",
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
    sources, qmd_source_specs = _validated_sources(watchlist, selected_rules)
    field_by_id = {field.field_id: field for field in FIELD_DEFINITIONS}
    query_plan_by_id = {plan.plan_id: plan for plan in QUERY_PLANS}
    external_features: list[dict[str, Any]] = []
    source_ids = {_base_source_id(source) for source in sources}
    for source_id in sorted(source_ids - QMD_SOURCE_IDS):
        field = field_by_id.get(source_id)
        if field is None:
            raise ValueError(f"historical Watchlist source is not registered: {source_id}")
        if field.status != "implemented" or field.historical_support != "point_in_time":
            raise ValueError(
                f"historical Watchlist source is not causally available: {source_id} "
                f"(status={field.status}, historical_support={field.historical_support})"
            )
        query_plan = query_plan_by_id.get(field.query_plan_id)
        if query_plan is None:
            raise ValueError(
                f"historical Watchlist query plan is not registered: {field.query_plan_id}"
            )
        external_features.append(
            {
                "available_at": field.available_at,
                "event_at": field.event_at,
                "field_id": source_id,
                "identity_join": field.identity_join,
                "owner": field.owner,
                "query_plan_id": field.query_plan_id,
                "query_plan_version": query_plan.version,
                "schema_version": field.schema_version,
                "source_path": field.source_path,
            }
        )

    cadence_ms = max(1, int(watchlist.get("refresh_interval_ms") or 0))
    maximum_size = max(1, int(watchlist.get("maximum_size") or 1))
    max_evaluations_per_chunk = min(
        MAX_EVALUATIONS_PER_CHUNK,
        max(1, MAX_MEMBERSHIP_SLOTS_PER_CHUNK // maximum_size),
    )
    evaluation_windows = _evaluation_windows(start, end)
    body = {
        "schema_version": HISTORICAL_WATCHLIST_PLAN_SCHEMA_VERSION,
        "watchlist_id": watchlist_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "evaluation_windows": evaluation_windows,
        "cadence_ms": cadence_ms,
        "chunk_duration_ms": cadence_ms * max_evaluations_per_chunk,
        "max_evaluations_per_chunk": max_evaluations_per_chunk,
        "source_scan_id": str(watchlist.get("source_scan_id") or ""),
        "inclusion_operator": str(watchlist.get("inclusion_operator") or "all"),
        "inclusion_rule_sets": [str(value) for value in watchlist.get("inclusion_rule_sets") or []],
        "exclusion_rule_sets": [str(value) for value in watchlist.get("exclusion_rule_sets") or []],
        "rule_sets": selected_rules,
        "ranking_field": str(watchlist.get("ranking_field") or ""),
        "ranking_direction": str(watchlist.get("ranking_direction") or "descending"),
        "maximum_size": maximum_size,
        "focused_seed_multiplier": FOCUSED_SEED_MULTIPLIER,
        "membership_expiry": str(watchlist.get("membership_expiry") or "end_of_trading_day"),
        "membership_ttl_ms": max(0, int(watchlist.get("membership_ttl_ms") or 0)),
        "manual_inclusions": sorted({str(value).strip().upper() for value in watchlist.get("manual_inclusions") or [] if str(value).strip()}),
        "manual_exclusions": sorted({str(value).strip().upper() for value in watchlist.get("manual_exclusions") or [] if str(value).strip()}),
        "qmd_sources": sorted(
            source
            for source in sources
            if _base_source_id(source) in QMD_SOURCE_IDS
        ),
        "qmd_source_specs": qmd_source_specs,
        "external_features": external_features,
        "output_mode": "initial_membership_then_transition_deltas",
        "state_carry_required": True,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return {**body, "plan_hash": f"sha256:{hashlib.sha256(encoded).hexdigest()}"}


def compile_signal_stream_recovery_templates(
    configuration: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Compile QMD History catch-up plans from materialized Signal Streams.

    Event-native streams retain their owning durable source. Rule-evaluated
    Core Scan streams reuse the historical Watchlist timeline engine because
    it already owns causal Data Field projection, Rule Set evaluation, source
    completeness, and transition state.
    """

    discovery = dict(configuration.get("market_discovery") or {})
    column_by_id = {
        str(row.get("column_id") or ""): deepcopy(row)
        for row in discovery.get("column_catalog") or []
        if str(row.get("column_id") or "")
    }
    templates: list[dict[str, Any]] = []
    for raw_stream in discovery.get("signal_streams") or []:
        stream = deepcopy(raw_stream)
        stream_id = str(stream.get("signal_stream_id") or "").strip()
        if not stream_id or not bool(stream.get("enabled", True)):
            continue
        occurrence_source = str(stream.get("occurrence_source") or "").strip()
        source_type = str(stream.get("source_type") or "core_scan").strip()
        if occurrence_source or source_type != "core_scan":
            templates.append({
                "signal_stream_id": stream_id,
                "recovery_kind": "source_native",
                "source_type": source_type,
                "occurrence_source": occurrence_source,
            })
            continue

        synthetic_id = f"signal-recovery:{stream_id}"
        synthetic = {
            "watchlist_id": synthetic_id,
            "enabled": True,
            "source_scan_id": str(stream.get("source_scan_id") or "qmd-core-scan"),
            "inclusion_rule_sets": list(stream.get("inclusion_rule_sets") or []),
            "inclusion_operator": str(stream.get("inclusion_operator") or "all"),
            "exclusion_rule_sets": [],
            "ranking_field": "market.liquidity_rank",
            "ranking_direction": "descending",
            "maximum_size": 5_000,
            "refresh_interval_ms": max(100, int(stream.get("refresh_interval_ms") or 1_000)),
            "membership_expiry": "end_of_trading_day",
            "membership_ttl_ms": 0,
            "manual_inclusions": [],
            "manual_exclusions": [],
        }
        scoped = deepcopy(configuration)
        scoped_discovery = dict(scoped.get("market_discovery") or {})
        scoped_discovery["watchlists"] = [synthetic]
        scoped["market_discovery"] = scoped_discovery
        try:
            plan = compile_historical_watchlist_plan(
                scoped, synthetic_id, start=start, end=end
            )
            _add_signal_projection_sources(plan, stream, column_by_id)
            plan["output_mode"] = "signal_transitions_only"
            plan["plan_hash"] = _plan_hash(plan)
            templates.append({
                "signal_stream_id": stream_id,
                "recovery_kind": "qmd_history_timeline",
                "source_type": source_type,
                "plan": plan,
                "external_feature_revisions": [],
                "external_feature_intervals": [],
            })
        except ValueError as exc:
            templates.append({
                "signal_stream_id": stream_id,
                "recovery_kind": "coverage_unavailable",
                "source_type": source_type,
                "reason": str(exc),
            })
    return templates


def _add_signal_projection_sources(
    plan: dict[str, Any],
    stream: dict[str, Any],
    column_by_id: dict[str, dict[str, Any]],
) -> None:
    """Include reconstructable trigger-time presentation fields in evidence."""

    sources = set(str(value) for value in plan.get("qmd_sources") or [])
    specs = {
        str(row.get("instance_id") or ""): dict(row)
        for row in plan.get("qmd_source_specs") or []
        if str(row.get("instance_id") or "")
    }
    intervals = dict(stream.get("column_intervals") or {})
    aggregations = dict(stream.get("column_aggregations") or {})
    for column_id in stream.get("columns") or []:
        column = column_by_id.get(str(column_id), {})
        source_id = str(column.get("source_id") or "")
        if source_id not in QMD_SOURCE_IDS:
            continue
        interval = interval_expression(intervals.get(str(column_id)))
        aggregation = normalize_aggregation_function(aggregations.get(str(column_id)))
        spec = _qmd_source_spec(source_id, interval, aggregation)
        sources.add(spec["instance_id"])
        specs[spec["instance_id"]] = spec
    plan["qmd_sources"] = sorted(sources)
    plan["qmd_source_specs"] = [specs[key] for key in sorted(specs)]


def _plan_hash(plan: dict[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_hash"}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


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


def _validated_sources(
    watchlist: dict[str, Any], rules: list[dict[str, Any]]
) -> tuple[set[str], list[dict[str, str]]]:
    sources = {str(watchlist.get("ranking_field") or "")}
    specs: dict[str, dict[str, str]] = {}
    ranking_source = str(watchlist.get("ranking_field") or "")
    if ranking_source:
        specs[ranking_source] = _qmd_source_spec(ranking_source, "")
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
            if str(condition.get("left_value_selection") or "latest") != "latest":
                raise ValueError("historical Watchlist condition supports only latest left value selection")
            left_interval = interval_expression(condition.get("left_interval"))
            left_aggregation = normalize_aggregation_function(
                condition.get("left_aggregation")
            )
            left_instance = field_instance_ref(left, left_interval, left_aggregation)
            condition["left_instance_id"] = left_instance
            sources.add(left_instance)
            specs[left_instance] = _qmd_source_spec(
                left, left_interval, left_aggregation
            )
            if right:
                if str(condition.get("right_value_selection") or "latest") != "latest":
                    raise ValueError("historical Watchlist condition supports only latest right value selection")
                right_interval = interval_expression(condition.get("right_interval"))
                right_aggregation = normalize_aggregation_function(
                    condition.get("right_aggregation")
                )
                right_instance = field_instance_ref(
                    right, right_interval, right_aggregation
                )
                condition["right_instance_id"] = right_instance
                sources.add(right_instance)
                specs[right_instance] = _qmd_source_spec(
                    right, right_interval, right_aggregation
                )
    sources.discard("")
    unknown = sorted(
        source
        for source in sources
        if _base_source_id(source) not in SOURCE_FIELDS
        and _base_source_id(source) not in QMD_SOURCE_IDS
    )
    if unknown:
        raise ValueError(f"historical Watchlist uses unsupported sources: {', '.join(unknown)}")
    qmd_specs = [
        specs[source]
        for source in sorted(sources)
        if _base_source_id(source) in QMD_SOURCE_IDS
    ]
    return sources, qmd_specs


def _source_instance(source_id: str, interval: str) -> str:
    return f"{source_id}@@{interval}" if interval else source_id


def _base_source_id(instance_id: str) -> str:
    return str(instance_id).split("@@", 1)[0].split("##", 1)[0]


def _qmd_source_spec(
    source_id: str, interval: str, aggregation: str = ""
) -> dict[str, str]:
    field = next((row for row in FIELD_DEFINITIONS if row.field_id == source_id), None)
    runtime_fields = dict(field.aggregation_runtime_fields) if field is not None else {}
    if aggregation:
        runtime_field = runtime_fields.get(aggregation)
        if not runtime_field:
            raise ValueError(
                f"historical Watchlist source does not support aggregation "
                f"{source_id}##{aggregation}"
            )
    else:
        runtime_field = SOURCE_FIELDS.get(source_id, source_id)
    return {
        "instance_id": field_instance_ref(source_id, interval, aggregation),
        "source_id": source_id,
        "runtime_field": runtime_field,
        "interval": interval,
        "aggregation": aggregation,
    }
