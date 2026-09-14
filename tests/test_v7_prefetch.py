import asyncio
from datetime import timedelta
import json
from threading import Lock
from types import SimpleNamespace

from src.backend.replay_run_service import BoundedFrameLookahead, ReplayRunController
from src.market_engine.v7_qmd import Service
from tests.test_v7_qmd import Source, at
from tests.test_v7_runtime_cache import MultiCatalog


def test_lookahead_is_bounded_and_preserves_every_input():
    read = []
    def source():
        for i in range(191):
            read.append(i)
            yield i
    frames = BoundedFrameLookahead(source())
    assert next(frames) == 0
    assert len(read) == 64
    assert [0, *frames] == list(range(191))


def test_parallel_preparation_keeps_exact_frame_event_cutoffs_and_deferred_errors(tmp_path, monkeypatch):
    import src.backend.v7_book_cursor as module
    import src.backend.experimental_structure_book as books
    monkeypatch.setattr(books, 'resolve', lambda _: dict(ticker='*', fingerprint='fixture', version='causal-level-book-v7-mle-1'))
    services = {ticker: Service(MultiCatalog(tmp_path / ticker), Source()) for ticker in ('AAA', 'BBB')}
    reference = Service(MultiCatalog(tmp_path / 'reference'), Source())
    calls = []
    guard = Lock()
    first, last = (at(reference.source.bars[i]['t']) for i in (5, 10))
    def request(ticker, cutoff, *, cursor_id, delta, base_version):
        with guard:
            calls.append((ticker, cutoff))
        if cutoff == last + timedelta(seconds=1):
            raise ValueError('source failure at later boundary')
        return json.loads(json.dumps(services[ticker].snapshot_delta(ticker, cutoff, cursor_id=cursor_id, base_version=base_version)))
    monkeypatch.setattr(module, 'qmd_level_book_v7', request)
    run = object.__new__(ReplayRunController)
    run.definition = SimpleNamespace(experimental_structure_book='level-book-v7',
        experimental_structure_fingerprint='fixture', minimum_p_norm=.8,
        configuration_revision={'payload': {'strategy': {'parameters': {'historical_hod_contract': True}}}})
    run._record_data_authority = lambda *args: None
    run._record_v7_data_authority = lambda *args: None
    async def check():
        frames = [SimpleNamespace(ticker=ticker, as_of=cutoff, timeframe='1s')
            for cutoff in (first, last, last + timedelta(seconds=1)) for ticker in services]
        await run._prefetch_v7_frames(frames, last)
        assert len(calls) == 4
        assert all(cutoff <= last for _, cutoff in calls)
        for ticker in services:
            cursor = run._v7_prefetch_cursors[ticker]
            # Future preparation cannot replace a requested earlier snapshot.
            assert cursor.snapshot(first) == reference.snapshot(ticker, first, include_segments=False)
            assert cursor.snapshot(last) == reference.snapshot(ticker, last, include_segments=False)
            early = await run._experimental_structure_snapshot(ticker, first, 'event')
            await run._experimental_structure_snapshot(ticker, last, 'frame')
            assert await run._experimental_structure_snapshot(ticker, first, 'event') == early
        await run._prefetch_v7_frames(frames[-2:], last + timedelta(seconds=1))
        for ticker in services:
            import pytest
            with pytest.raises(ValueError, match='later boundary'):
                await run._experimental_structure_snapshot(ticker, last + timedelta(seconds=1), 'frame')
    try:
        asyncio.run(check())
    finally:
        for service in [*services.values(), reference]:
            service.close()
