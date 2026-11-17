"""Native reconstruction and fixed-label information probes; no future labels."""
import numpy as np
from .model import PRICES, ACTIVITY
from ..v5.shards.phase1_contract import FRAMES, CHANNELS


def distribution(x):
    x=np.asarray(x,dtype=np.float64)
    x=x[np.isfinite(x)]
    return dict(count=int(x.size),mean=float(x.mean()),median=float(np.median(x)),p90=float(np.quantile(x,.9))) if x.size else dict(count=0)


def evaluate(pred, rows, mask=None):
    target=rows['values'].astype(np.float64)
    valid=rows['valid'].astype(bool) if mask is None else rows['valid'].astype(bool)&mask
    result={}
    for frame in FRAMES:
        take=rows['timeframe_us']==frame
        p,t,v=pred[take],target[take],valid[take]
        finite=np.isfinite(p)
        item=dict(windows=int(take.sum()),nonfinite=int((~finite&v).sum()),negative=int(((p<0)&v).sum()),channels={})
        for j,name in enumerate(CHANNELS):
            ok=v[...,j]&finite[...,j]
            pv,tv=p[...,j][ok],t[...,j][ok]
            e=np.abs(pv-tv)
            c=dict(count=int(ok.sum()),mae=float(e.mean()) if len(e) else None,
                prediction_variance=float(pv.var()) if len(e) else None,target_variance=float(tv.var()) if len(e) else None,
                zero_denominators=int((tv==0).sum()),tiny_denominators=int(((tv>0)&(tv<1e-8)).sum()))
            counts=ok.sum(1);observed=counts>0
            predicted_means=np.where(ok,p[...,j],0).sum(1)/np.maximum(counts,1)
            target_means=np.where(ok,t[...,j],0).sum(1)/np.maximum(counts,1)
            c['across_example_prediction_mean_variance']=float(predicted_means[observed].var()) if observed.any() else None
            c['across_example_target_mean_variance']=float(target_means[observed].var()) if observed.any() else None
            if j in PRICES:
                pos=tv>0
                c['relative']=distribution(e[pos]/tv[pos])
                c['bands']={label:distribution(e[m]/tv[m]) for label,m in zip(
                    ('below_0.1','0.1_to_1','1_to_5','5_to_20','20_to_100','100_plus'),
                    [(tv>0)&(tv<.1),(tv>=.1)&(tv<1),(tv>=1)&(tv<5),(tv>=5)&(tv<20),(tv>=20)&(tv<100),tv>=100])}
            else:
                c['relative_positive']=distribution(e[tv>0]/tv[tv>0])
                admissible=pv>-1
                c['log1p_absolute_error']=distribution(np.abs(np.log1p(pv[admissible])-np.log1p(tv[admissible])))
                c['invalid_log_predictions']=int((~admissible).sum())
            item['channels'][name]=c
        violations={}
        for side,offset in (('trade',0),('bid',6),('ask',12)):
            ok=v[...,offset:offset+4].all(-1)
            bad=(p[...,offset+1]<np.maximum(p[...,offset],p[...,offset+3]))|(p[...,offset+2]>np.minimum(p[...,offset],p[...,offset+3]))|(p[...,offset+1]<p[...,offset+2])
            violations[side]=dict(prediction=int((bad&ok).sum()),eligible=int(ok.sum()))
        ok=v[...,9]&v[...,15]
        violations['crossed_close']=dict(prediction=int(((p[...,9]>p[...,15])&ok).sum()),target=int(((t[...,9]>t[...,15])&ok).sum()),eligible=int(ok.sum()))
        item['structure']=violations
        result[str(frame)]=item
    groups=[c['relative']['mean'] for f in result.values() for c in f['channels'].values() if 'relative' in c and c['relative']['count']]
    bands=[b['mean'] for f in result.values() for c in f['channels'].values() for b in c.get('bands',{}).values() if b['count']]
    activity=[c['log1p_absolute_error']['mean'] for f in result.values() for c in f['channels'].values()
              if c.get('log1p_absolute_error',{}).get('count')]
    price=float(np.mean(groups));worst=float(max(bands));act=float(np.mean(activity))
    return dict(frames=result,balanced_price_relative_mean=price,worst_price_band_relative_mean=worst,
        balanced_activity_log_mae=act,selection_score=price+.1*worst+.1*act)


def summaries(rows):
    x=rows['values'].astype(np.float64);v=rows['valid'].astype(bool)
    # Targets are explicitly within-window information, not future/trading labels.
    close=x[...,3];log=np.log(close);ret=np.diff(log,axis=1)
    quote=v[...,9]&v[...,15]
    spread=np.where(quote,(x[...,15]-x[...,9])/close,0).sum(1)/np.maximum(quote.sum(1),1)
    labels=np.stack((log.mean(1),np.log1p(x[...,4]).mean(1),log[:,-1]-log[:,0],
        np.log(x[...,1].max(1)/x[...,2].min(1)),ret.std(1),spread,
        np.log1p(x[...,5]).mean(1),quote.mean(1)),1)
    names=['log_price_scale','log_activity_scale','window_log_return','log_range','return_volatility','relative_spread','log_trade_count','quote_coverage']
    # Cheap raw-summary control contains the same directly available statistics.
    return labels,names


def probes(train_z, eval_z, train_rows, eval_rows):
    y,names=summaries(train_rows);test_y,_=summaries(eval_rows)
    xm=train_z.mean(0);xs=train_z.std(0).clip(1e-8)
    a=(train_z-xm)/xs;b=(eval_z-xm)/xs
    ym=y.mean(0);ys=y.std(0).clip(1e-8)
    a=np.c_[a,np.ones(len(a))];b=np.c_[b,np.ones(len(b))]
    ridge=np.eye(a.shape[1]);ridge[-1,-1]=0
    coef=np.linalg.solve(a.T@a+ridge,(a.T@((y-ym)/ys)))
    pred=(b@coef)*ys+ym
    return {name:dict(mae=float(np.abs(pred[:,j]-test_y[:,j]).mean()),
        r2=float(1-((pred[:,j]-test_y[:,j])**2).sum()/max(((test_y[:,j]-test_y[:,j].mean())**2).sum(),1e-20))) for j,name in enumerate(names)}
