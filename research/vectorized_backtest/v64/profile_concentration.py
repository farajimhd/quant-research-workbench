"""Training-only retrospective coefficient sensitivity; never opens validation."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
from pathlib import Path
import json,hashlib,time
import numpy as np
import argparse
def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--generation',type=int,required=True);a=p.parse_args()
    root=a.campaign
    folder=root/'phase-2/global'/f'generation-{a.generation:04d}'
    training=json.loads((folder/'training.json').read_bytes())
    ids=np.asarray(training['ids']); n=len(ids); nets=[];loss=[];dd=[];base=[];penalty=[];eligible=[];days=[]
    for day,ref in sorted(training['receipts'].items()):
     p=Path(ref['receipt']);raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==ref['receipt_sha256'];r=json.loads(raw);del raw
     assert r['split']=='training' and not r['validation_opened'] and r['full_session']
     m=r['metrics'];assert len(m['net_pnl'])==n
     b=np.zeros(n);q=np.zeros(n);l=np.zeros(n)
     for i,(profits,listings) in enumerate(zip(m['episode_profits'],m['episode_listings'])):
      v=np.asarray(profits,float);ix=np.unique(listings,return_inverse=True)[1];count=np.bincount(ix);best=np.zeros(len(count));np.maximum.at(best,ix,v)
      l[i]=np.maximum(-v,0).sum();b[i]=v.sum()-l[i];q[i]=(best*(1-1/np.maximum(count,1))).sum()
     nets.append(m['net_pnl']);loss.append(l);dd.append(m['drawdown']);base.append(b);penalty.append(q);eligible.append(m['terminal_valid']);days.append(day)
     print(day,flush=True);del r,m
    arrays=dict(nets=np.array(nets),loss=np.array(loss),dd=np.array(dd),base=np.array(base),penalty=np.array(penalty),eligible=np.array(eligible),ids=ids)
    out=root/f'concentration-study-gen{a.generation}';out.mkdir(exist_ok=True)
    np.savez(out/'training-statistics.npz',**arrays)
    def score(ix,a):
     net=arrays['nets'][ix]; pos=(net>0).sum(0)
     return (arrays['base'][ix]+net-arrays['loss'][ix]-arrays['dd'][ix]).sum(0)-a*(arrays['penalty'][ix].sum(0)+np.maximum(net.max(0),0)*(1-1/np.maximum(pos,1)))
    results=[]
    for a in (0.,.1,.25,.5,.75,1.):
     row={'coefficient':a};folds=[]
     for end in (12,16,20):
      fit=np.arange(end);hold=np.arange(end,end+4);valid=arrays['eligible'][fit].all(0);s=score(fit,a);order=np.lexsort((ids,-np.where(valid,s,-np.inf)))[:32]
      h=arrays['nets'][hold][:,order];risk=(h-arrays['loss'][hold][:,order]-arrays['dd'][hold][:,order]).sum(0)
      folds.append(dict(start=days[end],median_risk_adjusted=float(np.median(risk)),median_net=float(np.median(h.sum(0))),median_profitable_days=float(np.median((h>0).sum(0))),median_net_ex_best=float(np.median(h.sum(0)-np.maximum(h.max(0),0)))))
     order=np.lexsort((ids,-np.where(arrays['eligible'].all(0),score(np.arange(24),a),-np.inf)))[:32]
     h=arrays['nets'][:,order];row.update(folds=folds,criterion=float(np.mean([x['median_risk_adjusted'] for x in folds])),top_ids=ids[order].tolist(),median_profitable_days=float(np.median((h>0).sum(0))),median_net=float(np.median(h.sum(0))),median_net_ex_best=float(np.median(h.sum(0)-np.maximum(h.max(0),0))))
     results.append(row)
    report=dict(generation=a.generation,candidates=n,training_only=True,validation_opened=False,limitation='Retrospective ranking sensitivity: these candidates already evolved on all training dates; folds are not independent out-of-sample evidence.',formula='coefficient * best_positive * (1 - 1 / count); count=closed trades per ticker-date or positive-net sessions',selection_criterion='Mean median net-minus-loss-minus-drawdown of top32 on next4 training dates after ranking on first12/16/20',results=results)
    (out/'comparison.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(results),flush=True)

if __name__=="__main__":main()
