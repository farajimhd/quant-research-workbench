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

    def map(self,fn):
        cache={};result={}
        for f in fields(self):
            value=getattr(self,f.name)
            if id(value) not in cache:cache[id(value)]=fn(value)
            result[f.name]=cache[id(value)]
        return Batch(**result)
    def pin_memory(self):return self.map(lambda x:x.pin_memory())
    def to(self,device,non_blocking=False):
        return self.map(lambda x:x.to(device,non_blocking=non_blocking))


def prepare(rows,native=False,compact=False,raw_gpu=False):
    if raw_gpu:return prepare_raw(rows,native)
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
    def __init__(self,*args,native=False,compact=False,raw_gpu=False,**kwargs):
        super().__init__(*args,**kwargs);self.native=native;self.compact=compact;self.raw_gpu=raw_gpu
        if not self.strict_market:raise ValueError('Only certified complete-market authority is permitted')
    def prepare_rows(self,rows,rng):return prepare(rows,self.native,self.compact,self.raw_gpu)


def loader(manifest,args,split,epoch=0):
    telemetry=args.output/'workers'/split;telemetry.mkdir(parents=True,exist_ok=True)
    batch_size=(getattr(args,'validation_batch_size',0) or args.batch_size) if split=='validation' else args.batch_size
    stream=Stream(manifest,split,None,batch_size,seed=args.seed,epoch=epoch,
                  native=split=='validation',compact=args.preparation=='gpu',raw_gpu=args.preparation=='raw_gpu',telemetry=str(telemetry))
    stream.shard_prefetch=getattr(args,'shard_prefetch',16);stream.shard_prefetch_bytes=getattr(args,'shard_prefetch_mib',64)*2**20;stream.prepare_threads=1
    return make_loader(stream,args.workers,args.prefetch,ordered=True)


def prepare_raw(rows,native=False):
    """Validate every source invariant before transferring compact raw tensors."""
    import numpy as np
    from ..v5.data_v3 import validate_complete_market
    from ..v5.shards.phase1_contract import FRAMES
    validate_complete_market(rows)
    raw=rows['values'];valid=rows['valid'].astype(bool)
    if not np.isin(rows['timeframe_us'],FRAMES).all():raise ValueError('Unsupported timeframe')
    for base in (0,6,12):
        p=raw[...,base:base+4];v=valid[...,base:base+4];complete=v.all(-1)
        if ((p<=0)&v).any() or ((raw[...,base+4:base+6]<0)&valid[...,base+4:base+6]).any():raise ValueError('Invalid source price/activity')
        o,h,l,c=np.moveaxis(p,-1,0)
        if (((h<np.maximum(o,c))|(l>np.minimum(o,c)))&complete).any():raise ValueError('Valid source OHLC ordering violation')
    complete=valid[...,6:10].all(-1)&valid[...,12:16].all(-1)
    if ((raw[...,6:10]>raw[...,12:16])&complete[...,None]).any():raise ValueError('Valid crossed source quote')
    values=torch.from_numpy(raw.copy())
    return Batch(values,torch.from_numpy(rows['end_us'].copy()),values,torch.from_numpy(valid),
                 values if native else torch.empty(0),torch.from_numpy(rows['timeframe_us'].copy()))


def gpu_inputs(b):
    """FP64 transforms and unchanged UTC arithmetic, then original FP32 features."""
    safe=torch.where(b.valid,b.grid,1).double();logs=safe.clone()
    for base in (0,6,12):
        logs[...,base:base+4]=safe[...,base:base+4].log()
        logs[...,base+4:base+6]=safe[...,base+4:base+6].log1p()
    logs=torch.where(b.valid,logs,0).float()
    present=b.slots>0;gaps=torch.diff(b.slots,dim=1)
    t=BarInputTransformV1.assemble(logs,b.valid,b.slots,b.frames,present,gaps)
    return t.values,t.valid,logs
