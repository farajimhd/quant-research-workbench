"""Raw market units, unchanged time channels, physical bar removal and feature dropout."""
from dataclasses import dataclass,fields
import torch
from ..v5.pretraining_v1 import validate_market
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
    native_raw:torch.Tensor
    frames:torch.Tensor

    def pin_memory(self):return Batch(**{f.name:getattr(self,f.name).pin_memory() for f in fields(self)})
    def to(self,device,non_blocking=False):
        return Batch(**{f.name:getattr(self,f.name).to(device,non_blocking=non_blocking) for f in fields(self)})


def prepare(raw,valid,clocks,frames,stats,generator,config,*,full=False,native=False,chunk_size=256):
    if raw.device.type!='cpu':raise ValueError('V5 workers prepare CPU batches')
    if clocks.shape!=raw.shape[:2] or frames.shape!=(len(raw),):raise ValueError('Bad source clocks/frame shape')
    b=len(raw)
    if chunk_size<1:raise ValueError('Preparation chunk size must be positive')
    if full:visible_ids=hidden_ids=torch.arange(64).expand(b,-1).clone()
    else:
        order=torch.rand(b,64,generator=generator).argsort(1)
        visible_ids=order[:,:config.visible_bars].sort(1).values
        hidden_ids=order[:,config.visible_bars:].sort(1).values
    # Draw augmentation for the ORIGINAL batch before chunking: lanes, random
    # draws, row order and optimizer batch boundaries remain exactly unchanged.
    drop=torch.rand(b,1,18,generator=generator)<config.feature_drop if not full and config.feature_drop else None
    chunks=[]
    for start in range(0,b,chunk_size):
        end=min(b,start+chunk_size);v=valid[start:end];vi=visible_ids[start:end];hi=hidden_ids[start:end]
        raw_part=raw[start:end];validate_market(raw_part,v)
        transformed=BarInputTransformV1()(raw_part,v,clocks[start:end],frames[start:end])
        values,mask=transformed.values,transformed.valid
        clean=torch.where(v,raw_part,0)
        gather=lambda a,ids:a.gather(1,ids[...,None].expand(-1,-1,a.shape[-1]))
        visible=gather(values,vi);evidence=gather(mask,vi)
        if drop is not None:
            evidence[...,:18]&=~drop[start:end]
            visible=torch.where(evidence,visible,0)
        chunks.append(Batch(visible,evidence,vi,gather(clean,hi),gather(v,hi),hi,
            gather(raw_part,hi) if native else torch.empty(0),frames[start:end]))
    if len(chunks)==1:return chunks[0]
    return Batch(**{f.name:torch.cat([getattr(c,f.name) for c in chunks]) for f in fields(Batch)})


class Stream(EligibleStream):
    def __init__(self,*args,config,full=False,native=False,**kwargs):
        super().__init__(*args,**kwargs);self.config=config;self.v5_full=full;self.v5_native=native

    def prepare_rows(self,rows,rng):
        return prepare(torch.from_numpy(rows['values'].copy()),torch.from_numpy(rows['valid'].astype(bool)),
            torch.from_numpy(rows['end_us'].copy()),torch.from_numpy(rows['timeframe_us'].copy()),
            self.stats,torch.Generator().manual_seed(rng),self.config,full=self.v5_full,native=self.v5_native)
