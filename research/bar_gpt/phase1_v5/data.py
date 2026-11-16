"""V4-exact transformations, then physical bar removal and small market-column dropout."""
from dataclasses import dataclass,fields
import torch
from ..phase2_v2.tokenizer import normalize
from ..v5.transforms_v1 import BarInputTransformV1
from ..v5.data_v3 import EligibleStream


@dataclass
class Batch:
    visible:torch.Tensor
    valid:torch.Tensor
    positions:torch.Tensor
    target:torch.Tensor
    target_valid:torch.Tensor
    target_positions:torch.Tensor
    reference_input:torch.Tensor
    reference_valid:torch.Tensor
    reference_target:torch.Tensor
    native_raw:torch.Tensor
    native_reference:torch.Tensor
    frames:torch.Tensor

    def pin_memory(self):return Batch(**{f.name:getattr(self,f.name).pin_memory() for f in fields(self)})
    def to(self,device,non_blocking=False):
        return Batch(**{f.name:getattr(self,f.name).to(device,non_blocking=non_blocking) for f in fields(self)})


def normalize_raw(raw,valid,clocks,frames):
    scaled,_,price,quantity=normalize(raw,valid,return_raw_references=True)
    time=BarInputTransformV1()(scaled,valid,clocks,frames)
    refs=torch.cat((price.reshape(-1,1).log(),quantity.log1p()),1)
    ref_valid=torch.cat((torch.ones(len(raw),1,dtype=torch.bool,device=raw.device),
                        valid[...,[4,5,10,11,16,17]].any(1)),1)
    return scaled,time.values[...,18:],time.valid[...,18:],refs,ref_valid,torch.cat((price.reshape(-1,1),quantity),1)


def prepare(raw,valid,clocks,frames,stats,generator,config,*,full=False,native=False):
    if raw.device.type!='cpu':raise ValueError('V5 workers prepare CPU batches')
    if clocks.shape!=raw.shape[:2] or frames.shape!=(len(raw),):raise ValueError('Bad source clocks/frame shape')
    scaled,time,time_valid,refs,ref_valid,actual_reference=normalize_raw(raw,valid,clocks,frames)
    b=len(raw)
    if full:visible_ids=hidden_ids=torch.arange(64).expand(b,-1).clone()
    else:
        order=torch.rand(b,64,generator=generator).argsort(1)
        visible_ids=order[:,:config.visible_bars].sort(1).values
        hidden_ids=order[:,config.visible_bars:].sort(1).values
    values=torch.cat((scaled,time),2);mask=torch.cat((valid,time_valid),2)
    gather=lambda a,ids:a.gather(1,ids[...,None].expand(-1,-1,a.shape[-1]))
    visible=gather(values,visible_ids);evidence=gather(mask,visible_ids)
    if not full and config.feature_drop:
        drop=torch.rand(b,1,18,generator=generator)<config.feature_drop
        evidence[...,:18]&=~drop
        visible=torch.where(evidence,visible,0)
    target=gather(scaled,hidden_ids);observed=gather(valid,hidden_ids)
    reference=torch.where(ref_valid,(refs-stats.reference_center)/stats.reference_scale,0).float()
    return Batch(visible,evidence,visible_ids,target,observed,hidden_ids,reference,ref_valid,
        reference,gather(raw,hidden_ids) if native else torch.empty(0),
        actual_reference if native else torch.empty(0),frames)


class Stream(EligibleStream):
    def __init__(self,*args,config,full=False,native=False,**kwargs):
        super().__init__(*args,**kwargs);self.config=config;self.v5_full=full;self.v5_native=native

    def prepare_rows(self,rows,rng):
        return prepare(torch.from_numpy(rows['values'].copy()),torch.from_numpy(rows['valid'].astype(bool)),
            torch.from_numpy(rows['end_us'].copy()),torch.from_numpy(rows['timeframe_us'].copy()),
            self.stats,torch.Generator().manual_seed(rng),self.config,full=self.v5_full,native=self.v5_native)
