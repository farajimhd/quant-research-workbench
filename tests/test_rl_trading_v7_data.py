"""Training-bound shared-shard adapter; synthetic metadata and market rows only."""
from datetime import date, timedelta
import json

import numpy as np
import polars as pl
import pytest

from research.market_shards.v1.contract import CONTRACT, digest, origin_us, sha
from research.rl_trading.v7 import data


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def sealed_root(tmp_path):
    root = tmp_path/'source'
    days = [(date(2026, 1, 2)+timedelta(days=i)).isoformat() for i in range(150)]
    source = {'synthetic': True}
    source['hash'] = digest(source)
    write(root/'source.json', source)
    sessions = [dict(day=day, selected=['A', 'B'], daily=[], hourly=[day]) for day in days]
    for day in days:
        write(root/'scanner'/day/'complete.json', dict(source_hash=source['hash'], day=day, selected=['A', 'B']))
    manifest = dict(version=CONTRACT['version'], role='training', source_hash=source['hash'],
                    contract=CONTRACT, sessions=sessions, shard_seals={})
    write(root/'training.json', manifest)
    write(root/'complete.json', dict(status='complete', manifests={'training': sha(root/'training.json')},
        source_hash=source['hash'], shard_seals={}))
    # No validation.json exists. Every successful test must work without it.
    return root


def fake_session_type():
    class FakeSession:
        calls = []
        future_mark = 100.

        def __init__(self, manifest, day, cache_bytes):
            assert manifest.name == 'training.json'
            self.day = day
            self.origin = origin_us(day)
            self.calls.append(('open', day))

        def selection(self, clocks):
            names = np.full((data.DECISION_SECONDS, 5), '', dtype=object)
            names[:2, 0] = 'A'
            names[100, 0] = 'B'
            return dict(tickers=names, valid=names != '')

        def execution_pages(self):
            for page in range(16):
                seconds = [14401, 14402, 14501] if page == 0 else []
                tickers = ['A', 'A', 'B'] if page == 0 else []
                marks = [5., 0., self.future_mark] if page == 0 else []
                # At second two a withdrawn book must replace the first quote.
                yield pl.DataFrame(dict(ticker=pl.Series(tickers, dtype=pl.String),
                    time_us=pl.Series([self.origin+s*1000000 for s in seconds], dtype=pl.Int64),
                    mark=pl.Series(marks, dtype=pl.Float64),
                    bid=pl.Series([4.9, 0., 99.] if page == 0 else [], dtype=pl.Float64),
                    ask=pl.Series([5.1, 0., 101.] if page == 0 else [], dtype=pl.Float64),
                    quote_us=pl.Series([self.origin+s*1000000-1 for s in seconds], dtype=pl.Int64),
                    bid_size=pl.Series([100., 0., 20.] if page == 0 else [], dtype=pl.Float64),
                    ask_size=pl.Series([100., 0., 20.] if page == 0 else [], dtype=pl.Float64),
                    book_valid=pl.Series([1., 0., 1.] if page == 0 else [], dtype=pl.Float64)))

        def windows(self, tickers, clocks, resolution, side):
            assert len(clocks) <= 5*512
            assert np.all(clocks <= self.origin+34200_000000)
            self.calls.append(('windows', tuple(tickers), clocks.copy(), resolution, side))
            fields = ('open', 'high', 'low', 'close', 'close_size', 'event_count') if side else (
                'open', 'high', 'low', 'close', 'volume', 'trade_count', 'notional')
            values = np.ones((len(clocks), 3, len(fields)), dtype=np.float32)
            values[:, 0, :4] = 5.
            values[:, 1, :4] = 6.
            # Large unavailable values must not enter any summary.
            values[:, 2, :] = 100000.
            valid = np.ones(values.shape, dtype=bool)
            valid[:, 2, :] = False
            return dict(values=values, valid=valid, fields=fields)
    return FakeSession


def test_training_guards_reject_before_session_open(sealed_root, monkeypatch):
    dataset = data.TrainingDataset(sealed_root)
    assert len(dataset.days) == 150

    def forbidden(*args, **kwargs):
        pytest.fail('Nontraining request must not open a market session')

    monkeypatch.setattr(data, 'Session', forbidden)
    with pytest.raises(ValueError, match='TRAINING'):
        dataset.load('2026-12-31')


