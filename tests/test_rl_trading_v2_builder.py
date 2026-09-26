"""Certified cache reuse, exact supplemental fields, and real Windows spawn workers."""
from datetime import date
from pathlib import Path
import os
import time
import numpy as np
import polars as pl
import pytest
from research.rl_trading.v2 import build_data, v1_cache
from research.rl_trading.v2.build_workers import results
from research.rl_trading.v2.io import write, read, REPO
from research.rl_trading.v1.common import digest, file_hash


def publish(root,plan,**complete):
    plan = dict(plan)
    plan['plan_hash'] = digest(plan)
    write(root/'plan.json',plan)
    write(root/'complete.json',dict(plan_hash=plan['plan_hash'],**complete))
    return plan


@pytest.fixture
def cache(tmp_path,monkeypatch):
    monkeypatch.setattr(v1_cache,'SECONDS',8)
    names = list(v1_cache.FEATURE_NAMES)
    listings = [dict(ticker='A',listing_id='a'),dict(ticker='B',listing_id='b')]
    units = {x['ticker']:dict(bars=dict(attempt_id='pinned')) for x in listings}
    source = dict(build_id='build',definition_hash='definition',units={'2026-08-20':units})
    p1 = publish(tmp_path/'p1',dict(date='2026-08-20',source_build_id='build',
        source_definition_hash='definition',source_units=units))
    p2 = publish(tmp_path/'p2',dict(date='2026-08-20',selected=listings,
        phase1_root=str(tmp_path/'p1'),phase1_plan_hash=p1['plan_hash']))
    root = tmp_path/'bank'
    hashes = {name:file_hash(REPO/name if name.startswith('src/') else
        REPO/'research/rl_trading/v1'/name) for name in (
        'features.py','reference_features.py','arte_source.py','arte_sql.py',
        'src/backend/fixed_v7_stream.py','src/backend/structural_v7_seed.py',
        'src/market_engine/streaming_level_book.py','src/market_engine/v7_qmd.py')}
    root.mkdir()
    features = np.arange(8*len(names),dtype=np.float32).reshape(1,8,len(names))
    volume = np.arange(8,dtype=np.float64)[None,:]
    np.save(root/'features.npy',features)
    np.save(root/'volume_60s.npy',volume)
    plan = publish(root,dict(version='rl-trading-structural-shards-v5',date='2026-08-20',
        market_build_id='build',feature_names=names,code_hashes=hashes,tickers=['A'],
        phase2_root=str(tmp_path/'p2'),phase2_plan_hash=p2['plan_hash'],phase1_plan_hash=p1['plan_hash']),
        files={name:file_hash(root/name) for name in ('features.npy','volume_60s.npy')})
    write(root/'progress.json',dict(plan_hash=plan['plan_hash'],done=dict(A=dict(
        features=build_data.bank_hash(features[0]),volume=build_data.bank_hash(volume[0]),
        reference={'coverage':'fixture'}))))
    return root,source,listings,features


def lookup(cache):
    root,source,listings,_ = cache
    return v1_cache.catalog([root],source=source,day=date(2026,8,20),listings=listings)


def test_cache_copies_only_matching_market_rows_without_teacher_arrays(cache):
    root,_,_,features = cache
    rows,report = lookup(cache)
    assert set(rows) == {'a'} and report[0]['listings'] == 1
    copied,reference = v1_cache.copy_row(rows['a'])
    np.testing.assert_array_equal(copied['features'],features[0])
    copied['features'][0,0] = -123
    assert np.load(root/'features.npy')[0,0,0] == 0
    assert reference == {'coverage':'fixture'}
    assert not (root/'actions.npy').exists()


def test_overlay_resolution_and_corruption_fail_closed(cache,tmp_path):
    root,source,listings,_ = cache
    plan = read(root/'plan.json')
    overlay = tmp_path/'overlay'
    publish(overlay,dict(base_shard_root=str(root),base_plan_hash=plan['plan_hash'],
        base_complete_hash=file_hash(root/'complete.json'),date=plan['date'],tickers=['A']))
    rows,_ = v1_cache.catalog([overlay],source=source,day=date(2026,8,20),listings=listings)
    assert rows['a']['root'] == str(root.resolve())
    with (root/'features.npy').open('ab') as stream:
        stream.write(b'corrupted')
    with pytest.raises(ValueError,match='integrity'):
        lookup(cache)


