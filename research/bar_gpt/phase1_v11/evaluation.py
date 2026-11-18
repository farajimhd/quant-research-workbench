"""Comparable hidden48/all-visible raw metrics, scale recovery, oracle diagnostics."""
import time
import numpy as np
import torch
from ..phase1_v8.data import batch
from ..phase1_v7.metrics import evaluate,require_structured,distribution
from .transforms import references,decode,inverse,REF_PRICES,REF_ACTIVITY


@torch.no_grad()
def evaluate_model(model,data,rows,output,status,controls=False,constant_logs=None):
    model.eval();result={};device=data['values'].device
    for mode in ('masked','all_visible'):
        started=time.monotonic();generator=torch.Generator(device=device).manual_seed(314159)
        predictions=[];oracles=[];masks=[];embeddings=[];reference_error=[];activity_error=[];baseline=[]
        for start in range(0,len(rows),256):
            ids=torch.arange(start,min(start+256,len(rows)),device=device)
            x,v,pos,t,tv,hidden=batch(data,ids,16,generator,full=mode=='all_visible')
            z=model.encoder(x,v,pos);out=model.decode(z)
            refs,rv=references(x[...,:18],v[...,:18],model.encoder.reference_mean)
            reference_error.append(torch.expm1(out['references'][:,REF_PRICES].double()-refs[:,REF_PRICES]).abs()[rv[:,REF_PRICES]].cpu().numpy())
            activity_error.append((out['references'][:,REF_ACTIVITY]-refs[:,REF_ACTIVITY]).abs()[rv[:,REF_ACTIVITY]].cpu().numpy())
            predictions.append(inverse(out['logs']).cpu().numpy());masks.append(hidden.cpu().numpy()[...,None]);embeddings.append(z.cpu().numpy())
            oracles.append(inverse(decode(out['code'],refs)).cpu().numpy())
            if controls:baseline.append(inverse(decode(torch.zeros_like(out['code']),refs)).cpu().numpy())
            if start%2048==0:status(stage='evaluation '+mode,done=start+len(ids),total=len(rows),windows_per_second=(start+len(ids))/(time.monotonic()-started))
        mask=np.concatenate(masks);pred=np.concatenate(predictions);metrics=evaluate(pred,rows,mask)
        require_structured(metrics,paired=True)
        metrics['reference_price_relative']=distribution(np.concatenate(reference_error));metrics['reference_activity_log_absolute']=distribution(np.concatenate(activity_error))
        z=np.concatenate(embeddings);variance=z.var(0)
        metrics['embedding']=dict(mean_variance=float(variance.mean()),minimum_variance=float(variance.min()),dead_dimensions=int((variance<1e-8).sum()))
        metrics['oracle_true_visible_reference_DIAGNOSTIC_ONLY']=evaluate(np.concatenate(oracles),rows,mask)
        if controls:metrics['visible_median_control']=evaluate(np.concatenate(baseline),rows,mask)
        if controls and constant_logs is not None:
            constant=inverse(torch.tensor(constant_logs)).cpu().numpy()
            metrics['training_constant_control']=evaluate(np.broadcast_to(constant,pred.shape),rows,mask)
        metrics['seconds']=time.monotonic()-started;metrics['windows_per_second']=len(rows)/metrics['seconds'];result[mode]=metrics
    return result
