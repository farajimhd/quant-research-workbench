from __future__ import annotations

import datetime as dt

from .fresh_acceptance_v2 import (
    SESSION_QUOTAS,
    _allocate_year_session,
    market_session,
    select_session_balanced_candidates,
)
from .fresh_acceptance_v4 import SESSION_QUOTAS as V4_SESSION_QUOTAS


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


def test_selection_supports_explicit_200_article_contract() -> None:
    hours = {
        "premarket": 8,
        "regular": 12,
        "after_hours": 18,
        "overnight": 2,
    }
    rows = []
    for year in range(2010, 2027):
        for session, hour in hours.items():
            for index in range(24):
                rows.append({
                    "source_id": f"{year}-{session}-{index}",
                    "source_timestamp": _utc_for_year(year, hour),
                    "event": {"title": f"{session} item {index}", "tickers": ["ABC"]},
                    "v5_units": [],
                })
    selected, allocation = select_session_balanced_candidates(
        rows,
        sample_size=200,
        session_quotas=dict(V4_SESSION_QUOTAS),
        sampling_seed="fresh-200-test",
    )
    assert len(selected) == 200
    assert {
        session: sum(market_session(row["source_timestamp"]) == session for row in selected)
        for session in hours
    } == dict(V4_SESSION_QUOTAS)
    assert sum(sum(values.values()) for values in allocation.values()) == 200


def _utc_for_year(year: int, local_hour: int) -> str:
    local = dt.datetime(
        year, 7, 14, local_hour,
        tzinfo=dt.timezone(dt.timedelta(hours=-4)),
    )
    return local.astimezone(dt.timezone.utc).isoformat()
