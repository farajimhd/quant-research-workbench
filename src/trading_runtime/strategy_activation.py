from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.trading_runtime.strategy_engine import StrategyObservation


NEW_YORK = ZoneInfo("America/New_York")


def run_plan_accepts_signal(
    run_plan: Mapping[str, Any],
    occurrence: Mapping[str, Any],
    *,
    eligible_tickers: Iterable[str] | None = None,
) -> bool:
    """Fail-closed activation gate shared by live and historical controllers."""

    if not bool(run_plan.get("enabled", True)):
        return False
    if str(occurrence.get("signal_stream_id") or "") not in {
        str(value) for value in run_plan.get("signal_stream_ids") or [] if str(value)
    }:
        return False
    event_time = _aware_datetime(
        occurrence.get("effective_at") or occurrence.get("event_time")
    )
    if event_time is None or not _enablement_accepts(
        dict(run_plan.get("enablement") or {}), event_time
    ):
        return False
    ticker = str(occurrence.get("ticker") or "").strip().upper()
    if not ticker:
        return False
    selected_watchlists = {
        str(value) for value in run_plan.get("watchlist_ids") or [] if str(value)
    }
    watchlist_policy = str(
        dict(run_plan.get("activation") or {}).get("watchlist_policy")
        or "any_selected"
    )
    if not selected_watchlists or watchlist_policy == "not_required":
        return True
    if eligible_tickers is None:
        return False
    return ticker in {
        str(value).strip().upper() for value in eligible_tickers if str(value).strip()
    }


