"""Exact streaming parity, prefix causality, durable cache and replay witnesses."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from concurrent.futures import ProcessPoolExecutor
from zoneinfo import ZoneInfo
import json

import numpy as np
import pytest
import torch

from src.backend.fixed_v7_stream import FixedV7Stream
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.streaming_level_book import StreamingLevelBook, EXTRACTION_VERSION
from src.market_engine.reaction_band import CONFIG
from research.vectorized_backtest.v6.torch_backtest import Candidate, SqueezeRunner
from research.vectorized_backtest.v6.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v6.torch_backtest.structural import (
    FIELDS, _claim, _compute, _initialize_worker, _load, _signature, algorithm_hash, stream_ticker)

DAY = '2026-08-24'
NY = ZoneInfo('America/New_York')


def seed():
    prior = dict(ticker="TEST",session="2026-08-20",available_at=0,levels=[],
                 source_extraction_version=EXTRACTION_VERSION,band_config=CONFIG)
    prior["checkpoint_hash"] = digest(prior)
    book = StreamingLevelBook(prior,ticker="TEST",session="2026-08-21",start=1000,end=10000)
    for i, price in enumerate((10.5, 10.501, 10.499)):
        book._proposal(price, 1100+i*10, 'resistance', {'t':1102+i*10}, .1)
    value = book.historical_checkpoint('fixture')
    value.update(session='2026-08-21', available_at=datetime(2026,8,21,20,tzinfo=NY).timestamp(),
                 input_policy=POLICY, source_checkpoint_hash='a'*64)
    value['checkpoint_hash'] = digest({k:v for k,v in value.items() if k!='checkpoint_hash'})
    return value


def inputs():
    start = int(datetime(2026,8,24,4,tzinfo=NY).timestamp())
    clocks = np.arange(start+301, start+341, dtype=np.int64)
    prices = np.resize(np.array([10,10.04,10.08,10.12,10.08,10.04,10]), len(clocks))
    ints = (prices*10000).astype(np.int64)
    rows = dict(zip(FIELDS, (clocks*1000000, ints, ints+1, ints-1, ints,
                            np.full(len(clocks),100.0))))
    return rows, prices+.005, clocks


def test_exact_canonical_projection_and_future_suffix_causality():
    rows, asks, clocks = inputs()
    actual, valid, _ = stream_ticker('TEST', DAY, seed(), (), rows, asks, clocks)
    expected = np.full_like(actual, np.inf)
    canonical = FixedV7Stream(seed(), ticker='TEST', session=date.fromisoformat(DAY))
    for index, clock in enumerate(clocks):
        canonical.update_second(dict(resolution_ms=1000,price_valid=1,extremes_valid=1,
            open_int=int(rows[FIELDS[1]][index]), high_int=int(rows[FIELDS[2]][index]),
            low_int=int(rows[FIELDS[3]][index]), close_int=int(rows[FIELDS[4]][index]),volume=100),
            at=datetime.fromtimestamp(int(clock),NY))
        levels = canonical.strategy_one_levels(as_of=datetime.fromtimestamp(int(clock),NY), seed_policy=POLICY)
        lower = sorted(float(r['lower']) for r in levels if r['role']=='resistance' and r['lower']>asks[index])[:15]
        expected[index,:len(lower)] = lower
    assert np.isfinite(actual).any()  # Parity must exercise real prior geometry.
    np.testing.assert_array_equal(actual, expected)
    assert valid.all()
    changed = {field:value.copy() for field,value in rows.items()}
    for field in FIELDS[1:5]:
        changed[field][20:] += 15000
    modified, _, _ = stream_ticker('TEST', DAY, seed(), (), changed, asks, clocks)
    np.testing.assert_array_equal(actual[:20], modified[:20])


@pytest.mark.parametrize('field,value', [('session',DAY),('available_at',datetime(2026,8,24,20,tzinfo=NY).timestamp())])
def test_same_day_or_late_checkpoint_rejected(field,value):
    rows, asks, clocks = inputs()
    value_seed = seed(); value_seed[field]=value
    with pytest.raises(ValueError,match='precede|available'):
        stream_ticker('TEST',DAY,value_seed,(),rows,asks,clocks)


def test_missing_seconds_are_not_structural_updates():
    rows, asks, clocks = inputs()
    sparse = {f:np.delete(v,10) for f,v in rows.items()}
    targets, valid, _ = stream_ticker('TEST',DAY,seed(),(),sparse,asks,clocks)
    assert not valid[10] and np.isposinf(targets[10]).all()
    assert valid[11]
    for field in FIELDS[1:5]:
        sparse[field][0] = 0
    with pytest.raises(ValueError, match='OHLC'):
        stream_ticker('TEST',DAY,seed(),(),sparse,asks,clocks)


def test_top_only_projection_retains_every_causal_bar(monkeypatch):
    from research.vectorized_backtest.v6.torch_backtest.sparse_structure import ranked_asks
    rows,asks,clocks=inputs()
    expected,expected_valid,_=stream_ticker('TEST',DAY,seed(),(),rows,asks,clocks)
    top=np.full((len(clocks),1),8,dtype=np.int64);top[20:]=7
    filtered=ranked_asks(asks,clocks,7,clocks,top)
    calls=[];original=FixedV7Stream.strategy_one_levels
    def observed(self,*args,**kwargs):
        calls.append(kwargs['as_of']);return original(self,*args,**kwargs)
    monkeypatch.setattr(FixedV7Stream,'strategy_one_levels',observed)
    actual,valid,metrics=stream_ticker('TEST',DAY,seed(),(),rows,filtered,clocks)
    assert len(calls)==20 and metrics['consumed_bars']==len(clocks)
    assert np.isposinf(actual[:20]).all()
    np.testing.assert_array_equal(actual[20:],expected[20:])
    np.testing.assert_array_equal(valid,expected_valid)
    with pytest.raises(ValueError,match='clock identity'):
        ranked_asks(asks,clocks+1,7,clocks,top)


def test_process_preparation_cache_integrity_and_lock_restart(tmp_path):
    rows, asks, clocks = inputs()
    value_seed = seed()
    signature = _signature('TEST',DAY,value_seed,(),rows,asks,clocks,'certified-source',algorithm_hash())
    # Signatures must survive the durable JSON representation (splits are sequences).
    signature = json.loads(json.dumps(signature))
    with _claim(tmp_path):
        with pytest.raises(RuntimeError,match='owned'):
            with _claim(tmp_path):
                pass
        with ProcessPoolExecutor(max_workers=1, initializer=_initialize_worker) as pool:
            pool.submit(_compute,'TEST',DAY,value_seed,(),rows,asks,clocks,tmp_path,signature).result(timeout=90)
    with _claim(tmp_path):
        targets, valid, receipt = _load(tmp_path,signature,len(clocks))
    reference, ref_valid, _ = stream_ticker('TEST',DAY,seed(),(),rows,asks,clocks)
    np.testing.assert_array_equal(targets,reference)
    np.testing.assert_array_equal(valid,ref_valid)
    assert receipt['seed_session'] < DAY
    with (tmp_path/'arrays.npz').open('ab') as stream:
        stream.write(b'corruption')
    with pytest.raises(ValueError,match='integrity'):
        _load(tmp_path,signature,len(clocks))


def test_dense_target_replay_matches_interval_fixture():
    legacy = synthetic_tape()
    dense = replace(legacy,structural_targets=legacy.level_lower[None].expand(len(legacy.clocks),-1,-1).clone()).validate()
    candidate = Candidate('signal',positions=15,target='structural')
    old = SqueezeRunner(legacy,[candidate]); new = SqueezeRunner(dense,[candidate])
    a,b=old.run(),new.run()
    assert torch.equal(old.ledger,new.ledger)
    assert torch.equal(a['equity'],b['equity'])


def test_pool_cache(monkeypatch,tmp_path):
    import polars as pl
    from types import SimpleNamespace
    from src.backend import structural_v7_seed
    from research.vectorized_backtest.v6.torch_backtest.structural import prepare_structure
    rows, asks, clocks = inputs()
    tickers = ('A','B')
    def books(reader,*,tickers,**kwargs):
        result = {}
        for ticker in tickers:
            value = seed(); value['ticker']=ticker
            value['checkpoint_hash']=digest({k:v for k,v in value.items() if k!='checkpoint_hash'})
            result[ticker]=value
        return result
    monkeypatch.setattr(structural_v7_seed,'load_seeds_batch',books)
    monkeypatch.setattr(structural_v7_seed,'split_evidence_batch',
        lambda reader,*,seed_sessions,**kwargs:{t:[] for t in seed_sessions})
    bars = pl.concat([pl.DataFrame(rows).with_columns(pl.lit(t).alias('ticker'),
        pl.lit(1).alias('price_valid_1000'),pl.lit(1).alias('extremes_valid_1000')) for t in tickers])
    market = SimpleNamespace(sessions=(DAY,),build_id='certified')
    seeds = SimpleNamespace(build_id='certified',units=[dict(ticker=t,backtest_session=DAY) for t in tickers])
    events = []
    kwargs = dict(workers=2,progress=events.append)
    cold = prepare_structure(None,market,seeds,bars,tickers,np.stack((asks,asks),axis=1),clocks,tmp_path,'source',**kwargs)
    warm = prepare_structure(None,market,seeds,bars,tickers,np.stack((asks,asks),axis=1),clocks,tmp_path,'source',**kwargs)
    np.testing.assert_array_equal(cold.targets,warm.targets)
    assert cold.token == warm.token and cold.metrics['reused']==0 and warm.metrics['reused']==2
    assert events[-1]['completed']==2 and events[-1]['total']==2


def test_replay_source_seal_is_independent_of_cold_or_warm_timings():
    from research.vectorized_backtest.v6.torch_backtest.prepare import tape_fingerprint
    cold = dict(source_key='source',structural_token='causal-stream',seed_token='prior',
                preparation={'seconds':100},structural_preparation={'reused':0,'workers':2})
    warm = {**cold,'preparation':{'seconds':2},'structural_preparation':{'reused':100,'workers':32}}
    assert tape_fingerprint(cold)==tape_fingerprint(warm)
    assert tape_fingerprint(cold)!=tape_fingerprint({**warm,'structural_token':'different-bars'})
