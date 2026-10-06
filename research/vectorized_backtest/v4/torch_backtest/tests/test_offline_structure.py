"""Opening-known references, real stream/cache and masked-reference behavior."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import polars as pl
from research.rl_trading.v6 import reference
from research.vectorized_backtest.v4.torch_backtest import offline_structure as module
from research.vectorized_backtest.v4.torch_backtest.structural import stream_ticker
from research.vectorized_backtest.v4.torch_backtest.tests.test_structural import seed,inputs,DAY


def test_native_offline_stream_matches_canonical_and_reuses_bound_cache(tmp_path,monkeypatch):
    rows,asks,clocks=inputs()
    frame=pl.DataFrame(rows).with_columns(pl.lit('TEST').alias('ticker'),
        pl.lit(1).alias('price_valid_1000'),pl.lit(1).alias('extremes_valid_1000'))
    watch=pl.DataFrame({'ticker':['TEST','MISSING']})
    def runtime(path):
        path.mkdir(parents=True,exist_ok=True)
        return path
    monkeypatch.setattr(module,'require_runtime',runtime)
    # Exercise real stream, locks, durable cache and validation without Windows
    # spawn importing a monkeypatched reference reader in a different process.
    monkeypatch.setattr(module,'ProcessPoolExecutor',ThreadPoolExecutor)
    monkeypatch.setattr(reference,'read_reference',lambda reader,day,listing:
        (seed(),[],{}, {'hash':'known'}) if listing['ticker']=='TEST'
        else (None,[],{}, {'hash':'missing'}))
    quotes=np.stack([asks,asks],axis=1)
    market=SimpleNamespace(sessions=(DAY,))
    first=module.prepare_offline_structure(None,market,watch,frame,quotes,clocks,tmp_path,'source',workers=1,progress=lambda e:None)
    second=module.prepare_offline_structure(None,market,watch,frame,quotes,clocks,tmp_path,'source',workers=1,progress=lambda e:None)
    expected,valid,_=stream_ticker('TEST',DAY,seed(),[],rows,asks,clocks)
    np.testing.assert_array_equal(first.targets[:,0],expected)
    np.testing.assert_array_equal(first.valid[:,0],valid)
    assert np.isposinf(first.targets[:,1]).all() and not first.valid[:,1].any()
    assert first.metrics['completed']==2 and first.metrics['missing_prior_v7']==1
    assert first.token==second.token