def strategy_observation_from_signal_occurrence(
    occurrence: Mapping[str, Any],
    *,
    position_quantity: float = 0.0,
    average_price: float = 0.0,
) -> StrategyObservation:
    """Convert frozen trigger-time evidence into the canonical Strategy input."""

    observed_at = _aware_datetime(
        occurrence.get("effective_at") or occurrence.get("event_time")
    )
    if observed_at is None:
        raise ValueError("Signal Stream occurrence requires a timezone-aware event time")
    ticker = str(occurrence.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Signal Stream occurrence requires a ticker")

    source_values: dict[str, Any] = {}
    source_ids: set[str] = set()
    source_timeframes: list[str] = []
    for instance_ref, raw in dict(occurrence.get("field_evidence") or {}).items():
        evidence = dict(raw or {})
        value = evidence.get("value")
        if value is None:
            continue
        field_ref = str(evidence.get("field_ref") or "")
        interval = str(evidence.get("interval") or "")
        aggregation = str(evidence.get("aggregation") or "")
        source_id = _source_id_from_field_ref(field_ref)
        record = {
            "value": value,
            "observed_at": str(
                evidence.get("available_at") or observed_at.isoformat()
            ),
        }
        for identity in (str(instance_ref), field_ref, source_id):
            if not identity:
                continue
            source_values[identity] = record
            if interval:
                source_values[f"{identity}@{interval}"] = record
                if aggregation:
                    source_values[f"{identity}@{interval}#{aggregation}"] = record
        if source_id:
            source_ids.add(source_id)
        if interval:
            source_timeframes.append(interval)
    for key, value in dict(occurrence.get("evidence") or {}).items():
        if value is not None and key not in source_values:
            source_values[str(key)] = {"value": value, "observed_at": observed_at.isoformat()}

    price = _numeric_value(
        source_values,
        (
            "market.last_price",
            "market.close",
            "bar.close",
            "last_price",
            "price",
            "close",
        ),
    )
    if price is None or price <= 0:
        raise ValueError(
            "Signal Stream occurrence is missing a positive trigger-time price Data Field"
        )
    bid = _numeric_value(source_values, ("quote.bid_price", "market.bid_price", "bid", "bid_price")) or 0.0
    ask = _numeric_value(source_values, ("quote.ask_price", "market.ask_price", "ask", "ask_price")) or 0.0
    session_phase = str(
        _scalar_value(source_values, ("session.phase", "market.session_phase", "session_phase"))
        or ""
    ).lower()
    return StrategyObservation(
        ticker=ticker,
        observed_at=observed_at,
        price=price,
        bar_open=_positive_numeric(
            _scalar_value(
                source_values,
                ("market.bar_open@1s", "bar.open@1s", "market.bar_open", "bar.open", "open"),
            )
        ),
        bid=bid,
        ask=ask,
        position_quantity=float(position_quantity),
        average_price=float(average_price),
        market_open=session_phase not in {"closed", "overnight"},
        evaluation_events=("signal_stream_occurrence",),
        changed_source_ids=tuple(sorted(source_ids)),
        source_signal_ids=(
            str(occurrence.get("signal_id") or occurrence.get("event_id") or ""),
        ),
        source_timeframe=source_timeframes[0] if source_timeframes else "",
        source_values=source_values,
    )


def strategy_observation_from_market_row(
    row: Mapping[str, Any],
    *,
    observed_at: Any,
    position_quantity: float = 0.0,
    average_price: float = 0.0,
) -> StrategyObservation:
    """Project a post-activation QMD scanner row into the shared Strategy input."""

    timestamp = _aware_datetime(observed_at)
    if timestamp is None:
        raise ValueError("Market row observation requires a timezone-aware event time")
    ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
    if not ticker:
        raise ValueError("Market row observation requires a ticker")
    source_values: dict[str, Any] = {}
    for identity, value in row.items():
        if value is None:
            continue
        source_values[str(identity)] = (
            dict(value)
            if isinstance(value, Mapping) and "value" in value
            else {"value": value, "observed_at": timestamp.isoformat()}
        )
    price = _numeric_value(
        source_values,
        ("market.last_price", "last_price", "price", "close"),
    )
    if price is None or price <= 0:
        raise ValueError("Market row is missing a positive price")
    bid = _numeric_value(
        source_values, ("quote.bid_price", "market.bid_price", "bid", "bid_price")
    ) or 0.0
    ask = _numeric_value(
        source_values, ("quote.ask_price", "market.ask_price", "ask", "ask_price")
    ) or 0.0
    session_phase = str(
        _scalar_value(source_values, ("session.phase", "market.session_phase", "session_phase"))
        or ""
    ).lower()
    unified_levels = [
        dict(level)
        for level in row.get("qmd_structure_unified_levels") or []
        if isinstance(level, Mapping)
    ]
    return StrategyObservation(
        ticker=ticker,
        observed_at=timestamp,
        price=price,
        bar_open=_positive_numeric(
            row.get("bar_open") or row.get("open") or row.get("market.bar_open@1s")
        ),
        bid=bid,
        ask=ask,
        position_quantity=float(position_quantity),
        average_price=float(average_price),
        swing_high=_positive_numeric(row.get("structure_swing_high")),
        swing_low=_positive_numeric(row.get("structure_swing_low")),
        structural_support_price=_positive_numeric(row.get("qmd_structure_support_price")),
        structural_support_lower=_positive_numeric(row.get("qmd_structure_support_lower")),
        structural_support_upper=_positive_numeric(row.get("qmd_structure_support_upper")),
        structural_support_strength=float(row.get("qmd_structure_support_strength") or 0),
        structural_support_confidence=float(row.get("qmd_structure_support_confidence") or 0),
        structural_resistance_price=_positive_numeric(row.get("qmd_structure_resistance_price")),
        structural_resistance_lower=_positive_numeric(row.get("qmd_structure_resistance_lower")),
        structural_resistance_upper=_positive_numeric(row.get("qmd_structure_resistance_upper")),
        structural_resistance_strength=float(row.get("qmd_structure_resistance_strength") or 0),
        structural_resistance_confidence=float(row.get("qmd_structure_resistance_confidence") or 0),
        structural_session_high=_positive_numeric(row.get("qmd_structure_session_high")),
        structural_support_levels=tuple(
            level for level in unified_levels if int(level.get("side") or 0) > 0
        ),
        structural_resistance_levels=tuple(
            level for level in unified_levels if int(level.get("side") or 0) < 0
        ),
        structural_up_probability=float(row.get("qmd_structure_up_probability") or 0.5),
        vwap=_positive_numeric(row.get("vwap")),
        execution_vwap=_positive_numeric(row.get("execution_vwap")),
        macd_line=_optional_numeric(row.get("macd_line")),
        macd_signal=_optional_numeric(row.get("macd_signal")),
        macd_histogram=_optional_numeric(row.get("macd_histogram")),
        volatility=float(row.get("atr_14") or 0),
        upper_luld_price=_positive_numeric(row.get("structure_luld_upper")),
        market_open=session_phase not in {"closed", "overnight"},
        evaluation_events=("market_data_update",),
        changed_source_ids=tuple(sorted(str(key) for key in source_values)),
        source_timeframe=str(row.get("indicator_interval") or row.get("timeframe") or ""),
        source_values=source_values,
    )


def _optional_numeric(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _positive_numeric(value: Any) -> float | None:
    number = _optional_numeric(value)
    return number if number is not None and number > 0 else None


def _enablement_accepts(enablement: Mapping[str, Any], event_time: datetime) -> bool:
    if str(enablement.get("state") or "disabled") != "enabled":
        return False
    scope = str(enablement.get("scope") or "persistent")
    if scope == "persistent":
        return True
    if scope != "current_session":
        return False
    effective_session = str(enablement.get("effective_session") or "")
    return bool(effective_session) and event_time.astimezone(NEW_YORK).date().isoformat() == effective_session


def _source_id_from_field_ref(field_ref: str) -> str:
    return field_ref.rsplit(":", 1)[-1] if ":" in field_ref else ""


def _scalar_value(source_values: Mapping[str, Any], candidates: Iterable[str]) -> Any:
    for candidate in candidates:
        value = source_values.get(candidate)
        if isinstance(value, Mapping):
            value = value.get("value")
        if value is not None:
            return value
    for identity, raw in source_values.items():
        if not any(str(identity).endswith(f":{candidate}") for candidate in candidates):
            continue
        return raw.get("value") if isinstance(raw, Mapping) else raw
    return None


def _numeric_value(
    source_values: Mapping[str, Any], candidates: Iterable[str]
) -> float | None:
    value = _scalar_value(source_values, candidates)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else None
