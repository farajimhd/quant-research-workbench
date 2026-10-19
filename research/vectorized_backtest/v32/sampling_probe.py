"""Bounded CPU gathering of causal training cross-sections, never validation."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
import polars as pl
import torch
from .features import CATALOG
from .genome import INPUTS,MARKET_INPUTS
from .calibration import PROBE_VERSION
from research.vectorized_backtest.v27.resident_features import ResidentFeatures

def resolve(path):
    p=Path(path)
    if p.exists():return p
    # Read the same workstation artifact; never substitute a different dataset.
    if str(path).replace('\\','/').startswith('D:/TradingML/'):
        other=Path('//DESKTOP-SAAI85T/Workstation-D')/str(path).replace('\\','/')[3:]
        if other.exists():return other
    raise FileNotFoundError(path)

def feature_rows(series, observed=None, count=None):
    """Retain null rows as unavailable, matching the resident replay loader."""
    if not series.dtype.is_integer():raise ValueError('Certified feature-row integer column required')
    rows=series.cast(pl.Int64).fill_null(-1).to_numpy()
    if np.any(rows < -1):raise ValueError('Invalid negative feature reference')
    if count is not None and np.any(rows>=count):raise ValueError('Feature reference outside certified bank')
    if observed is not None and np.any(np.asarray(observed,dtype=bool)&(rows<0)):
        raise ValueError('Observed market bar has no certified feature history')
    return rows


def build(training,day,starts=(1500,7500,13500),steps=16):
    """Full causal cross-section normalization, then bounded top-10 union slices.

    No full bank copies, GPU use, result caches, fitness, or future statistics.
    Independent windows reset recurrent memory. Held states are illustrative,
    not claims about historical fills or candidate financial performance.
    """
    training=Path(training);manifest=json.loads(training.read_text(encoding='utf-8-sig'))
    if day not in manifest['days'] or not 1<=len(starts)<=8 or not 2<=steps<=64:raise ValueError('Bounded training probe required')
    a=resolve(manifest['inputs'])/day;h=resolve(manifest['history'])/day
    for key,root in (('inputs',a),('history',h)):
        if sha256((root/'complete.json').read_bytes()).hexdigest()!=manifest['receipts'][day][key]:raise ValueError('Input certificate mismatch')
    for key,hashkey in (('indicator_root','indicator_receipts'),('enriched_root','enriched_receipts')):
        if sha256((resolve(manifest[key])/day/'complete.json').read_bytes()).hexdigest()!=manifest[hashkey][day]:raise ValueError('Feature certificate mismatch')
    load=lambda root,name:np.load(root/(name+'.npy'),mmap_mode='r',allow_pickle=False)
    clocks=load(a,'clocks');top=load(a,'top_indices');union=np.array(json.loads((h/'complete.json').read_text())['listing_ids'])
    if top.shape[1]!=10:raise ValueError('Expected certified top-10 entry universe')
    ticks=np.array([s+t for s in starts for t in range(steps)])
    if ticks.min()<0 or ticks.max()>=len(clocks):raise ValueError('Probe clock outside session')
    sr=load(h,'source_rows')[ticks];ids=load(h,'ids')[ticks];unique=np.unique(sr.clip(0));hist,hix=np.unique(ids,return_inverse=True)
    market=pl.scan_parquet(a/'market.parquet').with_row_index('idx').filter(pl.col('idx').is_in(unique)).select(
        'idx','feature_row','observed','mark','bid','ask','quote_us','volume','notional','trade_count').collect().sort('idx')
    fr=feature_rows(market['feature_row'],market['observed'].fill_null(False).to_numpy(),len(load(a,'features')))
    fu,fix=np.unique(fr.clip(0),return_inverse=True)
    t=lambda x:torch.from_numpy(np.array(x,copy=True))
    bank=ResidentFeatures.__new__(ResidentFeatures);bank.device=torch.device('cpu');bank.column_plans={}
    bank.tensors={'base':t(load(a,'features')[fu]),'base_valid':t(load(a,'feature_valid')[fu]),
        'history':t(load(h,'values')[hist].astype(np.float32)),'feature_row':t(np.where(fr>=0,fix,-1)),
        'market_clock':t(load(a,'market_keys')[unique]%(1<<32)),
        'indicators':t(load(resolve(manifest['indicator_root'])/day,'values')[fu]),
        'enriched':t(load(resolve(manifest['enriched_root'])/day,'values')[fu])}
    for k in ('mark','bid','ask','quote_us'):bank.tensors[k]=t(market[k].fill_null(float('nan')).to_numpy())
    bank.tensors['quote_seconds']=bank.tensors['quote_us']/1e6
    for k in ('volume','notional','trade_count'):bank.tensors['log_'+k]=t(np.log1p(market[k].fill_null(0).to_numpy()).astype(np.float32))
    rows=np.searchsorted(unique,sr.clip(0));rows[sr<0]=-1;ids=hix.reshape(ids.shape)
    log=torch.tensor([f.unit not in ('ratio','bool') for f in CATALOG]);projected=[];known=[];members=[];held=[]
    widths=[];chosen=[]
    for start in starts:
        selected=np.unique(top[start:start+steps]);selected=selected[selected>=0]
        chosen.append(np.searchsorted(union,selected));widths.append(len(selected))
    width=max(widths)
    if width>128:raise ValueError('Probe listing budget exceeded')
    for j,tick in enumerate(ticks):
        v,valid=bank._values_reference(t(rows[j,:,None]),t(ids[j,:,None]).long(),t(np.array([[clocks[tick]]])),list(range(len(CATALOG))))
        v=v[:,0];valid=valid[:,0]&torch.isfinite(v);clean=torch.where(valid,v,0.)
        absolute=torch.where(log,clean.sign()*clean.abs().log1p()/4,clean).tanh()
        count=valid.sum(0).clamp_min(1);anchor=clean.gather(0,valid.int().argmax(0,keepdim=True)).squeeze(0)
        shifted=torch.where(valid,clean-anchor,0.);dev=shifted-shifted.sum(0)/count
        scale=(torch.where(valid,dev.square(),0.).sum(0)/count).sqrt().clamp_min(1e-6)
        relative=(dev/(2*scale)).tanh()
        values=torch.cat((torch.where(valid,absolute,0.),torch.where(valid,relative,0.),valid.float()),1).numpy()
        selected=chosen[j//steps];n=len(selected);x=np.zeros((width,INPUTS),np.float32);x[:n,:MARKET_INPUTS]=values[selected]
        x[:n,-1]=np.tanh(np.float32(1/60))
        mask=np.zeros((width,len(CATALOG)),bool);mask[:n]=valid.numpy()[selected]
        eligible=np.zeros(width,bool);eligible[:n]=np.isin(union[selected],top[tick][top[tick]>=0])
        states=np.zeros((width,INPUTS-MARKET_INPUTS),np.float32)
        for k in range(n):
            scenario=k%3;request=(0.,.25,.75)[scenario]
            states[k]=np.tanh(np.array([1,(-.05,0.,.05)[scenario],(60,300,1800)[scenario]/3600,
                (.25,.5,1)[scenario],.02,scenario/20,(10,60,150)[scenario]/300,
                0,request,0,0,request,0,0,0,0,(0,4,16)[scenario]/31,1/60],np.float32))
        projected.append(x);known.append(mask);members.append(eligible);held.append(states)
    shape=(len(starts),steps,width)
    arrays=dict(inputs=np.asarray(projected).reshape(*shape,INPUTS),known=np.asarray(known).reshape(*shape,len(CATALOG)),
        eligible=np.asarray(members).reshape(shape),held_states=np.asarray(held).reshape(*shape,INPUTS-MARKET_INPUTS))
    meta=dict(version=PROBE_VERSION,training_sha256=sha256(training.read_bytes()).hexdigest(),days=[day],
        receipts={day:manifest['receipts'][day]},validation_opened=False,starts=list(starts),steps=steps,
        state_semantics='Flat entry; three hypothetical held states, no performance/future labels',
        projection_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        slice_sha256={k:sha256(v.tobytes()).hexdigest() for k,v in arrays.items()})
    return dict(**arrays,metadata=np.asarray(json.dumps(meta,sort_keys=True)))
