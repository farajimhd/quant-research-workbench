"""Separate future targets from causal sparse inputs using synthetic prices only."""
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pytest

from research.rl_trading.v7.experiments import predictive_data as data


def test_targets_use_current_admission_and_exact_horizons_only():
    mark = np.full((1000, 2), 5.)
    seconds = np.array([10], dtype=np.int64)
    selection = np.array([[0, 1, -1, -1, -1]], dtype=np.int32)
    mark[70, 0], mark[310, 0], mark[910, 0] = 10., 2.5, 20.
    mark[10, 1] = 0.
    targets, valid = data.forward_targets(mark, seconds, selection)
    np.testing.assert_allclose(targets[0, 0], 100*np.log([2., .5, 4.]), rtol=1e-6)
    assert valid[0, 0].all() and not valid[0, 1:].any()
    assert np.all(targets[~valid] == 0)
    mark[310, 0] = 0.
    _, valid = data.forward_targets(mark, seconds, selection)
    assert valid[0, 0].tolist() == [True, False, True]


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    days = [(date(2026, 1, 2)+timedelta(days=i)).isoformat() for i in range(150)]

    class Source:
        def __init__(self, root):
            self.root = Path(root)
            self.days = days
            self.items = {day: {'selected': ['A']} for day in days}
            self.binding = {'synthetic': True}
            self.maximum_gib = 4.

        def _check_day(self, day):
            if day not in days:
                raise ValueError('Only TRAINING dates')

    class Session:
        opens = 0

        def __init__(self, manifest, day, cache_bytes):
            assert manifest.name == 'training.json'
            type(self).opens += 1

        def selection(self, clocks):
            names = np.full((len(clocks), 5), '', dtype=object)
            names[:, 0] = 'A'
            return {'tickers': names, 'valid': names != ''}

        def windows(self, names, clocks, resolution, side):
            assert len(clocks) <= 2560
            fields = ('open', 'high', 'low', 'close', 'close_size', 'event_count') if side else (
                'open', 'high', 'low', 'close', 'volume', 'trade_count', 'notional')
            value = np.ones((len(clocks), 2, len(fields)), dtype=np.float32)
            value[..., :4] = 5.
            return {'values': value, 'valid': np.ones(value.shape, dtype=bool), 'fields': fields}

    marks = np.full((57600, 1), 5.)

    def execution(session, tickers, clocks):
        shape = marks.shape
        return dict(mark=marks.copy(), bid=np.full(shape, 4.99), ask=np.full(shape, 5.01),
            quote_us=np.broadcast_to(clocks[:, None]*1000000-1, shape).copy(),
            bid_size=np.full(shape, 1000.), ask_size=np.full(shape, 1000.),
            quote_valid=np.ones(shape, dtype=bool))

    monkeypatch.setattr(data, 'TrainingDataset', Source)
    monkeypatch.setattr(data, 'Session', Session)
    monkeypatch.setattr(data, 'execution_arrays', execution)
    monkeypatch.setattr(data, 'runtime_root', lambda: tmp_path)
    dataset = data.PredictiveDataset(tmp_path/'source', tmp_path/'output')
    return dataset, marks, Session


def test_future_target_change_cannot_change_current_snapshot(synthetic):
    dataset, marks, _ = synthetic
    day = dataset.days[0]
    before = data.snapshot_arrays(dataset.source, day)
    first = int(before['seconds'][0])
    marks[first+60, 0] = 10.
    after = data.snapshot_arrays(dataset.source, day)
    for name in ('features', 'candidate_valid', 'account', 'selection'):
        np.testing.assert_array_equal(before[name][0], after[name][0])
    assert before['y'][0, 0, 0] == 0.
    assert after['y'][0, 0, 0] == pytest.approx(100*np.log(2.))
    assert len(dataset.fit_days) == 120 and len(dataset.development_days) == 30
    assert max(dataset.fit_days) < min(dataset.development_days)


def test_rejected_validation_day_does_not_open_session(synthetic):
    dataset, _, session = synthetic
    before = session.opens
    with pytest.raises(ValueError, match='TRAINING'):
        dataset.prepare('2026-12-31')
    assert session.opens == before


def test_shard_seals_restart_and_hash_tampering(synthetic):
    dataset, _, session = synthetic
    day = dataset.days[0]
    receipt = dataset.prepare(day)
    assert receipt['rows'] == 660
    assert receipt['admitted_candidates'] == 660
    assert receipt['target_valid_counts'] == [660, 660, 660]
    opens = session.opens
    assert dataset.prepare(day) == receipt
    assert session.opens == opens
    loaded = dataset.load(day)
    assert isinstance(loaded['features'], np.memmap)
    with (dataset.root/day/'y.npy').open('r+b') as stream:
        stream.seek(-1, 2)
        old = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([old[0] ^ 1]))
    with pytest.raises(ValueError, match='hash mismatch'):
        dataset.load(day)
