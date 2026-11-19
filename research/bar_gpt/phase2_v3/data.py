import numpy as np
import torch
from ..phase2_v2.train import source_batches
from .tokenizer import SessionTokens


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
