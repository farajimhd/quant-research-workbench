"""Shared Massive market-status and holiday helpers.

The implementation currently lives in :mod:`services.market_hours` for
backward compatibility with existing gateways. New code should import from this
module so the shared gateway-core boundary matches the service standard.
"""

from services.market_hours import (
    ACTIVE_MARKET_STATES,
    DEFAULT_COLLECTION_WINDOW,
    DEFAULT_HOLIDAYS_URL,
    DEFAULT_STATUS_URL,
    EASTERN,
    MarketHoliday,
    MarketHoursSnapshot,
    MarketStatusSnapshot,
    MassiveMarketHoursClient,
    active_collection_window,
    append_api_key,
    fetch_json,
    get_market_hours_client,
    holiday_for,
    market_status_is_active,
    market_status_session,
    minute_in_window,
    parse_bool,
    parse_datetime,
    parse_holiday,
    service_collection_window,
)

__all__ = [
    "ACTIVE_MARKET_STATES",
    "DEFAULT_COLLECTION_WINDOW",
    "DEFAULT_HOLIDAYS_URL",
    "DEFAULT_STATUS_URL",
    "EASTERN",
    "MarketHoliday",
    "MarketHoursSnapshot",
    "MarketStatusSnapshot",
    "MassiveMarketHoursClient",
    "active_collection_window",
    "append_api_key",
    "fetch_json",
    "get_market_hours_client",
    "holiday_for",
    "market_status_is_active",
    "market_status_session",
    "minute_in_window",
    "parse_bool",
    "parse_datetime",
    "parse_holiday",
    "service_collection_window",
]
