"""Frozen training-only loss scales. No refitting on validation, epochs or resumes."""
from dataclasses import dataclass
import torch
from torch.nn import functional as F
from ..v5.pretraining_v1 import PRICES,ACTIVITY


@dataclass
class Stats:
    market_scale:torch.Tensor
    reference_center:torch.Tensor
    reference_scale:torch.Tensor
    split_hash:str
    evidence:list

    def __post_init__(self):
        if (self.market_scale.shape!=(18,) or self.reference_center.shape!=(7,)
            or self.reference_scale.shape!=(7,) or not self.split_hash or not self.evidence
            or not all(torch.isfinite(x).all() for x in (self.market_scale,self.reference_center,self.reference_scale))
            or not (self.market_scale>=.01).all() or not (self.reference_scale>=.01).all()):
            raise ValueError('Invalid bound training-only V5 loss scales')

    def to(self,device):return Stats(self.market_scale.to(device),self.reference_center.to(device),
        self.reference_scale.to(device),self.split_hash,self.evidence)

    def state(self):return dict(market_scale=self.market_scale.cpu().tolist(),reference_center=self.reference_center.cpu().tolist(),
        reference_scale=self.reference_scale.cpu().tolist(),split_hash=self.split_hash,evidence=self.evidence)

    @classmethod
    def load(cls,value):
        return cls(*(torch.tensor(value[k],dtype=torch.float64) for k in
            ('market_scale','reference_center','reference_scale')),value['split_hash'],value['evidence'])


def fit_stats(batches,split_hash):
    from .data import normalize_raw
    sums=torch.zeros(18,dtype=torch.float64);squares=sums.clone();counts=sums.clone()
    rs=torch.zeros(7,dtype=torch.float64);rq=rs.clone();rn=rs.clone();evidence=[]
    for raw,mask,clocks,frames,proof in batches:
        if proof.get('split')!='train':raise ValueError('V5 loss calibration is training-only')
        scaled,_,_,refs,rm=normalize_raw(raw,mask,clocks,frames)
        v=scaled.double();sums+=(v*mask).sum((0,1));squares+=(v.square()*mask).sum((0,1));counts+=mask.sum((0,1))
        rs+=(refs*rm).sum(0);rq+=(refs.square()*rm).sum(0);rn+=rm.sum(0);evidence.append(proof)
    if (counts==0).any() or (rn==0).any():raise ValueError('Calibration lacks observed channels/references')
    market=(squares/counts-(sums/counts).square()).clamp_min(0).sqrt().clamp_min(.01)
    ref=(rq/rn-(rs/rn).square()).clamp_min(0).sqrt().clamp_min(.01)
    return Stats(market,rs/rn,ref,split_hash,evidence)


def family_loss(predicted,target,mask):
    # Equal CHANNEL means: sparse quote availability cannot alter feature weights.
    n=mask.sum((0,1));error=F.smooth_l1_loss(predicted,target,reduction='none')*mask
    per_channel=error.sum((0,1))/n.clamp_min(1)
    return per_channel,n>0


def loss(bars,references,batch,stats):
    e,active=family_loss(bars.float()/stats.market_scale.float(),
                          batch.target.float()/stats.market_scale.float(),batch.target_valid)
    terms=[]
    for ids in (PRICES,ACTIVITY):
        ok=active[list(ids)];terms.append((e[list(ids)]*ok).sum()/ok.sum().clamp_min(1))
    rm=batch.reference_valid;n=rm.sum(0)
    re=(F.smooth_l1_loss(references.float(),batch.reference_target.float(),reduction='none')*rm).sum(0)/n.clamp_min(1)
    terms.append((re*(n>0)).sum()/(n>0).sum().clamp_min(1))
    return torch.stack(terms).mean(),torch.stack(terms)


def reconstruct(bars,references,stats):
    logs=references.double()*stats.reference_scale+stats.reference_center
    raw=bars.double().clone()
    raw[...,PRICES]=torch.exp(logs[:,:1,None].transpose(1,2)+raw[...,PRICES]/100)
    raw[...,ACTIVITY]=torch.expm1(logs[:,None,1:]+raw[...,ACTIVITY])
    refs=torch.cat((logs[:,:1].exp(),logs[:,1:].expm1()),1)
    return raw,refs
