"""The Strategy 1 scanner-to-Arrow handoff remains read-only and deterministic."""
import numpy as np
import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_strategy_one_preparation import (
    iter_strategy_one_entries, prepare_strategy_one_session,
    strategy_one_v7_tickers,
)
from src.trading_runtime.strategy_one_columnar import StrategyOneCandidateBatch


DAY = "2026-08-18"
ATTEMPT = "00000000-0000-0000-0000-000000000001"


def _plan():
    units = tuple(MarketDayUnit("build", DAY, ticker, stage, ATTEMPT,
                                "source", 1, "hash")
                  for ticker in ("AAA", "BBB", "CCC")
                  for stage in ("bars", "technical", "broker_100ms"))
    return CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAA", "BBB", "CCC"), units,
        (100, 1_000, 5_000, 10_000, 30_000), "pinned-token")


def _contract():
    stream = {
        "signal_stream_id": "price-squeeze-early",
        "occurrence_source": "qmd_squeeze_episode", "episode_role": "start",
        "episode_ttl_ms": 300_000,
        "inclusion_rule_sets": ["watchlist-squeeze-early-impulse-100ms"],
        "trigger_policy": "false_to_true", "rearm_policy": "after_false",
        "cooldown_ms": 0,
    }
    conditions = [
        ("price_change_1_bar_pct", "greater_or_equal", 0.05),
        ("trade_count_change", "greater_than", 0),
        ("volume_change", "greater_than", 0),
    ]
    activation = {"rule_sets": [{
        "rule_set_id": "watchlist-squeeze-early-impulse-100ms", "operator": "all",
        "conditions": [{"left_source_id": source, "comparator": comparator,
                        "value": value, "left_interval": {
                            "value": 100, "unit": "milliseconds"}}
                       for source, comparator, value in conditions],
    }]}
    return stream, activation


class Scanner:
    def iter_json_each_row(self, sql):
        assert "arte.bars_v1" in sql and "INSERT" not in sql
        return iter({
            "session_date": DAY, "ticker": ticker,
            "bucket_index": 144299, "open_int": 100_000,
            "close_int": 100_100, "previous_close_int": 100_000,
            "volume": 200., "previous_volume": 100.,
            "trade_count": 4, "previous_trade_count": 2,
        } for ticker in ("AAA", "BBB"))


def test_bounded_preparation_prunes_untriggered_tickers_and_merges_stably(monkeypatch):
    opened = []
    class Reader:
        def __init__(self):
            opened.append(self)
            self.closed = False
        def close(self):
            self.closed = True
    calls = []
    def load(_plan, *, tickers, client, **_kwargs):
        assert isinstance(client, Reader)
        calls.append(tickers)
        return {ticker: StrategyOneCandidateBatch(
            np.array([30_000, 30_100]),
            np.array([True, ticker == "BBB"]),
            np.zeros(2, dtype=np.uint8), np.full((2, 4), 30_000),
            np.full(2, 30_000), np.full(2, 97_000)) for ticker in tickers}
    monkeypatch.setattr(
        "src.backend.backtest_strategy_one_preparation.load_strategy_one_entry_batches",
        load)
    stream, activation = _contract()
    results = []
    for workers in (1, 2):
        before = len(opened)
        prepared = prepare_strategy_one_session(
            _plan(), session_date=DAY, through_boundary_ms=30_100,
            stream=stream, activation=activation, scan_client=Scanner(),
            client_factory=Reader, max_workers=workers)
        assert [item.ticker for item in prepared] == ["AAA", "BBB"]
        results.append([(row.boundary_ms, row.ticker, row.source_row_index,
                         row.episode_start_ms)
                        for row in iter_strategy_one_entries(prepared)])
        assert 1 <= len(opened) - before <= workers
    assert results[0] == results[1] == [
        (30_000, "AAA", 0, 30_000),
        (30_000, "BBB", 0, 30_000),
        (30_100, "BBB", 1, 30_000),
    ]
    assert calls == [("AAA", "BBB"), ("AAA", "BBB")]
    assert len(opened) <= 3 and all(reader.closed for reader in opened)
    assert strategy_one_v7_tickers(prepared) == ("AAA", "BBB")


