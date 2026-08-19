from __future__ import annotations

from math import isfinite
from typing import Any, Iterable

import polars as pl

from src.backend.application_registry import DISCOVERY_RUNTIME_FIELDS
from src.backend.data_field_contracts import field_instance_ref, interval_expression

SOURCE_FIELDS = {
    **DISCOVERY_RUNTIME_FIELDS,
    "liquidity-rank": "liquidity_rank",
}
PRECOMPUTED_RULE_PREFIX = "__rule_set__"


def classify_watchlist_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add the configured cap and float labels without mutating source facts."""

    result = dict(row)
    market_cap = _number(row.get("market_cap"))
    if market_cap is None or market_cap <= 0:
        result["market_cap_category"] = None
    elif market_cap < 2_000_000_000:
        result["market_cap_category"] = "Small Cap"
    elif market_cap < 10_000_000_000:
        result["market_cap_category"] = "Mid Cap"
    else:
        result["market_cap_category"] = "Large Cap"
    public_float = _number(row.get("float_shares"))
    if public_float is None or public_float < 0:
        result["float_category"] = None
    elif public_float < 500_000:
        result["float_category"] = "Tiny"
    elif public_float < 2_000_000:
        result["float_category"] = "Extra Small"
    elif public_float < 5_000_000:
        result["float_category"] = "Small"
    elif public_float < 10_000_000:
        result["float_category"] = "Medium"
    elif public_float < 20_000_000:
        result["float_category"] = "Medium+"
    elif public_float < 50_000_000:
        result["float_category"] = "Large"
    elif public_float < 100_000_000:
        result["float_category"] = "Extra Large"
    else:
        result["float_category"] = "Broad Float"
    short_interest = _number(row.get("short_interest"))
    if _number(row.get("short_interest_pct")) is None and short_interest is not None and public_float and public_float > 0:
        result["short_interest_pct"] = short_interest / public_float * 100
    return result


def resolve_watchlist_membership(
    watchlist: dict[str, Any],
    rule_sets: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically filter, rank, and limit one Watchlist snapshot.

    Missing evidence fails closed. This pure resolver is shared-contract-ready;
    the trading control plane must still supply causal scanner snapshots and
    persist the resulting membership events before Live use.
    """

    if not bool(watchlist.get("enabled", True)):
        return []

    rule_list = list(rule_sets)
    accepted: list[dict[str, Any]] = []
    observed_symbols: set[str] = set()
    for raw in candidates:
        symbol = str(raw.get("ticker") or raw.get("symbol") or "").upper()
        if symbol:
            observed_symbols.add(symbol)
        matched = evaluate_watchlist_candidate(watchlist, rule_list, raw)
        if matched is not None:
            accepted.append(matched)
    return rank_watchlist_membership(
        watchlist,
        accepted,
        observed_symbols=observed_symbols,
    )


