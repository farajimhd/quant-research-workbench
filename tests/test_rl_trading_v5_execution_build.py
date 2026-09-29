from pathlib import Path

import numpy as np
import polars as pl

from research.rl_trading.v1 import build_v5_execution as builder
from research.rl_trading.v1.v5_feature_binding import FeatureBinding
from src.market_engine.level_book_store import write


def test_execution_build_certifies_and_reuses_one_pinned_listing(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtimes'
    runtime.mkdir()
    supervision = runtime / 'supervision'
    features = runtime / 'features'
    supervision.mkdir()
    features.mkdir()
    manifest = runtime / 'market.json'
    ledger = runtime / 'ledger.sqlite3'
    manifest.touch()
    ledger.touch()
    write(features / 'plan.json', dict(date='2026-07-30',
        tickers=['ABC'], market_build_id='pinned-build'))
    binding = FeatureBinding('2026-07-30', ('ABC',), 57_481, 1,
        features / 'features.npy', 'feature-hash',
        supervision / 'orders.parquet', 'orders-hash', 'supervision-hash')
    source = dict(build_id='pinned-build', definition_hash='definition-hash',
        units={'2026-07-30':{'ABC':{'bars':{'attempt_id':'a', 'output_hash':'b'},
                                    'technical':{'attempt_id':'c', 'output_hash':'d'}}}})
    calls = []

    class Client:
        def close(self):
            calls.append('close')

    monkeypatch.setattr(builder, 'runtime_root', lambda: runtime)
    monkeypatch.setattr(builder, 'bind_existing_features', lambda *args: binding)
    monkeypatch.setattr(builder.arte_source, 'load_build', lambda *args, **kwargs: source)
    monkeypatch.setattr(builder.arte_source, 'storage_check', lambda client: None)
    monkeypatch.setattr(builder, 'load_env_files', lambda *args, **kwargs: None)
    monkeypatch.setattr(builder, 'ArteReader', lambda **kwargs: Client())
    monkeypatch.setattr(builder, 'verify_execution_source',
                        lambda *args: calls.append('verify'))
    monkeypatch.setattr(builder, 'read_prior_close', lambda *args: 9.)
    bars = pl.DataFrame(dict(bucket_index=[14_400], open_int=[100_000],
        close_int=[105_000], price_valid=[1], volume=[100.], trade_count=[10]))
    monkeypatch.setattr(builder, 'read_execution_bars', lambda *args: bars)
    kwargs = dict(supervision_root=supervision, shard_root=features,
                  market_manifest=manifest, market_ledger=ledger)
    root = builder.build(**kwargs)
    assert (root / 'complete.json').is_file()
    assert np.load(root / 'next_open.npy', mmap_mode='r')[0, 1] == 10.
    assert calls == ['verify', 'close']
    assert builder.build(**kwargs) == root
    assert calls == ['verify', 'close']
