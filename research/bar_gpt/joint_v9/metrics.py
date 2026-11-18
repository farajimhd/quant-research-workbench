"""Future-change skill, grouped uncertainty and native reconstruction evidence."""
import numpy as np
import time
import torch
from .data import anchor,historical_mean_anchor
from .model import structured
from ..phase1_v8.model import inverse,PRICES,ACTIVITY
from ..phase1_v7.metrics import evaluate as native_metrics,require_structured
from ..v5.shards.phase1_contract import SAMPLE_DTYPE


def future_rows(rows):
    result=np.zeros(len(rows),dtype=SAMPLE_DTYPE)
    for key in ('values','valid','end_us'):result[key]=rows['future'][key]
    result['timeframe_us']=1000000
    return result


def skill(prediction,target,valid,baseline,scale,identities):
    err=np.abs(prediction-target)/scale;base=np.abs(baseline-target)/scale
    m=valid.astype(bool);count=m.sum(1)
    per=np.where(m,err,0).sum(1)/np.maximum(count,1);pb=np.where(m,base,0).sum(1)/np.maximum(count,1)
    result={};scores=[]
    for label,ids in [('price',PRICES),('activity',ACTIVITY)]:
        a=per[:,ids].mean(1);b=pb[:,ids].mean(1)
        result[label+'_mae_skill']=float(1-a.mean()/max(b.mean(),1e-12))
        result[label+'_normalized_mae']=float(a.mean());result[label+'_baseline_normalized_mae']=float(b.mean())
        scores.append(a.mean()/max(b.mean(),1e-12))
        groups={}
        for i,item in enumerate(identities):groups.setdefault(item['ticker']+'|'+item['session'],[]).append(i)
        ga=np.array([a[ix].mean() for ix in groups.values()]);gb=np.array([b[ix].mean() for ix in groups.values()])
        result.setdefault('session_errors',{})[label]=[dict(session=key,model_mae=float(a[ix].mean()),baseline_mae=float(b[ix].mean()),origins=len(ix)) for key,ix in groups.items()]
        rng=np.random.default_rng(917);indices=rng.integers(len(ga),size=(500,len(ga)))
        boot=1-ga[indices].mean(1)/np.maximum(gb[indices].mean(1),1e-12)
        result[label+'_session_bootstrap95']=np.quantile(boot,[.025,.975]).tolist()
        result[label+'_groups']={}
        for liquid in (0,1):
            ix=np.array([r['liquid']==liquid for r in identities])
            if ix.any():result[label+'_groups']['liquid_'+str(liquid)]=dict(count=int(ix.sum()),skill=float(1-a[ix].mean()/max(b[ix].mean(),1e-12)))
    result['selection_score']=float(np.mean(scores));result['sessions']=len(groups)
    changes=prediction-baseline;actual=target-baseline
    result['forecast_change_variance']=np.var(changes,axis=(0,1)).tolist();result['target_change_variance']=np.var(actual,axis=(0,1)).tolist()
    result['horizon_close']={}
    for h in (1,8,16,32,64):
        ix=m[:,h-1,3]
        result['horizon_close'][str(h)]=dict(count=int(ix.sum()),invalid_count=int((~ix).sum()),
            mae=float(np.abs(prediction[ix,h-1,3]-target[ix,h-1,3]).mean()) if ix.any() else None,
            persistence_mae=float(np.abs(baseline[ix,h-1,3]-target[ix,h-1,3]).mean()) if ix.any() else None,
            sign_accuracy=float((np.sign(changes[ix,h-1,3])==np.sign(actual[ix,h-1,3])).mean()) if ix.any() else None,
            zero_target_changes=int((actual[ix,h-1,3]==0).sum()))
    return result