def test_certified_scan_reuse_keeps_v7_scope_to_nonempty_candidates(monkeypatch):
    from src.backend.fixed_bar_signal import load_first_squeeze_occurrences

    plan = _plan()
    stream, activation = _contract()
    scan = load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=30_100, client=Scanner())

    class Reader:
        def close(self):
            pass

    def load(_plan, *, tickers, **_kwargs):
        return {ticker: StrategyOneCandidateBatch(
            np.array([30_000]), np.array([ticker == "BBB"]),
            np.zeros(1, dtype=np.uint8), np.full((1, 4), 30_000),
            np.full(1, 30_000), np.full(1, 97_000)) for ticker in tickers}

    monkeypatch.setattr(
        "src.backend.backtest_strategy_one_preparation.load_first_squeeze_occurrences",
        lambda *_args, **_kwargs: pytest.fail("certified scan was repeated"))
    monkeypatch.setattr(
        "src.backend.backtest_strategy_one_preparation.load_strategy_one_entry_batches",
        load)
    prepared = prepare_strategy_one_session(
        plan, session_date=DAY, through_boundary_ms=30_100,
        stream=stream, activation=activation, scan_client=None,
        client_factory=Reader, certified_scan=scan)
    assert strategy_one_v7_tickers(prepared) == ("BBB",)


def test_certified_scan_rejects_a_different_boundary_or_content():
    from src.backend.fixed_bar_signal import load_first_squeeze_occurrences

    plan = _plan()
    stream, activation = _contract()
    scan = load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=30_100, client=Scanner())
    with pytest.raises(ValueError, match="pinned bar query"):
        prepare_strategy_one_session(
            plan, session_date=DAY, through_boundary_ms=30_200,
            stream=stream, activation=activation, scan_client=None,
            client_factory=lambda: None, certified_scan=scan)
    scan["occurrences"][0]["ticker"] = "CCC"
    with pytest.raises(ValueError, match="pinned bar query"):
        prepare_strategy_one_session(
            plan, session_date=DAY, through_boundary_ms=30_100,
            stream=stream, activation=activation, scan_client=None,
            client_factory=lambda: None, certified_scan=scan)


def test_preparation_rejects_non_100ms_strategy_contract():
    plan = _plan()
    wrong = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(200), plan.build_id, plan.definition_hash,
        plan.sessions, plan.tickers, plan.units, plan.required_resolutions_ms,
        plan.token)
    stream, activation = _contract()
    with pytest.raises(ValueError, match="100ms session"):
        prepare_strategy_one_session(
            wrong, session_date=DAY, through_boundary_ms=30_100,
            stream=stream, activation=activation, scan_client=Scanner(),
            client_factory=lambda: None)


def test_worker_client_closes_after_candidate_failure(monkeypatch):
    opened = []

    class Reader:
        def __init__(self):
            self.closed = False
            opened.append(self)

        def close(self):
            self.closed = True

    def fail_on_second(_plan, *, tickers, client, **_kwargs):
        assert client is opened[0] and not client.closed
        if "BBB" in tickers:
            raise ValueError("candidate source failed")
        return {ticker: StrategyOneCandidateBatch(
            np.array([30_000]), np.array([True]),
            np.zeros(1, dtype=np.uint8), np.full((1, 4), 30_000),
            np.full(1, 30_000), np.full(1, 97_000)) for ticker in tickers}

    monkeypatch.setattr(
        "src.backend.backtest_strategy_one_preparation.load_strategy_one_entry_batches",
        fail_on_second)
    stream, activation = _contract()
    with pytest.raises(ValueError, match="candidate source failed"):
        prepare_strategy_one_session(
            _plan(), session_date=DAY, through_boundary_ms=30_100,
            stream=stream, activation=activation, scan_client=Scanner(),
            client_factory=Reader, max_workers=1)
    assert len(opened) == 1 and opened[0].closed
