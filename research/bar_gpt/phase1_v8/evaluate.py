"""All-visible/hidden-only native reconstruction and metadata retention."""
import time
import numpy as np
import torch
from .data import batch
from .model import inverse
from ..phase1_v7.metrics import evaluate,probes,summaries,require_structured


@torch.no_grad()
def predict(model,data,batch_size,full=True,status=None):
    model.eval();pred=[];embeddings=[];masks=[];time_error=torch.zeros(9,device=model.input_mean.device,dtype=torch.float64)
    time_count=time_error.clone();valid_correct=valid_total=0;began=time.perf_counter()
    generator=torch.Generator(device=model.input_mean.device).manual_seed(731)
    n=len(data['values'])
    for start in range(0,n,batch_size):
        ids=torch.arange(start,min(start+batch_size,n),device=model.input_mean.device)
        x,v,pos,y,m,selected=batch(data,ids,model.config.visible,generator,full)
        z,outputs=model(x,v,pos);p,t,vl=outputs
        pred.append(inverse(p).cpu().numpy());embeddings.append(z.cpu().numpy());masks.append((m[...,:18]&selected[...,None]).cpu().numpy())
        mt=m[...,18:]&selected[...,None]
        actual=(t*model.input_scale[18:]+model.input_mean[18:]-y[...,18:]).abs()
        time_error+=torch.where(mt,actual,0).double().sum((0,1));time_count+=mt.sum((0,1))
        valid_correct+=int((((vl>=0)==m)&selected[...,None]).sum());valid_total+=int(selected.sum())*27
        if status and (start//batch_size)%8==0:
            done=min(start+batch_size,n);rate=done/max(time.perf_counter()-began,.001)
            status(evaluation_done=done,evaluation_total=n,evaluation_windows_per_second=rate,evaluation_eta_seconds=(n-done)/rate)
    metadata=dict(time_mae=(time_error/time_count.clamp_min(1)).cpu().tolist(),time_valid_counts=time_count.cpu().long().tolist(),
        validity_correct=valid_correct,validity_total=valid_total,validity_accuracy=valid_correct/max(1,valid_total),seconds=time.perf_counter()-began)
    return np.concatenate(pred),np.concatenate(embeddings),np.concatenate(masks),metadata


def measure(model,data,rows,batch_size,full=True,status=None):
    p,z,m,metadata=predict(model,data,batch_size,full,status)
    result=evaluate(p,rows,m);result['metadata']=metadata
    failures=sum(f['nonfinite']+f['negative'] for f in result['frames'].values())
    if failures:raise FloatingPointError(f'Invalid native predictions: {failures}')
    if model.config.output!='direct':require_structured(result,model.config.output=='joint')
    return result,z
