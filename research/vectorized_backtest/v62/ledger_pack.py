"""Stable GPU ledger compaction with a known output size; no nonzero sync."""
import numpy as np
import torch
import triton
import triton.language as tl


@triton.jit
def kernel(FIELDS,COUNTS,OFFSETS,OUT,STRIDES:tl.constexpr,N:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0);j=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK)
    count=tl.load(COUNTS+row);offset=tl.load(OFFSETS+row);live=j<count
    for field in tl.static_range(N):
        value=tl.load(FIELDS[field]+row*STRIDES[field][0]+j*STRIDES[field][1],live,0).to(tl.float64)
        tl.store(OUT+(offset+j)*N+field,value,live)


def pack(fields,device_counts,host_counts):
    counts=np.asarray(host_counts,dtype=np.int64).reshape(-1)
    total=int(counts.sum());width=int(counts.max(initial=0))
    out=torch.empty((total,len(fields)),dtype=torch.float64,device=device_counts.device)
    if not total:return out
    offsets=np.cumsum(counts)-counts
    offsets=torch.from_numpy(offsets).to(device_counts.device)
    columns=tuple(f.reshape(len(counts),-1) for f in fields)
    kernel[(len(counts),triton.cdiv(width,128))](columns,device_counts,offsets,out,
        tuple(f.stride() for f in columns),len(columns),128,num_warps=4)
    return out
