from __future__ import annotations

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.fixed_bar_signal import (
    CONTRACT, candidate_projection_tickers, canonical_stream_activation,
    first_squeeze_sql, load_first_squeeze_occurrences,
    validate_stream,
)


def test_canonical_producer_stream_uses_the_same_validator():
    stream, activation = canonical_stream_activation()
    validate_stream(stream, activation)
    assert stream["signal_stream_id"] == "price-squeeze-early"
    assert len(activation["rule_sets"][0]["conditions"]) == 3


DAY = "2026-08-18"
TICKER = "ABCD"
ATTEMPT = "00000000-0000-0000-0000-000000000001"


def _plan():
    unit = MarketDayUnit("build", DAY, TICKER, "bars", ATTEMPT, "source", 3, "output")
    return CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,), (TICKER,),
        (unit,), (100, 1000), "pinned-token",
    )


def _contract():
    stream = {
        "signal_stream_id": "price-squeeze-early",
        "occurrence_source": "qmd_squeeze_episode", "episode_role": "start",
        "episode_ttl_ms": 300_000,
        "inclusion_rule_sets": ["watchlist-squeeze-early-impulse-100ms"],
        "trigger_policy": "false_to_true", "rearm_policy": "after_false",
        "cooldown_ms": 0, "maximum_events": 5000,
    }
    conditions = [
        ("price_change_1_bar_pct", "greater_or_equal", 0.05),
        ("trade_count_change", "greater_than", 0),
        ("volume_change", "greater_than", 0),
    ]
    activation = {"rule_sets": [{
        "rule_set_id": "watchlist-squeeze-early-impulse-100ms", "operator": "all",
        "conditions": [{"left_source_id": source, "comparator": comparator,
                        "value": value, "right_source_id": "", "enabled": True,
                        "left_interval": {"value": 100, "unit": "milliseconds"}}
                       for source, comparator, value in conditions],
    }]}
    return stream, activation


def test_first_squeeze_is_read_only_and_available_at_completed_boundary():
    plan = _plan()
    stream, activation = _contract()
    class Client:
        def iter_json_each_row(self, sql):
            assert sql.startswith("WITH ordered AS")
            assert "arte.bars_v1" in sql
            assert "bucket_index<342000" in sql
            assert "INSERT" not in sql
            return iter([{"session_date": DAY, "ticker": TICKER,
                          "bucket_index": 147000, "open_int": 99_900,
                          "close_int": 100_100, "previous_close_int": 100_000,
                          "volume": 200., "previous_volume": 100.,
                          "trade_count": 4, "previous_trade_count": 2}])
    result = load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=19_800_000, client=Client(),
    )
    event = result["occurrences"][0]
    assert event["available_at"] == "2026-08-18T04:05:00.100000-04:00"
    assert event["last_price"] == 10.01
    assert event["squeeze_anchor_price"] == 10.
    assert event["evidence"]["trade_count_change"] == 2
    assert result["authority"]["authority"] == CONTRACT
    assert result["authority"]["row_count"] == 1


def test_first_squeeze_contract_change_fails_closed():
    stream, activation = _contract()
    activation["rule_sets"][0]["conditions"][0]["value"] = 0.06
    try:
        validate_stream(stream, activation)
    except ValueError as exc:
        assert "thresholds" in str(exc)
    else:
        raise AssertionError("Changed signal threshold was accepted")
    assert "UNION ALL" not in first_squeeze_sql(_plan(), through_boundary_ms=19_800_000)


def test_squeeze_episode_renews_only_at_completed_expiry_boundary():
    plan = _plan()
    stream, activation = _contract()
    def candidate(bucket):
        return {"session_date": DAY, "ticker": TICKER, "bucket_index": bucket,
                "open_int": 99_900, "close_int": 100_100,
                "previous_close_int": 100_000, "volume": 200.,
                "previous_volume": 100., "trade_count": 4,
                "previous_trade_count": 2}
    class Client:
        def iter_json_each_row(self, sql):
            assert "candidate_number=1" not in sql
            return iter([candidate(147000), candidate(147001),
                         candidate(149999), candidate(150000)])
    result = load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=19_800_000, client=Client())
    assert [row["available_at"] for row in result["occurrences"]] == [
        "2026-08-18T04:05:00.100000-04:00",
        "2026-08-18T04:10:00.100000-04:00",
    ]
    assert len({row["event_id"] for row in result["occurrences"]}) == 2


def test_squeeze_candidates_must_be_ordered_without_display_cap_truncation():
    plan = _plan()
    stream, activation = _contract()
    row = {"session_date": DAY, "ticker": TICKER, "bucket_index": 147000,
           "open_int": 99_900, "close_int": 100_100,
           "previous_close_int": 100_000, "volume": 200.,
           "previous_volume": 100., "trade_count": 4,
           "previous_trade_count": 2}
    class Client:
        def __init__(self, buckets):
            self.buckets = buckets
        def iter_json_each_row(self, _sql):
            return iter([{**row, "bucket_index": bucket} for bucket in self.buckets])
    assert len(load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=19_800_000,
        client=Client([147000, 147001]))["occurrences"]) == 1
    import pytest
    with pytest.raises(ValueError, match="unordered"):
        load_first_squeeze_occurrences(
            plan, stream=stream, activation=activation,
            through_boundary_ms=19_800_000,
            client=Client([147001, 147000]))
    stream["maximum_events"] = 1
    assert len(load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=19_800_000,
        client=Client([147000, 150000]))["occurrences"]) == 2


def test_squeeze_candidate_stream_closes_on_invalid_evidence():
    import pytest
    stream, activation = _contract()
    class Source:
        def __init__(self):
            self.closed = False
        def __iter__(self):
            return self
        def __next__(self):
            return {"session_date": DAY, "ticker": TICKER,
                    "bucket_index": 147000, "close_int": 0,
                    "previous_close_int": 100_000, "volume": 200.,
                    "previous_volume": 100., "trade_count": 4,
                    "previous_trade_count": 2}
        def close(self):
            self.closed = True
    source = Source()
    class Client:
        def iter_json_each_row(self, _sql):
            return source
    with pytest.raises(ValueError, match="invalid or unordered"):
        load_first_squeeze_occurrences(
            _plan(), stream=stream, activation=activation,
            through_boundary_ms=19_800_000, client=Client())
    assert source.closed


def test_candidate_projection_requires_sole_source_native_admission():
    stream, activation = _contract()
    activation["signal_streams"] = [stream]
    configuration = {"assignments": [], "signal_activation": activation,
                     "run_plan": {"signal_stream_ids": ["price-squeeze-early"],
                                  "activation": {"watchlist_policy": "not_required"}}}
    occurrences = [{"ticker": "ABCD"}, {"ticker": "XYZ"}]
    assert candidate_projection_tickers(configuration, occurrences) == ("ABCD", "XYZ")
    assert candidate_projection_tickers(configuration, occurrences,
                                        has_core_signal_plans=True) is None
    configuration["assignments"] = [{"ticker": "OTHER"}]
    assert candidate_projection_tickers(configuration, occurrences) is None
