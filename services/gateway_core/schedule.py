from __future__ import annotations

from services.gateway_core.market_calendar import MarketHoursSnapshot


def cadence_for_market_status(
    status: MarketHoursSnapshot,
    *,
    active_seconds: float,
    closed_seconds: float,
    weekend_seconds: float | None = None,
) -> float:
    if status.active_collection_window:
        return max(0.1, float(active_seconds))
    if weekend_seconds is not None and "local" in status.source and "closed" == status.session:
        return max(0.1, float(weekend_seconds))
    return max(0.1, float(closed_seconds))

