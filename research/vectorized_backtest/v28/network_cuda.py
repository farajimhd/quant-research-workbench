"""Runtime sparse topological scan in registers; no expanded neuron tape."""
import torch
import triton as tr
import triton.language as tl
from .config import MAX_NODES

@tr.jit
def kernel(V,VALID,SOURCES,WEIGHTS,BIAS,COUNTS,THRESHOLD,P,OPENED,PREVIOUS,CLOCK,OUT,U:tl.constexpr,C:tl.constexpr,N:tl.constexpr,BLOCK:tl.constexpr):
    member=tl.program_id(0);action=tl.program_id(1);u=tl.program_id(2)*BLOCK+tl.arange(0,BLOCK);live=u<U
    net=member*5+action;ncount=tl.load(COUNTS+net)
    values=tl.full((N,BLOCK),0,tl.float32);known=tl.full((N,BLOCK),True,tl.int1);row=tl.arange(0,N)[:,None]
    clock=tl.load(CLOCK).to(tl.float64)
    q=tl.load(P+(member*U+u)*9,live,0);held=q>1e-12
    avg=tl.load(P+(member*U+u)*9+1,live,1);stop=tl.load(P+(member*U+u)*9+2,live,0)
    adds=tl.load(P+(member*U+u)*9+5,live,0);last=tl.load(P+(member*U+u)*9+6,live,0)
    opened=tl.load(OPENED+member*U+u,live,0);price=tl.load(PREVIOUS+u,live,0)
    for i in range(ncount):
        z=tl.full((BLOCK,),0,tl.float32)+tl.load(BIAS+net*N+i);ok=live
        for e in tl.static_range(4):
            s=tl.load(SOURCES+(net*N+i)*4+e);w=tl.load(WEIGHTS+(net*N+i)*4+e)
            if w!=0:
                if s<C:
                    x=tl.load(V+u*C+s,live,0);valid=tl.load(VALID+u*C+s,live,False)
                elif s<C+7:
                    state=s-C
                    x64=tl.where(state==0,held.to(tl.float64),0.)
                    x64=tl.where(state==1,tl.where(held,price/tl.maximum(avg,1e-12)-1.,0.),x64)
                    x64=tl.where(state==2,tl.where(held,(clock-opened)/3600.,0.),x64)
                    x64=tl.where(state==3,q*price/1000.,x64)
                    x64=tl.where(state==4,tl.where(held,(avg-stop)/tl.maximum(avg,1e-12),0.),x64)
                    x64=tl.where(state==5,adds/20.,x64)
                    x64=tl.where(state==6,tl.where(held,(clock-last)/300.,0.),x64)
                    x=2./(1.+tl.exp(2.*-x64.to(tl.float32)))-1.;valid=live
                else:
                    ix=tl.full((1,BLOCK),0,tl.int32)+(s-C-7)
                    x=tl.gather(values,ix,axis=0).reshape((BLOCK,));valid=tl.gather(known,ix,axis=0).reshape((BLOCK,))
                z=z+x*w;ok=ok&valid
        h=2./(1.+tl.exp(2.*-z))-1.
        values=tl.where(row==i,h[None,:],values);known=tl.where(row==i,ok[None,:],known)
    ix=tl.full((1,BLOCK),0,tl.int32)+ncount-1
    h=tl.gather(values,ix,0).reshape((BLOCK,));ok=tl.gather(known,ix,0).reshape((BLOCK,))
    tl.store(OUT+(member*5+action)*U+u,ok&(h>tl.load(THRESHOLD+net)),live)

def signals(plan,values,valid,position,opened,previous,index,out=None):
    b=position.shape[1];u=position.shape[2]
    actions=torch.empty((b,5,u),device=position.device,dtype=torch.bool)
    kernel[(b,5,tr.cdiv(u,128))](values,valid,plan.sources,plan.weights,plan.bias,plan.counts,plan.threshold,
        position,opened,previous,index,actions,u,len(plan.columns),MAX_NODES,128,enable_fp_fusion=False)
    result=out if out is not None else torch.empty((1,b,u),device=position.device,dtype=torch.int16)
    pack[(tr.cdiv(b*u,256),)](actions,result,b*u,u,256)
    return result

@tr.jit
def pack(A,OUT,COUNT:tl.constexpr,U:tl.constexpr,BLOCK:tl.constexpr):
    x=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK);live=x<COUNT;base=(x//U)*5*U+x%U
    bits=tl.full((BLOCK,),0,tl.int32)
    for action in tl.static_range(5):bits=bits | (tl.load(A+base+action*U,live,False).to(tl.int32)<<action)
    tl.store(OUT+x,bits,live)
