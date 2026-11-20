"""Fixed-size, vectorized move buckets; no example loops or extra inference."""
from pathlib import Path
import numpy as np
import torch

CHANNELS=('trade_close','bid_close','ask_close')
RANGES=('0_to_0.05pct','0.05_to_0.1pct','0.1_to_0.25pct','0.25_to_0.5pct','above_0.5pct')
BINS=('unchanged',)+tuple(f'{side}_{band}' for side in ('down','up') for band in RANGES)
FIELDS=('count','correct','moving','correct_moving','raw_error','relative_error_pct',
        'last_error','shifted_error','actual_return_pct','predicted_return_pct')


class MoveMetrics:
    def __init__(self,device):
        self.edges=torch.tensor([.05,.1,.25,.5],dtype=torch.float64,device=device)
        self.offset=torch.arange(64*3,device=device).reshape(1,64,3)*11
        self.sums=torch.zeros((2,2,64*3*11,10),device=device,dtype=torch.float64)

    def bucket(self,returns):
        # (lower, upper], separate exact zero; no fitted or changing boundaries.
        magnitude=torch.bucketize(returns.abs().contiguous(),self.edges,right=False)
        return torch.where(returns==0,0,1+magnitude+5*(returns>0).long())

    @torch.no_grad()
    def add(self,p,target):
        ids=[3,9,15]
        y=target['raw'][...,ids].double();valid=target['valid'][...,ids]
        last=target['last_raw'][:,None,ids].double();lv=target['last_valid'][:,None,ids]
        last_log=target['last'][:,None,ids].double();p=p[...,ids].double();raw_p=p.exp()
        previous=torch.cat((last,y[:,:-1]),1);pv=torch.cat((lv,valid[:,:-1]),1)
        pred_previous=torch.cat((last_log,p[:,:-1]),1)
        # Use one common valid population for both baselines within each bucket.
        mask=valid & lv & pv & (y>0) & (last>0) & (previous>0)
        error=(raw_p-y).abs();relative=100*error/torch.where(mask,y,1)
        last_error=(last-y).abs();shifted_error=(previous-y).abs()
        for definition,(reference,pred_reference) in enumerate(((last,last_log),(previous,pred_previous))):
            actual_return=100*(y/torch.where(mask,reference,1)-1)
            predicted_return=100*torch.expm1(p-pred_reference)
            moving=actual_return!=0
            correct=predicted_return.sign()==actual_return.sign()
            values=torch.stack((torch.ones_like(y),correct,moving,correct&moving,error,relative,
                                last_error,shifted_error,actual_return,predicted_return),-1)
            values=torch.where(mask[...,None],values,0).reshape(-1,10)
            for grouping,returns in enumerate((actual_return,predicted_return)):
                index=(self.offset+self.bucket(returns)).reshape(-1,1).expand(-1,10)
                self.sums[definition,grouping].scatter_add_(0,index,values)

    def arrays(self):return self.sums.cpu().numpy().reshape(2,2,64,3,11,10)

    @staticmethod
    def row(v,population):
        population=float(population)
        n,correct,moving,correct_moving,err,rel,old,new,actual,predicted=map(float,v)
        return dict(count=int(n),share_pct=100*n/population if population else None,
            direction_accuracy_pct=100*correct/n if n else None,
            direction_moving_pct=100*correct_moving/moving if moving else None,
            raw_mae=err/n if n else None,price_mape_pct=rel/n if n else None,
            last_observed_mae=old/n if n else None,shifted_actual_mae=new/n if n else None,
            skill_last=1-err/old if old else None,skill_shifted=1-err/new if new else None,
            actual_return_pct=actual/n if n else None,predicted_return_pct=predicted/n if n else None)

    def result(self):
        a=self.arrays();out={}
        for d,definition in enumerate(('from_origin','consecutive_path')):
            for g,grouping in enumerate(('actual','predicted')):
                for c,channel in enumerate(CHANNELS):
                    scopes=[('all',a[d,g,:,c].sum(0))]
                    if c==0:scopes += [(f'bar_{h+1}',a[d,g,h,c]) for h in range(3)]
                    for scope,table in scopes:
                        for b,name in enumerate(BINS):
                            stem=f'forecast/moves/{definition}/{grouping}/{scope}/{channel}/{name}/'
                            out.update({stem+k:v for k,v in self.row(table[b],table[:,0].sum()).items()})
        return out

    def save(self,root,step):
        root=Path(root);path=root/'move-buckets-latest.npz';temp=root/'move-buckets-latest.tmp'
        a=self.arrays()
        if not np.isfinite(a).all():raise FloatingPointError('Nonfinite move bucket sums')
        with temp.open('wb') as f:
            np.savez_compressed(f,sums=a,step=np.array(step),fields=np.array(FIELDS),bins=np.array(BINS),
                channels=np.array(CHANNELS),definitions=np.array(['from_origin','consecutive_path']),
                groupings=np.array(['actual','predicted']))
        temp.replace(path)
        return path
