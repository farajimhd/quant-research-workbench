from datetime import date
from types import SimpleNamespace

import pytest

from pipelines.strategy_one import strategy_forty_three_structure as producer
from src.backend.structural_v7_seed import load_seed
from tests.test_structural_v7_seed import Client
from tests.test_strategy_one_v7_interval_derivation import _plans


def setup(monkeypatch):
    prior = load_seed(Client(), ticker="TEST", session=date(2026, 8, 18))
    market, seeds = _plans(prior)
    read = SimpleNamespace(execute=lambda _: None, iter_json_each_row=lambda _: iter(()))
    bar = dict(ticker="TEST", resolution_ms=1000, bucket_index=14_700,
        price_valid=1, extremes_valid=1, open_int=100_000, high_int=100_100,
        low_int=99_900, close_int=100_050, volume=100.)
    monkeypatch.setattr(producer, "load_seed", lambda *_args, **_kwargs: prior)
    monkeypatch.setattr(producer, "split_evidence", lambda *_args, **_kwargs: [])
    return prior, market, seeds, read, bar


def test_point_witness_uses_real_stream_only_through_admission(monkeypatch):
    prior, market, seeds, read, bar = setup(monkeypatch)
    seen = []
    def rows(_market, **kwargs):
        seen.append(kwargs["through_boundary_ms"])
        return iter((bar,))
    monkeypatch.setattr(producer, "iter_persisted_v7_seconds", rows)
    witness = producer.derive_entry(market=market, seeds=seeds, session_date="2026-08-18",
        ticker="TEST", boundary_ms=301_000, reader=read)
    assert seen == [301_000]
    assert witness.boundary_ms == 301_000 and witness.levels
    assert witness.source_checkpoint_hash == prior["source_checkpoint_hash"]
    assert witness.decoded_seed_hash == prior["checkpoint_hash"]
    assert all(row[5] <= int(producer.market_day_boundary("2026-08-18", 301_000).timestamp() * 1000)
               for row in witness.levels)


def test_returned_future_bar_fails_instead_of_silently_ignoring_it(monkeypatch):
    _, market, seeds, read, bar = setup(monkeypatch)
    monkeypatch.setattr(producer, "iter_persisted_v7_seconds",
        lambda *_args, **_kwargs: iter((bar, {**bar, "bucket_index": 14_701})))
    with pytest.raises(ValueError, match="future or unordered"):
        producer.derive_entry(market=market, seeds=seeds, session_date="2026-08-18",
            ticker="TEST", boundary_ms=301_000, reader=read)


def test_tested_day_seed_cannot_be_used_for_entry_witness(monkeypatch):
    prior, market, seeds, read, _ = setup(monkeypatch)
    monkeypatch.setattr(producer, "load_seed", lambda *_args, **_kwargs: {**prior, "session": "2026-08-18"})
    with pytest.raises(ValueError, match="precede"):
        producer.derive_entry(market=market, seeds=seeds, session_date="2026-08-18",
            ticker="TEST", boundary_ms=301_000, reader=read)
