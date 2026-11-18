"""Frozen within-window information probes, not forecasts or trading labels."""
import numpy as np
import torch
from ..phase1_v7.metrics import probes,summaries


@torch.no_grad()
def encode(model,data,ids):
    model.eval();result=[]
    for start in range(0,len(ids),256):
        take=torch.as_tensor(ids[start:start+256],device=data['values'].device)
        x=data['values'][take];v=data['valid'][take];p=torch.arange(64,device=x.device).expand(len(x),-1)
        result.append(model.encoder(x,v,p).cpu().numpy())
    return np.concatenate(result)


def compare(model,untrained,data,rows):
    rng=np.random.default_rng(7711)
    ids={k:np.sort(rng.choice(len(rows[k]),min(8192,len(rows[k])),replace=False)) for k in ('train','development')}
    selected={k:rows[k][ids[k]] for k in ids};result={}
    for name,m in (('trained',model),('untrained_same_architecture',untrained)):
        z={k:encode(m,data[k],ids[k]) for k in ids}
        result[name]=probes(z['train'],z['development'],selected['train'],selected['development'])
    raw={k:summaries(selected[k])[0] for k in ids}
    result['raw_summary_control']=probes(raw['train'],raw['development'],selected['train'],selected['development'])
    result['scope']='All-visible within-window information only. Raw control contains the defined labels and is an information ceiling, not an independent forecasting result. No future labels used.'
    result['counts']={k:len(v) for k,v in ids.items()};result['seed']=7711
    return result
