"""Masked normalized training losses and honest raw-unit validation with forecast references."""
import math
import torch
from ..v5.shards.phase1_contract import CHANNELS
from ..v5.pretraining_v1 import PRICES,ACTIVITY
from ..v5.price_relative_v3 import PriceRelative,CONTRACT as PRICE_CONTRACT
from .targets import BAR_WIDTH,OUTPUTS,REFERENCE_NAMES,reconstruct

CONTRACT=dict(version='phase2-direct-bars-predicted-references-v1',
    population='all source-valid values in all eligible next64 observed1s targets; overlap retained',
    inverse='predicted future references plus known origin-window references ONLY',
    conditioning='no actual future references, timestamps, bars or validity as model inputs',
    guard='normalized bar MSE + reference-change MSE, equal component weights',
    references='actual seven local reference values retained from the future-window transformation',
    quantiles='price-only raw-unit APE histogram median/P90; fixed shared bin semantics',
    negative='not clipped; negative activity/reference forecasts and structural violations counted',
    price_relative=PRICE_CONTRACT)


def flatten_target(target):
    return (torch.cat((target['bars'].flatten(1),target['references']),1),
        torch.cat((target['valid'].flatten(1),target['reference_valid']),1))


class Metrics:
    def __init__(self,device):
        self.count=0
        self.sums=torch.zeros(4,device=device,dtype=torch.float64)
        # Prediction sum/square, target sum/square, valid count per output coordinate.
        self.moments=torch.zeros(5,OUTPUTS,device=device,dtype=torch.float64)

    @torch.no_grad()
    def add(self,predicted,target,persistence=None):
        t,mask=flatten_target(target);p=predicted.double();t=t.double();m=mask.double()
        errors=(p-t).square()*m
        self.count+=len(p)
        self.sums+=torch.stack((errors[:,:BAR_WIDTH].sum(),errors[:,BAR_WIDTH:].sum(),
            m[:,:BAR_WIDTH].sum(),m[:,BAR_WIDTH:].sum()))
        self.moments+=torch.stack(((p*m).sum(0),(p.square()*m).sum(0),
            (t*m).sum(0),(t.square()*m).sum(0),m.sum(0)))

    def baseline(self):
        return self.moments[2]/self.moments[4].clamp_min(1)

    def result(self,baseline=None):
        if not self.count:raise ValueError('No eligible bar targets')
        b,r,bn,rn=self.sums.cpu().tolist()
        result=dict(origins=self.count,mse=b/max(1,bn)+r/max(1,rn),
            normalized_bar_mse=b/max(1,bn),reference_change_mse=r/max(1,rn),
            observed_bar_values=int(bn),observed_references=int(rn))
        p,pp,t,tt,n=self.moments
        active=n>0;den=n.clamp_min(1)
        result.update(prediction_variance=float(((pp/den-(p/den).square())[active]).mean()),
            target_variance=float(((tt/den-(t/den).square())[active]).mean()))
        if baseline is not None:
            error=(tt-2*baseline*t+baseline.square()*n).clamp_min(0)
            result['training_mean_mse']=float(error[:BAR_WIDTH].sum()/max(1,bn)+error[BAR_WIDTH:].sum()/max(1,rn))
        return result

    def state(self):
        return dict(count=self.count,sums=self.sums.cpu().clone(),moments=self.moments.cpu().clone())

    def restore(self,state):
        self.count=state['count'];self.sums.copy_(state['sums']);self.moments.copy_(state['moments'])

    def reset(self):
        self.count=0;self.sums.zero_();self.moments.zero_()