def test_incompatible_code_is_reported_and_identity_mismatch_fails(cache):
    root,source,listings,_ = cache
    listings[0]['listing_id'] = 'different'
    rows,_ = lookup(cache)
    assert not rows  # Same ticker does not establish listing identity.
    listings[0]['listing_id'] = 'a'
    listings[0]['exchange'] = 'different'
    with pytest.raises(ValueError,match='listing/source'):
        lookup(cache)
    listings[0].pop('exchange')
    plan = read(root/'plan.json')
    plan.pop('plan_hash')
    plan['code_hashes']['features.py'] = 'old'
    publish(root,plan,files=read(root/'complete.json')['files'])
    rows,report = lookup(cache)
    assert not rows and 'features.py' in report[0]['reason']


def test_cached_extraction_never_fetches_features_or_reference(cache,monkeypatch):
    rows,_ = lookup(cache)
    monkeypatch.setattr(build_data,'SECONDS',8)
    bars = pl.DataFrame(dict(bucket_index=[14400,14402],close_int=[12345,20000],
        price_valid=[1,1],volume=[23.,45.],trade_count=[2,3]))
    def forbidden(*args):
        raise AssertionError('Fetched already cached features')
    for name in ('read_reference','read_arte_seconds','encode'):
        monkeypatch.setattr(build_data,name,forbidden)
    monkeypatch.setattr(build_data,'verify_execution_source',lambda *a:None)
    monkeypatch.setattr(build_data,'read_execution_bars',lambda *a:bars)
    values,_ = build_data.extract(None,cache[1],date(2026,8,20),cache[2][0],rows['a'])
    np.testing.assert_array_equal(values['features'],cache[3][0])
    assert values['prices'][1] == 1.2345 and values['prices'][3] == 2
    assert values['volume'][1] == 23 and values['trades_60s'][3] == 5


def copy_job(job):
    # Importable top-level function exercises spawn, not a thread/mock executor.
    values,_ = v1_cache.copy_row(job['descriptor'])
    started = time.monotonic()
    time.sleep(.15)
    if job.get('fail'):
        raise ValueError('deliberate worker failure')
    return os.getpid(),started,time.monotonic(),build_data.bank_hash(values['features'])


def test_real_process_pool_bounded_copy_and_stop(cache):
    rows,_ = lookup(cache)
    jobs = [dict(index=i,descriptor=rows['a']) for i in range(6)]
    outputs = list(results(jobs,copy_job,workers=2,stopped=lambda:False))
    assert len(outputs) == 6
    assert len({x[1][0] for x in outputs}) == 2
    assert all(x[1][3] == rows['a']['hashes']['features'] for x in outputs)
    assert any(a[1][0] != b[1][0] and max(a[1][1],b[1][1]) < min(a[1][2],b[1][2])
               for a in outputs for b in outputs)
    consumed = []
    for item in results(jobs,copy_job,workers=2,stopped=lambda:bool(consumed)):
        consumed.append(item)
    assert len(consumed) <= 2  # At most the already-completed bounded batch.
    with pytest.raises(ValueError,match='deliberate'):
        list(results([dict(jobs[0],fail=True)],copy_job,workers=2,stopped=lambda:False))


