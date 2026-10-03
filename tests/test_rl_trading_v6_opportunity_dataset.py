from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path
import json
import numpy as np
import polars as pl
import pytest

from research.rl_trading.v6 import opportunity_dataset as data
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.price_action_opportunities import Config, calculate, VERSION
from research.rl_trading.v6.training_gate import require_dataset
from research.rl_trading.v6.teacher_data import load_teacher
from research.rl_trading.v6.episode_windows import load_episode_teacher
from research.rl_trading.v6.ticker_targets import attach_targets


def fixture_shard(tmp_path, monkeypatch, scale=1.):
    prices=np.array([10.,9.,9.2,9.5,10.,10.1])*scale
    raw=np.zeros((len(prices),len(SCALAR_NAMES)),np.float32)
    for name in ('open','high','low','close'): raw[:,SCALAR_NAMES.index('log_'+name)]=np.log(prices)
    raw[:,35:37]=1; raw[:,20]=1
    raw[:,14]=[-1,-1,1,1,1,1]
    # One rejected activity row is retained in source accounting.
    raw[-1,35:37]=0
    values=SimpleNamespace(close_us=np.arange(1,7,dtype=np.int64)*10_000_000,scalar=raw)
    source=tmp_path/'bank'; source.mkdir(); (source/'complete.json').write_text('{}')
    monkeypatch.setattr(data,'open_bank',lambda *a,**k:SimpleNamespace(listing=lambda identity:values))
    binding=dict(bank_manifest_sha256=data.file_hash(source/'complete.json'))
    output=tmp_path/'labels'/'shards'/'00000'
    task=(str(source),str(output),['identity'],binding,asdict(Config()))
    proof=data.build_shard(task)
    day=tmp_path/'labels'
    certificate=dict(version=data.DAY_VERSION,algorithm=VERSION,status=data.STATUS,day='2026-07-31',role='train',
        bank_certificate_sha256='bankhash',binding=binding,config=asdict(Config()),identities=['identity'],
        shards=[dict(path='shards/00000',sha256=data.file_hash(output/'complete.json'))],sealed_test_accessed=False,
        **{k:proof[k] for k in ('activity_rows','valid_rows','invalid_price_rows')})
    data.write_json(day/'complete.json',certificate)
    return task,day,values


def test_saved_raw_quality_exact_and_resume(tmp_path,monkeypatch):
    task,day,values=fixture_shard(tmp_path,monkeypatch)
    proof=data.verify_day(day,'bankhash')
    assert (proof['activity_rows'],proof['valid_rows'],proof['invalid_price_rows'])==(6,5,1)
    bars,_=data.decoded_bars(values)
    expected=calculate(bars)[0]
    actual=pl.read_parquet(Path(task[1])/'labels.parquet').drop('listing_id')
    assert actual.equals(expected)
    assert data.build_shard(task)==data.verify_shard(task[1],task[3])


def test_teacher_raw_values_and_threshold_no_old_candidate(tmp_path,monkeypatch):
    _,day,_=fixture_shard(tmp_path,monkeypatch)
    session=SimpleNamespace(role='train',day='2026-07-31',source_certificate_sha256='bankhash',listings=('identity',))
    labels,outcomes=load_teacher(day,session,runtime_root=tmp_path)
    assert labels and not outcomes
    assert all(d.label_version==VERSION and d.size_fraction is None and d.entry_stop_bps is None for d in labels)
    for item in labels:
        if item.held_index.size:
            assert item.opportunity_value_bps == pytest.approx(item.raw_exit_gain/item.held_features[0,1]*10000,rel=1e-6)
            rows=pl.read_parquet(day/'shards/00000/labels.parquet')
            prior=rows.filter(pl.col('time_us')<item.close_us).sort('time_us')['close'][-1]
            entry=item.held_features[0,1]
            assert item.held_features[0,3]==pytest.approx((prior-entry)/entry,abs=1e-6)
        else:
            row=pl.read_parquet(day/'shards/00000/labels.parquet').filter(pl.col('time_us')==item.close_us).row(0,named=True)
            assert item.opportunity_value_bps==pytest.approx(row['entry_gain']/row['close']*10000)
            assert item.token==(1 if row['entry_gain']>0 and row['entry_quality']>=.9 else 0)
    attach_targets(labels,session,None)
    with pytest.raises(ValueError,match='Legacy'): attach_targets(labels,session,tmp_path)


