from __future__ import annotations

from math import isfinite
from typing import Any, Iterable

from src.backend.application_registry import DISCOVERY_RUNTIME_FIELDS
from src.backend.data_field_contracts import field_instance_ref

SOURCE_FIELDS = {
    **DISCOVERY_RUNTIME_FIELDS,
    "liquidity-rank": "liquidity_rank",
}


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
    rule_by_id = {
        str(rule.get("rule_set_id") or ""): rule for rule in rule_sets
    }
    include_ids = [
        str(value) for value in watchlist.get("inclusion_rule_sets") or []
    ]
    exclude_ids = [
        str(value) for value in watchlist.get("exclusion_rule_sets") or []
    ]
    include_results = [
        _rule_matches(rule_by_id.get(rule_id), row) for rule_id in include_ids
    ]
    include_operator = str(watchlist.get("inclusion_operator") or "all")
    included = not include_results or (
        any(include_results)
        if include_operator == "any"
        else all(include_results)
    )
    excluded = any(
        _rule_matches(rule_by_id.get(rule_id), row) for rule_id in exclude_ids
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
    ranking_interval = str(watchlist.get("ranking_interval") or "")
    ranking_field = (
        field_instance_ref(ranking_ref, ranking_interval)
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
    left_ref = str(condition.get("left_field_ref") or "")
    left_interval = str(condition.get("left_interval") or "")
    left = _source_value(row, field_instance_ref(left_ref, left_interval)) if left_ref else None
    if left is None and (not left_ref or not left_interval):
        left = _source_value(row, str(condition.get("left_source_id") or ""))
    comparator = str(condition.get("comparator") or "")
    if comparator == "is_true":
        return left is True
    right_ref = str(condition.get("right_field_ref") or "")
    right_interval = str(condition.get("right_interval") or "")
    right_source = str(condition.get("right_source_id") or "")
    right = _source_value(row, field_instance_ref(right_ref, right_interval)) if right_ref else None
    if right is None and (not right_ref or not right_interval):
        right = _source_value(row, right_source) if right_source else condition.get("value")
    left_number, right_number = _number(left), _number(right)
    if left_number is None or right_number is None:
        return False
    if comparator == "above_by_bps":
        return right_number > 0 and left_number >= right_number * (1 + float(condition.get("value") or 0) / 10_000)
    if comparator == "equals":
        return left == right
    if comparator == "greater_or_equal":
        return left_number >= right_number
    if comparator == "greater_than":
        return left_number > right_number
    if comparator == "less_or_equal":
        return left_number <= right_number
    if comparator == "less_than":
        return left_number < right_number
    return False


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
