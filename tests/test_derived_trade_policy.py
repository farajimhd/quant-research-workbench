from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.market_engine.derived_trade_policy import eligible_trade_time, eligible_completed_second, POLICY
from src.market_engine.streaming_level_book import StreamingLevelBook
from tests.test_streaming_level_book import engine


@pytest.mark.parametrize('month,hour', [(1, 9), (8, 8)])
def test_cutoff_is_new_york_time_across_dst(month, hour):
    cutoff = datetime(2026, month, 21, hour, 5, tzinfo=timezone.utc).timestamp()
    assert not eligible_trade_time(cutoff-.001)
    assert eligible_trade_time(cutoff)
    assert not eligible_completed_second(cutoff)
    assert eligible_completed_second(cutoff+1)


def test_excluded_seconds_do_not_seed_hod_noise_or_levels_and_are_audited():
    stream = engine()
    for offset in [1, 150, 300]:
        stream.update(dict(t=stream.start+offset, open=100, high=200, low=1, close=100, volume=1000))
    assert stream.hod is None and stream.previous is None and not stream.small
    assert stream.bars_processed == 0 and stream.excluded_early_seconds == 3
    resumed = StreamingLevelBook.restore(stream.checkpoint())
    resumed.update(dict(t=stream.start+301, open=10, high=11, low=9, close=10, volume=100))
    assert resumed.hod == 11 and resumed.lod == 9 and resumed.bars_processed == 1
    assert resumed.snapshot()['input_policy'] == POLICY


def test_replay_hod_excludes_early_trade_and_resets_next_session():
    from src.backend.replay_run_service import ReplayRunService
    # Resolve the controller class exposing the shared accumulator without services.
    import src.backend.replay_run_service as module
    cls = next(v for v in vars(module).values() if isinstance(v, type) and hasattr(v, '_experimental_session_high'))
    controller = SimpleNamespace(definition=SimpleNamespace(experimental_structure_book='level-book-v7'))
    cutoff = datetime(2026, 8, 21, 8, 5, tzinfo=timezone.utc)
    assert cls._experimental_session_high(controller, 'TEST', cutoff.replace(minute=4), 100) is None
    assert cls._experimental_session_high(controller, 'TEST', cutoff, 10) == 10
    assert cls._experimental_session_high(controller, 'TEST', cutoff.replace(day=22), 9) == 9
