from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.backend.fixed_watchlist_transitions import (
    CompletedMembership, MembershipCandidate, reduce_completed_memberships,
    universe_fingerprint,
)


AT = datetime(2026, 8, 18, 14, 0, 0, 100000, tzinfo=timezone.utc)
UNIVERSE = ("AAPL", "MSFT")


def _snapshot(watchlist, at, *ranks):
    return CompletedMembership(
        watchlist, "a" * 64, "b" * 64, "c" * 64,
        universe_fingerprint(UNIVERSE), UNIVERSE, at,
        tuple(MembershipCandidate(ticker, rank, Decimal(str(score)), "rules passed")
              for ticker, rank, score in ranks))


def test_same_ticker_in_two_watchlists_and_rank_change_are_not_collapsed():
    rows = reduce_completed_memberships((
        _snapshot("W1", AT, ("AAPL", 1, 10), ("MSFT", 2, 9)),
        _snapshot("W2", AT, ("AAPL", 1, 8)),
        _snapshot("W1", AT + timedelta(milliseconds=100),
                  ("MSFT", 1, 11), ("AAPL", 2, 10)),
        _snapshot("W2", AT + timedelta(milliseconds=100)),
    ), expected_tickers=UNIVERSE)
    assert [(r.watchlist_id, r.ticker, r.event, r.rank) for r in rows] == [
        ("W1", "AAPL", "added", 1), ("W1", "MSFT", "added", 2),
        ("W2", "AAPL", "added", 1),
        ("W1", "AAPL", "rank_changed", 2),
        ("W1", "MSFT", "rank_changed", 1),
        ("W2", "AAPL", "removed", None),
    ]
    assert all(row.market_plan_token == "b" * 64 for row in rows)


def test_incomplete_or_changed_boundary_fails_closed():
    with pytest.raises(ValueError, match="complete typed boundary"):
        _snapshot("W1", AT + timedelta(milliseconds=1), ("AAPL", 1, 10))
    with pytest.raises(ValueError, match="complete typed boundary"):
        _snapshot("W1", AT, ("AAPL", 1, 10), ("AAPL", 2, 9))
    with pytest.raises(ValueError, match="authority or completed clock"):
        reduce_completed_memberships((
            _snapshot("W1", AT, ("AAPL", 1, 10)),
            _snapshot("W1", AT, ("AAPL", 1, 11)),
        ), expected_tickers=UNIVERSE)
    with pytest.raises(ValueError, match="incomplete"):
        reduce_completed_memberships((
            _snapshot("W1", AT, ("AAPL", 1, 10)),
        ), expected_tickers=("AAPL", "GOOG", "MSFT"))
