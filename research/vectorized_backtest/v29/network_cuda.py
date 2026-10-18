"""Ragged recurrent graphs: memory and current-clock scratch stay separate."""
import torch
import triton as tr
import triton.language as tl
from .config import FAN_IN,STATES

@tr.jit
def workspace_kernel(V,VALID,SOURCES,WEIGHTS,BIAS,COUNTS,OFFSETS,THRESHOLD,SIZE_SOURCE,SIZE_WEIGHT,SIZE_BIAS,FEEDBACK,MEMORY,MEMORY_KNOWN,RATIOS,P,OPENED,PREVIOUS,CLOCK,WORK,KNOWN,OUT,U:tl.constexpr,C:tl.constexpr,STATES:tl.constexpr,FAN:tl.constexpr,BLOCK:tl.constexpr):
    """One clock of scratch, directly indexed; no N-way register gathers."""
    member=tl.program_id(0);action=tl.program_id(1);u=tl.program_id(2)*BLOCK+tl.arange(0,BLOCK);live=u<U
    net=member*5+action;ncount=tl.load(COUNTS+net);base=tl.load(OFFSETS+net)
    clock=tl.load(CLOCK).to(tl.float64)
    q=tl.load(P+(member*U+u)*9,live,0);held=q>1e-12
    avg=tl.load(P+(member*U+u)*9+1,live,1);stop=tl.load(P+(member*U+u)*9+2,live,0)
    adds=tl.load(P+(member*U+u)*9+5,live,0);last=tl.load(P+(member*U+u)*9+6,live,0)
    opened=tl.load(OPENED+member*U+u,live,0);price=tl.load(PREVIOUS+u,live,0)
    known_price=(price==price)&(tl.abs(price)<float('inf'))&(price>0)
    h=tl.full((BLOCK,),0,tl.float32);ok=tl.full((BLOCK,),False,tl.int1)
    for i in range(ncount):
        z=tl.full((BLOCK,),0,tl.float32)+tl.load(BIAS+base+i);ok=tl.full((BLOCK,),False,tl.int1)
        for e in tl.static_range(FAN):
            s=tl.load(SOURCES+(base+i)*FAN+e);w=tl.load(WEIGHTS+(base+i)*FAN+e)
            if w!=0:
                if s<0:
                    offset=(base-s-1)*U+u
                    x=tl.load(MEMORY+offset,live,0);valid=tl.load(MEMORY_KNOWN+offset,live,False)
                elif s<3*C:
                    x=tl.load(V+s*U+u,live,0);valid=tl.load(VALID+(s%C)*U+u,live,False)&(s<2*C)
                elif s<3*C+STATES:
                    state=s-3*C
                    x64=tl.where(state==0,held.to(tl.float64),0.)
                    x64=tl.where(state==1,tl.where(held&known_price,price/tl.maximum(avg,1e-12)-1.,0.),x64)
                    x64=tl.where(state==2,tl.where(held,(clock-opened)/3600.,0.),x64)
                    x64=tl.where(state==3,tl.where(held&known_price,q*price/1000.,0.),x64)
                    x64=tl.where(state==4,tl.where(held,(avg-stop)/tl.maximum(avg,1e-12),0.),x64)
                    x64=tl.where(state==5,adds/20.,x64)
                    x64=tl.where(state==6,tl.where(held,(clock-last)/300.,0.),x64)
                    if state>=7:x64=tl.load(FEEDBACK+(member*U+u)*(STATES-7)+state-7,live,0).to(tl.float64)
                    x=2./(1.+tl.exp(2.*-x64.to(tl.float32)))-1.;valid=tl.full((BLOCK,),False,tl.int1)
                else:
                    offset=(base+s-3*C-STATES)*U+u
                    x=tl.load(WORK+offset,live,0);valid=tl.load(KNOWN+offset,live,False)
                z=z+x*w;ok=ok|valid
        h=2./(1.+tl.exp(2.*-z))-1.
        offset=(base+i)*U+u
        tl.store(WORK+offset,h,live);tl.store(KNOWN+offset,ok,live)
    tl.store(OUT+(member*5+action)*U+u,ok&(h>tl.load(THRESHOLD+net)),live)

    if action==0 or action==2 or action==3:
        head=tl.load(SIZE_SOURCE+net);value=tl.load(WORK+(base+head)*U+u,live,0)
        z=value*tl.load(SIZE_WEIGHT+net)+tl.load(SIZE_BIAS+net)
        fraction=(2./(1.+tl.exp(-2.*z))-1.+1.)*.5
        channel=tl.where(action==0,0,tl.where(action==2,1,2))
        tl.store(RATIOS+(member*U+u)*3+channel,fraction,live)


def signals(plan,values,valid,position,opened,previous,index,out=None):
    b=position.shape[1];u=position.shape[2];values=values.T.contiguous();valid=valid.T.contiguous()
    actions=torch.empty((b,5,u),device=position.device,dtype=torch.bool)
    block=getattr(plan,'cuda_block',128)
    if block not in (32,64,128,256):raise ValueError('Unsupported neural CUDA tile')
    workspace_kernel[(b,5,tr.cdiv(u,block))](values,valid,plan.sources,plan.weights,plan.bias,plan.counts,plan.offsets,plan.threshold,
        plan.size_source,plan.size_weight,plan.size_bias,plan.feedback,plan.memory,plan.memory_known,plan.ratios,
        position,opened,previous,index,plan.workspace,plan.known,actions,u,len(plan.columns),len(STATES),FAN_IN,block,enable_fp_fusion=False)
    plan.memory.copy_(plan.workspace);plan.memory_known.copy_(plan.known)
    result=out if out is not None else torch.empty((1,b,u),device=position.device,dtype=torch.int16)
    pack[(tr.cdiv(b*u,256),)](actions,result,b*u,u,256)
    return result

@tr.jit
def pack(A,OUT,COUNT:tl.constexpr,U:tl.constexpr,BLOCK:tl.constexpr):
    x=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK);live=x<COUNT;base=(x//U)*5*U+x%U
    bits=tl.full((BLOCK,),0,tl.int32)
    for action in tl.static_range(5):bits=bits | (tl.load(A+base+action*U,live,False).to(tl.int32)<<action)
    tl.store(OUT+x,bits,live)
