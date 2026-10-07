from threading import Event
import pytest
from research.vectorized_backtest.v4.torch_backtest.session_prefetch import SessionPrefetch


def test_next_load_overlaps_consumer_and_never_runs_two_ahead():
    next_started = Event()
    release = Event()
    seen = []
    def load(item):
        seen.append(item)
        if item == 1:
            next_started.set()
            assert release.wait(3)
        return item
    with SessionPrefetch(range(3), load) as queue:
        assert queue.take(0)[0] == 0
        assert next_started.wait(3)
        assert seen == [0, 1]
        release.set()
        assert queue.take(1)[0] == 1
        assert queue.take(2)[0] == 2


def test_error_propagates_in_order_and_pool_closes():
    def load(item):
        if item == 1:
            raise RuntimeError('certified input changed')
        return item
    with pytest.raises(RuntimeError, match='certified input changed'):
        with SessionPrefetch([0, 1, 2], load) as queue:
            assert queue.take(0)[0] == 0
            queue.take(1)


def test_out_of_order_consumption_rejected():
    with SessionPrefetch([0], lambda item:item) as queue:
        with pytest.raises(ValueError, match='sequential'):
            queue.take(1)


def test_prefetched_split_metadata_does_not_mutate_shared_bank(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import torch
    from research.vectorized_backtest.v4.torch_backtest import offline_data, splits
    bank = SimpleNamespace(day={'day': '2026-07-30'}, certificate_hash='cert',
                           manifest={'offsets': {}}, split_basis='original')
    tape = SimpleNamespace(tickers=[], clocks=torch.tensor([1]))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps(dict(bank_certificate_sha256='cert', listing_to_ticker={})))
    monkeypatch.setattr(offline_data, 'load_execution', lambda root: (tape, {}))
    monkeypatch.setattr(offline_data, 'bank_cached', lambda *args: bank)
    monkeypatch.setattr(splits, 'load_basis', lambda *args: ('session-specific', 'split-hash'))
    spec = dict(day='2026-07-30', execution_root='unused', feature_root='unused',
                identity_map=str(mapping), split_certificate='unused')
    loaded = offline_data.load_session(spec, isolated_banks=True)
    assert loaded[1] is not bank
    assert loaded[1].split_basis == 'session-specific'
    assert bank.split_basis == 'original'
    assert loaded[1].manifest is bank.manifest