def test_training_manifest_hash_mismatch_fails_closed(sealed_root):
    manifest = json.loads((sealed_root/'training.json').read_text())
    manifest['sessions'][0]['selected'] = ['C']
    write(sealed_root/'training.json', manifest)
    with pytest.raises(ValueError, match='seal mismatch'):
        data.TrainingDataset(sealed_root)


def test_missing_window_values_have_explicit_masks():
    fields = ('open', 'high', 'low', 'close', 'volume', 'trade_count', 'notional')
    window = dict(values=np.full((1, 3, 7), 999., dtype=np.float32),
                  valid=np.zeros((1, 3, 7), dtype=bool), fields=fields)
    result = data.summarize(window, np.array([5.]))
    np.testing.assert_array_equal(result[0, :7], 0.)
    np.testing.assert_array_equal(result[0, 7:12], 0.)
    np.testing.assert_array_equal(result[0, 12:], 1.)


def test_causal_selection_features_and_withdrawn_execution_book(sealed_root, monkeypatch):
    fake = fake_session_type()
    monkeypatch.setattr(data, 'Session', fake)
    dataset = data.TrainingDataset(sealed_root)
    prepared = dataset.load(dataset.days[0])
    assert prepared.features.shape == (19800, 5, 98)
    assert len(prepared.feature_names) == 98
    assert prepared.clocks.shape == (57600,)
    assert prepared.selection[0].tolist() == [0, -1, -1, -1, -1]
    assert prepared.selection[100, 0] == 1
    assert np.all(prepared.features[prepared.selection < 0] == 0)
    assert prepared.features[0, 0, 0] == pytest.approx(.2)
    assert prepared.features[0, 0, 1] == pytest.approx(.2)
    assert prepared.features[0, 0, 5] == pytest.approx(2/3)
    assert prepared.execution['mark'][0, 1] == 0.  # No future-union price fill.
    assert prepared.execution['mark'][100, 1] == 100.
    assert prepared.execution['quote_valid'][0, 0]
    assert not prepared.execution['quote_valid'][1:, 0].any()
    assert np.all(prepared.execution['mark'][:, 0] == 5.)
    assert np.all(prepared.execution['bid'][1:, 0] == 0.)
    fake.future_mark = 1000.
    changed = dataset.load(dataset.days[0])
    np.testing.assert_array_equal(prepared.features[:100], changed.features[:100])
    assert not prepared.features.flags.writeable


def test_prepared_cache_is_hash_bound_and_avoids_market_reopening(sealed_root, tmp_path, monkeypatch):
    fake = fake_session_type()
    monkeypatch.setattr(data, 'Session', fake)
    monkeypatch.setattr(data, 'runtime_root', lambda: tmp_path)
    dataset = data.TrainingDataset(sealed_root)
    cache = tmp_path/'prepared'
    first = dataset.load(dataset.days[0], cache_root=cache)
    calls = len(fake.calls)
    second = dataset.load(dataset.days[0], cache_root=cache)
    assert len(fake.calls) == calls
    assert isinstance(second.features, np.memmap)
    np.testing.assert_array_equal(first.features, second.features)
    with (cache/dataset.days[0]/'features.npy').open('r+b') as stream:
        stream.seek(-1, 2)
        byte = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([byte[0] ^ 1]))
    with pytest.raises(ValueError, match='cache hash mismatch'):
        dataset.load(dataset.days[0], cache_root=cache)


def test_future_execution_quote_is_rejected(sealed_root, monkeypatch):
    fake = fake_session_type()
    original = fake.execution_pages

    def future_pages(self):
        for index, frame in enumerate(original(self)):
            if index == 0:
                frame = frame.with_columns((pl.col('time_us')+1).alias('quote_us'))
            yield frame

    fake.execution_pages = future_pages
    monkeypatch.setattr(data, 'Session', fake)
    dataset = data.TrainingDataset(sealed_root)
    with pytest.raises(ValueError, match='future quote timestamp'):
        dataset.load(dataset.days[0])
