import json
from types import SimpleNamespace
import numpy as np
import pytest
from research.rl_trading.v7.experiments import temporal_data as temporal


def window(group=0,n=1):
    fields=temporal.shared_reader.QUOTE_FIELDS if group>=5 else temporal.shared_reader.TRADE_FIELDS
    length=temporal.LENGTHS[group]
    values=np.ones((n,length,len(fields)),np.float32)
    values[...,:4]=11.
    clocks=np.broadcast_to(np.arange(-length,0),(n,length)).copy()
    return dict(values=values,valid=np.ones_like(values,bool),fields=fields,clocks=clocks)


def test_normalization_order_padding_masks():
    w=window(1)
    w['values'][0,0,0]=12.
    w['valid'][0,2,1]=False
    w['values'][0,4,4]=-2.
    seq,counts=temporal.sequence(w,np.array([10.]),np.array([0]),1)
    assert seq.shape==(1,60,14)
    assert not seq[:,:30].any()
    assert seq[0,30,0]==20.
    assert seq[0,-1,0]==10.
    assert seq[0,32,1]==seq[0,32,8]==0
    assert seq[0,34,4]==seq[0,34,11]==0
    assert seq[0,-1,4]==pytest.approx(np.log(2)/10)
    assert counts['rejected_valid']==1 and counts['source_invalid']==1


def test_quote_unavailable_and_missing_mark():
    seq,_=temporal.sequence(window(5),np.array([0.]),np.array([0]),5)
    assert not seq[...,:4].any() and not seq[...,7:11].any()
    assert not seq[...,6].any() and not seq[...,13].any()
    assert seq[...,11:13].all()


def test_future_and_reordered_clocks_rejected():
    w=window()
    w['clocks'][0,-1]=1
    with pytest.raises(ValueError,match='future'):
        temporal.sequence(w,np.array([10.]),np.array([0]),0)
    w=window();w['clocks'][0,[0,1]]=w['clocks'][0,[1,0]]
    with pytest.raises(ValueError,match='Unordered'):
        temporal.sequence(w,np.array([10.]),np.array([0]),0)


def test_daily_ordinal_authority():
    temporal.sequence(window(4),np.array([10.]),np.array([0]),4,['2026-01-01'],'2026-01-05')
    with pytest.raises(ValueError,match='historical'):
        temporal.sequence(window(4),np.array([10.]),np.array([0]),4,['2026-01-05'],'2026-01-05')


def test_prepare_parity_restart_guard_and_tamper(tmp_path,monkeypatch):
    day='2026-01-05'
    seconds=temporal.sampled_seconds(day)
    selection=np.zeros((len(seconds),5),np.int32)
    features=np.zeros((len(seconds),5,112),np.float32)
    for g,(_,side) in enumerate(temporal.SPECS):
        features[...,g*14:(g+1)*14]=temporal.summarize(window(g),np.array([10.]),side)[0]
    root=tmp_path/'snapshots';(root/day).mkdir(parents=True)
    (root/day/'complete.json').write_text('{}')
    source=SimpleNamespace(binding={'training':True},items={day:{'selected':['ABC']}},root=tmp_path,
                           maximum_gib=8,_check_day=lambda d:None)
    predictive=SimpleNamespace(days=[day],binding={'snapshots':True},source=source,root=root,
        load=lambda d:dict(seconds=seconds,selection=selection,features=features))
    monkeypatch.setattr(temporal,'PredictiveDataset',lambda *_:predictive)
    class Session:
        item={'daily':['2026-01-02']}
        def __init__(self,*a,**kw):pass
        def selection(self,clocks):return dict(valid=np.ones((len(clocks),5),bool),tickers=np.full((len(clocks),5),'ABC'))
        def windows(self,names,clocks,resolution,side):return window(temporal.SPECS.index((resolution,side)),len(names))
    monkeypatch.setattr(temporal.shared_reader,'Session',Session)
    monkeypatch.setattr(temporal,'execution_arrays',lambda *_:dict(mark=np.full((57600,1),10.)))
    data=temporal.TemporalDataset('unused',root,tmp_path/'temporal')
    receipt=data.prepare(day)
    assert receipt['parity_exact'] and receipt['parity_scalar_count']==66*5*98
    loaded=data.load(day)
    assert loaded['seq'].shape==(66,5,7,60,14)
    assert data.prepare(day)==receipt
    with pytest.raises(ValueError,match='TRAINING'):data.prepare('2030-01-01')
    with (data.root/day/'seq.npy').open('ab') as f:f.write(b'tampered')
    with pytest.raises(ValueError,match='hash'):data.load(day)
