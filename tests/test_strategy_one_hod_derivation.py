"""Producer scans pinned bars once and retains only candidate HOD rows."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipelines.strategy_one.hod_derivation import derive_hod_context
from src.backend.backtest_market_data import market_day_boundary
from src.backend.fixed_v7_stream import FixedV7Stream
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION
from src.trading_runtime.strategy_one_hod import HodObservation, observe_completed_hod
from src.trading_runtime.strategy_one_hod_product import HodContext


NY = ZoneInfo("America/New_York")


def stream():
    seed = dict(ticker="TEST", session="2026-08-17",
                available_at=datetime(2026, 8, 17, 20, tzinfo=NY).timestamp(),
                source_extraction_version=EXTRACTION_VERSION,
                band_config={**CONFIG, "coverage": .8}, levels=[],
                input_policy="legacy-unfiltered")
    seed["checkpoint_hash"] = digest(seed)
    return FixedV7Stream(seed, ticker="TEST", session=date(2026, 8, 18))


def test_one_second_and_100ms_bars_share_causal_stream():
    one_second = dict(ticker="TEST", resolution_ms=1000,
                      bucket_index=14700, price_valid=1, extremes_valid=1,
                      open_int=100_000, high_int=100_100, low_int=99_900,
                      close_int=100_000, volume=100)
    bars = [dict(ticker="TEST", resolution_ms=100, bucket_index=147009 + index,
                 price_valid=1, extremes_valid=1,
                 open_int=100_000, high_int=120_000, low_int=99_000,
                 close_int=100_000, volume=10)
            for index in range(3)]
    book = stream()
    result = derive_hod_context(
        bars, (one_second,), ticker="TEST", session_date="2026-08-18",
        candidate_boundaries=(301_100, 301_200), stream=book,
        seed_policy="exclude-trades-before-0405-et-v1")
    assert tuple(row.boundary_ms for row in result) == (301_100, 301_200)
    assert result[0].session_open_int == 100_000
    assert result[0].prior_hod_int == 120_000
    assert result[0].late_mode is True
    assert result[0].gate_level_id == ""
    assert book.engine.bars_processed == 1


def test_missing_candidate_bucket_fails_instead_of_fabricating_bar():
    bars = [dict(ticker="TEST", resolution_ms=100, bucket_index=147009,
                 price_valid=1, extremes_valid=1, open_int=100_000,
                 high_int=100_000, low_int=100_000, close_int=100_000)]
    with pytest.raises(ValueError, match="ended before all candidates"):
        derive_hod_context(
            bars, (), ticker="TEST", session_date="2026-08-18",
            candidate_boundaries=(301_100,), stream=stream(),
            seed_policy="exclude-trades-before-0405-et-v1")


def test_unchanged_v7_geometry_is_reused_without_carrying_stale_levels():
    day = date(2026, 8, 18)
    seconds = [dict(ticker="TEST", resolution_ms=1000,
                    bucket_index=14700, price_valid=1, extremes_valid=1,
                    open_int=100_000, high_int=100_100, low_int=99_900,
                    close_int=100_000, volume=100),
               dict(ticker="TEST", resolution_ms=1000,
                    bucket_index=14701, price_valid=0, extremes_valid=0)]
    bars = [dict(ticker="TEST", resolution_ms=100,
                 bucket_index=147009 + index, price_valid=1,
                 extremes_valid=1,
                 open_int=100_000 if index == 0 else 94_000 if index == 1 else 96_000,
                 high_int=100_000 if index == 0 else 96_000,
                 low_int=94_000 if index <= 1 else 96_000,
                 close_int=94_000 if index == 0 else 96_000)
            for index in range(13)]
    candidates = (301_100, 301_500, 302_000, 302_200)
    policy = "exclude-trades-before-0405-et-v1"
    optimized = stream()
    resistance = {"unified_level_id": "r1", "lower": 9.4,
                  "upper": 9.6, "role": "resistance"}
    def admitted(book, *, as_of):
        return ((resistance,) if as_of.timestamp() - book.engine.as_of <= 1.000001
                else ())
    calls = []
    def counted(*, as_of, seed_policy):
        calls.append(as_of)
        return admitted(optimized, as_of=as_of)
    optimized.strategy_one_levels = counted
    actual = derive_hod_context(
        bars, seconds, ticker="TEST", session_date=day.isoformat(),
        candidate_boundaries=candidates, stream=optimized,
        seed_policy=policy)

    reference = stream()
    reference.strategy_one_levels = lambda *, as_of, seed_policy: admitted(
        reference, as_of=as_of)
    reference.update_second(seconds[0], completed_second_ms=301_000)
    state = HodObservation()
    expected = []
    for bar in bars:
        boundary = (bar["bucket_index"] + 1) * 100 - 14_400_000
        levels = reference.strategy_one_levels(
            as_of=market_day_boundary(day, boundary), seed_policy=policy)
        state = observe_completed_hod(
            state, dict(bar, boundary_ms=boundary), admitted_levels=levels)
        if boundary in candidates:
            expected.append(HodContext.from_observation(state))
    assert actual == tuple(expected)
    assert actual[0].gate_level_id == "r1"
    assert actual[-1].gate_level_id == ""
    assert len(calls) == 1