class RawMetrics:
    def __init__(self,device):
        self.sums=torch.zeros(18,64,device=device,dtype=torch.float64)
        self.squares=torch.zeros_like(self.sums)
        self.counts=torch.zeros_like(self.sums,dtype=torch.int64)
        self.bad=torch.zeros_like(self.counts)
        self.ref_sums=torch.zeros(7,device=device,dtype=torch.float64)
        self.ref_counts=torch.zeros(7,device=device,dtype=torch.int64)
        self.ref_bad=torch.zeros_like(self.ref_counts)
        self.negative_activity=torch.zeros(6,device=device,dtype=torch.int64)
        self.negative_references=torch.zeros(6,device=device,dtype=torch.int64)
        self.invalid_ohlc=torch.zeros(3,device=device,dtype=torch.int64)
        self.crossed=torch.zeros(2,device=device,dtype=torch.int64)
        self.relative=PriceRelative()

    @torch.no_grad()
    def add(self,predicted,target):
        raw,refs=reconstruct(predicted,target['origin_log_reference'])
        truth=target['raw'].double();mask=target['valid'];rm=target['reference_valid']
        error=(raw-truth).abs();square=error.square();finite=torch.isfinite(error)&torch.isfinite(square)
        self.sums+=torch.where(mask&finite,error,0).sum(0)
        self.squares+=torch.where(mask&finite,square,0).sum(0)
        self.counts+=mask.sum(0);self.bad+=(mask&~finite).sum(0)
        re=(refs-target['actual_reference']).abs();rf=torch.isfinite(re)
        self.ref_sums+=torch.where(rm&rf,re,0).sum(0);self.ref_counts+=rm.sum(0);self.ref_bad+=(rm&~rf).sum(0)
        self.negative_activity+=((raw[:,ACTIVITY]<0)&mask[:,ACTIVITY]).sum((0,2))
        self.negative_references+=((refs[:,1:]<0)&rm[:,1:]).sum(0)
        for f,start in enumerate((0,6,12)):
            o,h,l,c=raw[:,start:start+4].unbind(1)
            usable=mask[:,start:start+4].all(1)
            self.invalid_ohlc[f]+=((h<torch.maximum(o,c))|(l>torch.minimum(o,c))|(l>h)).logical_and(usable).sum()
        for i,k in enumerate((0,3)):
            self.crossed[i]+=((raw[:,6+k]>raw[:,12+k])&mask[:,6+k]&mask[:,12+k]).sum()
        self.relative.add(error,truth,mask,torch.zeros(len(raw),device=raw.device,dtype=torch.int64))

    def result(self):
        result={}
        sums,squares,counts,bad=(v.cpu() for v in (self.sums,self.squares,self.counts,self.bad))
        for c,name in enumerate(CHANNELS):
            for h in (None,*range(64)):
                path=f'1s/{name}'+('' if h is None else f'/horizon_{h+1:02d}')
                s,q,n,b=(v[c].sum() if h is None else v[c,h] for v in (sums,squares,counts,bad))
                ok=int(n)>0 and int(b)==0 and math.isfinite(float(s)) and math.isfinite(float(q))
                result[f'full_raw_mae/{path}']=float(s/n) if ok else None
                result[f'full_raw_rmse/{path}']=math.sqrt(float(q/n)) if ok else None
                result[f'full_raw_counts/{path}']=int(n);result[f'full_raw_overflows/{path}']=int(b)
        for i,name in enumerate(REFERENCE_NAMES):
            n,b=int(self.ref_counts[i]),int(self.ref_bad[i])
            result[f'reference_raw_mae/{name}']=float(self.ref_sums[i])/n if n and not b else None
            result[f'reference_raw_counts/{name}']=n;result[f'reference_raw_overflows/{name}']=b
        for i,c in enumerate(ACTIVITY):
            result[f'negative_activity_predictions/{CHANNELS[c]}']=int(self.negative_activity[i])
            result[f'negative_reference_predictions/{REFERENCE_NAMES[i+1]}']=int(self.negative_references[i])
        for i,name in enumerate(('trade','bid','ask')):result[f'invalid_predicted_ohlc/{name}']=int(self.invalid_ohlc[i])
        result['predicted_crossed_quote_open']=int(self.crossed[0]);result['predicted_crossed_quote_close']=int(self.crossed[1])
        result.update(self.relative.metrics('full_raw_mae',('1s',),CHANNELS))
        return result
