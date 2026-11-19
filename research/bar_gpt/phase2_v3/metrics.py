import torch
from types import SimpleNamespace
from .model import loss
from ..phase1_v11.transforms import PRICES,ACTIVITY
from ..phase1_v10.train import Accumulator as Native
from .horizon_metrics import HorizonPriceRelative


class Metrics:
    def __init__(self,device,price_factor=1000.):
        self.count=0;self.price_factor=price_factor
        self.sums=torch.zeros(1,device=device,dtype=torch.float64)
        self.moments=torch.zeros(5,64,18,device=device,dtype=torch.float64)
        self.errors=torch.zeros(64,18,device=device,dtype=torch.float64)
    @torch.no_grad()
    def add(self,predicted,target,persistence=None):
        n=len(predicted);self.count+=n;self.sums+=loss(predicted,target,self.price_factor).double()*n
        p=predicted.double();t=target['logs'].double();m=target['valid']
        self.errors+=torch.where(m,(p-t).abs(),0).sum(0)
        self.moments+=torch.stack([torch.where(m,v,0).sum(0) for v in (p,p.square(),t,t.square(),torch.ones_like(t))])
    def baseline(self):return self.moments[2]/self.moments[4].clamp_min(1)
    def result(self,baseline=None):
        if not self.count:raise ValueError('Empty forecast metrics')
        p,pp,t,tt,n=self.moments;den=n.clamp_min(1)
        return dict(origins=self.count,loss=float(self.sums[0]/self.count),
            price_log_mae=float((self.errors/den)[:,list(PRICES)].mean()),activity_log_mae=float((self.errors/den)[:,list(ACTIVITY)].mean()),
            prediction_variance=float((pp/den-(p/den).square())[n>0].mean()),target_variance=float((tt/den-(t/den).square())[n>0].mean()),
            log_mae_by_horizon_channel=(self.errors/den).cpu().tolist())
    def state(self):return dict(count=self.count,sums=self.sums.cpu().clone(),moments=self.moments.cpu().clone(),errors=self.errors.cpu().clone())
    def restore(self,state):
        self.count=state['count']
        for key in ('sums','moments','errors'):getattr(self,key).copy_(state[key])
    def reset(self):
        self.count=0
        for key in ('sums','moments','errors'):getattr(self,key).zero_()


class ValidationMetrics:
    def __init__(self,device):
        self.native=Native();self.device=device
        self.native.reducers=[HorizonPriceRelative(),HorizonPriceRelative()]
        self.adapter=SimpleNamespace(input_mean=torch.zeros(27,device=device),input_scale=torch.ones(27,device=device))
        self.controls={k:torch.zeros(2,18,device=device,dtype=torch.float64) for k in ('last','mean','median','training_mean')}
        self.model_errors={k:v.clone() for k,v in self.controls.items()}
    @torch.no_grad()
    def add(self,p,target,mean):
        t=target['logs'];v=target['valid'];n=len(p);zero=p.new_zeros((n,64,9));false=torch.zeros_like(zero,dtype=torch.bool)
        self.native.add(self.adapter,(p,zero,p.new_zeros((n,64,27))),torch.cat((t,zero),-1),torch.cat((v,false),-1),
            torch.ones(n,64,device=p.device,dtype=torch.bool),SimpleNamespace(raw=target['raw'],frames=torch.full((n,),1000000,device=p.device,dtype=torch.int64)))
        for key in self.controls:
            value=mean if key=='training_mean' else target[key][:,None]
            mask=v if key=='training_mean' else v&(target['last_valid'] if key=='last' else target['history_valid'])[:,None]
            count=mask.sum((0,1));error=torch.where(mask,(value-t).double().abs(),0).sum((0,1))
            self.controls[key]+=torch.stack((error,count))
            self.model_errors[key]+=torch.stack((torch.where(mask,(p-t).double().abs(),0).sum((0,1)),count))
    def result(self):
        result=self.native.finish()
        if result['nonfinite'] or result['negative'] or result['structure_violations']:raise FloatingPointError('Native forecast validity failure')
        for key,(s,n) in self.controls.items():
            model=self.model_errors[key][0];valid=n>0
            for family,ids in (('price',PRICES),('activity',ACTIVITY)):
                ix=list(ids);den=n[ix].clamp_min(1);control=(s[ix]/den)[valid[ix]].mean();ours=(model[ix]/den)[valid[ix]].mean()
                result[f'control/{key}/{family}_log_mae']=float(control)
                result[f'skill/{key}/{family}']=float(1-ours/control) if control>0 else None
        return result
