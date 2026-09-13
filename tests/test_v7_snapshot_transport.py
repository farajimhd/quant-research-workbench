from copy import deepcopy
import json
import pytest

from src.market_engine.v7_snapshot_transport import Encoder, Decoder
from tests.test_v7_qmd import make, at


def test_lossless_incremental_rewind_and_reset(tmp_path):
    service, source = make(tmp_path)
    decoder = Decoder()
    for index in [5, 9, 10, 5, 20, -1]:
        cutoff = at(source.bars[index]['t'])
        expected = deepcopy(service.snapshot('TEST', cutoff, include_segments=False, cursor_id='reference'))
        packet = service.snapshot_delta('TEST', cutoff, cursor_id='delta', base_version=decoder.version)
        # Exercise the actual JSON wire boundary, including alias restoration.
        actual = decoder.decode(json.loads(json.dumps(packet, allow_nan=False)))
        assert actual == expected
        assert actual['unified_levels'] is actual['qmd_structure_unified_levels']
    service.transports.clear()  # Worker/cache eviction must send a full base.
    packet = service.snapshot_delta('TEST', cutoff, cursor_id='delta', base_version=decoder.version)
    assert packet['base_version'] is None
    assert decoder.decode(packet) == expected


def test_delta_deletion_reorder_metadata_and_frozen_fit():
    encoder, decoder = Encoder(), Decoder()
    rows = [dict(unified_level_id=str(i), fit={'center': i, 'data': list(range(100))}) for i in range(10)]
    snapshot = dict(unified_levels=rows, qmd_structure_unified_levels=rows, as_of=1, old=True)
    old = decoder.decode(json.loads(json.dumps(encoder.encode(snapshot))))
    rows[0]['fit']['center'] = 99
    rows[:] = rows[2:] + rows[:1]
    snapshot.pop('old'); snapshot['as_of'] = 2
    packet = encoder.encode(snapshot, decoder.version)
    assert len(packet['upsert']) == 1 and packet['removed'] == ['1']
    assert decoder.decode(json.loads(json.dumps(packet))) == snapshot
    assert old['unified_levels'][0]['fit']['center'] == 0
    unchanged = encoder.encode(snapshot, decoder.version)
    assert not unchanged['upsert'] and not unchanged['metadata']
    assert len(json.dumps(unchanged)) < len(json.dumps(snapshot)) / 10
    with pytest.raises(ValueError, match='base mismatch'):
        Decoder().decode(unchanged)


def test_shared_cursor_exact_cutoffs_eviction_and_fits(tmp_path, monkeypatch):
    from src.backend.v7_book_cursor import V7BookCursor
    import src.backend.v7_book_cursor as module
    import src.backend.experimental_structure_book as books
    service, source = make(tmp_path)
    monkeypatch.setattr(books, 'resolve', lambda _: dict(ticker='TEST', fingerprint='fixture'))
    calls = []
    def request(ticker, cutoff, *, cursor_id, delta, base_version):
        calls.append(cutoff)
        return json.loads(json.dumps(service.snapshot_delta(ticker, cutoff, cursor_id=cursor_id, base_version=base_version)))
    monkeypatch.setattr(module, 'qmd_level_book_v7', request)
    cursor = V7BookCursor('level-book-v7', 'TEST', 'fixture')
    start = int(source.bars[5]['t'])
    early = deepcopy(cursor.snapshot(at(start)))
    later = deepcopy(cursor.snapshot(at(start + 10)))
    assert cursor.snapshot(at(start)) == early
    assert cursor.snapshot(at(start + 10)) == later
    assert len(calls) == 2
    for second in range(start + 11, start + 50): cursor.snapshot(at(second))
    assert len(cursor.snapshots) == 32
    assert cursor.snapshot(at(start)) == early  # Eviction rebuilds; no future state.
    assert cursor.snapshot(at(start + 10)) == later


def test_replay_frame_and_event_share_only_exact_time_snapshots(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.backend.replay_run_service import ReplayRunController
    import src.backend.v7_book_cursor as module
    import src.backend.experimental_structure_book as books
    service, source = make(tmp_path)
    monkeypatch.setattr(books, 'resolve', lambda _: dict(ticker='TEST', fingerprint='fixture', version='causal-level-book-v7-mle-1'))
    calls = []
    def request(ticker, cutoff, *, cursor_id, delta, base_version):
        calls.append(cutoff)
        return json.loads(json.dumps(service.snapshot_delta(ticker, cutoff, cursor_id=cursor_id, base_version=base_version)))
    monkeypatch.setattr(module, 'qmd_level_book_v7', request)
    run = object.__new__(ReplayRunController)
    run.definition = SimpleNamespace(experimental_structure_book='level-book-v7',
        experimental_structure_fingerprint='fixture', minimum_p_norm=.8,
        configuration_revision={'payload': {}})
    run._record_data_authority = lambda *args: None
    run._record_v7_data_authority = lambda *args: None
    first, last = at(source.bars[5]['t']), at(source.bars[10]['t'])
    async def check():
        early = await run._experimental_structure_snapshot('TEST', first, 'event')
        later = await run._experimental_structure_snapshot('TEST', last, 'frame')
        assert await run._experimental_structure_snapshot('TEST', first, 'event') == early
        assert await run._experimental_structure_snapshot('TEST', last, 'event') == later
        assert run._experimental_cursors[('TEST','event')] is run._experimental_cursors[('TEST','frame')]
        assert len(calls) == 2
    asyncio.run(check())
