from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.backend.fixed_v7_stream import FixedV7Stream
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION


NY = ZoneInfo("America/New_York")


def seed():
    value = dict(ticker="TEST", session="2026-08-17",
        available_at=datetime(2026, 8, 17, 20, tzinfo=NY).timestamp(),
        source_extraction_version=EXTRACTION_VERSION,
        band_config={**CONFIG, "coverage": .8}, levels=[],
        input_policy="legacy-unfiltered")
    value["checkpoint_hash"] = digest(value)
    return value


def test_completed_second_advances_causally_from_empty_seed():
    stream = FixedV7Stream(seed(), ticker="TEST", session=date(2026, 8, 18))
    before = datetime(2026, 8, 18, 4, 5, tzinfo=NY)
    assert stream.context(as_of=before)["qmd_structure_unified_levels"] == []
    at = datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY)
    row = dict(resolution_ms=1000, price_valid=1, extremes_valid=1,
               open_int=100000, high_int=100100, low_int=99900,
               close_int=100050, volume=100)
    stream.update_second(row, at=at)
    assert stream.context(as_of=at)["v7_seed_input_policy"] == "exclude-trades-before-0405-et-v1"
    assert stream.engine.bars_processed == 1
    with pytest.raises(ValueError, match="rewind"):
        stream.context(as_of=before)
    with pytest.raises(ValueError, match="strictly increasing"):
        stream.update_second(row, at=at)
