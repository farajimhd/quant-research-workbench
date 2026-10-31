"""V60-owned, training-only market/strategy boundary probes; no saved fitness."""
import hashlib,json,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np

VERSION='v60-training-boundary-probe-v1'
STEPS=16
LISTINGS=21  # Preserve the prior 8 x 16 x 21 bounded calibration work budget.


def prepare(job):
    import torch
    from .authority import REGISTRY_SHA256,coverage
    from .features import CATALOG
    from .full_common import config
    from .publication import write_json
    from .runtime import require_runtime
    code=Path(__file__).parent
    source_files=('calibration_probe.py','network.py','features.py','feature_policy.py','data.py','swing_projection.py')
    source=hashlib.sha256(b''.join((code/f).read_bytes() for f in source_files)).hexdigest()
    identity=dict(version=VERSION,source=source,registry=REGISTRY_SHA256,
                  training=job['training_sha256'],execution_tail=job['execution_tail'],
                  assets=job['asset_sha256'])
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    root=require_runtime(Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v60/probes')/key)
    path=root/'global.npz'
    if path.exists():
        from .probe_reader import load_probe
        load_probe(path,job['training']);return path
    root.mkdir(parents=True,exist_ok=True)
    days=job['training_days'];chosen=[days[i] for i in np.unique(np.linspace(0,len(days)-1,min(8,len(days)),dtype=int))]
    cfg=config(job,'workstation','training');torch.set_num_threads(1)
    def window(day):
        from .data import SessionData
        from .io_pipeline import prepare_host
        from .network import NetworkPlan
        from .genome import sample
        started=time.perf_counter()
        item=prepare_host(SessionData(Path(cfg['inputs'])/day,Path(cfg['history'])/day,device='cpu',
            maximum_gib=cfg['maximum_data_gib'],maximum_quote_age_seconds=None,
            execution_tail=Path(cfg['execution_tail'])/'training'/day,
            feature_cache=Path(cfg['feature_cache'])/day if cfg['feature_cache'] else None,
            indicator_root=Path(cfg['indicator_root'])/day if cfg.get('indicator_root') else None,
            enriched_root=Path(cfg['enriched_root'])/day if cfg.get('enriched_root') else None),resident=True)
        plan=None
        try:
            if item.receipt.get('validation_opened'):raise ValueError('Validation cannot calibrate candidates')
            plan=NetworkPlan(sample(np.random.default_rng(0),1),'cpu')
            member=item.tensors['membership'].numpy();ready=member&item.tensors['quote_valid'].numpy()
            start=min(max(0,int(ready.sum(1).argmax())-STEPS//2),max(0,len(member)-STEPS-1))
            ticks=list(range(start,start+STEPS));n=min(LISTINGS,member.shape[1]);c=len(CATALOG)
            # Select listings by availability, never by profit or model outcome.
            listings=np.argsort(-ready[ticks].sum(0),kind='stable')[:n]
            values=[];masks=[];caps=[]
            for tick in ticks:
                index=torch.tensor(tick);v,m=plan.project(item,index)
                values.append(v[listings].numpy());masks.append(m[listings].numpy())
                bank=item.resident_features;a=bank.tensors
                raw,valid=bank._values(a['source'][tick:tick+1].T,a['ids'][tick:tick+1].T.long(),a['clocks'][tick:tick+1][None],plan.columns)
                caps.append(coverage(raw[:,0].numpy(),valid[:,0].numpy()))
            x=np.zeros((STEPS,n,3*c+18),np.float32);x[...,:3*c]=np.stack(values)
            held=np.zeros((STEPS,n,18),np.float32);held[...,0]=np.tanh(1.)
            held[...,1]=np.tanh(np.linspace(-.15,.15,STEPS)[:,None]);held[...,2]=np.tanh(np.arange(STEPS)[:,None]/3600.)
            held[...,3]=np.tanh(1.);held[...,4]=np.tanh(.03);held[...,6]=np.tanh(np.arange(STEPS)[:,None]/300.)
            pick=lambda name:item.tensors[name].numpy()[np.ix_(ticks,listings)]
            swing_ids=pick('v60_entry_swing_ids');swings=item.tensors['v60_entry_swing_values'].numpy()[swing_ids]
            arrays=dict(inputs=x,known=np.stack(masks),eligible=member[np.ix_(ticks,listings)],held_states=held,
                        ask=pick('ask'),bid=pick('bid'),quote_valid=pick('quote_valid'),ask_size=pick('ask_size'),swings=swings)
            return arrays,dict(day=day,start_clock=start,listings=listings.tolist(),seconds=time.perf_counter()-started,
                cap_coverage={k:sum(r[k] for r in caps).tolist() for k in caps[0]})
        finally:
            if plan:plan.close()
            item.close()
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='v60-probe') as pool:results=list(pool.map(window,chosen))
    width=max(a['inputs'].shape[1] for a,_ in results)
    arrays={}
    for name in results[0][0]:
        pieces=[]
        for row,_ in results:
            value=row[name];padding=[(0,0)]*value.ndim;padding[1]=(0,width-value.shape[1])
            pieces.append(np.pad(value,padding,constant_values=np.nan if name=='swings' else 0))
        arrays[name]=np.stack(pieces)
    manifest=json.loads(Path(job['training']).read_bytes())
    meta=dict(identity,training_sha256=job['training_sha256'],days=chosen,
              receipts={d:manifest['receipts'][d] for d in chosen},validation_opened=False,projected_policy=True,
              slice_sha256={k:hashlib.sha256(v.tobytes()).hexdigest() for k,v in arrays.items()})
    temporary=path.with_suffix('.tmp.npz');np.savez(temporary,**arrays,metadata=np.asarray(json.dumps(meta,sort_keys=True)))
    temporary.replace(path);write_json(root/'coverage.json',dict(identity=identity,windows=[r for _,r in results],
        meaning='Bounded training coverage only; construction caps do not clip observations'))
    from .probe_reader import load_probe
    load_probe(path,job['training']);return path
