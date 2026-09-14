from datetime import datetime, timezone
from copy import deepcopy
import pytest

from src.market_engine.v7_qmd import Service, QmdSource
from src.market_engine.v7_runtime_cache import RuntimeCache
from src.market_engine.historical_level_checkpoint import digest
from tests.test_v7_qmd import Catalog, Source, at


class MultiCatalog(Catalog):
    def select(self, ticker, day):
        value, provenance = super().select(ticker, day)
        value['ticker'] = ticker
        value['checkpoint_hash'] = digest({k: v for k, v in value.items() if k != 'checkpoint_hash'})
        return value, provenance


def test_eviction_is_exact_for_interleaved_tickers_rewind_and_late_future_input(tmp_path):
    source = Source()
    cached = Service(MultiCatalog(tmp_path / 'cached'), source, max_sessions=2)
    reference = Service(MultiCatalog(tmp_path / 'reference'), deepcopy(source), max_sessions=64)
    try:
        for second in (5, 9, 14, 7, 17):
            for ticker in ('AAA', 'BBB', 'CCC', 'DDD', 'EEE'):
                cutoff = at(source.bars[second]['t'])
                expected = reference.snapshot(ticker, cutoff, cursor_id='run')
                assert cached.snapshot(ticker, cutoff, cursor_id='run') == expected
                assert expected['max_input_timestamp'] <= cutoff.timestamp()
        assert cached.cache.metrics['state_restores'] >= 15
        assert len(cached.sessions) == 2
    finally:
        cached.close()
        reference.close()


def test_spilled_state_corruption_fails_closed(tmp_path):
    cache = RuntimeCache(tmp_path)
    try:
        cache.save_state(('T',), {'cutoff': 10})
        cache.db.execute("UPDATE states SET sha256='corrupt'")
        with pytest.raises(ValueError, match='integrity'):
            cache.take_state(('T',))
    finally:
        cache.close()


def test_worker_shutdown_protocol_does_not_boot_an_unused_engine():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-B', 'scripts/qmd_level_book_v7_worker.py'],
        input='{"operation":"shutdown"}\n', text=True, capture_output=True, timeout=30, check=True)
    import json
    assert json.loads(result.stdout) == {'ok': True, 'result': {'closed': True}}


def test_delta_bases_survive_full_market_style_eviction(tmp_path):
    from src.market_engine.v7_snapshot_transport import Decoder
    service = Service(MultiCatalog(tmp_path), Source(), max_sessions=1)
    decoders = {ticker: Decoder() for ticker in ('AAA', 'BBB', 'CCC')}
    try:
        for i, second in enumerate((5, 9, 14, 7, 17)):
            for ticker, decoder in decoders.items():
                cutoff = at(service.source.bars[second]['t'])
                packet = service.snapshot_delta(ticker, cutoff, cursor_id='run', base_version=decoder.version)
                assert (packet['base_version'] is not None) == (i > 0)
                assert decoder.decode(packet) == service.snapshot(ticker, cutoff, include_segments=False, cursor_id='run')
    finally:
        service.close()


def test_historical_source_reads_once_and_never_exposes_future_rows(tmp_path, monkeypatch):
    from src.backend import qmd_gateway_client
    calls = []
    def fetch(path, query, **options):
        ticker = path.rsplit('/', 1)[-1]
        calls.append(ticker)
        return {'complete': True, 'source_revision': {'request_complete': True}, 'bars': [
            dict(sym=ticker, timeframe='1s', is_closed=True, bar_end=f'2026-08-21T08:00:0{i}+00:00',
                 open=10., high=11., low=9., close=10., volume=100.) for i in range(1, 9)]}
    monkeypatch.setattr(qmd_gateway_client, 'qmd_history_get_json', fetch)
    cache = RuntimeCache(tmp_path)
    source = QmdSource(cache)
    start = datetime(2026, 8, 21, 8, tzinfo=timezone.utc).timestamp()
    try:
        for cutoff in (2, 5, 3, 8):
            for ticker in ('A', 'B', 'C', 'D', 'E', 'F'):
                rows, _ = source.seconds(ticker, '2026-08-21', start, start + cutoff, 'history')
                assert [r['t'] for r in rows] == [start + i for i in range(1, cutoff + 1)]
        assert len(calls) == 6
        assert cache.metrics['source_hits'] == 18
    finally:
        cache.close()