@torch.no_grad()
def measure(model,data,rows,identities,batch_size=128,reconstruction=True,status=None):
    model.eval();pred=[];past=[];means=[];rolling=[];medians=[];recon=[];zs=[];metadata=[];started=time.perf_counter()
    for start in range(0,len(rows),batch_size):
        d={k:v[start:start+batch_size] for k,v in data.items()}
        o=model(d['x'],d['valid'],d['ages'],reconstruct=reconstruction)
        p=o['forecast'];base=anchor(d['x'],d['valid'],check=False)
        if not torch.isfinite(p).all():raise FloatingPointError('Nonfinite forecast')
        pred.append(p.cpu().numpy());past.append(structured(base[:,None]).expand(-1,64,-1).cpu().numpy())
        means.append(structured(base[:,None]+model.mean_future_delta).cpu().numpy());zs.append(o['joint'].cpu().numpy())
        rolling.append(structured(historical_mean_anchor(d['x'],d['valid']))[:,None].expand(-1,64,-1).cpu().numpy())
        medians.append(structured(historical_mean_anchor(d['x'],d['valid'],'median'))[:,None].expand(-1,64,-1).cpu().numpy())
        if reconstruction:
            recon.append(inverse(o['reconstruction'][0]).cpu().numpy())
            _,t,vl=o['reconstruction'];v=d['valid'];tv=v[...,18:]
            time_error=(t*model.input_scale[18:]+model.input_mean[18:]-d['x'][...,18:]).abs()
            yes=vl>=0
            metadata.append(dict(time_sum=torch.where(tv,time_error,0).sum((0,1,2)).cpu().numpy(),time_count=tv.sum((0,1,2)).cpu().numpy(),
                tp=int((yes&v).sum()),tn=int((~yes&~v).sum()),fp=int((yes&~v).sum()),fn=int((~yes&v).sum())))
        if status:
            done=min(start+batch_size,len(rows));elapsed=time.perf_counter()-started
            status(evaluation_done=done,evaluation_total=len(rows),evaluation_origins_per_second=done/max(elapsed,1e-9),evaluation_eta_seconds=(len(rows)-done)*elapsed/done)
    p=np.concatenate(pred);baseline=np.concatenate(past);mean=np.concatenate(means);target=data['target'].cpu().numpy();valid=data['target_valid'].cpu().numpy()
    result=skill(p,target,valid,baseline,model.loss_scale.cpu().numpy(),identities)
    result['training_mean_control']=skill(mean,target,valid,baseline,model.loss_scale.cpu().numpy(),identities)
    history_mean=np.concatenate(rolling)
    result['history_mean_control']=skill(history_mean,target,valid,baseline,model.loss_scale.cpu().numpy(),identities)
    result['activity_history_mean_skill']=1-result['activity_normalized_mae']/max(result['history_mean_control']['activity_normalized_mae'],1e-12)
    result['history_mean_selection_score']=.5*(1-result['price_mae_skill']+1-result['activity_history_mean_skill'])
    history_median=np.concatenate(medians)
    result['history_median_control']=skill(history_median,target,valid,baseline,model.loss_scale.cpu().numpy(),identities)
    result['activity_history_median_skill']=1-result['activity_normalized_mae']/max(result['history_median_control']['activity_normalized_mae'],1e-12)
    future=future_rows(rows)
    result['native_forecast']=native_metrics(inverse(torch.from_numpy(p)).numpy(),future)
    require_structured(result['native_forecast'],paired=True)
    result['native_persistence']=native_metrics(inverse(torch.from_numpy(baseline)).numpy(),future)
    result['native_history_mean']=native_metrics(inverse(torch.from_numpy(history_mean)).numpy(),future)
    result['native_history_median']=native_metrics(inverse(torch.from_numpy(history_median)).numpy(),future)
    if reconstruction:
        result['reconstruction']=native_metrics(np.concatenate(recon).reshape(-1,64,18),rows['context'].reshape(-1))
        require_structured(result['reconstruction'],paired=True)
        result['reconstruction_metadata']=dict(time_mae=(sum(x['time_sum'] for x in metadata)/np.maximum(sum(x['time_count'] for x in metadata),1)).tolist(),
            time_valid_counts=sum(x['time_count'] for x in metadata).tolist(),validity_confusion={k:sum(x[k] for x in metadata) for k in ('tp','tn','fp','fn')})
    result['joint_embedding_variance']=np.var(np.concatenate(zs),axis=0).tolist()
    return result


@torch.no_grad()
def masked_reconstruction(model,data,rows,batch_size=128,seed=917):
    """Sixteen random visible bars per window; no hidden reference in encoder."""
    model.eval();rng=np.random.default_rng(seed);pred=[]
    for start in range(0,len(rows),batch_size):
        x=data['x'][start:start+batch_size];v=data['valid'][start:start+batch_size]
        ranks=rng.random(tuple(v.shape[:-1])).argsort(-1).argsort(-1)
        visible=torch.from_numpy(ranks<16).to(v.device)
        z=model.encode_windows(x,v&visible[...,None]);p=model.reconstruct(z)[0]
        pred.append(inverse(p).cpu().numpy())
    native=native_metrics(np.concatenate(pred).reshape(-1,64,18),rows['context'].reshape(-1));require_structured(native,paired=True)
    return dict(visible_bars=16,seed=seed,masking='All 27 input feature validities hidden for 48 bars. Original source-valid targets retained. No copied references.',native=native)


@torch.no_grad()
def reconstruction_controls(model,data,rows,batch_size=128,seed=917):
    """Explicit input-reading controls, never connections in the learned decoder."""
    constant=inverse(structured(model.reconstruction_mean)).cpu().numpy()
    count=len(rows)*16;targets=rows['context'].reshape(-1)
    result=dict(training_constant=native_metrics(np.broadcast_to(constant,(count,64,18)),targets),
        policy='Constant uses frozen training-only coordinates. Median controls read visible historical input directly; they are reconstruction controls, not exported representations.')
    for masked in (False,True):
        rng=np.random.default_rng(seed);pred=[]
        for start in range(0,len(rows),batch_size):
            x=data['x'][start:start+batch_size,...,:18];v=data['valid'][start:start+batch_size,...,:18]
            if masked:
                visible=rng.random(tuple(v.shape[:-1])).argsort(-1).argsort(-1)<16
                v=v&torch.from_numpy(visible).to(v.device)[...,None]
            median=torch.nanmedian(torch.where(v,x,float('nan')),dim=2).values
            median=torch.where(torch.isfinite(median),median,model.input_mean[:18])
            pred.append(inverse(median).cpu().numpy())
        values=np.concatenate(pred).reshape(-1,18)
        result['visible_median_16' if masked else 'visible_median_64']=native_metrics(np.broadcast_to(values[:,None],(count,64,18)),targets)
    return result
