import torch
from .model import objective
from ..phase1_v11.diagnostic_metrics import Diagnostics
from ..phase1_v11.transforms import inverse
from ..v5.shards.phase1_contract import FRAMES,CHANNELS
from ..phase1_v5_raw.engine import FRAME_NAMES


class Metrics:
    def __init__(self,device='cuda'):
        self.moves=Diagnostics(device);self.raw=torch.zeros(6,18,6,device=device,dtype=torch.float64)
        self.loss=torch.zeros(4,device=device,dtype=torch.float64);self.windows=0;self.nonfinite=0

    @torch.no_grad()
    def add(self,model,out,x,v,b):
        loss,parts=objective(model,out,x,v);self.loss+=torch.cat((loss[None],parts)).double()*len(x);self.windows+=len(x)
        raw=b.grid.double();pred=inverse(out['logs']);valid=v[...,:18]
        self.nonfinite+=(~torch.isfinite(pred)).sum()
        frame=(b.frames[:,None]==torch.tensor(FRAMES,device=raw.device)).long().argmax(1)
        group=torch.nn.functional.one_hot(frame,6).T.double();err=pred-raw;nonzero=valid&(raw!=0)
        relative=100*err/torch.where(nonzero,raw,1)
        for i,z in enumerate((valid.double(),torch.where(valid,err.abs(),0),torch.where(valid,err,0),
                              torch.where(nonzero,relative.abs(),0),torch.where(nonzero,relative,0),nonzero.double())):
            self.raw[:,:,i]+=group@z.sum(1)
        y=raw.reshape(-1,64,3,6);p=pred.reshape_as(y);m=valid.reshape_as(y)
        o,h,l,c=y[...,:4].unbind(-1);po,ph,pl,pc=p[...,:4].unbind(-1)
        def ratio(a,b):return 100*(a/torch.where(b>0,b,1)-1)
        self.moves.moves('candle',ratio(c,o),ratio(pc,po),m[...,0]&m[...,3],frame)
        self.moves.moves('consecutive_close',ratio(c[:,1:],c[:,:-1]),ratio(pc[:,1:],pc[:,:-1]),m[:,1:,:,3]&m[:,:-1,:,3],frame)
        self.moves.moves('range',ratio(h,l),ratio(ph,pl),m[...,1]&m[...,2],frame)

    def result(self):
        result=dict(windows=self.windows,bars=self.windows*64,loss=float(self.loss[0]/self.windows),nonfinite=int(self.nonfinite),
                    raw_fields=['count','absolute_error_sum','signed_error_sum','absolute_relative_pct_sum','signed_relative_pct_sum','nonzero_count'],
                    raw=self.raw.cpu().tolist(),moves=self.moves.finish())
        return result


def scalars(result):
    import numpy as np
    r=np.array(result['raw']);out={'loss':result['loss'],'bars':result['bars'],'nonfinite':result['nonfinite']}
    for f,name in enumerate(FRAME_NAMES):
        for j,channel in enumerate(CHANNELS):
            n,ae,se,re,sr,nz=r[f,j]
            if n:out.update({f'raw_mae/{name}/{channel}':ae/n,f'signed_raw/{name}/{channel}':se/n})
            if nz:out.update({f'mape_pct/{name}/{channel}':re/nz,f'signed_pct/{name}/{channel}':sr/nz})
    signs=np.array([0,-1,-1,-1,-1,-1,1,1,1,1,1]);neutral=np.array([0,0,-1,-1,-1,-1,0,1,1,1,1]);mag=np.array([0,1,2,3,4,5,1,2,3,4,5])
    for name,table in result['moves']['moves'].items():
        t=np.array(table);conf=np.array(result['moves']['confusion'][name])
        for f,frame in enumerate(FRAME_NAMES):
            for c,family in enumerate(('trade','bid','ask')):
                q=t[f,c];m=conf[f,c];n=q[:,0].sum();moving=q[1:,0].sum()
                if not n:continue
                prefix=f'{name}/{frame}/{family}'
                out.update({prefix+'/count':n,prefix+'/direction_pct':100*q[:,3].sum()/n,
                    prefix+'/neutral_005_direction_pct':100*m[neutral[:,None]==neutral[None,:]].sum()/n,
                    prefix+'/bin_accuracy_pct':100*np.trace(m)/n,prefix+'/magnitude_accuracy_pct':100*m[mag[:,None]==mag[None,:]].sum()/n,
                    prefix+'/mae_percentage_points':q[:,1].sum()/n,prefix+'/actual_mean_pct':q[:,4].sum()/n,prefix+'/predicted_mean_pct':q[:,5].sum()/n})
                if moving:out[prefix+'/moving_direction_pct']=100*q[1:,3].sum()/moving
    return out
