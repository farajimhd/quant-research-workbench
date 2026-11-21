import torch
from torch.nn import functional as F
from .model import gather
from ..phase0_v1.metrics import Metrics as BaseRawMetrics,scalars as raw_scalars
from ..phase0_v1.model import objective as raw_objective
from ..phase1_v11.transforms import inverse
from ..phase1_v5_raw.engine import FRAME_NAMES
from ..v5.shards.phase1_contract import FRAMES


class RawMetrics(BaseRawMetrics):
    """Same Phase0 reducer with frame constants allocated before CUDA capture."""
    def __init__(self,device='cuda'):
        super().__init__(device);self.frame_codes=torch.tensor(FRAMES,device=device)

    @torch.no_grad()
    def add(self,model,out,x,v,b):
        loss,parts=raw_objective(model,out,x,v);self.loss+=torch.cat((loss[None],parts)).double()*len(x);self.windows+=len(x)
        raw=b.grid.double();pred=inverse(out['logs']);valid=v[...,:18]
        self.nonfinite+=(~torch.isfinite(pred)).sum()
        frame=(b.frames[:,None]==self.frame_codes).long().argmax(1)
        group=F.one_hot(frame,6).T.double();err=pred-raw;nonzero=valid&(raw!=0)
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


class Metrics:
    def __init__(self,device='cuda',teacher_reference=None):
        self.windows=0;self.count=torch.zeros(2,6,device=device,dtype=torch.float64)
        # SmoothL1, squared error, absolute error, target/prediction moments, two controls.
        self.sums=torch.zeros(2,6,9,16,device=device,dtype=torch.float64)
        self.reference=teacher_reference
        self.frame_codes=torch.tensor(FRAMES,device=device)
        modes=('masked','all_visible') if teacher_reference else ('masked','all_visible','teacher_masked','teacher_all_visible')
        self.raw={k:RawMetrics(device) for k in modes}

    @torch.no_grad()
    def add(self,model,teacher,b,data,pos):
        latent,metadata,valid,values,source_valid,masked,visible=data
        target=(latent-model.encoder.mean)/model.encoder.scale
        known=torch.zeros_like(valid).scatter(1,pos,True)
        selected_valid=valid.gather(1,pos)
        mean=(gather(target,pos)*selected_valid[...,None]).sum(1)/selected_valid.sum(1).clamp_min(1)[:,None]
        frames=(b.frames[:,None]==self.frame_codes).long().argmax(1)
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
    out['loss']=out['masked/all/latent_loss']
    out['windows']=result['windows'];out['nonfinite']=result['nonfinite']
    return out


class MetricsReplay:
    """Capture accumulation only; all targets/predictions remain live graph inputs.

    Warmup and capture contributions are erased. A real-input eager/captured
    comparison checks EVERY accumulator before allowing steady-state replay.
    Python window counters advance once per actual batch, outside capture.
    """
    @torch.no_grad()
    def __init__(self,metrics,model,teacher,inference):
        if metrics.windows:raise ValueError('Metric graph requires empty accumulators')
        self.metrics=metrics;self.batch_size=len(inference.batch.grid)
        def add():metrics.add(model,teacher,inference.batch,inference.output,inference.pos)
        add()  # Initialize lazy confusion tables and nonfinite tensor counters.
        self.buffers=[metrics.count,metrics.sums]
        for raw in metrics.raw.values():
            self.buffers.extend((raw.raw,raw.loss,raw.nonfinite))
            self.buffers.extend(raw.moves.tables.values());self.buffers.extend(raw.moves.confusions.values())
        expected=[t.clone() for t in self.buffers];self.zero()
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):add()
        torch.cuda.current_stream().wait_stream(stream);self.zero();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.graph(self.graph,stream=stream):add()
        torch.cuda.current_stream().wait_stream(stream);self.zero();self.graph.replay()
        for actual,wanted in zip(self.buffers,expected):torch.testing.assert_close(actual,wanted,rtol=1e-6,atol=1e-8)
        self.zero()

    def zero(self):
        for t in self.buffers:t.zero_()
        self.metrics.windows=0
        for raw in self.metrics.raw.values():raw.windows=0

    def __call__(self):
        self.graph.replay();self.metrics.windows+=self.batch_size
        for raw in self.metrics.raw.values():raw.windows+=self.batch_size
