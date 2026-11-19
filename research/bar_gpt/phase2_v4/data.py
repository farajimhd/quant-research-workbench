import numpy as np
import torch
from ..phase2_v2.train import source_batches
from .tokenizer import SessionTokens


def load_units(roots=(),release=None):
    import hashlib,polars as pl
    from ..phase2_v2.data import load_units as original
    from ..phase2_v2.release import ReleasedUnits
    from ..v5.shards.contract import digest
    if not release:raise ValueError('V4 requires the certified full release')
    units,identity=original(roots,release)
    groups=[];hashes=[]
    for i,(root,binding,table) in enumerate(units.groups):
        # Shuffle only session traversal. Bars/windows retain their causal order;
        # validation keeps its original deterministic order and population.
        train=table.filter(table['unit_split']==0).sample(fraction=1.,shuffle=True,seed=17041+i)
        validation=table.filter(table['unit_split']==1)
        groups.append((root,binding,train.vstack(validation)))
        hashes.append(hashlib.sha256(groups[-1][2].select('path','receipt_hash').hash_rows(seed=17041).to_numpy().tobytes()).hexdigest())
    order=dict(version='phase2-v4-session-shuffle-v1',seed_base=17041,polars=pl.__version__,per_root_order_sha256=hashes,
               scope='all training sessions within each certified root; validation unchanged')
    return ReleasedUnits(groups),dict(identity,ordering=order,ordering_hash=digest(order))


def target_size(target):return len(target['logs'])


def batches(units,split,teacher,teacher_sha,args,*,cursor=(0,0,0),report=None,reconstruction=False):
    if reconstruction:raise ValueError('Raw target evidence already included')
    source=source_batches(units,split,teacher,teacher_sha,args,cursor=cursor,report=report,session_type=SessionTokens)
    chunks=[];count=0;last=None
    def packed():
        token,meta=(torch.cat([c[i] for c in chunks]) for i in range(2))
        target={k:torch.cat([c[2][k] for c in chunks]) for k in chunks[0][2]}
        return token,meta,target,last,np.concatenate([c[3] for c in chunks]),np.concatenate([c[4] for c in chunks])
    try:
        for token,meta,target,next_cursor,origins,liquid in source:
            if not args.pack_batches:
                yield token,meta,target,next_cursor,origins,liquid;continue
            begin=0;n=target_size(target)
            while begin<n:
                take=min(args.batch_size-count,n-begin);end=begin+take
                chunks.append((token[begin:end],meta[begin:end],{k:v[begin:end] for k,v in target.items()},origins[begin:end],np.full(take,liquid,dtype=np.int8)))
                last=(*next_cursor[:2],next_cursor[2]-n+end);count+=take;begin=end
                if count==args.batch_size:yield packed();chunks=[];count=0
        if count:yield packed()
    finally:source.close()
