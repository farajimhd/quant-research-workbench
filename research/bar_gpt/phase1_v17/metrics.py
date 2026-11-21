"""Vectorized consecutive-activity metrics; no pairs across window boundaries."""
import torch
from torch.nn import functional as F
from ..phase1_v5_raw.engine import FRAME_NAMES
from ..v5.shards.phase1_contract import FRAMES
from .objectives import ACTIVITY

NAMES = ('trade_volume', 'trade_count', 'bid_size', 'bid_count', 'ask_size', 'ask_count')


class ActivityMetrics:
    def __init__(self, device='cuda'):
        self.frames = torch.tensor(FRAMES, device=device)
        self.sums = torch.zeros(6, 6, 7, device=device, dtype=torch.float64)
        self.confusion = torch.zeros(6, 6, 3, 3, device=device, dtype=torch.float64)
        self.maxima = torch.zeros(6, 6, 2, device=device, dtype=torch.float64)

    @torch.no_grad()
    def add(self, logs, raw, valid, frames):
        mask = valid[..., ACTIVITY]
        y = raw[..., ACTIVITY].double()
        p_log = logs[..., ACTIVITY].double()
        p = p_log.expm1()
        target_log = torch.where(mask, y, 0).log1p()
        frame = (frames[:, None] == self.frames).long().argmax(1)
        groups = F.one_hot(frame, 6).T.double()
        stats = torch.stack((mask.double(), torch.where(mask, (p-y).abs(), 0),
                             torch.where(mask, (p_log-target_log).abs(), 0),
                             (mask & (y == 0)).double(), (mask & ~torch.isfinite(p)).double(),
                             (mask & (p_log-target_log > torch.log(torch.tensor(10., device=y.device)))).double(),
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
