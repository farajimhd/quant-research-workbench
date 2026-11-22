"""Frozen development retention rules; never rank a regressed model as a winner."""
import math
from ..phase1_v25.metrics import summary


def check(metrics,reference):
    s=summary(metrics);r=summary(reference);fail=[];ratios={}
    for family in ('price','activity'):
        pairs=[]
        for k,v in metrics.items():
            if not k.startswith('all_visible/raw_mae/') or reference.get(k,0)<=0:continue
            is_activity=k.split('/')[-1].endswith(('volume','size','count'))
            if is_activity==(family=='activity'):pairs.append((k,v/reference[k]))
        ratios[family]=dict(geomean=math.exp(sum(math.log(max(v,1e-12)) for _,v in pairs)/len(pairs)),worst=max(v for _,v in pairs),worst_groups=sorted(pairs,key=lambda x:-x[1])[:6])
    if s['candle']<r['candle']-1:fail.append('candle macro exceeds 1pp regression')
    if s['range']<r['range']-1:fail.append('range macro exceeds 1pp regression')
    if ratios['price']['geomean']>1.05:fail.append('price raw MAE geometric mean exceeds 5% regression')
    if ratios['price']['worst']>1.25:fail.append('price worst group exceeds 25% regression')
    if ratios['activity']['geomean']>1.15:fail.append('activity raw MAE geometric mean exceeds 15% regression')
    if ratios['activity']['worst']>1.5:fail.append('activity worst group exceeds 50% regression')
    if s['activity']<r['activity']-1:fail.append('activity balanced direction exceeds 1pp regression')
    for k,v in metrics.items():
        if k.startswith('all_visible/candle/') and k.endswith('/moving_direction_pct') and v<reference[k]-2:fail.append('candle worst group exceeds 2pp regression: '+k)
        if k.startswith('all_visible/audit/price_band') and k.endswith('/median_pct') and v>reference[k]*1.1+.05:fail.append('price band median exceeds 10% plus .05pp: '+k)
    for suffix,tolerance in (('moving_direction_pct',1.),('flat_recall_pct',2.)):
        keys=[k for k in metrics if k.startswith('activity/all_visible/') and k.endswith('/'+suffix) and k in reference]
        if keys and sum(metrics[k]-reference[k] for k in keys)/len(keys)<-tolerance:fail.append('activity '+suffix+' retention')
    score=.5*(ratios['price']['geomean']+ratios['activity']['geomean'])
    return dict(accepted=not fail,reasons=fail,summary=s,ratios=ratios,score=score)
