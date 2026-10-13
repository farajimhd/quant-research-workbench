"""Register-only bit reductions; no gate-sized temporary allocation."""
import torch
import triton
import triton.language as tl


@triton.jit
def kernel(GATES,OUT,cells:tl.constexpr,BLOCK:tl.constexpr):
    candidate=tl.program_id(0);index=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK)
    values=tl.load(GATES+candidate*cells+index,index<cells,0)
    for bit in tl.static_range(5):
        amount=tl.sum(((values & (1<<bit))!=0).to(tl.int32),axis=0)
        tl.atomic_add(OUT+candidate*5+bit,amount.to(tl.int64))


def reduce(gates):
    cells=gates.shape[1]*gates.shape[2]
    result=torch.zeros((gates.shape[0],5),device=gates.device,dtype=torch.int64)
    if cells:kernel[(gates.shape[0],triton.cdiv(cells,4096))](gates,result,cells,4096,num_warps=4)
    return result
