import sqlite3
import pytest
from src.market_engine.v7_preparation_cache import ArtifactCache, identity
from src.market_engine import v7_catalog as V
from tests.test_v7_catalog import prepared
from tests.test_v7_prepared_stream import fixture
from tests.test_v7_qmd import make, at
from src.market_engine.v7_prepared_stream import PreparedStream
from src.market_engine.v7_snapshot_transport import Decoder


def test_durable_selection_reuses_verified_payload_and_invalidates_mutation(tmp_path, monkeypatch):
    target, _ = prepared(tmp_path, monkeypatch)
    original = V.Catalog(tmp_path).select('TEST', '2026-08-21')
    def forbidden(*args):
        raise AssertionError('Full checkpoint verification repeated')
    monkeypatch.setattr(V, 'verified_book', forbidden)
    assert V.Catalog(tmp_path).select('TEST', '2026-08-21') == original
    path = target / 'books' / '2026-08-20.json.gz'
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(AssertionError, match='verification repeated'):
        V.Catalog(tmp_path).select('TEST', '2026-08-21')


def test_absent_publication_is_invalidated(tmp_path):
    cache = ArtifactCache(tmp_path / 'cache')
    source = tmp_path / 'future.json'
    cache.put('key', {'verified': True}, {str(source): None})
    assert cache.get('key') == {'verified': True}
    source.write_text('{}')
    assert cache.get('key') is None


def test_corrupt_cache_fails_closed_and_space_is_bounded(tmp_path):
    cache = ArtifactCache(tmp_path, max_bytes=200)
    cache.put('first', bytes(range(128)), {})
    cache.put('second', bytes(reversed(range(128))), {})
    assert cache.get('first') is None
    with sqlite3.connect(cache.path) as db:
        db.execute("UPDATE artifacts SET checksum='bad' WHERE key='second'")
    with pytest.raises(ValueError, match='checksum mismatch'):
        cache.get('second')


def test_mutated_source_during_build_cannot_be_certified(tmp_path):
    cache = ArtifactCache(tmp_path / 'cache')
    source = tmp_path / 'source'
    source.write_text('before')
    deps = {str(source): identity(source)}
    source.write_text('after')
    with pytest.raises(ValueError, match='changed during preparation'):
        cache.put('key', {}, deps)


def test_new_stream_reuses_zero_input_seed_and_bars_without_sharing_playback(tmp_path, monkeypatch):
    service, source = make(tmp_path)
    path = tmp_path / 'frames.sqlite3'
    fixture(path, source.bars)
    first = PreparedStream(service, path, '2026-08-21', 'fixture')
    try:
        assert not first.prepare(['TEST'])[0]['opening_seed_reused']
        expected = Decoder().decode(first.snapshot('TEST', at(source.bars[-1]['t'])))
    finally:
        first.close()
        service.close()
    service, source = make(tmp_path)
    second = PreparedStream(service, path, '2026-08-21', 'fixture')
    try:
        def forbidden(*args, **kwargs):
            raise AssertionError('Opening kernel rebuilt')
        monkeypatch.setattr('src.market_engine.v7_prepared_stream.StreamingLevelBook', forbidden)
        receipt = second.prepare(['TEST'])[0]
        assert receipt['prepared_bars_reused'] and receipt['opening_seed_reused']
        assert second.states['TEST']['engine'].bars_processed == 0
        assert Decoder().decode(second.snapshot('TEST', at(source.bars[-1]['t']))) == expected
    finally:
        second.close()
        service.close()
