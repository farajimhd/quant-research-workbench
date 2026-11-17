"""Future-change skill, grouped uncertainty and native reconstruction evidence."""
import numpy as np
import torch
from .data import anchor
from .model import structured
from ..phase1_v8.model import inverse,PRICES,ACTIVITY
from ..phase1_v7.metrics import evaluate as native_metrics
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
    result['horizon_close']={str(h):dict(mae=float(np.abs(prediction[:,h-1,3]-target[:,h-1,3]).mean()),
        persistence_mae=float(np.abs(baseline[:,h-1,3]-target[:,h-1,3]).mean()),
        sign_accuracy=float((np.sign(changes[:,h-1,3])==np.sign(actual[:,h-1,3])).mean()),zero_target_changes=int((actual[:,h-1,3]==0).sum())) for h in (1,8,16,32,64)}
    return result


@torch.no_grad()
def measure(model,data,rows,identities,batch_size=128,reconstruction=True,status=None):
    model.eval();pred=[];past=[];means=[];recon=[];zs=[]
    for start in range(0,len(rows),batch_size):
        d={k:v[start:start+batch_size] for k,v in data.items()}
        o=model(d['x'],d['valid'],d['ages'],reconstruct=reconstruction)
        p=o['forecast'];base=anchor(d['x'],d['valid'],check=False)
        if not torch.isfinite(p).all():raise FloatingPointError('Nonfinite forecast')
        pred.append(p.cpu().numpy());past.append(structured(base[:,None]).expand(-1,64,-1).cpu().numpy())
        means.append(structured(base[:,None]+model.mean_future_delta).cpu().numpy());zs.append(o['joint'].cpu().numpy())
        if reconstruction:recon.append(inverse(o['reconstruction'][0]).cpu().numpy())
        if status:status(evaluation_done=min(start+batch_size,len(rows)),evaluation_total=len(rows))
    p=np.concatenate(pred);baseline=np.concatenate(past);mean=np.concatenate(means);target=data['target'].cpu().numpy();valid=data['target_valid'].cpu().numpy()
    result=skill(p,target,valid,baseline,model.loss_scale.cpu().numpy(),identities)
    result['training_mean_control']=skill(mean,target,valid,baseline,model.loss_scale.cpu().numpy(),identities)
    future=future_rows(rows)
    result['native_forecast']=native_metrics(inverse(torch.from_numpy(p)).numpy(),future)
    result['native_persistence']=native_metrics(inverse(torch.from_numpy(baseline)).numpy(),future)
    if reconstruction:result['reconstruction']=native_metrics(np.concatenate(recon).reshape(-1,64,18),rows['context'].reshape(-1))
    result['joint_embedding_variance']=np.var(np.concatenate(zs),axis=0).tolist()
    return result
