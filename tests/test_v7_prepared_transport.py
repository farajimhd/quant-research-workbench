import pytest

from src.backend.v7_book_cursor import PreparedV7Cursors
from src.market_engine.v7_prepared_stream import PreparedStream
from tests.test_v7_prepared_stream import fixture, model
from tests.test_v7_qmd import make, at


def test_batch_chains_deltas_and_preserves_old_cutoffs(tmp_path, monkeypatch):
    service, source = make(tmp_path)
    path = tmp_path / 'frames.sqlite3'
    fixture(path, source.bars)
    stream = PreparedStream(service, path, '2026-08-21', 'fixture')
    stream.prepare(['TEST'])
    service.prepared_streams['test'] = stream
    pool = PreparedV7Cursors('test', dict(fingerprint='fixture'), ['TEST'])
    packets = []
    def post(url, request, **kwargs):
        rows = service.prepared(request)
        packets.extend(rows)
        return dict(rows=rows)
    monkeypatch.setattr('src.backend.qmd_gateway_client.qmd_history_post_json', post)
    try:
        cuts = [at(b['t']) for b in source.bars]
        pool.fetch([('TEST', cut) for cut in cuts])
        assert packets[0]['packet']['base_version'] is None
        assert all(row['packet']['base_version'] for row in packets[1:])
        for cut in cuts:
            actual = pool.cursors['TEST'].snapshot(cut)
            expected = service.snapshot('TEST', cut, include_segments=False)
            assert model(actual) == model(expected)
        before = len(packets)
        pool.fetch([('TEST', cut) for cut in cuts])
        assert len(packets) == before
        pool.close()
        assert not service.prepared_streams
    finally:
        service.close()


@pytest.mark.parametrize('response', [dict(rows=[]), None])
def test_transport_failure_is_deferred_to_consumption(monkeypatch, response):
    def post(*args, **kwargs):
        if response is None:
            raise OSError('transport unavailable')
        return response
    monkeypatch.setattr('src.backend.qmd_gateway_client.qmd_history_post_json', post)
    pool = PreparedV7Cursors('test', dict(fingerprint='fixture'), ['TEST'])
    cut = at(1787299201)
    pool.fetch([('TEST', cut)])
    with pytest.raises(ValueError, match='incomplete|unavailable'):
        pool.cursors['TEST'].snapshot(cut)


def test_preparation_rejects_changed_file(tmp_path):
    service, source = make(tmp_path)
    path = tmp_path / 'frames.sqlite3'
    fixture(path, source.bars)
    stream = PreparedStream(service, path, '2026-08-21', 'fixture')
    try:
        with path.open('ab') as handle:
            handle.write(b'changed')
        with pytest.raises(ValueError, match='changed during warm-up'):
            stream.prepare(['TEST'])
    finally:
        stream.close()
        service.close()


def test_assignment_index_tracks_replacement_and_new_legs():
    from dataclasses import replace
    from tests.test_long_momentum_strategy import AssignedLongMomentumStrategy, assignment
    first=assignment()
    strategy=AssignedLongMomentumStrategy([first])
    updated=replace(first,state={'marker': 'updated'})
    strategy.upsert_assignment(updated)
    second=replace(first,assignment_id='second',account_id='DU456')
    strategy.upsert_assignment(second)
    assert strategy.assignments_for_ticker('aapl') == (updated,second)
    assert strategy.assignments_for_ticker('MSFT') == ()


def test_resumed_source_clock_identity_rejects_changed_policy():
    from src.market_engine.v7_prepared_stream import source_clock_identity
    original='42:100:0:2026-08-23 14:00:00:Archive:1:2:split-sha256:abc:structure-input-v1:clock'
    wider='42:900:0:2026-08-23 14:00:00:Archive:0:9:split-sha256:abc:structure-input-v1:clock'
    assert source_clock_identity(original)==source_clock_identity(wider)
    assert source_clock_identity(original)!=source_clock_identity(wider.replace('abc','changed'))


def test_prefetch_covers_intrasecond_frames_without_future_observation():
    import asyncio
    from types import SimpleNamespace
    from src.backend.replay_run_service import ReplayRunController
    seen=[]
    frames=[SimpleNamespace(ticker='TEST',timeframe=frame,as_of=at(second))
        for frame,second in [('100ms',1787299201.1),('1s',1787299202),('100ms',1787299202.8),('5s',1787299205)]]
    controller=SimpleNamespace(definition=SimpleNamespace(experimental_structure_book='level-book-v7'),
        _prepared_v7=SimpleNamespace(fetch=lambda requests:seen.extend(requests)),_record_stage_time=lambda *args:None)
    asyncio.run(ReplayRunController._prefetch_v7_frames(controller,frames,at(1787299202)))
    assert seen==[('TEST',at(1787299201)),('TEST',at(1787299202)),('TEST',at(1787299202))]
