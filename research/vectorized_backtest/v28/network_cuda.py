"""Runtime sparse topological scan in registers; no expanded neuron tape."""
import torch
import triton as tr
import triton.language as tl
from .config import MAX_NODES,FAN_IN

@tr.jit
def kernel(V,VALID,SOURCES,WEIGHTS,BIAS,COUNTS,THRESHOLD,P,OPENED,PREVIOUS,CLOCK,OUT,U:tl.constexpr,C:tl.constexpr,N:tl.constexpr,STRIDE:tl.constexpr,FAN:tl.constexpr,BLOCK:tl.constexpr):
    member=tl.program_id(0);action=tl.program_id(1);u=tl.program_id(2)*BLOCK+tl.arange(0,BLOCK);live=u<U
    net=member*5+action;ncount=tl.load(COUNTS+net)
    values=tl.full((N,BLOCK),0,tl.float32);known=tl.full((N,BLOCK),False,tl.int1);row=tl.arange(0,N)[:,None]
    clock=tl.load(CLOCK).to(tl.float64)
    q=tl.load(P+(member*U+u)*9,live,0);held=q>1e-12
    avg=tl.load(P+(member*U+u)*9+1,live,1);stop=tl.load(P+(member*U+u)*9+2,live,0)
    adds=tl.load(P+(member*U+u)*9+5,live,0);last=tl.load(P+(member*U+u)*9+6,live,0)
    opened=tl.load(OPENED+member*U+u,live,0);price=tl.load(PREVIOUS+u,live,0)
    for i in range(ncount):
        z=tl.full((BLOCK,),0,tl.float32)+tl.load(BIAS+net*STRIDE+i);ok=tl.full((BLOCK,),False,tl.int1)
        for e in tl.static_range(FAN):
            s=tl.load(SOURCES+(net*STRIDE+i)*FAN+e);w=tl.load(WEIGHTS+(net*STRIDE+i)*FAN+e)
            if w!=0:
                if s<3*C:
                    x=tl.load(V+s*U+u,live,0);valid=tl.load(VALID+(s%C)*U+u,live,False)&(s<2*C)
                elif s<3*C+7:
                    state=s-3*C
                    x64=tl.where(state==0,held.to(tl.float64),0.)
                    x64=tl.where(state==1,tl.where(held,price/tl.maximum(avg,1e-12)-1.,0.),x64)
                    x64=tl.where(state==2,tl.where(held,(clock-opened)/3600.,0.),x64)
                    x64=tl.where(state==3,q*price/1000.,x64)
                    x64=tl.where(state==4,tl.where(held,(avg-stop)/tl.maximum(avg,1e-12),0.),x64)
                    x64=tl.where(state==5,adds/20.,x64)
                    x64=tl.where(state==6,tl.where(held,(clock-last)/300.,0.),x64)
                    x=2./(1.+tl.exp(2.*-x64.to(tl.float32)))-1.;valid=tl.full((BLOCK,),False,tl.int1)
                else:
                    ix=tl.full((1,BLOCK),0,tl.int32)+(s-3*C-7)
                    x=tl.gather(values,ix,axis=0).reshape((BLOCK,));valid=tl.gather(known,ix,axis=0).reshape((BLOCK,))
                z=z+x*w;ok=ok|valid
        h=2./(1.+tl.exp(2.*-z))-1.
        values=tl.where(row==i,h[None,:],values);known=tl.where(row==i,ok[None,:],known)
    ix=tl.full((1,BLOCK),0,tl.int32)+ncount-1
    h=tl.gather(values,ix,0).reshape((BLOCK,));ok=tl.gather(known,ix,0).reshape((BLOCK,))
    tl.store(OUT+(member*5+action)*U+u,ok&(h>tl.load(THRESHOLD+net)),live)

