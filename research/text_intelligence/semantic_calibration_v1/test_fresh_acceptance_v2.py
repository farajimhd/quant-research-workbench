from __future__ import annotations

import datetime as dt

from .fresh_acceptance_v2 import (
    SESSION_QUOTAS,
    _allocate_year_session,
    market_session,
)


def _utc(local_hour: int, local_minute: int = 0) -> str:
    local = dt.datetime(
        2026, 7, 14, local_hour, local_minute,
        tzinfo=dt.timezone(dt.timedelta(hours=-4)),
    )
    return local.astimezone(dt.timezone.utc).isoformat()


def test_market_session_boundaries_use_new_york_clock() -> None:
    assert market_session(_utc(3, 59)) == "overnight"
    assert market_session(_utc(4, 0)) == "premarket"
    assert market_session(_utc(9, 29)) == "premarket"
    assert market_session(_utc(9, 30)) == "regular"
    assert market_session(_utc(15, 59)) == "regular"
    assert market_session(_utc(16, 0)) == "after_hours"
    assert market_session(_utc(19, 59)) == "after_hours"
    assert market_session(_utc(20, 0)) == "overnight"


def test_year_session_flow_satisfies_both_marginals() -> None:
    year_quotas = {2025: 6, 2026: 4}
    session_quotas = {
        "premarket": 2,
        "regular": 4,
        "after_hours": 3,
        "overnight": 1,
    }
    cells = {
        (year, session): [{}] * 10
        for year in year_quotas
        for session in SESSION_QUOTAS
    }
    allocation = _allocate_year_session(year_quotas, session_quotas, cells)
    assert {year: sum(row.values()) for year, row in allocation.items()} == year_quotas
    assert {
        session: sum(allocation[year][session] for year in allocation)
        for session in SESSION_QUOTAS
    } == session_quotas