def test_builder_mixes_v1_copy_and_missing_listing_then_resumes(cache,tmp_path,monkeypatch):
    import json
    from research.rl_trading.v2.data import MarketSession
    from research.rl_trading.v2.market_status import VERSION
    from research.rl_trading.v1.common import bounds
    root,source,listings,_ = cache
    seconds = build_data.SECONDS
    monkeypatch.setattr(v1_cache,'SECONDS',seconds)
    features = np.zeros((1,seconds,len(v1_cache.FEATURE_NAMES)),dtype=np.float32)
    features[:,:,2] = 7  # Distinguish copied content from newly encoded content.
    np.save(root/'features.npy',features)
    np.save(root/'volume_60s.npy',np.zeros((1,seconds),dtype=np.float64))
    complete = read(root/'complete.json')
    complete['files'] = {name:file_hash(root/name) for name in complete['files']}
    write(root/'complete.json',complete)
    progress = read(root/'progress.json')
    progress['done']['A']['features'] = build_data.bank_hash(features[0])
    progress['done']['A']['volume'] = build_data.bank_hash(np.zeros(seconds,dtype=np.float64))
    write(root/'progress.json',progress)
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    class Client:
        def close(self):
            pass
    monkeypatch.setattr(build_data,'ArteReader',lambda _:Client())
    monkeypatch.setattr(build_data,'load_env_files',lambda *a,**k:None)
    monkeypatch.setattr(build_data,'discover_clickhouse_env_files',lambda:[])
    monkeypatch.setattr(build_data.arte_source,'load_build',lambda *a:source)
    monkeypatch.setattr(build_data.arte_source,'storage_check',lambda *a:None)
    monkeypatch.setattr(build_data,'storage_check',lambda *a:None)
    monkeypatch.setattr(build_data.arte_source,'population',lambda *a:(listings,{'certificate':{'tradable_count':2}}))
    seeds = []
    monkeypatch.setattr(build_data,'missing_seeds',lambda c,d,t:seeds.extend(t) or [])
    monkeypatch.setattr(build_data.arte_source,'verify_listing',lambda *a:None)
    monkeypatch.setattr(build_data,'verify_execution_source',lambda *a:None)
    bars = pl.DataFrame(dict(bucket_index=[14400],close_int=[50000],price_valid=[1],
                            volume=[0.],trade_count=[0]))
    fetched = []
    def raw(c,s,d,t):
        fetched.append(('bars',t))
        return bars
    def full(c,s,d,t):
        fetched.append(('features',t))
        return bars,None
    monkeypatch.setattr(build_data,'read_execution_bars',raw)
    monkeypatch.setattr(build_data,'read_arte_seconds',full)
    monkeypatch.setattr(build_data,'read_reference',lambda *a:({},[],{},{}))
    monkeypatch.setattr(build_data,'encode',lambda *a:(np.zeros_like(features[0]),np.zeros(seconds)))
    status = tmp_path/'status'
    status.mkdir()
    first = bounds(date(2026,8,20))[0]
    (status/'events.jsonl').write_text('\n'.join(json.dumps(dict(listing_id=x['listing_id'],
        effective_us=first,available_us=first,state=1)) for x in listings),encoding='utf-8')
    write(status/'complete.json',dict(version=VERSION,state='complete',date='2026-08-20',
        authority_table='market_sip_compact.events_2026',producer_owner='canonical_ingestion',
        source_certificate_hash='fixture',first_us=first,end_us=first+(seconds-1)*1000000,
        listing_ids=['a','b'],clock='provider_effective_and_first_available',event_count=2,
        events_hash=file_hash(status/'events.jsonl')))
    args = ['--manifest',str(root/'plan.json'),'--ledger',str(tmp_path/'ledger'),
        '--date','2026-08-20','--status-sidecar',str(status),'--v1-shards',str(root),'--workers','1']
    assert build_data.main(args) == 0
    assert fetched == [('bars','A'),('features','B')] and seeds == ['B']
    output = next((tmp_path/'rl-trading/v2/market/2026-08-20').iterdir())
    session = MarketSession.load(output)
    np.testing.assert_array_equal(session.arrays['features'][0],features[0])
    assert not session.arrays['features'][1].any()
    stats = read(output/'progress.json')
    assert stats['copied_v1'] == stats['extracted'] == 1
    (output/'complete.json').unlink()
    assert build_data.main(args) == 0
    assert len(fetched) == 2
    # V2 training is independent of the source cache after publication.
    del session
    (root/'features.npy').rename(root/'features-moved.npy')
    assert MarketSession.load(output).n == 2
