from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.backend.daily_session_bars import daily_session_trade_bars_relation_sql


def test_daily_session_relation_is_complete_causal_and_identity_safe() -> None:
    sql = daily_session_trade_bars_relation_sql(
        database="market_sip_compact",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 7, 15),
        as_of=datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
        ticker="META",
    )

    assert "daily_session_bars_by_symbol_time_v1" in sql
    assert "canonical_ticker = 'META'" in sql
    assert "identity_status != 'ambiguous_source_ticker'" in sql
    assert "available_at_us <=" in sql
    assert "uniqExact(session_kind) = 3" in sql
    assert "sum(trade_event_count) AS event_count" in sql


def test_daily_session_relation_rejects_noncausal_inputs() -> None:
    with pytest.raises(ValueError, match="timezone"):
        daily_session_trade_bars_relation_sql(
            database="market_sip_compact",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            as_of=datetime(2026, 1, 2),
        )
