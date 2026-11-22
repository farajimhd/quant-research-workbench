"""One forward per visibility condition; original raw and directional reducers."""
import time
from types import SimpleNamespace
import torch
from ..phase0_v1.metrics import Metrics,scalars
from ..phase1_v17.metrics import ActivityMetrics,scalars as activity_scalars
from ..phase1_v11.transforms import inverse,PRICES
from ..phase1_v12.model import positions


@torch.no_grad()
def evaluate(model,data,progress=None,batch=256):
    start=time.perf_counter();model.eval();detail={};metrics={};total=len(data[0])*3;done=0
    for name,visible in [('all_visible',64),('hidden8',56),('hidden48',16)]:
        metric=Metrics();act=ActivityMetrics();predictions=[];targets=[];masks=[];zsum=None;z2=None
        gen=torch.Generator(device='cuda').manual_seed(917)
        for i in range(0,len(data[0]),batch):
            x,v,raw,f=[t[i:i+batch] for t in data];pos=positions(len(x),'cuda',visible,gen)
            out,z=model(x,v,pos);mask=v.clone();inactive=[j for j in range(18) if j not in model.active];mask[...,inactive]=False
            if visible<64:mask&=(~torch.zeros_like(v[...,0]).scatter(1,pos,True))[...,None]
            metric.add(model,out,x,mask,SimpleNamespace(grid=raw,frames=f));act.add(out['logs'],raw,mask,f)
            predictions.append(inverse(out['logs']));targets.append(raw.double());masks.append(mask[...,:18]);zz=z.double()
            s=zz.sum(0);q=zz.square().sum(0);zsum=s if zsum is None else zsum+s;z2=q if z2 is None else z2+q
            done+=len(x)
            if progress and (i//batch)%8==0:progress(done,total,time.perf_counter()-start)
        d=metric.result();a=act.result();detail[name]=d;detail['activity_'+name]=a
        metrics.update({name+'/'+k:v for k,v in scalars(d).items()});metrics.update({'activity/'+name+'/'+k:v for k,v in activity_scalars(a).items()})
        p=torch.cat(predictions);t=torch.cat(targets);m=torch.cat(masks);audit={}
        zvar=(z2/len(data[0])-(zsum/len(data[0])).square()).clamp_min(0)
        audit['constant_embedding_coordinates']=int((zvar<1e-16).sum());audit['embedding_mean_std']=float(zvar.sqrt().mean())
        audit['negative']=int(((p<0)&m).sum());audit['nonfinite']=int((~p.isfinite()&m).sum())
        for j in model.active:
            keep=m[...,j];pp=p[...,j][keep];tt=t[...,j][keep]
            audit[f'channel{j}/prediction_variance']=float(pp.var()) if pp.numel()>1 else 0.
            audit[f'channel{j}/target_variance']=float(tt.var()) if tt.numel()>1 else 0.
            audit[f'channel{j}/zero_denominators']=int((tt==0).sum())
        price=[j for j in PRICES if j in model.active]
        if price:
            pp=p[...,price];tt=t[...,price];mm=m[...,price]
            audit['nonpositive_price_denominators']=int((mm&(tt<=0)).sum());audit['tiny_price_denominators']=int((mm&(tt>0)&(tt<1e-8)).sum())
            for lo,hi in zip((0,.1,1,5,20,100),(.1,1,5,20,100,float('inf'))):
                keep=mm&(tt>0)&(tt>=lo)&(tt<hi);n=int(keep.sum());prefix=f'price_band_{lo}_{hi}/';audit[prefix+'count']=n
                if n:
                    er=(pp-tt).abs()[keep];rel=100*er/tt[keep]
                    audit.update({prefix+'mae':float(er.mean()),prefix+'mean_pct':float(rel.mean()),prefix+'median_pct':float(rel.median()),prefix+'p90_pct':float(rel.quantile(.9))})
            bad=0
            for b in (0,6,12):
                o,h,l,c=p[...,b:b+4].unbind(-1);keep=m[...,b:b+4].all(-1);bad+=int((keep&((h<torch.maximum(o,c))|(l>torch.minimum(o,c)))).sum())
            audit['ohlc_violations']=bad;audit['quote_crossings']=int(((p[...,6:10]>p[...,12:16])&m[...,6:10]&m[...,12:16]).sum())
        detail['audit_'+name]=audit;metrics.update({name+'/audit/'+k:v for k,v in audit.items()})
        if d['nonfinite'] or audit['nonfinite']:raise FloatingPointError('Nonfinite evaluation')
    metrics['evaluation_seconds']=time.perf_counter()-start;metrics['evaluation_windows_per_second']=total/metrics['evaluation_seconds'];model.train();return detail,metrics


def summary(metrics,mode='all_visible'):
    def avg(prefix,suffix):
        v=[x for k,x in metrics.items() if k.startswith(prefix) and k.endswith(suffix)];return sum(v)/len(v) if v else None
    return dict(close_mae=metrics.get(mode+'/raw_mae/1s/trade_close'),candle=avg(mode+'/candle/','/moving_direction_pct'),
        range=avg(mode+'/range/','/bin_accuracy_pct'),activity=avg('activity/'+mode+'/','/balanced_direction_pct'))