def evaluate_watchlist_candidate(
    watchlist: dict[str, Any],
    rule_sets: Iterable[dict[str, Any]],
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    """Evaluate one symbol without applying cross-symbol rank or size limits."""

    if not bool(watchlist.get("enabled", True)):
        return None
    row = classify_watchlist_row(dict(raw))
    symbol = str(row.get("ticker") or row.get("symbol") or "").upper()
    if not symbol:
        return None
    row["ticker"] = symbol
    manual_inclusions = {
        str(value).upper() for value in watchlist.get("manual_inclusions") or []
    }
    manual_exclusions = {
        str(value).upper() for value in watchlist.get("manual_exclusions") or []
    }
    if symbol in manual_exclusions:
        return None
    include_ids = [
        str(value) for value in watchlist.get("inclusion_rule_sets") or []
    ]
    exclude_ids = [
        str(value) for value in watchlist.get("exclusion_rule_sets") or []
    ]
    selected_ids = [*include_ids, *exclude_ids]
    uses_precomputed = bool(selected_ids) and all(
        f"{PRECOMPUTED_RULE_PREFIX}{rule_id}" in row for rule_id in selected_ids
    )
    rule_by_id = {} if uses_precomputed else {
        str(rule.get("rule_set_id") or ""): rule for rule in rule_sets
    }
    include_results = (
        [row.get(f"{PRECOMPUTED_RULE_PREFIX}{rule_id}") is True for rule_id in include_ids]
        if uses_precomputed
        else [_rule_matches(rule_by_id.get(rule_id), row) for rule_id in include_ids]
    )
    include_operator = str(watchlist.get("inclusion_operator") or "all")
    included = not include_results or (
        any(include_results)
        if include_operator == "any"
        else all(include_results)
    )
    excluded = (
        any(row.get(f"{PRECOMPUTED_RULE_PREFIX}{rule_id}") is True for rule_id in exclude_ids)
        if uses_precomputed
        else any(_rule_matches(rule_by_id.get(rule_id), row) for rule_id in exclude_ids)
    )
    if not ((included and not excluded) or symbol in manual_inclusions):
        return None
    return {
        **row,
        "membership_reason": (
            "manual inclusion" if symbol in manual_inclusions else "rules passed"
        ),
    }


def evaluate_rule_set_result(rule_set: dict[str, Any] | None, raw: dict[str, Any]) -> bool:
    """Evaluate one registered Rule Set for presentation or composition reuse."""

    return _rule_matches(rule_set, classify_watchlist_row(dict(raw)))


def evaluate_rule_sets_frame(
    rule_sets: Iterable[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> dict[str, list[bool]]:
    """Evaluate reusable Rule Sets as one vectorized Polars funnel.

    The returned masks retain input-row order. Missing fields and non-numeric
    comparison operands fail closed, matching the scalar evaluator used for
    incremental single-symbol updates.
    """

    rule_list = [dict(rule) for rule in rule_sets]
    classified = [classify_watchlist_row(dict(row)) for row in rows]
    if not classified:
        return {str(rule.get("rule_set_id") or ""): [] for rule in rule_list}
    frame = pl.from_dicts(classified, strict=False, infer_schema_length=None)
    schema = set(frame.schema)
    masks: dict[str, list[bool]] = {}
    for rule_set in rule_list:
        rule_id = str(rule_set.get("rule_set_id") or "")
        if not rule_id or not bool(rule_set.get("enabled", True)):
            masks[rule_id] = [False] * frame.height
            continue
        conditions = [
            condition
            for condition in rule_set.get("conditions") or []
            if bool(condition.get("enabled", True))
        ]
        expressions = [_condition_expression(condition, schema) for condition in conditions]
        if not expressions:
            masks[rule_id] = [False] * frame.height
            continue
        operator = str(rule_set.get("operator") or "all")
        if operator == "any":
            combined = pl.any_horizontal(expressions)
        elif operator == "score":
            required = float(rule_set.get("required_score") or 1)
            combined = (
                pl.sum_horizontal([expression.cast(pl.Int8) for expression in expressions])
                / len(expressions)
            ) >= required
        else:
            combined = pl.all_horizontal(expressions)
        masks[rule_id] = [
            bool(value)
            for value in frame.select(combined.fill_null(False).alias("matches"))["matches"].to_list()
        ]
    return masks


def rank_watchlist_membership(
    watchlist: dict[str, Any],
    accepted: Iterable[dict[str, Any]],
    *,
    observed_symbols: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Apply deterministic manual fallback, rank, and maximum-size semantics."""

    rows = [dict(row) for row in accepted]
    observed = {str(value).upper() for value in observed_symbols}
    manual_inclusions = {
        str(value).upper() for value in watchlist.get("manual_inclusions") or []
    }
    manual_exclusions = {
        str(value).upper() for value in watchlist.get("manual_exclusions") or []
    }
    for symbol in sorted(manual_inclusions - observed - manual_exclusions):
        rows.append(
            {
                "ticker": symbol,
                "membership_reason": "manual inclusion; scanner evidence unavailable",
            }
        )
    ranking_ref = str(watchlist.get("ranking_field_ref") or "")
    ranking_interval = interval_expression(watchlist.get("ranking_interval"))
    ranking_aggregation = str(watchlist.get("ranking_aggregation") or "")
    ranking_field = (
        field_instance_ref(ranking_ref, ranking_interval, ranking_aggregation)
        if ranking_ref
        else SOURCE_FIELDS.get(
            str(watchlist.get("ranking_field") or ""),
            str(watchlist.get("ranking_field") or ""),
        )
    )
    legacy_ranking_field = SOURCE_FIELDS.get(
        str(watchlist.get("ranking_field") or ""),
        str(watchlist.get("ranking_field") or ""),
    )
    descending = str(watchlist.get("ranking_direction") or "descending") == "descending"
    rows.sort(
        key=lambda row: _rank_key(
            row.get(ranking_field)
            if row.get(ranking_field) is not None
            else row.get(legacy_ranking_field) if not ranking_interval else None,
            descending,
        ),
        reverse=descending,
    )
    return rows[: max(1, int(watchlist.get("maximum_size") or 1))]


def _rule_matches(rule_set: dict[str, Any] | None, row: dict[str, Any]) -> bool:
    if not rule_set or not bool(rule_set.get("enabled", True)):
        return False
    rule_id = str(rule_set.get("rule_set_id") or "")
    precomputed_key = f"{PRECOMPUTED_RULE_PREFIX}{rule_id}"
    if rule_id and precomputed_key in row:
        return row.get(precomputed_key) is True
    results = [_condition_matches(condition, row) for condition in rule_set.get("conditions") or [] if bool(condition.get("enabled", True))]
    if not results:
        return False
    operator = str(rule_set.get("operator") or "all")
    if operator == "any":
        return any(results)
    if operator == "score":
        return sum(results) / len(results) >= float(rule_set.get("required_score") or 1)
    return all(results)


def _condition_matches(condition: dict[str, Any], row: dict[str, Any]) -> bool:
    if str(condition.get("left_value_selection") or "latest") != "latest":
        return False
    left_ref = str(condition.get("left_field_ref") or "")
    left_interval = interval_expression(condition.get("left_interval"))
    left = _source_value(row, field_instance_ref(left_ref, left_interval, condition.get("left_aggregation"))) if left_ref else None
    if left is None and (not left_ref or not left_interval):
        left = _source_value(row, str(condition.get("left_source_id") or ""))
    comparator = str(condition.get("comparator") or "")
    if comparator == "is_true":
        return left is True
    right_ref = str(condition.get("right_field_ref") or "")
    right_interval = interval_expression(condition.get("right_interval"))
    right_source = str(condition.get("right_source_id") or "")
    if right_source and str(condition.get("right_value_selection") or "latest") != "latest":
        return False
    right = _source_value(row, field_instance_ref(right_ref, right_interval, condition.get("right_aggregation"))) if right_ref else None
    if right is None and (not right_ref or not right_interval):
        right = _source_value(row, right_source) if right_source else condition.get("value")
    if comparator == "equals":
        return left == right
    if comparator == "not_equals":
        return left != right
    left_number, right_number = _number(left), _number(right)
    if left_number is None or right_number is None:
        return False
    if comparator == "above_by_bps":
        return right_number > 0 and left_number >= right_number * (1 + float(condition.get("value") or 0) / 10_000)
    if comparator == "greater_or_equal":
        return left_number >= right_number
    if comparator == "greater_than":
        return left_number > right_number
    if comparator == "less_or_equal":
        return left_number <= right_number
    if comparator == "less_than":
        return left_number < right_number
    return False


def _condition_expression(
    condition: dict[str, Any], schema: set[str]
) -> pl.Expr:
    left = _operand_expression(condition, "left", schema)
    comparator = str(condition.get("comparator") or "")
    if left is None:
        return pl.lit(False)
    if comparator == "is_true":
        return left.cast(pl.Boolean, strict=False).fill_null(False)
    right = _operand_expression(condition, "right", schema)
    if right is None:
        right = pl.lit(condition.get("value"))
    if comparator == "equals":
        return left.eq(right).fill_null(False)
    if comparator == "not_equals":
        return left.ne(right).fill_null(False)
    left_number = left.cast(pl.Float64, strict=False)
    right_number = right.cast(pl.Float64, strict=False)
    if comparator == "above_by_bps":
        buffer = float(condition.get("value") or 0) / 10_000
        return ((right_number > 0) & (left_number >= right_number * (1 + buffer))).fill_null(False)
    comparisons = {
        "greater_or_equal": left_number >= right_number,
        "greater_than": left_number > right_number,
        "less_or_equal": left_number <= right_number,
        "less_than": left_number < right_number,
    }
    return comparisons.get(comparator, pl.lit(False)).fill_null(False)


def _operand_expression(
    condition: dict[str, Any], side: str, schema: set[str]
) -> pl.Expr | None:
    if str(condition.get(f"{side}_value_selection") or "latest") != "latest":
        return None
    field_ref = str(condition.get(f"{side}_field_ref") or "")
    interval = interval_expression(condition.get(f"{side}_interval"))
    aggregation = condition.get(f"{side}_aggregation")
    source_id = str(condition.get(f"{side}_source_id") or "")
    candidates = []
    if field_ref:
        candidates.append(field_instance_ref(field_ref, interval, aggregation))
        if not interval:
            candidates.append(field_ref)
    if not interval and source_id:
        candidates.extend((source_id, SOURCE_FIELDS.get(source_id, source_id)))
    for candidate in dict.fromkeys(candidates):
        if candidate in schema:
            return pl.col(candidate)
    return None


def _source_value(row: dict[str, Any], source_id: str) -> Any:
    if source_id in row:
        return row[source_id]
    return row.get(SOURCE_FIELDS.get(source_id, source_id))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _rank_key(value: Any, descending: bool) -> float:
    number = _number(value)
    if number is not None:
        return number
    return float("-inf") if descending else float("inf")
