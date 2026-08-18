from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Iterable

from src.backend.application_registry import DISCOVERY_RUNTIME_FIELDS, FIELD_DEFINITIONS, _presentation_value_type


DATA_FIELD_CONTRACT_VERSION = 5

AGGREGATION_FUNCTIONS = {
    "first", "last", "min", "max", "sum", "mean", "median", "count",
    "volume_weighted_mean",
}
_BAR_INTRINSIC_AGGREGATIONS = {
    "open": "first", "high": "max", "low": "min", "close": "last",
    "volume": "sum", "dollar_volume": "sum", "trade_count": "count",
    "vwap": "volume_weighted_mean", "avg_trade_size": "mean",
    "median_trade_size": "median", "max_trade_size": "max",
    "quote_count": "count", "bid_open": "first", "bid_high": "max",
    "bid_low": "min", "bid_close": "last", "ask_open": "first",
    "ask_high": "max", "ask_low": "min", "ask_close": "last",
    "mid_open": "first", "mid_high": "max", "mid_low": "min",
    "mid_close": "last", "spread_open": "first", "spread_high": "max",
    "spread_low": "min", "spread_close": "last", "spread_mean": "mean",
    "quoted_bid_size_mean": "mean", "quoted_ask_size_mean": "mean",
}

INTERVAL_UNIT_SUFFIXES = {
    "milliseconds": "ms",
    "seconds": "s",
    "minutes": "m",
    "hours": "h",
    "days": "d",
    "weeks": "w",
    "months": "mo",
}
_INTERVAL_SUFFIX_UNITS = {suffix: unit for unit, suffix in INTERVAL_UNIT_SUFFIXES.items()}

_NON_INTERVAL_CONTEXTS = {
    "event",
    "session",
    "filing",
    "settlement",
    "scanner_clock",
    "evaluation",
}
_REFERENCE_SEMANTICS = {"clock", "event", "reference", "system"}
_FIXED_WINDOWS = {
    "market.trade_rate_10s": "10s",
    "market.trade_rate_60s": "60s",
}
_EXECUTION_INTERVAL_DEFAULTS = {
    "indicator.vwap.value": ["1s"],
}


def field_output_ref(data_field_id: str, revision: int, output_id: str) -> str:
    return f"{data_field_id}@{max(1, int(revision))}:{output_id}"


def normalize_interval_spec(value: Any) -> dict[str, Any] | None:
    """Normalize a saved interval to the user-facing value/unit contract.

    Legacy compact strings remain readable, but persisted configuration uses
    this structured form. The compact value is an execution expression only.
    """

    if value in (None, ""):
        return None
    if isinstance(value, dict):
        unit = str(value.get("unit") or "").strip().lower()
        try:
            count = int(value.get("value"))
        except (TypeError, ValueError):
            return None
        if unit in INTERVAL_UNIT_SUFFIXES and count > 0:
            return {"value": count, "unit": unit}
        return None
    match = re.fullmatch(r"([1-9]\d*)(ms|s|m|h|d|w|mo)", str(value).strip().lower())
    if not match:
        return None
    return {"value": int(match.group(1)), "unit": _INTERVAL_SUFFIX_UNITS[match.group(2)]}


def interval_expression(value: Any) -> str:
    interval = normalize_interval_spec(value)
    if interval is None:
        return ""
    return f"{interval['value']}{INTERVAL_UNIT_SUFFIXES[interval['unit']]}"


