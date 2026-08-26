from __future__ import annotations

from datetime import date
from typing import Any


SESSION_CHANGE_AUTHORITY = "session_change.v1"


def session_change_projection(
    *,
    current_price: float | None,
    raw_previous_close: float | None,
    expected_previous_session_date: str,
    reference_previous_session_date: str,
    session_date: str,
    previous_close_source: str = "",
    split_execution_date: str = "",
    split_from: float | None = None,
    split_to: float | None = None,
    corporate_action_reference_status: str = "ready",
) -> dict[str, Any]:
    """Return the one canonical previous-close session change projection.

    Completed-session bars remain raw.  When a published split executes on the
    evaluated session, the prior close is converted into the current share
    units before any absolute or percentage change is calculated.
    """

    unavailable = {
        "previous_close": None,
        "previous_close_raw": raw_previous_close,
        "change_actual": None,
        "change_pct": None,
        "previous_close_reference_status": "unavailable",
        "previous_close__null_reason": "previous_session_authority_unavailable",
        "session_change_authority": SESSION_CHANGE_AUTHORITY,
        "session_change_adjustment_factor": None,
    }
    if not _valid_date(expected_previous_session_date) or not _valid_date(session_date):
        return unavailable
    if raw_previous_close is None or raw_previous_close <= 0:
        return unavailable
    if corporate_action_reference_status != "ready":
        return {
            **unavailable,
            "previous_close__null_reason": "corporate_action_reference_unavailable",
        }
    if reference_previous_session_date != expected_previous_session_date:
        return {
            **unavailable,
            "previous_close_reference_status": "stale",
            "previous_close__null_reason": "stale_previous_session_reference",
        }
    adjustment_factor = 1.0
    adjusted_source = previous_close_source
    if split_execution_date == session_date:
        if split_from is None or split_to is None or split_from <= 0 or split_to <= 0:
            return {
                **unavailable,
                "previous_close_reference_status": "invalid_corporate_action",
                "previous_close__null_reason": "invalid_split_adjustment",
            }
        adjustment_factor = split_from / split_to
        adjusted_source = "+".join(
            value for value in (previous_close_source, "q_live.market_stock_split_v1") if value
        )

    previous_close = raw_previous_close * adjustment_factor
    if current_price is None or current_price <= 0 or previous_close <= 0:
        change_actual = None
        change_pct = None
    else:
        change_actual = current_price - previous_close
        change_pct = (current_price / previous_close - 1.0) * 100.0
    return {
        "previous_close": previous_close,
        "previous_close_raw": raw_previous_close,
        "change_actual": change_actual,
        "change_pct": change_pct,
        "previous_close_reference_status": "ready",
        "previous_close__null_reason": "",
        "previous_close_source": adjusted_source,
        "session_change_authority": SESSION_CHANGE_AUTHORITY,
        "session_change_adjustment_factor": adjustment_factor,
        "split_adjusted": adjustment_factor != 1.0,
    }


def session_change_for_row(
    row: dict[str, Any],
    *,
    session_date: str,
    expected_previous_session_date: str,
    reference_previous_session_date: str | None = None,
) -> dict[str, Any]:
    """Adapt any Scanner, Watchlist, Signal, or ticker row to the authority."""

    return session_change_projection(
        current_price=_number(row, "last_price", "last_close", "current_open", "last", "close"),
        raw_previous_close=_number(row, "previous_close"),
        expected_previous_session_date=expected_previous_session_date,
        reference_previous_session_date=(
            str(row.get("previous_session_date") or "")
            if reference_previous_session_date is None
            else reference_previous_session_date
        ),
        session_date=session_date,
        previous_close_source=str(row.get("previous_close_source") or ""),
        split_execution_date=str(row.get("split_execution_date") or ""),
        split_from=_number(row, "split_from"),
        split_to=_number(row, "split_to"),
        corporate_action_reference_status=str(
            row.get("corporate_action_reference_status") or "ready"
        ),
    )


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
