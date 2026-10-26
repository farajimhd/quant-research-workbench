"""Parallel sparse recurrent cells, then six readouts over their shared state."""
import torch
import triton as tr
import triton.language as tl
from research.vectorized_backtest.v42.config import STATES
from .genome import HIDDEN,FAN_IN

@tr.jit
def state_kernel(P,OPENED,PREVIOUS,CLOCK,FEEDBACK,OUT,U:tl.constexpr,S:tl.constexpr,T:tl.constexpr):
    b=tl.program_id(0);u=tl.program_id(1)*T+tl.arange(0,T);live=u<U
    clock=tl.load(CLOCK).to(tl.float64);q=tl.load(P+(b*U+u)*9,live,0);held=q>1e-12
    avg=tl.load(P+(b*U+u)*9+1,live,1);stop=tl.load(P+(b*U+u)*9+2,live,0)
    adds=tl.load(P+(b*U+u)*9+5,live,0);last=tl.load(P+(b*U+u)*9+6,live,0)
    opened=tl.load(OPENED+b*U+u,live,0);price=tl.load(PREVIOUS+u,live,0)
    kp=(price==price)&(tl.abs(price)<float('inf'))&(price>0)
    for s in tl.static_range(S):
        if s==0:x=held.to(tl.float64)
        elif s==1:x=tl.where(held&kp,price/tl.maximum(avg,1e-12)-1.,0.)
        elif s==2:x=tl.where(held,(clock-opened)/3600.,0.)
        elif s==3:x=tl.where(held&kp,q*price/1000.,0.)
        elif s==4:x=tl.where(held,(avg-stop)/tl.maximum(avg,1e-12),0.)
        elif s==5:x=adds/20.
        elif s==6:x=tl.where(held,(clock-last)/300.,0.)
        else:x=tl.load(FEEDBACK+(b*U+u)*(S-7)+s-7,live,0).to(tl.float64)
        tl.store(OUT+(b*S+s)*U+u,2./(1.+tl.exp(-2.*x.to(tl.float32)))-1.,live)

@tr.jit
def core(V,K,SRC,W,BIAS,STATE,M,MK,A,AK,PHASE,COUNTS,OFFSETS,TILES,U:tl.constexpr,C:tl.constexpr,S:tl.constexpr,T:tl.constexpr,H:tl.constexpr):
    tile=tl.program_id(0);b=tl.load(TILES+tile*2);start=tl.load(TILES+tile*2+1);base=tl.load(OFFSETS+b);count=tl.load(COUNTS+b)
    h=(start+tl.arange(0,H))[:,None];u=(tl.program_id(1)*T+tl.arange(0,T))[None,:];live=(u<U)&(h<count)
    phase=tl.load(PHASE)&1;old=tl.where(phase!=0,A,M);oldk=tl.where(phase!=0,AK,MK)
    new=tl.where(phase!=0,M,A);newk=tl.where(phase!=0,MK,AK)
    z=tl.load(BIAS+base+h,h<count,0.)+tl.full((H,T),0,tl.float32);known=tl.full((H,T),False,tl.int1)
    for e in tl.static_range(8):
        s=tl.load(SRC+(base+h)*8+e,h<count,0);w=tl.load(W+(base+h)*8+e,h<count,0.)
        recur=s<0;market=(s>=0)&(s<3*C);state=s>=3*C
        x=tl.load(old+(base-s-1)*U+u,live&recur,0.)
        k=tl.load(oldk+(base-s-1)*U+u,live&recur,False)
        x=x+tl.load(V+s*U+u,live&market,0.)+tl.load(STATE+(b*S+s-3*C)*U+u,live&state,0.)
        k=k|tl.load(K+(s%C)*U+u,live&market&(s<2*C),False)
        z=z+x*w;known=known|(k&(w!=0))
    y=2./(1.+tl.exp(-2.*z))-1.
    tl.store(new+(base+h)*U+u,y,live);tl.store(newk+(base+h)*U+u,known,live)