def normalize_aggregation_function(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in AGGREGATION_FUNCTIONS else ""


def field_instance_ref(field_ref: str, interval: Any = "", aggregation: Any = "") -> str:
    """Identify a configured use of a Data Field without redefining the field."""

    expression = interval_expression(interval)
    aggregation_id = normalize_aggregation_function(aggregation)
    instance = f"{field_ref}@@{expression}" if expression else field_ref
    return f"{instance}##{aggregation_id}" if aggregation_id else instance


def atomic_field_catalog(extra_inputs: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Return every source-owned application field plus declared producer inputs.

    Atomic means source-authoritative at the application boundary. A gateway may
    normalize a wire value or publish canonical market-clock state internally;
    users still consume it as a non-configurable Atomic Field.
    """

    rows: dict[str, dict[str, Any]] = {}
    for field in FIELD_DEFINITIONS:
        if field.provenance in {"derived", "model"}:
            continue
        rows[field.field_id] = {
            "atomic_field_id": field.field_id,
            "schema_version": field.schema_version,
            "name": field.presentation_label or field.label,
            "description": field.source_summary or f"Source-owned {field.group} observation.",
            "source_summary": field.source_summary,
            "calculation_summary": field.calculation_summary,
            "group": field.group,
            "owner": field.owner,
            "source_path": field.source_path,
            "query_plan_id": field.query_plan_id,
            "value_type": field.value_type,
            "presentation_value_type": field.presentation_value_type,
            "unit": field.unit,
            "entity_grain": field.entity_grain,
            "event_at": field.event_at,
            "available_at": field.available_at,
            "update_cadence": field.publication_cadence,
            "historical_support": field.historical_support,
            "modes": list(field.modes),
            "freshness_policy": field.freshness_policy,
            "null_reasons": list(field.null_reasons),
            "source_columns": list(field.source_columns),
            "known_values": [
                {"value": value, "label": label, "description": description}
                for value, label, description in field.known_values
            ],
            "status": field.status,
            "configurable": False,
            "provenance": field.provenance,
        }
    for raw in extra_inputs:
        atomic_field_id = str(raw).strip()
        if (
            not atomic_field_id
            or atomic_field_id in rows
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", atomic_field_id) is None
        ):
            continue
        rows[atomic_field_id] = {
            "atomic_field_id": atomic_field_id,
            "schema_version": 1,
            "name": _readable(atomic_field_id),
            "description": "Producer-declared atomic input. Exact source semantics remain owned by its registered producer.",
            "group": "producer_input",
            "owner": "qmd_gateway",
            "source_path": "qmd://definition-catalog",
            "query_plan_id": "qmd.runtime-capability-catalog",
            "value_type": "producer_defined",
            "unit": "producer_defined",
            "entity_grain": "producer_defined",
            "event_at": "producer event clock",
            "available_at": "QMD acceptance clock",
            "update_cadence": "producer cadence",
            "historical_support": "producer_declared",
            "modes": ["live", "paper", "replay", "backtest", "backtest_debug"],
            "freshness_policy": "producer declared",
            "null_reasons": ["source_unavailable", "not_observed", "outside_coverage"],
            "status": "implemented",
            "configurable": False,
            "provenance": "source",
            "source_summary": "Producer-declared source input.",
            "calculation_summary": "No application-side calculation is registered.",
            "source_columns": [atomic_field_id],
            "known_values": [],
        }
    return [rows[key] for key in sorted(rows)]


def _field_dimension_instances(
    source_id: str,
    field: dict[str, Any],
    calculation: dict[str, Any],
    supported_intervals: list[str],
) -> list[dict[str, Any]]:
    """Return only dimensions that change the meaning of this exact field.

    Producer scheduling is intentionally excluded. A capability may publish at
    several cadences without making a current price, session total, reference
    value, or event observation interval-dependent.
    """

    declared = [str(value) for value in field.get("timeframes") or [] if str(value)]
    semantic_type = str(field.get("semantic_type") or calculation.get("capability_type") or "").lower()
    dimensions: dict[str, Any] = {"dimension_kind": "point_in_time"}
    encoded_window = _FIXED_WINDOWS.get(source_id)
    if not encoded_window:
        match = re.search(r"(?:^|[_.])(\d+)(ms|s|m|h|d)$", source_id.lower())
        encoded_window = "".join(match.groups()) if match else ""
    if encoded_window:
        dimensions.update({
            "dimension_kind": "rolling_window",
            "window": encoded_window,
            "window_configurable": False,
        })
        return [dimensions]
    if "session" in declared:
        dimensions.update({"dimension_kind": "anchored", "anchor": "market_session"})
        return [dimensions]
    if "filing" in declared:
        dimensions.update({"dimension_kind": "as_of", "as_of": "latest_available_filing"})
        return [dimensions]
    if "settlement" in declared:
        dimensions.update({"dimension_kind": "as_of", "as_of": "latest_available_settlement"})
        return [dimensions]
    if "event" in declared or semantic_type in {"clock", "event"}:
        dimensions.update({"dimension_kind": "as_of", "as_of": "evaluation_clock"})
        return [dimensions]
    if semantic_type in _REFERENCE_SEMANTICS:
        dimensions.update({"dimension_kind": "as_of", "as_of": "latest_available_publication"})
        return [dimensions]
    intervals = list(dict.fromkeys(supported_intervals or [
        value for value in declared if value not in _NON_INTERVAL_CONTEXTS
    ]))
    if source_id in {"vwap", "dollar_volume"}:
        intervals = [value for value in intervals if value not in {"1d", "1w", "1mo", "1y"}]
    if intervals:
        interval_semantics = str(field.get("interval_semantics") or "")
        aggregation_functions = [
            str(value) for value in field.get("aggregation_functions") or []
            if str(value) in AGGREGATION_FUNCTIONS
        ]
        intrinsic = str(field.get("intrinsic_aggregation") or "")
        if not interval_semantics and source_id in _BAR_INTRINSIC_AGGREGATIONS:
            interval_semantics = "bar_timeframe"
            intrinsic = _BAR_INTRINSIC_AGGREGATIONS[source_id]
        aggregation = (
            {
                "mode": "required",
                "allowed": aggregation_functions,
                "default": str(field.get("default_aggregation") or aggregation_functions[0]),
            }
            if interval_semantics == "event_window" and aggregation_functions
            else {"mode": "intrinsic", "function": intrinsic}
            if interval_semantics == "bar_timeframe" and intrinsic
            else {"mode": "none"}
        )
        return [{
            "dimension_kind": "interval",
            "available_intervals": intervals,
            "interval_required_when_used": True,
            "interval_semantics": interval_semantics or "bar_timeframe",
            "aggregation": aggregation,
        }]
    return [dimensions]


def _preferred_producer_intervals(values: list[str]) -> list[str]:
    """Select one execution interval without turning it into field meaning."""

    unique = list(dict.fromkeys(value for value in values if value))
    for preferred in ("1s", "100ms", "10s", "1m"):
        if preferred in unique:
            return [preferred]
    return unique[:1]


def _enrich_field_metadata(source_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Fill mechanical QMD output metadata without overriding registered fields."""

    field = dict(raw)
    normalized = source_id.lower()
    names = {
        "open": "Open price",
        "high": "High price",
        "low": "Low price",
        "close": "Close price",
        "vwap": "Interval VWAP",
        "volume": "Interval volume",
        "dollar_volume": "Dollar volume",
        "trade_count": "Trade count",
        "price_change": "Bar price change",
        "price_change_pct": "Bar price change %",
        "high_low_range_pct": "High-low range",
    }
    descriptions = {
        "price_change": "Close minus open inside the selected bar timeframe.",
        "price_change_pct": "Close minus open inside the selected bar timeframe, divided by the absolute open and expressed as a percentage.",
        "high_low_range_pct": "High-to-low price range within the selected interval, expressed as a percentage.",
        "volume": "Eligible share volume accumulated inside the selected interval.",
        "close": "Last eligible trade price in the selected completed or developing interval.",
    }
    if source_id in names:
        field.setdefault("name", names[source_id])
    if source_id in descriptions:
        field.setdefault("description", descriptions[source_id])
    if source_id.startswith("return_"):
        bars = source_id.removeprefix("return_").removesuffix("_bar")
        field.setdefault("name", f"Price change % vs {bars} bar")
        field.setdefault("description", f"Close versus the close {bars} completed bar(s) earlier, expressed as a percentage of the absolute comparison close.")
        field.setdefault("formula", "100 * (current_close - comparison_close) / abs(comparison_close); null when the comparison close is missing or zero")
    elif source_id.startswith("price_change_") and source_id.endswith("_bar_pct"):
        bars = source_id.removeprefix("price_change_").removesuffix("_bar_pct")
        field.setdefault("name", f"Price change % vs {bars} bar")
        field.setdefault("description", f"Close versus the close {bars} completed bar(s) earlier, expressed as a percentage of the absolute comparison close.")
        field.setdefault("formula", "100 * (current_close - comparison_close) / abs(comparison_close); null when the comparison close is missing or zero")
    elif source_id.startswith("price_change_") and source_id.endswith("_bar"):
        bars = source_id.removeprefix("price_change_").removesuffix("_bar")
        field.setdefault("name", f"Price change vs {bars} bar")
        field.setdefault("description", f"Close minus the close {bars} completed bar(s) earlier.")
        field.setdefault("formula", "current_close - comparison_close; null until the comparison bar exists")
    elif source_id.startswith("price_ratio_"):
        bars = source_id.removeprefix("price_ratio_").removesuffix("_bar")
        field.setdefault("name", f"Price ratio vs {bars} bar")
        field.setdefault("description", f"Close divided by the close {bars} completed bar(s) earlier.")
        field.setdefault("formula", "current_close / comparison_close; null when the comparison close is missing or zero")
    elif source_id.endswith("_change_pct"):
        subject = source_id.removesuffix("_change_pct").replace("_", " ")
        field.setdefault("name", f"{subject.title()} change %")
        field.setdefault("description", f"Current {subject} minus its previous completed-bar value, divided by the absolute previous value and expressed as a percentage.")
        field.setdefault("formula", "100 * (current - previous_bar) / abs(previous_bar); null when the previous value is missing or zero")
    elif source_id.endswith("_change"):
        subject = source_id.removesuffix("_change").replace("_", " ")
        field.setdefault("name", f"{subject.title()} change")
        field.setdefault("description", f"Current {subject} minus its previous completed-bar value.")
        field.setdefault("formula", "current - previous_bar; null until the previous bar exists")
    elif source_id.endswith("_ratio"):
        subject = source_id.removesuffix("_ratio").replace("_", " ")
        field.setdefault("name", f"{subject.title()} ratio")
        field.setdefault("description", f"Current {subject} divided by its previous completed-bar value.")
        field.setdefault("formula", "current / previous_bar; null when the previous value is missing or zero")
    if normalized.endswith("_pct") or normalized.endswith(".pct") or source_id.startswith("return_"):
        field.setdefault("unit", "percent")
        field.setdefault("value_type", "number")
    elif normalized.endswith("_ratio") or normalized.startswith("price_ratio_"):
        field.setdefault("unit", "multiple")
        field.setdefault("value_type", "number")
    elif "spread_bps" in normalized or normalized.endswith("_bps"):
        field.setdefault("unit", "basis_points")
        field.setdefault("value_type", "number")
    elif source_id in {"open", "high", "low", "close", "vwap", "price_change", "vwap_change", "spread_close_change"} or normalized.startswith("price_change_") or re.search(
        r"(?:^|_)(?:bid|ask|mid)_(?:open|high|low|close)$", normalized
    ):
        field.setdefault("unit", "currency")
        field.setdefault("value_type", "number")
    elif normalized.startswith("dollar_volume") or normalized.endswith("_notional"):
        field.setdefault("unit", "currency")
        field.setdefault("value_type", "number")
    elif normalized.startswith(("trade_count", "quote_count", "trade_rate", "quote_rate")):
        field.setdefault("unit", "count")
        field.setdefault("value_type", "number")
    elif normalized.endswith("volume") or "volume_" in normalized or normalized.startswith("avg_trade_size"):
        field.setdefault("unit", "shares")
        field.setdefault("value_type", "number")
    elif normalized.endswith("count") or "_count_" in normalized:
        field.setdefault("unit", "count")
        field.setdefault("value_type", "integer")
    return field


def _source_contract(field: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    capability_key = str(calculation.get("capability_key") or calculation.get("capability_id") or "registered-output")
    return {
        "owner": str(calculation.get("owner") or calculation.get("provider") or field.get("source") or "unknown"),
        "location": str(field.get("source_path") or calculation.get("source_path") or f"qmd://{capability_key}"),
        "query_plan_id": str(field.get("query_plan_id") or calculation.get("query_plan_id") or "qmd.runtime-capability-catalog"),
        "source_fields": [
            str(value)
            for value in field.get("source_columns") or field.get("input_field_ids") or calculation.get("inputs") or []
            if str(value)
        ],
        "summary": str(
            field.get("source_summary")
            or calculation.get("source_summary")
            or calculation.get("provider")
            or "Registered producer source."
        ),
        "available_at": str(field.get("available_at") or calculation.get("available_at") or "producer publication clock"),
    }


def _calculation_contract(field: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    provenance = str(field.get("provenance") or "computed")
    summary = str(
        field.get("calculation_summary")
        or calculation.get("calculation")
        or calculation.get("description")
        or "The exact producer operation is not registered."
    )
    return {
        "kind": "source_read" if provenance in {"raw", "reported"} else "producer_output" if str(calculation.get("catalog_authority") or "") == "qmd_runtime_catalog" else "derivation",
        "summary": summary,
        "formula": str(field.get("formula") or ""),
        "documentation_status": "partial" if "not registered" in summary.lower() or "not yet" in summary.lower() else "complete",
    }


def build_data_field_catalog(
    calculation_rows: list[dict[str, Any]],
    field_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build user-facing configurable Data Fields and exact output identities."""

    fields_by_source = {
        str(row.get("source_id") or row.get("field_id") or ""): dict(row)
        for row in field_catalog
        if str(row.get("source_id") or row.get("field_id") or "")
    }
    covered: set[str] = set()
    emitted: set[str] = set()
    result: list[dict[str, Any]] = []
    for calculation in calculation_rows:
        capability_id = str(calculation.get("capability_id") or "").strip()
        if (
            not capability_id
            or str(calculation.get("output_type") or "").lower() == "system"
            or str(calculation.get("capability_type") or "").lower() == "system"
        ):
            continue
        sources = [
            str(value)
            for value in calculation.get("fields") or []
            if str(value) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", str(value))
        ]
        if not sources:
            sources = [capability_id]
        revision = int(calculation.get("implementation_version") or 1)
        supported_intervals = [
            str(value)
            for value in calculation.get("selected_timeframes") or calculation.get("timeframes") or []
            if str(value) and str(value) not in _NON_INTERVAL_CONTEXTS
        ]
        for source_id in dict.fromkeys(sources):
            field = _enrich_field_metadata(source_id, fields_by_source.get(source_id, {}))
            dimensions = _field_dimension_instances(
                source_id, field, calculation, supported_intervals
            )
            for dimension in dimensions:
                if source_id in emitted:
                    continue
                emitted.add(source_id)
                covered.add(source_id)
                data_field_id = f"data.{source_id}"
                output = _data_field_output(
                    data_field_id,
                    revision,
                    source_id,
                    field,
                    output_id="value",
                )
                field_name = str(field.get("name") or _readable(source_id))
                result.append({
                    "data_field_id": data_field_id,
                    "revision": revision,
                    "name": field_name,
                    "description": str(
                        field.get("description")
                        or calculation.get("calculation")
                        or calculation.get("description")
                        or "Registered calculation."
                    ),
                    "category": str(field.get("semantic_type") or calculation.get("category") or calculation.get("capability_type") or "Data Field"),
                    "recipe_id": str(calculation.get("capability_key") or capability_id),
                    "recipe_version": revision,
                    "owner": str(calculation.get("owner") or calculation.get("provider") or "qmd_gateway"),
                    "inputs": [
                        str(value)
                        for value in field.get("input_field_ids") or calculation.get("inputs") or []
                        if str(value)
                    ],
                    "source": _source_contract(field, calculation),
                    "calculation": _calculation_contract(field, calculation),
                    "known_values": list(field.get("known_values") or []),
                    "context": {
                        **dimension,
                        "available_intervals": list(dimension.get("available_intervals") or []),
                        "update_cadence": str(calculation.get("cadence") or "producer cadence"),
                        "execution_scope": str(calculation.get("execution_scope") or calculation.get("tier") or "focused"),
                        "allowed_scopes": [str(value) for value in calculation.get("allowed_scopes") or [] if str(value)],
                    },
                    "execution": {
                        "producer_intervals": [] if dimension.get("dimension_kind") == "interval" else _preferred_producer_intervals(supported_intervals or _EXECUTION_INTERVAL_DEFAULTS.get(source_id, [])),
                        "market_discovery_supported": True,
                        "aggregation_runtime_fields": dict(field.get("aggregation_runtime_fields") or {}),
                    },
                    "parameters": ({"interval": {"required": True, "allowed": list(dimension.get("available_intervals") or [])}, "aggregation": dict(dimension.get("aggregation") or {})} if dimension.get("dimension_kind") == "interval" else {}),
                    "policies": {
                        "warm_up_bars": calculation.get("warm_up_bars"),
                        "missing": "unavailable",
                        "gaps": "preserve",
                        "late_events": "producer_watermark",
                    },
                    "outputs": [output],
                    "enabled": bool(calculation.get("enabled", True)),
                    "configurable": bool(calculation.get("configurable")),
                    "system_required": bool(calculation.get("system_required")),
                    "implementation_status": str(calculation.get("implementation_status") or calculation.get("availability") or "unknown"),
                    "live_support": str(calculation.get("implementation_status") or calculation.get("availability") or "unknown") not in {"offline_only", "planned"},
                    "historical_support": True,
                    "cost_class": str(calculation.get("cost_class") or "unknown"),
                    "stateful": bool(calculation.get("stateful")),
                    "contract_version": DATA_FIELD_CONTRACT_VERSION,
                })

    # Every registered field remains usable even when it is a direct source
    # projection rather than an output of a multi-output QMD capability.
    for source_id, field in sorted(fields_by_source.items()):
        if source_id in covered:
            continue
        field = _enrich_field_metadata(source_id, field)
        dimensions = _field_dimension_instances(source_id, field, {}, [])
        for dimension in dimensions:
            data_field_id = f"data.{source_id}"
            field_name = str(field.get("name") or _readable(source_id))
            result.append({
                "data_field_id": data_field_id,
                "revision": 1,
                "name": field_name,
                "description": str(field.get("description") or f"Direct projection of {source_id}."),
                "category": str(field.get("semantic_type") or "Projection"),
                "recipe_id": "registered_projection",
                "recipe_version": 1,
                "owner": str(field.get("source") or "application_registry"),
                "inputs": [
                    str(value)
                    for value in field.get("input_field_ids") or [field.get("field_id") or source_id]
                    if str(value)
                ],
                "source": _source_contract(field, {}),
                "calculation": _calculation_contract(field, {}),
                "known_values": list(field.get("known_values") or []),
                "context": {
                    **dimension,
                    "available_intervals": list(dimension.get("available_intervals") or []),
                    "update_cadence": "source cadence",
                    "execution_scope": "consumer_selected",
                    "allowed_scopes": ["core_scan", "watchlist", "signal_stream", "strategy_run", "request", "offline"],
                },
                "execution": {
                    "producer_intervals": [],
                    "market_discovery_supported": bool(field.get("market_discovery_supported")),
                    "aggregation_runtime_fields": dict(field.get("aggregation_runtime_fields") or {}),
                },
                "parameters": ({"interval": {"required": True, "allowed": list(dimension.get("available_intervals") or [])}, "aggregation": dict(dimension.get("aggregation") or {})} if dimension.get("dimension_kind") == "interval" else {}),
                "policies": {"missing": "unavailable", "gaps": "preserve", "late_events": "source_policy"},
                "outputs": [_data_field_output(
                    data_field_id,
                    1,
                    source_id,
                    field,
                    output_id="value",
                )],
                "enabled": bool(field.get("market_discovery_supported")) and str(field.get("implementation_status") or "implemented") in {"implemented", "live_only"},
                "configurable": False,
                "system_required": False,
                "implementation_status": str(field.get("implementation_status") or "implemented"),
                "live_support": str(field.get("implementation_status") or "implemented") != "offline_only",
                "historical_support": str(field.get("implementation_status") or "implemented") != "live_only",
                "cost_class": "projection",
                "stateful": False,
                "contract_version": DATA_FIELD_CONTRACT_VERSION,
            })
    validate_data_field_catalog(result)
    return sorted(result, key=lambda row: str(row["data_field_id"]))


def data_field_output_index(data_fields: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for data_field in data_fields:
        context = dict(data_field.get("context") or {})
        execution = dict(data_field.get("execution") or {})
        for output in data_field.get("outputs") or []:
            indexed = {
                **dict(output),
                "dimension_kind": str(context.get("dimension_kind") or "point_in_time"),
                "available_intervals": list(context.get("available_intervals") or []),
                "interval_semantics": str(context.get("interval_semantics") or ""),
                "aggregation": dict(context.get("aggregation") or {}),
                "aggregation_runtime_fields": dict(execution.get("aggregation_runtime_fields") or {}),
            }
            field_ref = str(output.get("field_ref") or "")
            source_id = str(output.get("source_id") or "")
            if field_ref:
                index[field_ref] = indexed
            if source_id:
                index.setdefault(source_id, indexed)
    return index


def project_data_field_outputs(
    rows: Iterable[dict[str, Any]],
    data_fields: Iterable[dict[str, Any]],
    *,
    field_refs: Iterable[str] | None = None,
    field_instances: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach exact Data Field output identities to producer rows.

    This is a projection only; multi-row calculations remain owned by QMD Live
    or the historical vectorized executor.
    """

    selected_refs = (
        {str(value) for value in field_refs if str(value)}
        if field_refs is not None
        else None
    )
    selected_instances: dict[str, set[tuple[str, str]]] | None = None
    if field_instances is not None:
        selected_instances = {}
        for instance in field_instances:
            field_ref = str(instance.get("field_ref") or "")
            if not field_ref:
                continue
            selected_instances.setdefault(field_ref, set()).add(
                (
                    interval_expression(instance.get("interval")),
                    normalize_aggregation_function(instance.get("aggregation")),
                )
            )
    outputs = [
        (dict(output), dict(data_field.get("context") or {}), dict(data_field.get("execution") or {}))
        for data_field in data_fields
        for output in data_field.get("outputs") or []
        if str(output.get("field_ref") or "")
        and (
            selected_refs is None
            or str(output.get("field_ref") or "") in selected_refs
            or str(output.get("source_id") or "") in selected_refs
        )
    ]
    prepared_outputs = []
    for output, context, execution in outputs:
        field_ref = str(output["field_ref"])
        intervals = tuple(str(value) for value in context.get("available_intervals") or [] if str(value))
        aggregation = dict(context.get("aggregation") or {})
        if not intervals:
            requested_instances: tuple[tuple[str, str], ...] = ()
        elif selected_instances is None:
            functions = tuple(aggregation.get("allowed") or ()) if aggregation.get("mode") == "required" else ("",)
            requested_instances = tuple((interval, function) for interval in intervals for function in functions)
        else:
            requested_instances = tuple(sorted(
                (interval, function)
                for interval, function in selected_instances.get(field_ref, set())
                if _interval_uses_supported_unit(interval, intervals)
            ))
        prepared_outputs.append((
            output,
            intervals,
            requested_instances,
            str(aggregation.get("default") or ""),
            dict(execution.get("aggregation_runtime_fields") or {}),
        ))
    projected: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        technical_keys: dict[tuple[str, str], str] = {}
        for key in row:
            if not key.startswith("technical__"):
                continue
            parts = key.split("__", 3)
            if len(parts) >= 3:
                technical_keys.setdefault((parts[1], parts[2]), key)
        for output, intervals, requested_instances, default_function, runtime_fields in prepared_outputs:
            field_ref = str(output["field_ref"])
            runtime_field = str(output.get("runtime_field") or output.get("source_id") or "")
            source_id = str(output.get("source_id") or "")
            observed_interval = str(row.get("indicator_interval") or row.get("indicator_timeframe") or row.get("working_timeframe") or "")
            if intervals:
                found_any = False
                for interval, function in requested_instances:
                    selected_runtime_field = str(runtime_fields.get(function) or runtime_field)
                    value_found, value = _projected_value(
                        row,
                        selected_runtime_field,
                        selected_runtime_field if function else source_id,
                        interval,
                        allow_generic=bool(observed_interval),
                        technical_keys=technical_keys,
                    )
                    if observed_interval and observed_interval != interval:
                        value_found, value = False, None
                    instance_ref = field_instance_ref(field_ref, interval, function)
                    # Indicator snapshots are fetched independently per interval
                    # and merged afterwards. A producer that does not own this
                    # interval must not publish a null placeholder: doing so
                    # erases a value materialized by an earlier interval.
                    if value_found:
                        result[instance_ref] = value
                    if not value_found and instance_ref not in result:
                        result[f"{instance_ref}__null_reason"] = "producer_output_missing"
                    found_any = found_any or value_found or result.get(instance_ref) is not None
                if observed_interval and _interval_uses_supported_unit(observed_interval, intervals):
                    result[field_ref] = result.get(field_instance_ref(field_ref, observed_interval, default_function))
                value_found = found_any
                value = result.get(field_ref)
            else:
                value_found, value = _projected_value(row, runtime_field, source_id)
                if value_found:
                    result[field_ref] = value
            # Canvas tables address configured presentations by column id while
            # rules address the immutable Data Field output reference. Publish
            # both names from the same resolved value so presentation never has
            # to reconstruct a producer-specific runtime key.
            for presentation in output.get("column_presentations") or []:
                presentation_id = str(presentation.get("presentation_id") or "")
                if presentation_id and not intervals and value_found:
                    result[presentation_id] = value
            if not value_found:
                result[f"{field_ref}__null_reason"] = "producer_output_missing"
            for suffix in ("_available_at", "_published_at", "_source_date", "_null_reason"):
                key = f"{runtime_field}{suffix}"
                if key in row:
                    result[f"{field_ref}__{suffix.removeprefix('_')}"] = row.get(key)
        projected.append(result)
    return projected


def project_composition_data_field_columns(
    rows: Iterable[dict[str, Any]],
    composition: dict[str, Any],
    column_catalog: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize a composition's instantiated fields into stable Canvas columns."""

    columns = {
        str(row.get("column_id") or ""): row
        for row in column_catalog
        if str(row.get("column_id") or "")
    }
    bindings = {
        str(key): interval_expression(value)
        for key, value in dict(composition.get("column_intervals") or {}).items()
        if str(key) and str(value)
    }
    aggregations = {
        str(key): normalize_aggregation_function(value)
        for key, value in dict(composition.get("column_aggregations") or {}).items()
        if str(key) and str(value)
    }
    projected: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        for column_id in composition.get("columns") or []:
            column = columns.get(str(column_id), {})
            field_ref = str(column.get("field_ref") or "")
            if not field_ref:
                continue
            interval = bindings.get(str(column_id), "")
            value_ref = field_instance_ref(field_ref, interval, aggregations.get(str(column_id), ""))
            # Some canonical presentation IDs are supplied directly by the
            # producer. Retain them when an optional Data Field output is absent.
            if value_ref in row:
                result[str(column_id)] = row.get(value_ref)
            null_reason = row.get(f"{value_ref}__null_reason")
            if null_reason:
                result[f"{column_id}__null_reason"] = null_reason
        projected.append(result)
    return projected


def migrate_rule_set_field_refs(
    rule_sets: list[dict[str, Any]],
    data_fields: list[dict[str, Any]],
    *,
    legacy_data_fields: list[dict[str, Any]] | None = None,
) -> None:
    index = data_field_output_index(data_fields)
    legacy_index = data_field_output_index(legacy_data_fields or [])
    for rule_set in rule_sets:
        rule_set.pop("atomic", None)
        rule_set.setdefault("origin", "system")
        rule_set.setdefault("protected", str(rule_set.get("origin")) == "system")
        rule_set.setdefault("editable", str(rule_set.get("origin")) != "system")
        for condition in rule_set.get("conditions") or []:
            for side in ("left", "right"):
                legacy_id = str(condition.get(f"{side}_source_id") or "")
                existing_ref = str(condition.get(f"{side}_field_ref") or "")
                legacy_timeframe = str(condition.get(f"{side}_timeframe") or "")
                if not legacy_timeframe and existing_ref:
                    legacy_output = legacy_index.get(existing_ref, {})
                    legacy_timeframe = str(
                        legacy_output.get("context_interval")
                        or legacy_output.get("context_timeframe")
                        or ""
                    )
                existing_output = index.get(existing_ref)
                if existing_output is not None and legacy_id and str(existing_output.get("source_id") or "") != legacy_id:
                    existing_output = None
                output = existing_output or index.get(f"{legacy_id}@@{legacy_timeframe}") or index.get(legacy_id)
                if output is not None:
                    condition[f"{side}_field_ref"] = str(output["field_ref"])
                    condition[f"{side}_source_id"] = str(output.get("source_id") or legacy_id)
                    allowed = [str(value) for value in output.get("available_intervals") or []]
                    if legacy_timeframe in allowed:
                        condition[f"{side}_interval"] = normalize_interval_spec(legacy_timeframe)
                    elif allowed and normalize_interval_spec(condition.get(f"{side}_interval")) is None:
                        condition[f"{side}_interval"] = normalize_interval_spec(_preferred_instance_interval(allowed))
                    elif not allowed:
                        condition.pop(f"{side}_interval", None)
                    aggregation = dict(output.get("aggregation") or {})
                    if str(aggregation.get("mode") or "none") == "required":
                        allowed_aggregations = [str(value) for value in aggregation.get("allowed") or []]
                        selected = normalize_aggregation_function(condition.get(f"{side}_aggregation"))
                        condition[f"{side}_aggregation"] = selected if selected in allowed_aggregations else str(aggregation.get("default") or allowed_aggregations[0])
                    else:
                        condition.pop(f"{side}_aggregation", None)
                elif legacy_id:
                    condition[f"{side}_field_ref"] = legacy_id
                condition.pop(f"{side}_timeframe", None)


def build_column_catalog(
    data_fields: list[dict[str, Any]], rule_sets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    seen: set[str] = set()
    for data_field in data_fields:
        for output in data_field.get("outputs") or []:
            for presentation in output.get("column_presentations") or []:
                column_id = str(presentation.get("presentation_id") or "")
                if not column_id or column_id in seen:
                    continue
                seen.add(column_id)
                columns.append({
                    "column_id": column_id,
                    "field_ref": str(output.get("field_ref") or ""),
                    "field_id": str(output.get("field_id") or ""),
                    "source_kind": "data_field",
                    "source_id": str(output.get("source_id") or ""),
                    "name": str(presentation.get("label") or output.get("name") or column_id),
                    "description": str(output.get("description") or data_field.get("description") or "Data Field output."),
                    "value_type": str(output.get("value_type") or "number"),
                    "presentation_value_type": str(output.get("presentation_value_type") or _presentation_value_type(str(output.get("field_id") or output.get("source_id") or column_id), str(output.get("value_type") or "number"), str(output.get("unit") or "scalar"))),
                    "unit": str(output.get("unit") or "scalar"),
                    "default_visible": bool(presentation.get("default_visible")),
                    "filterable": bool(output.get("filterable")),
                    "filter_operators": list(output.get("filter_operators") or []),
                    "sortable": bool(output.get("sortable")),
                    "source": str(data_field.get("owner") or "data_field_registry"),
                    "source_path": f"data-field://{data_field.get('data_field_id')}@{data_field.get('revision')}",
                    "query_plan_id": "data_field.compiled_plan.v1",
                    "provenance": "computed",
                    "available_at": "field output availability clock",
                    "implementation_status": str(data_field.get("implementation_status") or "unknown"),
                    "registry_authority": "data_field_registry",
                    "semantic_type": str(data_field.get("category") or "data_field"),
                    "available_intervals": list(dict(data_field.get("context") or {}).get("available_intervals") or []),
                    "dimensions": deepcopy(dict(data_field.get("context") or {})),
                    "presentation": deepcopy(presentation),
                })
    for rule_set in rule_sets:
        rule_set_id = str(rule_set.get("rule_set_id") or "")
        if not rule_set_id:
            continue
        columns.append({
            "column_id": f"rule_set:{rule_set_id}",
            "field_ref": "",
            "field_id": "",
            "source_kind": "rule_set",
            "source_id": rule_set_id,
            "name": str(rule_set.get("name") or rule_set_id),
            "description": str(rule_set.get("description") or "Boolean Rule Set result."),
            "value_type": "boolean",
            "presentation_value_type": "boolean",
            "unit": "boolean",
            "default_visible": False,
            "filterable": True,
            "filter_operators": ["equals", "is_true"],
            "sortable": True,
            "source": "rule_set_registry",
            "source_path": f"rule-set://{rule_set_id}",
            "query_plan_id": "data_field.compiled_plan.v1",
            "provenance": "derived",
            "available_at": "candidate evaluation clock",
            "implementation_status": "implemented",
            "registry_authority": "rule_set_registry",
            "semantic_type": "rule_set",
            "timeframes": ["evaluation"],
        })
    return columns


def compile_data_field_plan(
    discovery: dict[str, Any], *, composition_ids: Iterable[str] = ()
) -> dict[str, Any]:
    """Compile exact field demand from rules, ranking, columns and evidence."""

    data_fields = list(discovery.get("data_fields") or [])
    output_index = data_field_output_index(data_fields)
    columns = {
        str(row.get("column_id") or ""): row
        for row in discovery.get("column_catalog") or []
    }
    rules = {
        str(row.get("rule_set_id") or ""): row for row in discovery.get("rule_sets") or []
    }
    compositions = [dict(discovery.get("core_scan") or {})]
    compositions.extend(
        dict(row)
        for row in discovery.get("watchlists") or []
        if bool(row.get("enabled", True))
        and str(row.get("availability") or "available") == "available"
    )
    compositions.extend(
        dict(row)
        for row in discovery.get("signal_streams") or []
        if bool(row.get("enabled", True))
    )
    requested_ids = {str(value) for value in composition_ids if str(value)}
    if requested_ids:
        compositions = [
            row
            for row in compositions
            if str(row.get("scan_id") or row.get("watchlist_id") or row.get("signal_stream_id") or "") in requested_ids
        ]
    field_refs: set[str] = set()
    field_instances: set[tuple[str, str, str]] = set()
    rule_ids: set[str] = set()
    for composition in compositions:
        for key in ("inclusion_rule_sets", "exclusion_rule_sets"):
            rule_ids.update(str(value) for value in composition.get(key) or [] if str(value))
        ranking = str(composition.get("ranking_field_ref") or composition.get("ranking_field") or "")
        if ranking:
            output = output_index.get(ranking)
            resolved = str((output or {}).get("field_ref") or ranking)
            field_refs.add(resolved)
            field_instances.add((resolved, interval_expression(composition.get("ranking_interval")), normalize_aggregation_function(composition.get("ranking_aggregation"))))
        column_intervals = dict(composition.get("column_intervals") or {})
        column_aggregations = dict(composition.get("column_aggregations") or {})
        for column_id in composition.get("columns") or []:
            column = columns.get(str(column_id), {})
            if str(column.get("field_ref") or ""):
                resolved = str(column["field_ref"])
                field_refs.add(resolved)
                field_instances.add((resolved, interval_expression(column_intervals.get(str(column_id))), normalize_aggregation_function(column_aggregations.get(str(column_id)))))
    for rule_id in rule_ids:
        for condition in rules.get(rule_id, {}).get("conditions") or []:
            if not bool(condition.get("enabled", True)):
                continue
            for side in ("left", "right"):
                field_ref = str(condition.get(f"{side}_field_ref") or condition.get(f"{side}_source_id") or "")
                if field_ref:
                    output = output_index.get(field_ref)
                    resolved = str((output or {}).get("field_ref") or field_ref)
                    field_refs.add(resolved)
                    field_instances.add((resolved, interval_expression(condition.get(f"{side}_interval")), normalize_aggregation_function(condition.get(f"{side}_aggregation"))))
    selected_fields = []
    atomic_inputs: set[str] = set()
    timeframes: set[str] = set()
    technical_timeframes: set[str] = set()
    for data_field in data_fields:
        outputs = {str(row.get("field_ref") or "") for row in data_field.get("outputs") or []}
        if not outputs.intersection(field_refs):
            continue
        selected_fields.append({
            "data_field_id": str(data_field.get("data_field_id") or ""),
            "revision": int(data_field.get("revision") or 1),
            "recipe_id": str(data_field.get("recipe_id") or ""),
            "output_refs": sorted(outputs.intersection(field_refs)),
            "execution_scope": str(dict(data_field.get("context") or {}).get("execution_scope") or "focused"),
            "stateful": bool(data_field.get("stateful")),
        })
        atomic_inputs.update(str(value) for value in data_field.get("inputs") or [] if str(value))
        definition_intervals = {
            interval
            for field_ref, interval, _aggregation in field_instances
            if field_ref in outputs and interval
        }
        timeframes.update(definition_intervals)
        producer_intervals = [
            str(value)
            for value in dict(data_field.get("execution") or {}).get("producer_intervals") or []
            if str(value)
        ]
        timeframes.update(producer_intervals)
        matched_outputs = [
            output
            for output in data_field.get("outputs") or []
            if str(output.get("field_ref") or "") in field_refs
        ]
        qmd_owned = (
            str(data_field.get("recipe_id") or "").startswith("qmd.family.")
            or str(data_field.get("owner") or "").lower() in {"qmd", "qmd_gateway"}
        )
        if definition_intervals and qmd_owned:
            technical_timeframes.update(definition_intervals)
        elif any(
            str(output.get("source_id") or "").startswith("indicator.")
            or str(output.get("source_id") or "") == "market.relative_volume"
            for output in matched_outputs
        ):
            technical_timeframes.update(producer_intervals)
    payload = {
        "schema_version": 1,
        "authority": "data_field_compiler",
        "field_refs": sorted(field_refs),
        "field_instances": [
            {
                "field_ref": field_ref,
                "interval": interval,
                "aggregation": aggregation,
                "instance_ref": field_instance_ref(field_ref, interval, aggregation),
            }
            for field_ref, interval, aggregation in sorted(field_instances)
        ],
        "data_fields": sorted(selected_fields, key=lambda row: row["data_field_id"]),
        "atomic_inputs": sorted(atomic_inputs),
        "rule_set_ids": sorted(rule_ids),
        "timeframes": sorted(timeframes),
        "technical_timeframes": sorted(technical_timeframes),
    }
    payload["content_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def validate_data_field_catalog(data_fields: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    refs: set[str] = set()
    for row in data_fields:
        data_field_id = str(row.get("data_field_id") or "")
        if not data_field_id or data_field_id in ids:
            raise ValueError(f"Invalid or duplicate Data Field id: {data_field_id or '<empty>'}")
        ids.add(data_field_id)
        context = dict(row.get("context") or {})
        if "timeframes" in context:
            raise ValueError(
                f"Data Field {data_field_id} uses the retired generic timeframe dimension"
            )
        dimension_kind = str(context.get("dimension_kind") or "")
        interval = str(context.get("interval") or "")
        available_intervals = [str(value) for value in context.get("available_intervals") or []]
        if dimension_kind == "interval" and not available_intervals:
            raise ValueError(f"Data Field {data_field_id} requires available intervals")
        if interval:
            raise ValueError(
                f"Data Field {data_field_id} stores an interval before it is instantiated"
            )
        aggregation = dict(context.get("aggregation") or {})
        aggregation_mode = str(aggregation.get("mode") or "none")
        allowed_aggregations = [str(value) for value in aggregation.get("allowed") or []]
        if aggregation_mode == "required":
            if str(context.get("interval_semantics") or "") != "event_window" or not allowed_aggregations:
                raise ValueError(f"Data Field {data_field_id} has an invalid required aggregation contract")
            if any(value not in AGGREGATION_FUNCTIONS for value in allowed_aggregations):
                raise ValueError(f"Data Field {data_field_id} has an unknown aggregation function")
            runtime_fields = dict(dict(row.get("execution") or {}).get("aggregation_runtime_fields") or {})
            if set(allowed_aggregations) - set(runtime_fields):
                raise ValueError(f"Data Field {data_field_id} lacks a runtime field for an allowed aggregation")
        if aggregation_mode == "intrinsic" and not str(aggregation.get("function") or ""):
            raise ValueError(f"Data Field {data_field_id} lacks its intrinsic bar aggregation")
        outputs = list(row.get("outputs") or [])
        if not outputs:
            raise ValueError(f"Data Field {data_field_id} has no outputs")
        for output in outputs:
            field_ref = str(output.get("field_ref") or "")
            if not field_ref or field_ref in refs:
                raise ValueError(f"Invalid or duplicate Data Field output: {field_ref or '<empty>'}")
            refs.add(field_ref)
            if str(output.get("context_interval") or ""):
                raise ValueError(
                    f"Data Field output {field_ref} stores an interval before it is instantiated"
                )
            if not list(output.get("column_presentations") or []):
                raise ValueError(f"Data Field output {field_ref} has no column presentation")


def _data_field_output(
    data_field_id: str,
    revision: int,
    source_id: str,
    field: dict[str, Any],
    *,
    interval: str | None = None,
    qualified_presentation: bool = False,
    output_id: str | None = None,
) -> dict[str, Any]:
    output_id = output_id or source_id
    value_type = str(field.get("value_type") or "number")
    unit = str(field.get("unit") or "scalar")
    base_column_id = str(field.get("column_id") or _generated_column_id(source_id))
    column_id = f"{base_column_id}__{_slug(interval)}" if interval and qualified_presentation else base_column_id
    field_ref = field_output_ref(data_field_id, revision, output_id)
    normalized_type = value_type.lower()
    normalized_unit = unit.lower()
    numeric = normalized_type in {
        "number", "integer", "float", "score", "ratio", "percent", "price", "bps_per_second"
    }
    known_values = [dict(value) for value in field.get("known_values") or []]
    if known_values:
        domain_kind = "enum"
    elif normalized_type == "boolean":
        domain_kind = "boolean"
    elif numeric:
        domain_kind = "number"
    elif normalized_type in {"json", "vector", "record", "object", "array"}:
        domain_kind = "structured"
    elif normalized_type in {"date", "time", "timestamp", "datetime"}:
        domain_kind = "timestamp" if normalized_type == "datetime" else normalized_type
    elif normalized_unit in {"date", "time", "timestamp", "datetime"}:
        domain_kind = "timestamp" if normalized_unit == "datetime" else normalized_unit
    else:
        domain_kind = "text"
    filterable = domain_kind != "structured" and (bool(field.get("filterable")) or normalized_type in {
        "boolean", "number", "integer", "float", "score", "ratio", "percent", "price", "bps_per_second", "string", "date", "time", "timestamp"
    })
    if not filterable:
        filter_operators = []
    elif domain_kind == "boolean":
        filter_operators = ["is_true", "equals", "not_equals"]
    elif domain_kind == "number":
        filter_operators = ["greater_than", "greater_or_equal", "less_than", "less_or_equal", "equals", "not_equals", "above_by_bps"]
    else:
        filter_operators = ["equals", "not_equals"]
    return {
        "output_id": output_id,
        "field_ref": field_ref,
        "source_id": source_id,
        "field_id": str(field.get("field_id") or source_id),
        "runtime_field": DISCOVERY_RUNTIME_FIELDS.get(source_id, column_id or source_id),
        "context_interval": interval or "",
        "name": str(field.get("name") or _readable(source_id)),
        "description": str(field.get("description") or f"Output {source_id}."),
        "value_type": value_type,
        "presentation_value_type": str(field.get("presentation_value_type") or _presentation_value_type(str(field.get("field_id") or source_id), value_type, unit, tuple((str(row.get("value") or ""), str(row.get("label") or ""), str(row.get("description") or "")) for row in known_values))),
        "unit": unit,
        "value_domain": {
            "kind": domain_kind,
            "closed": bool(known_values),
            "allowed_values": known_values,
            "unit": unit,
        },
        "entity_grain": str(field.get("entity_grain") or "security_at_market_clock"),
        "filterable": filterable,
        "filter_operators": filter_operators,
        "sortable": bool(field.get("sortable")),
        "column_presentations": [{
            "presentation_id": column_id,
            "label": str(field.get("name") or _readable(source_id)),
            "format": _column_format(value_type, unit),
            "precision": 2 if unit in {"currency", "percent", "multiple", "basis_points"} else None,
            "alignment": "right" if numeric else "left",
            "default_visible": bool(field.get("default_visible")) and not qualified_presentation,
            "null_display": "—",
        }],
        "chart_presentations": ([{
            "presentation_id": f"chart.{_slug(source_id)}",
            "label": str(field.get("name") or _readable(source_id)),
            "render_type": "line",
            "placement": "price_overlay" if unit == "currency" else "separate_pane",
            "axis_unit": unit,
            "theme_role": "primary",
            "default_visible": False,
        }] if numeric else []),
    }


def _generated_column_id(source_id: str) -> str:
    return f"field__{_slug(source_id)}"


def _preferred_instance_interval(values: Iterable[str]) -> str:
    available = [str(value) for value in values if str(value)]
    for preferred in ("1m", "5m", "1s", "10s", "30s", "1h", "100ms"):
        if preferred in available:
            return preferred
    return available[0] if available else ""


def _interval_uses_supported_unit(interval: str, examples: Iterable[str]) -> bool:
    """Accept arbitrary positive values for units supported by a Data Field.

    Catalog intervals are examples/capabilities, not a closed enumeration. A
    configured 3-minute bar is valid when the producer supports minute bars,
    even if the catalog happens to advertise 1m and 5m as common choices.
    """

    selected = normalize_interval_spec(interval)
    if selected is None:
        return False
    supported_units = {
        str(spec["unit"])
        for value in examples
        if (spec := normalize_interval_spec(value)) is not None
    }
    return str(selected["unit"]) in supported_units


def _projected_value(
    row: dict[str, Any],
    runtime_field: str,
    source_id: str,
    interval: str = "",
    *,
    allow_generic: bool = True,
    technical_keys: dict[tuple[str, str], str] | None = None,
) -> tuple[bool, Any]:
    candidates = [runtime_field, source_id] if allow_generic else []
    # Historical Scanner bars predate the canonical QMD Data Field names.
    # Keep compatibility at this projection boundary so consumers do not need
    # to know which producer vocabulary supplied the same semantic value.
    producer_aliases = {
        "price_change_pct": ("change_pct",),
        "high_low_range_pct": ("range_pct",),
    }
    aliases = producer_aliases.get(source_id, ())
    if allow_generic:
        candidates.extend(aliases)
    if interval:
        metric_names = list(dict.fromkeys([
            source_id,
            source_id.replace(".", "_"),
            source_id.rsplit(".", 1)[-1],
            runtime_field,
            *aliases,
        ]))
        candidates = [
            *(f"technical__{metric}__{interval}" for metric in metric_names),
            *(
                key
                for metric in metric_names
                if (key := (technical_keys or {}).get((metric, interval)))
            ),
            *candidates,
        ]
    for key in candidates:
        if key in row:
            return True, row.get(key)
    return False, None


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "__", value).strip("_").lower()


def _readable(value: str) -> str:
    return re.sub(r"[_\-.]+", " ", value).strip().title()


def _column_format(value_type: str, unit: str) -> str:
    if unit == "currency":
        return "currency"
    if unit in {"percent", "basis_points"}:
        return "percent"
    if value_type in {"number", "integer", "float"}:
        return "number"
    if unit in {"timestamp", "date", "time"}:
        return unit
    if value_type == "boolean":
        return "boolean"
    return "text"
