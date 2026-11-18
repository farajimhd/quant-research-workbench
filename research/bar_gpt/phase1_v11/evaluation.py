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
        predictions=[];oracles=[];masks=[];embeddings=[];reference_error=[];activity_error=[];baseline=[];reference_rows=[];reference_valid=[]
        for start in range(0,len(rows),256):
            ids=torch.arange(start,min(start+256,len(rows)),device=device)
            x,v,pos,t,tv,hidden=batch(data,ids,16,generator,full=mode=='all_visible')
            z=model.encoder(x,v,pos);out=model.decode(z)
            refs,rv=references(x[...,:18],v[...,:18],model.encoder.reference_mean)
            reference_error.append(torch.expm1(out['references'][:,REF_PRICES].double()-refs[:,REF_PRICES]).abs()[rv[:,REF_PRICES]].cpu().numpy())
            activity_error.append((out['references'][:,REF_ACTIVITY]-refs[:,REF_ACTIVITY]).abs()[rv[:,REF_ACTIVITY]].cpu().numpy())
            reference_rows.append(torch.stack((out['references'],refs),-1).cpu().numpy());reference_valid.append(rv.cpu().numpy())
            predictions.append(inverse(out['logs']).cpu().numpy());masks.append(hidden.cpu().numpy()[...,None]);embeddings.append(z.cpu().numpy())
            oracles.append(inverse(decode(out['code'],refs)).cpu().numpy())
            if controls:baseline.append(inverse(decode(torch.zeros_like(out['code']),refs)).cpu().numpy())
            if start%2048==0:status(stage='evaluation '+mode,done=start+len(ids),total=len(rows),windows_per_second=(start+len(ids))/max(time.monotonic()-started,1e-9))
        mask=np.concatenate(masks);pred=np.concatenate(predictions);metrics=evaluate(pred,rows,mask)
        require_structured(metrics,paired=True)
        metrics['reference_price_relative']=distribution(np.concatenate(reference_error));metrics['reference_activity_log_absolute']=distribution(np.concatenate(activity_error))
        rp=np.concatenate(reference_rows).astype(np.float64);rv=np.concatenate(reference_valid)
        names=('trade_price','trade_volume','trade_count','bid_price','bid_size','bid_count','ask_price','ask_size','ask_count')
        metrics['references_by_timeframe']={}
        for frame in np.unique(rows['timeframe_us']):
            group={};take=rows['timeframe_us']==frame
            for j,name in enumerate(names):
                p,t=rp[take,j,0][rv[take,j]],rp[take,j,1][rv[take,j]]
                native_p=np.exp(p) if j%3==0 else np.expm1(p);native_t=np.exp(t) if j%3==0 else np.expm1(t)
                group[name]=dict(log_absolute=distribution(np.abs(p-t)),raw_absolute=distribution(np.abs(native_p-native_t)),
                    relative=distribution(np.abs(native_p[native_t>0]-native_t[native_t>0])/native_t[native_t>0]),zero_denominators=int((native_t==0).sum()))
            metrics['references_by_timeframe'][str(frame)]=group
        z=np.concatenate(embeddings);variance=z.var(0)
        metrics['embedding']=dict(mean_variance=float(variance.mean()),minimum_variance=float(variance.min()),dead_dimensions=int((variance<1e-8).sum()))
        metrics['oracle_true_visible_reference_DIAGNOSTIC_ONLY']=evaluate(np.concatenate(oracles),rows,mask)
        if controls:metrics['visible_median_control']=evaluate(np.concatenate(baseline),rows,mask)
        if controls and constant_logs is not None:
            constant=inverse(torch.tensor(constant_logs)).cpu().numpy()
            metrics['training_constant_control']=evaluate(np.broadcast_to(constant,pred.shape),rows,mask)
        metrics['seconds']=time.monotonic()-started;metrics['windows_per_second']=len(rows)/max(metrics['seconds'],1e-9);result[mode]=metrics
    return result