@tr.jit
def workspace_kernel(V,VALID,SOURCES,WEIGHTS,BIAS,COUNTS,THRESHOLD,P,OPENED,PREVIOUS,CLOCK,WORK,KNOWN,OUT,U:tl.constexpr,C:tl.constexpr,N:tl.constexpr,STRIDE:tl.constexpr,FAN:tl.constexpr,BLOCK:tl.constexpr):
    """One clock of scratch, directly indexed; no N-way register gathers."""
    member=tl.program_id(0);action=tl.program_id(1);u=tl.program_id(2)*BLOCK+tl.arange(0,BLOCK);live=u<U
    net=member*5+action;ncount=tl.load(COUNTS+net)
    clock=tl.load(CLOCK).to(tl.float64)
    q=tl.load(P+(member*U+u)*9,live,0);held=q>1e-12
    avg=tl.load(P+(member*U+u)*9+1,live,1);stop=tl.load(P+(member*U+u)*9+2,live,0)
    adds=tl.load(P+(member*U+u)*9+5,live,0);last=tl.load(P+(member*U+u)*9+6,live,0)
    opened=tl.load(OPENED+member*U+u,live,0);price=tl.load(PREVIOUS+u,live,0)
    h=tl.full((BLOCK,),0,tl.float32);ok=tl.full((BLOCK,),False,tl.int1)
    for i in range(ncount):
        z=tl.full((BLOCK,),0,tl.float32)+tl.load(BIAS+net*STRIDE+i);ok=tl.full((BLOCK,),False,tl.int1)
        for e in tl.static_range(FAN):
            s=tl.load(SOURCES+(net*STRIDE+i)*FAN+e);w=tl.load(WEIGHTS+(net*STRIDE+i)*FAN+e)
            if w!=0:
                if s<3*C:
                    x=tl.load(V+s*U+u,live,0);valid=tl.load(VALID+(s%C)*U+u,live,False)&(s<2*C)
                elif s<3*C+7:
                    state=s-3*C
                    x64=tl.where(state==0,held.to(tl.float64),0.)
                    x64=tl.where(state==1,tl.where(held,price/tl.maximum(avg,1e-12)-1.,0.),x64)
                    x64=tl.where(state==2,tl.where(held,(clock-opened)/3600.,0.),x64)
                    x64=tl.where(state==3,q*price/1000.,x64)
                    x64=tl.where(state==4,tl.where(held,(avg-stop)/tl.maximum(avg,1e-12),0.),x64)
                    x64=tl.where(state==5,adds/20.,x64)
                    x64=tl.where(state==6,tl.where(held,(clock-last)/300.,0.),x64)
                    x=2./(1.+tl.exp(2.*-x64.to(tl.float32)))-1.;valid=tl.full((BLOCK,),False,tl.int1)
                else:
                    offset=(net*N+s-3*C-7)*U+u
                    x=tl.load(WORK+offset,live,0);valid=tl.load(KNOWN+offset,live,False)
                z=z+x*w;ok=ok|valid
        h=2./(1.+tl.exp(2.*-z))-1.
        offset=(net*N+i)*U+u
        tl.store(WORK+offset,h,live);tl.store(KNOWN+offset,ok,live)
    tl.store(OUT+(member*5+action)*U+u,ok&(h>tl.load(THRESHOLD+net)),live)


def signals(plan,values,valid,position,opened,previous,index,out=None):
    b=position.shape[1];u=position.shape[2]
    # A warp evaluates adjacent listings for one source. Transpose once per
    # clock so its feature reads are contiguous across all candidate networks.
    values=values.T.contiguous();valid=valid.T.contiguous()
    actions=torch.empty((b,5,u),device=position.device,dtype=torch.bool)
    if getattr(plan,'register_engine',False):
        kernel[(b,5,tr.cdiv(u,32))](values,valid,plan.sources,plan.weights,plan.bias,plan.counts,plan.threshold,
            position,opened,previous,index,actions,u,len(plan.columns),plan.kernel_nodes,MAX_NODES,FAN_IN,32,enable_fp_fusion=False)
    else:
        if getattr(plan,'workspace_shape',None)!=(b,u):
            plan.workspace=torch.empty((b,5,plan.kernel_nodes,u),device=position.device,dtype=torch.float32)
            plan.known=torch.empty_like(plan.workspace,dtype=torch.bool);plan.workspace_shape=(b,u)
        block=getattr(plan,'cuda_block',128)
        if block not in (32,64,128,256):raise ValueError('Unsupported neural CUDA tile')
        workspace_kernel[(b,5,tr.cdiv(u,block))](values,valid,plan.sources,plan.weights,plan.bias,plan.counts,plan.threshold,
            position,opened,previous,index,plan.workspace,plan.known,actions,u,len(plan.columns),plan.kernel_nodes,MAX_NODES,FAN_IN,block,enable_fp_fusion=False)
    result=out if out is not None else torch.empty((1,b,u),device=position.device,dtype=torch.int16)
    pack[(tr.cdiv(b*u,256),)](actions,result,b*u,u,256)
    return result

@tr.jit
def pack(A,OUT,COUNT:tl.constexpr,U:tl.constexpr,BLOCK:tl.constexpr):
    x=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK);live=x<COUNT;base=(x//U)*5*U+x%U
    bits=tl.full((BLOCK,),0,tl.int32)
    for action in tl.static_range(5):bits=bits | (tl.load(A+base+action*U,live,False).to(tl.int32)<<action)
    tl.store(OUT+x,bits,live)
