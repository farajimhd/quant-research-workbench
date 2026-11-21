import torch
from torch.nn import functional as F
from .model import gather
from ..phase0_v1.metrics import Metrics as RawMetrics,scalars as raw_scalars
from ..phase1_v5_raw.engine import FRAME_NAMES
from ..v5.shards.phase1_contract import FRAMES


class Metrics:
    def __init__(self,device='cuda',teacher_reference=None):
        self.windows=0;self.count=torch.zeros(2,6,device=device,dtype=torch.float64)
        # SmoothL1, squared error, absolute error, target/prediction moments, two controls.
        self.sums=torch.zeros(2,6,9,16,device=device,dtype=torch.float64)
        self.reference=teacher_reference
        modes=('masked','all_visible') if teacher_reference else ('masked','all_visible','teacher_masked','teacher_all_visible')
        self.raw={k:RawMetrics(device) for k in modes}

    @torch.no_grad()
    def add(self,model,teacher,b,data,pos):
        latent,metadata,valid,values,source_valid,masked,visible=data
        target=(latent-model.encoder.mean)/model.encoder.scale
        known=torch.zeros_like(valid).scatter(1,pos,True)
        selected_valid=valid.gather(1,pos)
        mean=(gather(target,pos)*selected_valid[...,None]).sum(1)/selected_valid.sum(1).clamp_min(1)[:,None]
        frames=(b.frames[:,None]==torch.tensor(FRAMES,device=b.frames.device)).long().argmax(1)
        groups=F.one_hot(frames,6).T.double()
        teacher_output=teacher.decode(latent) if self.reference is None else None
        for i,(name,prediction,mask) in enumerate((('masked',masked,valid&~known),('all_visible',visible,valid))):
            self.count[i]+=groups@mask.double().sum(1)
            err=prediction-target
            stats=torch.stack((F.smooth_l1_loss(prediction,target,reduction='none'),err.square(),err.abs(),target,target.square(),prediction,prediction.square(),
                               F.smooth_l1_loss(torch.zeros_like(target),target,reduction='none'),F.smooth_l1_loss(mean[:,None].expand_as(target),target,reduction='none')),dim=2)
            totals=torch.where(mask[...,None,None],stats.double(),0).sum(1)
            self.sums[i]+=(groups@totals.flatten(1)).reshape(6,9,16)
            vm=source_valid if name=='all_visible' else source_valid&(~known)[...,None]
            self.raw[name].add(teacher,teacher.decode(model.denormalize(prediction)),values,vm,b)
            if teacher_output is not None:self.raw['teacher_'+name].add(teacher,teacher_output,values,vm,b)
        self.windows+=len(latent)

    def result(self):
        out=dict(windows=self.windows,counts=self.count.cpu().tolist(),sums=self.sums.cpu().tolist(),raw={k:v.result() for k,v in self.raw.items()})
        if self.reference:out['raw'].update(self.reference)
        out['nonfinite']=sum(r['nonfinite'] for r in out['raw'].values())
        return out


def scalars(result):
    import numpy as np
    sums=np.asarray(result['sums']);counts=np.asarray(result['counts']);out={}
    for i,name in enumerate(('masked','all_visible')):
        for f,frame in enumerate((*FRAME_NAMES,'all')):
            n=counts[i,f] if f<6 else counts[i].sum()
            if not n:continue
            s=(sums[i,f] if f<6 else sums[i].sum(0))/n
            prefix=f'{name}/{frame}'
            out.update({prefix+'/latent_loss':s[0].mean(),prefix+'/latent_mse':s[1].mean(),prefix+'/latent_mae':s[2].mean(),
                        prefix+'/target_variance':np.maximum(0,s[4]-s[3]**2).mean(),prefix+'/prediction_variance':np.maximum(0,s[6]-s[5]**2).mean(),
                        prefix+'/constant_loss':s[7].mean(),prefix+'/visible_mean_loss':s[8].mean()})
    for mode,raw in result['raw'].items():out.update({mode+'/'+k:v for k,v in raw_scalars(raw).items()})
    out['windows']=result['windows'];out['nonfinite']=result['nonfinite']
    return out
