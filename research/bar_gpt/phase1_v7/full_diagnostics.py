"""Frozen development diagnostics; masked metrics are explicitly subset-only."""
import time
import numpy as np
import torch
from .train import cache,batch
from .metrics import evaluate,require_structured
from ..v5.shards.phase1_contract import write_json


@torch.no_grad()
def run(model,args,step):
    started=time.perf_counter();was_training=model.training;model.eval()
    rows=np.load(args.data/'development.npy',allow_pickle=False)
    data=cache(rows,torch.zeros(18),torch.ones(18),'cuda');result={}
    for full in (True,False):
        predictions=[];masks=[];gen=torch.Generator(device='cuda').manual_seed(731)
        for start in range(0,len(rows),512):
            ids=torch.arange(start,min(start+512,len(rows)),device='cuda')
            x,v,pos,target,mask=batch(data,ids,16,gen,full)
            grid,slots=model.prepare(x,v,pos,check=False)
            out=model.decode(model.encode_prepared(grid,slots,args.precision))
            predictions.append(model.transform.inverse(out).cpu().numpy());masks.append(mask.cpu().numpy())
        metrics=evaluate(np.concatenate(predictions),rows,np.concatenate(masks));require_structured(metrics,paired=True)
        result['all_visible' if full else 'masked_48_of_64']=metrics
    result.update(step=step,windows=len(rows),scope='frozen campaign development subset, not full validation',seconds=time.perf_counter()-started)
    write_json(args.output/'development-latest.json',result)
    model.train(was_training)
    return {f'development/{mode}/{k}':result[mode][k] for mode in ('all_visible','masked_48_of_64')
            for k in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean')}