@tr.jit
def heads(HS,HW,HB,HT,MODE,SIZE,M,MK,A,AK,PHASE,OFFSETS,R,OUT,U:tl.constexpr,T:tl.constexpr):
    b=tl.program_id(0);u=tl.program_id(1)*T+tl.arange(0,T);live=u<U
    phase=tl.load(PHASE)&1;new=tl.where(phase!=0,M,A);newk=tl.where(phase!=0,MK,AK)
    base=tl.load(OFFSETS+b)
    bits=tl.full((T,),0,tl.int32)
    for a in tl.static_range(6):
        z0=tl.full((T,),0,tl.float32)+tl.load(HB+(b*6+a)*2);z1=tl.full((T,),0,tl.float32)+tl.load(HB+(b*6+a)*2+1)
        k0=tl.full((T,),False,tl.int1);k1=tl.full((T,),False,tl.int1)
        for e in tl.static_range(8):
            i=(b*6+a)*16+e;s0=tl.load(HS+i);s1=tl.load(HS+i+8);w0=tl.load(HW+i);w1=tl.load(HW+i+8)
            z0=z0+tl.load(new+(base+s0)*U+u,live,0.)*w0;z1=z1+tl.load(new+(base+s1)*U+u,live,0.)*w1
            k0=k0|(tl.load(newk+(base+s0)*U+u,live,False)&(w0!=0));k1=k1|(tl.load(newk+(base+s1)*U+u,live,False)&(w1!=0))
        z0=2./(1.+tl.exp(-2.*z0))-1.;z1=2./(1.+tl.exp(-2.*z1))-1.
        x=k0&(z0>tl.load(HT+(b*6+a)*2));y=k1&(z1>tl.load(HT+(b*6+a)*2+1));mode=tl.load(MODE+b*6+a)
        decision=tl.where(mode==0,x,tl.where(mode==1,x|y,tl.where(mode==2,x&y,tl.where(mode==3,x^y,(x==y)&k0&k1))))
        if a<5:bits=bits|(decision.to(tl.int32)<<a)
        selected=tl.where(mode==0,z0,tl.where(x,tl.where(y,tl.maximum(z0,z1),z0),tl.where(y,z1,tl.maximum(z0,z1))))
        z=selected*tl.load(SIZE+(b*6+a)*2)+tl.load(SIZE+(b*6+a)*2+1);ratio=1./(1.+tl.exp(-2.*z))
        if a==0 or a==2 or a==3:
            channel=0 if a==0 else 1 if a==2 else 2
            tl.store(R+(b*U+u)*4+channel,ratio.to(tl.float64),live)
        if a==5:
            tl.store(R+(b*U+u)*4+3,.001+.199*ratio.to(tl.float64),live);bits=bits|(k0.to(tl.int32)<<5)
    tl.store(OUT+b*U+u,bits,live)

def signals(p,v,k,position,opened,previous,index,out=None):
    b,u=p.workspace_shape;t=p.cuda_block
    v=v.T.contiguous();k=k.T.contiguous()
    state_kernel[(b,tr.cdiv(u,256))](position,opened,previous,index,p.feedback,p.position_states,u,len(STATES),256,enable_fp_fusion=False)
    tiles=p.core_tiles[p.core_tile]
    core[(tiles.shape[0],tr.cdiv(u,t))](v,k,p.sources,p.weights,p.bias,p.position_states,p.memory,p.memory_known,p.workspace,p.known,p.buffer_phase,p.counts,p.offsets,tiles,u,len(p.columns),len(STATES),t,p.core_tile,enable_fp_fusion=False)
    out=out if out is not None else torch.empty((1,b,u),device=p.device,dtype=torch.int16)
    heads[(b,tr.cdiv(u,128))](p.hs,p.hw,p.hb,p.ht,p.modes,p.sizes,p.memory,p.memory_known,p.workspace,p.known,p.buffer_phase,p.offsets,p.ratios,out,u,128,enable_fp_fusion=False)
    p.buffer_phase.add_(1)
    return out
