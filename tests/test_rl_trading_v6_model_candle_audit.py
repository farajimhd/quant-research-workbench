import numpy as np
import pytest
import json
from hashlib import sha256

from research.rl_trading.v6.features import CandleFeatures
from research.rl_trading.v6.model_candle_audit import select_window, project


def features(count, start=0):
    scalar=np.zeros((count,37),dtype=np.float32)
    scalar[:,:4]=np.log(10.)
    scalar[:,35:37]=1
    scalar[:,4]=.02;scalar[:,5]=.03;scalar[:,6:8]=1
    scalar[:,14]=.01;scalar[:,15]=.005;scalar[:,20]=1
    levels=np.zeros((count,2,5,11),dtype=np.float32)
    levels[:,0,0,0]=-.02;levels[:,0,0,6]=1;levels[:,0,0,10]=1
    return CandleFeatures((np.arange(start,start+count,dtype=np.int64)+1)*1_000_000,scalar,levels)


def test_120_context_plus_120_session_and_strict_prior_masked_model_history():
    prior=features(150);current=features(200,1000)
    current.scalar[3,35:37]=0
    rows,window=select_window(current,prior)
    assert (window['context_candles'],window['session_candles'])==(120,120)
    assert rows[0]['time_us']==prior.close_us[-120]
    first=rows[120]
    assert first['input_close_us']==prior.close_us[-120:].tolist()
    assert first['time_us'] not in first['input_close_us']
    assert first['diagnostic_input_close_us'][-1]==first['time_us']
    # Missing-price bank rows aren't drawn, but remain real model history.
    fifth=rows[124]
    assert current.close_us[3] in fifth['input_close_us']
    assert current.close_us[3] not in [r['time_us'] for r in rows]
    next_rows,next_window=select_window(current,prior,offset=120)
    assert next_window['context_candles']==120
    assert next_rows[120]['time_us']==current.close_us[121]
    assert len(next_rows)==199 and not next_window['next_available']


def test_short_session_empty_session_and_unseen_prior():
    rows,window=select_window(features(1),features(5,-10))
    assert len(rows)==6 and window['session_candles']==1
    rows,window=select_window(features(2))
    assert len(rows)==2 and rows[0]['input_padding']==120
    assert rows[1]['input_close_us']==[1_000_000]
    rows,window=select_window(features(0),features(3,-10))
    assert window['session_candles']==0 and len(rows)==3


def test_indicators_and_v7_project_saved_float32_values_without_replay():
    source=features(2)
    rows,_=select_window(source)
    candles,overlays,oscillator=project(rows)
    close=float(np.exp(float(source.scalar[0,3])))
    assert candles[0]['close']==close
    assert overlays[0]['data'][0]['value']==close*(1+float(source.scalar[0,4]))
    level=next(s for s in overlays if s['column']=='v7_0_0_center')
    assert level['data'][0]['value']==close*(1+float(source.levels[0,0,0,0]))
    assert next(s for s in overlays if s['column']=='v7_1_0_center')['data']==[]
    assert oscillator[0]['data'][0]['value']==close*float(source.scalar[0,14])
    assert rows[0]['scalar']==source.scalar[0].astype(float).tolist()
    assert rows[0]['levels']==source.levels[0].astype(float).tolist()


def test_target_clock_jump_selects_observed_candles_not_empty_clock_interval():
    rows,window=select_window(features(10,5000),start_us=1_000_000)
    assert window['session_candles']==10
    rows,window=select_window(features(10,5000),start_us=5005_000_000)
    assert window['offset']==4 and rows[4]['part']=='session'


def test_zero_episode_symbol_uses_saved_compiler_receipt(tmp_path,monkeypatch):
    from research.rl_trading.v6 import saved_label_audit as audit
    from research.rl_trading.v1.common import digest,file_hash
    identity='listing:security:sample:nyse:usd'
    plan=dict(census={identity:0});plan['hash']=digest(plan)
    (tmp_path/'plan.json').write_text(json.dumps(plan))
    (tmp_path/'complete.json').write_text(json.dumps(dict(plan_hash=plan['hash'],outputs={'episodes':{'rows':0,'sha256':None}})))
    fragment=tmp_path/'fragments'/sha256(identity.encode()).hexdigest()[:24]
    fragment.mkdir(parents=True)
    (fragment/'complete.json').write_text(json.dumps(dict(listing_id=identity,ticker='NUV',report={'candles':0})))
    def no_episode_file(*args):
        raise AssertionError('Context-only bank has no episode file')
    monkeypatch.setattr(audit,'identity_rows',no_episode_file)
    assert audit.saved_symbols(str(tmp_path),file_hash(tmp_path/'complete.json'))=={identity:'NUV'}


def test_empty_price_status_is_bound_to_shard_receipt(tmp_path):
    from research.rl_trading.v6 import saved_label_audit as audit
    from research.rl_trading.v1.common import file_hash
    folder=tmp_path/'shards/0';folder.mkdir(parents=True)
    binding={'source':'exact'}
    (folder/'complete.json').write_text(json.dumps(dict(binding=binding,all_invalid_listings=['empty'])))
    proof=dict(binding=binding,identities=['empty','priced'],shards=[{'path':'shards/0','sha256':file_hash(folder/'complete.json')}])
    (tmp_path/'complete.json').write_text(json.dumps(proof))
    assert audit.empty_price_listings(str(tmp_path),file_hash(tmp_path/'complete.json'))=={'empty'}
