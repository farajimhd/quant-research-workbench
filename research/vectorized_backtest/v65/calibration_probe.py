"""Training-only calibration in RAM; disk stores a recipe, never derived inputs."""
import json,hashlib
from pathlib import Path
from functools import lru_cache
import numpy as np

VERSION='v65-top5-in-memory-probe-v1'

def prepare(job):
    from .full_common import config
    from .publication import write_json
    from .runtime import require_runtime
    from .authority import REGISTRY_SHA256
    from .feature_layout import COLUMNS
    days=job['training_days']
    chosen=[days[i] for i in np.unique(np.linspace(0,len(days)-1,min(8,len(days)),dtype=int))]
    spec=dict(version=VERSION,training_sha256=job['training_sha256'],training=job['training'],
              days=chosen,config=config(job,'workstation','training'),registry=REGISTRY_SHA256,
              feature_columns=list(COLUMNS),validation_opened=False,assets=job['asset_sha256'],
              execution_tail=job['execution_tail'])
    root=require_runtime(Path(job['campaign'])/'qualification')
    path=root/'probe-spec.json';write_json(path,spec)
    return path

@lru_cache(maxsize=1)
def load(path,training,stamp):
    import torch
    from .data import SessionData
    from .io_pipeline import prepare_host
    from .network import NetworkPlan
    from .genome import sample
    from .feature_layout import COLUMNS
    from .market_inference import project
    from .attention import selected_columns
    spec=json.loads(Path(path).read_bytes())
    if spec['version']!=VERSION or spec['training_sha256']!=hashlib.sha256(Path(training).read_bytes()).hexdigest():
        raise ValueError('Calibration recipe identity changed')
    from .price_references import References
    from .price_program import REFERENCES
    cfg=spec['config'];rows=[];price_rows=[]
    for day in spec['days']:
        item=SessionData(Path(cfg['inputs'])/day,Path(cfg['history'])/day,device='cpu',manifest=cfg['shard_manifest'],
            maximum_quote_age_seconds=None,execution_tail=Path(cfg['execution_tail'])/'training'/day,
            indicator_root=None,enriched_root=None)
        plan=None
        try:
            prepare_host(item,resident=True)
            plan=NetworkPlan(sample(np.random.default_rng(0),1),'cpu',feature_columns=COLUMNS)
            starts=np.linspace(0,max(0,item.clocks-16),4,dtype=int)
            ticks=np.concatenate([np.arange(s,s+16) for s in starts])
            slots=torch.tensor(selected_columns(item.arrays['top_indices'],item.listing_ids)[ticks],dtype=torch.long)
            indices=torch.tensor(ticks,dtype=torch.long)
            v,m=project(plan,item,indices,slots=slots)
            lag,lm=project(plan,item,(indices-1).clamp_min(0),True,slots=slots)
            take=lambda k:item.host_tensors[k][indices].gather(1,slots.clamp_min(0)).numpy()
            x=np.pad(v.numpy(),((0,0),(0,0),(0,18)))
            refs=References(item,selected_columns(item.arrays['top_indices'],item.listing_ids))
            price_rows.append({name:refs.selected(name,ticks) for name in (*REFERENCES,'volatility')})
            rows.append(dict(lag_absolute=lag.numpy(),lag_known=(lm&(indices[:,None,None]>0)).numpy(),inputs=x,known=m.numpy(),eligible=(slots.numpy()>=0),held_states=np.zeros((64,5,18),np.float32),
                             ask=take('ask'),bid=take('bid'),quote_valid=take('quote_valid'),ask_size=take('ask_size')))
        finally:
            if plan:plan.close()
            item.close()
    result={k:np.stack([r[k] for r in rows]) for k in rows[0]}
    result['price_references']={k:np.stack([r[k] for r in price_rows]) for k in price_rows[0]}
    result['metadata']=dict(spec,projected_policy=True,segment_starts=[0,16,32,48])
    result['sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return result
