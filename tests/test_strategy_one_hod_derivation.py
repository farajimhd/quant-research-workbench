"""Producer scans pinned bars once and retains only candidate HOD rows."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipelines.strategy_one.hod_derivation import derive_hod_context
from src.backend.fixed_v7_stream import FixedV7Stream
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION


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