@pytest.mark.parametrize('loader',[load_teacher,load_episode_teacher])
def test_obsolete_loaders_fail_closed(tmp_path,loader):
    data.write_json(tmp_path/'complete.json',dict(version='rl-v6-independent-episode-windows-rolling15-v1'))
    session=SimpleNamespace(role='train',day='2026-07-31',source_certificate_sha256='bankhash',listings=('identity',))
    with pytest.raises(ValueError,match='Old labels'): loader(tmp_path,session,runtime_root=tmp_path)


def test_old_dataset_gate_and_corrupted_shard_rejected(tmp_path,monkeypatch):
    data.write_json(tmp_path/'old.json',dict(version='rl-trading-v6-audited-training-split-1'))
    with pytest.raises(ValueError,match='legacy labels'): require_dataset(tmp_path/'old.json',runtime_root=tmp_path)
    _,day,_=fixture_shard(tmp_path,monkeypatch)
    with (day/'shards/00000/labels.parquet').open('ab') as stream: stream.write(b'changed')
    with pytest.raises(ValueError,match='bytes/count'): data.verify_day(day)


def test_high_price_teacher_bookkeeping_does_not_change_raw_targets(tmp_path,monkeypatch):
    _,day,_=fixture_shard(tmp_path,monkeypatch,scale=2000)
    session=SimpleNamespace(role='train',day='2026-07-31',source_certificate_sha256='bankhash',listings=('identity',))
    labels,_=load_teacher(day,session,runtime_root=tmp_path)
    held=[item for item in labels if item.held_index.size]
    assert held and all(0<item.held_features[0,0]<1 and item.account[0]>=0 for item in held)
    assert all(item.raw_exit_gain>100 for item in held)


def test_bounded_audit_uses_authentic_population(tmp_path,monkeypatch):
    _,day,_=fixture_shard(tmp_path,monkeypatch)
    session=SimpleNamespace(role='train',day='2026-07-31',source_certificate_sha256='bankhash',listings=('identity',))
    with pytest.raises(ValueError,match='explicit audit mode'):
        data.load_teacher(day,session,runtime_root=tmp_path,audit_listing_ids=['identity'])
    actual,_=data.load_teacher(day,session,runtime_root=tmp_path,audit_development=True,audit_listing_ids=['identity'])
    expected,_=load_teacher(day,session,runtime_root=tmp_path)
    assert [(d.close_us,d.token,d.raw_entry_gain,d.raw_exit_gain) for d in actual]==[(d.close_us,d.token,d.raw_entry_gain,d.raw_exit_gain) for d in expected]


def test_atomic_receipt_retry_preserves_old_json(tmp_path,monkeypatch):
    path=tmp_path/'progress.json'; data.write_json(path,{'status':'old'})
    original=Path.replace; attempts=[]
    def racing_replace(temporary,destination):
        attempts.append(1)
        if len(attempts)<3:
            assert json.loads(path.read_text())=={'status':'old'}
            raise PermissionError('Reader denies delete sharing')
        return original(temporary,destination)
    monkeypatch.setattr(Path,'replace',racing_replace)
    monkeypatch.setattr(data.time,'sleep',lambda seconds:None)
    data.write_json(path,{'status':'new'})
    assert len(attempts)==3 and json.loads(path.read_text())=={'status':'new'}


@pytest.mark.parametrize('long',[True,False])
def test_single_price_candle_is_accounted(long):
    bars=pl.DataFrame(dict(time_us=[1_000_000],open=[1.],high=[1.],low=[1.],close=[1.],macd_line=[1. if long else -1.],macd_signal=[0.]))
    labels,_,_,trades=calculate(bars)
    assert labels['action'].to_list()==['WAIT'] and labels['entry_gain'].item()==0 and trades.height==0
