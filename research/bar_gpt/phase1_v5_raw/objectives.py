"""Raw-space Huber residuals, with frozen training-only channel loss weights."""
from dataclasses import dataclass
import torch
from torch.nn import functional as F
from ..v5.pretraining_v1 import validate_market


@dataclass
class Stats:
    market_scale: torch.Tensor
    split_hash: str
    evidence: list

    def __post_init__(self):
        if (self.market_scale.shape != (18,) or not torch.isfinite(self.market_scale).all()
                or not (self.market_scale >= .01).all() or not self.split_hash or not self.evidence
                or any(p.get('split') != 'train' for p in self.evidence)):
            raise ValueError('Raw baseline requires sealed training-only loss scales')

    def to(self, device):
        return Stats(self.market_scale.to(device), self.split_hash, self.evidence)

    def state(self):
        return dict(market_scale=self.market_scale.cpu().tolist(), split_hash=self.split_hash,
                    evidence=self.evidence)

    @classmethod
    def load(cls, value):
        if set(value) != {'market_scale', 'split_hash', 'evidence'}:
            raise ValueError('Incompatible raw baseline calibration')
        return cls(torch.tensor(value['market_scale'], dtype=torch.float64),
                   value['split_hash'], value['evidence'])


def fit_stats(batches, split_hash):
    count=torch.zeros(18,dtype=torch.float64);mean=count.clone();m2=count.clone();evidence=[]
    for raw,mask,clocks,frames,proof in batches:
        if proof.get('split')!='train':raise ValueError('Calibration is training-only')
        validate_market(raw,mask)
        values=torch.where(mask,raw,0).double();n=mask.sum((0,1)).double()
        batch_mean=values.sum((0,1))/n.clamp_min(1)
        batch_m2=torch.where(mask,(values-batch_mean).square(),0).sum((0,1))
        total=count+n;delta=batch_mean-mean
        m2+=batch_m2+delta.square()*count*n/total.clamp_min(1)
        mean+=delta*n/total.clamp_min(1);count=total;evidence.append(proof)
    if (count==0).any():raise ValueError('Calibration lacks observed channels')
    return Stats((m2/count).sqrt().clamp_min(.01),split_hash,evidence)


def loss(predicted,batch,stats):
    # Targets/predictions remain in native units. Huber delta is ONE native unit;
    # inverse std weights balance the resulting channel losses, not model inputs.
    valid=batch.target_valid
    residual=F.smooth_l1_loss(predicted.float(),batch.target.float(),reduction='none')
    n=valid.sum((0,1));per_channel=torch.where(valid,residual,0).sum((0,1))/n.clamp_min(1)
    weighted=per_channel/stats.market_scale.float();active=n>0;terms=[]
    for part in (slice(0,4),slice(4,6)):
        v=weighted.reshape(3,6)[:,part].reshape(-1)
        ok=active.reshape(3,6)[:,part].reshape(-1)
        terms.append(torch.where(ok,v,0).sum()/ok.sum().clamp_min(1))
    terms=torch.stack(terms)
    return terms.mean(),terms
