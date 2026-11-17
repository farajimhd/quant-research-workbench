"""Certified full-population streaming; no altered sampling or market semantics."""
from dataclasses import dataclass,fields
from types import SimpleNamespace
import torch
from .model import Autoencoder,Transform
from ..v5.data_v3 import EligibleStream,make_loader
from ..v5.transforms_v1 import BarInputTransformV1


@dataclass
class Batch:
    grid:torch.Tensor
    slots:torch.Tensor
    target:torch.Tensor
    valid:torch.Tensor
    raw:torch.Tensor
    frames:torch.Tensor

    def pin_memory(self):return Batch(**{f.name:getattr(self,f.name).pin_memory() for f in fields(self)})
    def to(self,device,non_blocking=False):
        return Batch(**{f.name:getattr(self,f.name).to(device,non_blocking=non_blocking) for f in fields(self)})


def prepare(rows,native=False,compact=False):
    parts=[]
    helper=SimpleNamespace(config=SimpleNamespace(variant='geometry_joint'),joint=True,input_width=98)
    for start in range(0,len(rows),256):
        r=rows[start:start+256]
        raw=torch.from_numpy(r['values'].copy());valid=torch.from_numpy(r['valid'].astype(bool))
        logs=Transform.logs(raw,valid).float()
        t=BarInputTransformV1()(raw,valid,torch.from_numpy(r['end_us'].copy()),torch.from_numpy(r['timeframe_us'].copy()))
        t.values[...,:18]=logs
        if compact:
            # Validate in worker before permitting unchecked capture-safe arithmetic.
            from .model import geometry,quote_geometry
            geometry(logs,valid);quote_geometry(logs,valid)
            grid,slots=t.values,t.valid
        else:grid,slots=Autoencoder.prepare(helper,t.values,t.valid,torch.arange(64).expand(len(r),-1))
        parts.append(Batch(grid,slots,logs,valid,raw if native else torch.empty(0),torch.from_numpy(r['timeframe_us'].copy())))
    return Batch(**{f.name:torch.cat([getattr(b,f.name) for b in parts]) for f in fields(Batch)})


class Stream(EligibleStream):
    def __init__(self,*args,native=False,compact=False,**kwargs):
        super().__init__(*args,**kwargs);self.native=native;self.compact=compact
        if not self.strict_market:raise ValueError('Only certified complete-market authority is permitted')
    def prepare_rows(self,rows,rng):return prepare(rows,self.native,self.compact)


def loader(manifest,args,split,epoch=0):
    telemetry=args.output/'workers'/split;telemetry.mkdir(parents=True,exist_ok=True)
    stream=Stream(manifest,split,None,args.batch_size,seed=args.seed,epoch=epoch,
                  native=split=='validation',compact=args.preparation=='gpu',telemetry=str(telemetry))
    stream.shard_prefetch=16;stream.shard_prefetch_bytes=64*2**20;stream.prepare_threads=1
    return make_loader(stream,args.workers,args.prefetch,ordered=True)
