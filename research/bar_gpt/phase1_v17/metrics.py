"""Vectorized consecutive-activity metrics; no pairs across window boundaries."""
import torch
import math
from torch.nn import functional as F
from ..phase1_v5_raw.engine import FRAME_NAMES
from ..v5.shards.phase1_contract import FRAMES
from .objectives import ACTIVITY

NAMES = ('trade_volume', 'trade_count', 'bid_size', 'bid_count', 'ask_size', 'ask_count')


class ActivityMetrics:
    def __init__(self, device='cuda'):
        self.frames = torch.tensor(FRAMES, device=device)
        self.indices = torch.tensor(ACTIVITY,device=device)
        self.sums = torch.zeros(6, 6, 7, device=device, dtype=torch.float64)
        self.confusion = torch.zeros(6, 6, 3, 3, device=device, dtype=torch.float64)
        self.maxima = torch.zeros(6, 6, 2, device=device, dtype=torch.float64)

    @torch.no_grad()
    def add(self, logs, raw, valid, frames):
        mask = valid.index_select(-1,self.indices)
        y = raw.index_select(-1,self.indices).double()
        p_log = logs.index_select(-1,self.indices).double()
        p = p_log.expm1()
        target_log = torch.where(mask, y, 0).log1p()
        frame = (frames[:, None] == self.frames).long().argmax(1)
        groups = F.one_hot(frame, 6).T.double()
        stats = torch.stack((mask.double(), torch.where(mask, (p-y).abs(), 0),
                             torch.where(mask, (p_log-target_log).abs(), 0),
                             (mask & (y == 0)).double(), (mask & ~torch.isfinite(p)).double(),
                             (mask & (p_log-target_log > math.log(10.))).double(),
                             torch.where(mask, p-y, 0)), -1)
        self.sums += (groups @ stats.sum(1).flatten(1)).reshape(6, 6, 7)
        # Reconstruct both members, never replace the predicted previous bar with truth.
        pair = mask[:, 1:] & mask[:, :-1]
        true_class = torch.sign(y[:, 1:]-y[:, :-1]).long()+1
        pred_class = torch.sign(p_log[:, 1:]-p_log[:, :-1]).long()+1
        confusion = F.one_hot(true_class*3+pred_class, 9).double()*pair[..., None]
        self.confusion += (groups @ confusion.sum(1).flatten(1)).reshape(6, 6, 3, 3)
        for f in range(6):
            selected = mask & (frame == f)[:, None, None]
            values = torch.stack((torch.where(selected, (p-y).abs(), 0).amax((0, 1)),
                                  torch.where(selected, (p_log-target_log).abs(), 0).amax((0, 1))), -1)
            self.maxima[f] = torch.maximum(self.maxima[f], values)

    def result(self):
        return dict(sums=self.sums.cpu().tolist(), confusion=self.confusion.cpu().tolist(), maxima=self.maxima.cpu().tolist())


def scalars(result):
    import numpy as np
    sums = np.asarray(result['sums']); confusion = np.asarray(result['confusion']); maxima = np.asarray(result['maxima'])
    out = {}
    for f, frame in enumerate(FRAME_NAMES):
        for j, channel in enumerate(NAMES):
            s = sums[f, j]; c = confusion[f, j]; prefix = f'{frame}/{channel}/'
            n = s[0]; pairs = c.sum(); moving = c[0].sum()+c[2].sum()
            values = dict(count=n, pairs=pairs, raw_mae=s[1]/max(n, 1), log_mae=s[2]/max(n, 1), zero_targets=s[3],
                          nonfinite=s[4], overshoot_10x_count=s[5], raw_bias=s[6]/max(n, 1),
                          max_raw_error=maxima[f,j,0], max_log_error=maxima[f,j,1],
                          direction_pct=100*c.trace()/max(pairs, 1), moving_direction_pct=100*(c[0,0]+c[2,2])/max(moving, 1),
                          unchanged_baseline_pct=100*c[1].sum()/max(pairs, 1), moving_pairs=moving)
            recalls=[]
            for k, name in enumerate(('down','flat','up')):
                count=c[k].sum();values[name+'_count']=count;values[name+'_predicted_count']=c[:,k].sum()
                if count:
                    values[name+'_recall_pct']=100*c[k,k]/count;recalls.append(values[name+'_recall_pct'])
            values['balanced_direction_pct']=float(np.mean(recalls)) if recalls else 0.
            out.update({prefix+k:float(v) for k,v in values.items()})
    return out


from ..phase1_v12.metrics import Metrics as NativeMetrics, MetricsReplay as NativeReplay, scalars as native_scalars


class FullMetrics(NativeMetrics):
    """Eager metric adapter for the existing full-data inference pipeline."""
    def __init__(self, device='cuda', teacher_reference=None):
        super().__init__(device, teacher_reference)
        self.activity={name:ActivityMetrics(device) for name in ('masked','all_visible','teacher')}

    @torch.no_grad()
    def add(self, model, teacher, batch, data, pos):
        super().add(model,teacher,batch,data,pos)
        z,meta,valid,x,v,masked,visible=data
        hidden=~torch.zeros_like(valid).scatter(1,pos,True)
        for name,pred,mask in (('masked',model.denormalize(masked),v&hidden[...,None]),('all_visible',model.denormalize(visible),v),('teacher',z,v)):
            self.activity[name].add(teacher.decode(pred)['logs'],batch.grid,mask,batch.frames)

    def result(self):
        result=super().result()
        result['activity']={k:v.result() for k,v in self.activity.items()}
        return result


def full_scalars(result):
    out=native_scalars(result)
    for name,r in result['activity'].items():out.update({'activity/'+name+'/'+k:v for k,v in scalars(r).items()})
    return out


class FullMetricsReplay(NativeReplay):
    """Capture all native AND activity buffers, with eager/captured parity gates."""
    @torch.no_grad()
    def __init__(self, metrics, model, teacher, inference):
        if metrics.windows:raise ValueError('Empty accumulators required')
        self.metrics=metrics;self.batch_size=len(inference.batch.grid)
        def add():metrics.add(model,teacher,inference.batch,inference.output,inference.pos)
        add()
        self.buffers=[metrics.count,metrics.sums]
        for raw in metrics.raw.values():
            self.buffers.extend((raw.raw,raw.loss,raw.nonfinite))
            self.buffers.extend(raw.moves.tables.values());self.buffers.extend(raw.moves.confusions.values())
        for activity in metrics.activity.values():self.buffers.extend((activity.sums,activity.confusion,activity.maxima))
        expected=[t.clone() for t in self.buffers];self.zero()
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):add()
        torch.cuda.current_stream().wait_stream(stream);self.zero();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.graph(self.graph,stream=stream):add()
        torch.cuda.current_stream().wait_stream(stream);self.zero();self.graph.replay()
        for actual,wanted in zip(self.buffers,expected):torch.testing.assert_close(actual,wanted,rtol=1e-6,atol=1e-8)
        self.zero()
