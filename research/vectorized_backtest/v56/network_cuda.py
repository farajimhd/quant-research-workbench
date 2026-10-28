"""Ragged recurrent graphs: memory and current-clock scratch stay separate."""
import torch
import triton as tr
import triton.language as tl
import triton.language.extra.cuda.libdevice as libdevice
from .config import FAN_IN,STATES

@tr.jit
def state_kernel(P,OPENED,PREVIOUS,CLOCK,FEEDBACK,OUT,U:tl.constexpr,STATES:tl.constexpr,BLOCK:tl.constexpr):
    # Share position loads/calculations across channels for each listing tile.
    member=tl.program_id(0);u=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK);live=u<U
    clock=tl.load(CLOCK).to(tl.float64)
    q=tl.load(P+(member*U+u)*9,live,0);held=q>1e-12
    avg=tl.load(P+(member*U+u)*9+1,live,1);stop=tl.load(P+(member*U+u)*9+2,live,0)
    adds=tl.load(P+(member*U+u)*9+5,live,0);last=tl.load(P+(member*U+u)*9+6,live,0)
    opened=tl.load(OPENED+member*U+u,live,0);price=tl.load(PREVIOUS+u,live,0)
    known_price=(price==price)&(tl.abs(price)<float('inf'))&(price>0)
    for state in tl.static_range(STATES):
        if state==0:x64=held.to(tl.float64)
        elif state==1:x64=tl.where(held&known_price,price/tl.maximum(avg,1e-12)-1.,0.)
        elif state==2:x64=tl.where(held,(clock-opened)/3600.,0.)
        elif state==3:x64=tl.where(held&known_price,q*price/1000.,0.)
        elif state==4:x64=tl.where(held,(avg-stop)/tl.maximum(avg,1e-12),0.)
        elif state==5:x64=adds/20.
        elif state==6:x64=tl.where(held,(clock-last)/300.,0.)
        else:x64=tl.load(FEEDBACK+(member*U+u)*(STATES-7)+state-7,live,0).to(tl.float64)
        x=2./(1.+tl.exp(2.*-x64.to(tl.float32)))-1.
        tl.store(OUT+(member*STATES+state)*U+u,x,live)


@tr.jit
def workspace_kernel(V,VALID,SOURCES,WEIGHTS,EDGE_COUNTS,OPS,BIAS,COUNTS,OFFSETS,THRESHOLD,SIZE_SOURCE,SIZE_WEIGHT,SIZE_BIAS,POSITION_STATES,MEMORY,MEMORY_KNOWN,RATIOS,WORK,KNOWN,PHASE,OUT,OPENED,CLOCK,OWNER_IDS,OWNED:tl.constexpr,OWNED_K:tl.constexpr,U:tl.constexpr,C:tl.constexpr,STATES:tl.constexpr,FAN:tl.constexpr,BLOCK:tl.constexpr,ORDER,TILES:tl.constexpr,SORTED:tl.constexpr):
    """One clock of scratch, directly indexed; no N-way register gathers."""
    if SORTED:
        net_order=tl.load(ORDER+tl.program_id(0)//TILES)
        member=net_order//5;action=net_order%5
        j=(tl.program_id(0)%TILES)*BLOCK+tl.arange(0,BLOCK)
    else:
        member=tl.program_id(0);action=tl.program_id(1);j=tl.program_id(2)*BLOCK+tl.arange(0,BLOCK)
    if OWNED:
        u=tl.load(OWNER_IDS+member*OWNED_K+j,j<OWNED_K,-1);live=(j<OWNED_K)&(u>=0)&(u<U)
    else:
        u=j;live=u<U
    net=member*5+action;ncount=tl.load(COUNTS+net);base=tl.load(OFFSETS+net)
    phase=tl.load(PHASE)&1
    old=tl.where(phase!=0,WORK,MEMORY);current=tl.where(phase!=0,MEMORY,WORK)
    old_known=tl.where(phase!=0,KNOWN,MEMORY_KNOWN);current_known=tl.where(phase!=0,MEMORY_KNOWN,KNOWN)
    h=tl.full((BLOCK,),0,tl.float32);ok=tl.full((BLOCK,),False,tl.int1)
    for i in range(ncount):
        z=tl.full((BLOCK,),0,tl.float32)+tl.load(BIAS+base+i);ok=tl.full((BLOCK,),False,tl.int1)
        op=tl.load(OPS+base+i)
        aggregate=tl.full((BLOCK,),0,tl.float32)
        if op==1:aggregate=tl.full((BLOCK,),-float('inf'),tl.float32)
        elif op==2:aggregate=tl.full((BLOCK,),float('inf'),tl.float32)
        elif op==4:aggregate=tl.full((BLOCK,),1,tl.float32)
        query=tl.full((BLOCK,),0,tl.float32);key=tl.full((BLOCK,),0,tl.float32)
        peak=tl.full((BLOCK,),-float('inf'),tl.float32);den=tl.full((BLOCK,),0,tl.float32)
        # Fetch independent edges together, then consume each row in exact edge
        # order. No reduction/reassociation; masked padding and zero weights keep
        # the original operator-specific semantics.
        if op<9:
            ec=tl.load(EDGE_COUNTS+base+i)
            for first in range(0,ec,4):
                edges=first+tl.arange(0,4)
                ss=tl.load(SOURCES+(base+i)*FAN+edges,edges<ec,0)
                ww=tl.load(WEIGHTS+(base+i)*FAN+edges,edges<ec,0)
                enabled=(edges<ec)&((ww!=0)|((op>=1)&(op<=6)))
                delayed=ss<0;market=(ss>=0)&(ss<3*C)
                state=(ss>=3*C)&(ss<3*C+STATES)
                offsets=tl.where(delayed,base-ss-1,tl.where(market,ss,tl.where(state,member*STATES+ss-3*C,base+ss-3*C-STATES)))
                pointers=tl.where(delayed,old,tl.where(market,V,tl.where(state,POSITION_STATES,current)))
                xx=tl.load(pointers[:,None]+offsets[:,None]*U+u[None,:],enabled[:,None]&live[None,:],0)
                kp=tl.where(delayed,old_known,tl.where(market,VALID,current_known))
                ko=tl.where(market,ss%C,offsets)
                kk=tl.load(kp[:,None]+ko[:,None]*U+u[None,:],enabled[:,None]&~state[:,None]&live[None,:],False)
                kk=kk&(~market[:,None]|(ss[:,None]<2*C))
                for k in tl.static_range(4):
                    e=first+k
                    w=tl.gather(ww,tl.full((1,),k,tl.int32),0).reshape(())
                    if e<ec and (w!=0 or (op>=1 and op<=6)):
                        x=tl.gather(xx,tl.full((1,BLOCK),k,tl.int32),0).reshape((BLOCK,))
                        valid=tl.gather(kk,tl.full((1,BLOCK),k,tl.int32),0).reshape((BLOCK,))
                        term=libdevice.mul_rn(x,w);ok=ok|(valid&(w!=0))
                        if op==0 or op==7 or op==8:z=libdevice.add_rn(z,term)
                        elif op==1:aggregate=tl.maximum(aggregate,term)
                        elif op==2:aggregate=tl.minimum(aggregate,term)
                        elif op==3:aggregate=aggregate+term
                        elif op==4:aggregate=aggregate*term
                        elif op==5:
                            if e==0:query=term
                            else:aggregate=aggregate+term
                        elif op==6:
                            if e==0:query=term
                            elif e%2==1:key=term
                            else:
                                logit=query*key;next_peak=tl.maximum(peak,logit)
                                scale=tl.exp(peak-next_peak);weight=tl.exp(logit-next_peak)
                                aggregate=aggregate*scale+weight*term;den=den*scale+weight;peak=next_peak
        if op==1 or op==2 or op==4 or op==5:z=z+aggregate
        elif op==3:z=z+aggregate/tl.load(EDGE_COUNTS+base+i)
        elif op==6:z=z+aggregate/den
        h=2./(1.+tl.exp(2.*-z))-1.
        if op==5:h=h/(1.+tl.exp(-query))
        elif op==7:h=tl.minimum(1.,tl.maximum(-1.,z))
        if op>=9:
            source=tl.load(SOURCES+(base+i)*FAN)-3*C-STATES
            x0=tl.load(current+(base+source)*U+u,live,0)
            k0=tl.load(current_known+(base+source)*U+u,live,False)
            if op==9:
                threshold=tl.load(WEIGHTS+(base+i)*FAN)
                age_limit=tl.floor(tl.load(BIAS+base+i)*3600.+.5)
                age=tl.load(CLOCK).to(tl.float64)-tl.load(OPENED+member*U+u,live,0)
                h=(k0&(x0>threshold)&(age>=age_limit)).to(tl.float32);ok=k0
            elif op==13:
                h=x0*tl.load(WEIGHTS+(base+i)*FAN)+tl.load(BIAS+base+i);ok=k0
            else:
                source1=tl.load(SOURCES+(base+i)*FAN+1)-3*C-STATES
                x1=tl.load(current+(base+source1)*U+u,live,0)
                k1=tl.load(current_known+(base+source1)*U+u,live,False)
                ok=k0|k1
                if op==10:h=((x0>.5)|(x1>.5)).to(tl.float32)
                elif op==11:h=((x0>.5)&(x1>.5)).to(tl.float32)
                elif op==12:h=((x0>.5)^(x1>.5)).to(tl.float32)
                elif op==15:
                    h=((x0>.5)==(x1>.5)).to(tl.float32);ok=k0&k1
                elif op==14 or op==16:
                    s2=tl.load(SOURCES+(base+i)*FAN+2)-3*C-STATES
                    s3=tl.load(SOURCES+(base+i)*FAN+3)-3*C-STATES
                    z0=tl.load(current+(base+s2)*U+u,live,0);z1=tl.load(current+(base+s3)*U+u,live,0)
                    h=tl.where(x0>.5,tl.where(x1>.5,tl.maximum(z0,z1),z0),tl.where(x1>.5,z1,0.))
                    if op==16:h=tl.maximum(z0,z1);ok=k0&k1
        offset=(base+i)*U+u
        tl.store(current+offset,h,live);tl.store(current_known+offset,ok,live)
    tl.store(OUT+(member*5+action)*U+u,ok&(h>tl.load(THRESHOLD+net)),live)

    if action==0 or action==2 or action==3:
        head=tl.load(SIZE_SOURCE+net);value=tl.load(current+(base+head)*U+u,live,0)
        z=value*tl.load(SIZE_WEIGHT+net)+tl.load(SIZE_BIAS+net)
        fraction=(2./(1.+tl.exp(-2.*z))-1.+1.)*.5
        channel=tl.where(action==0,0,tl.where(action==2,1,2))
        tl.store(RATIOS+(member*U+u)*3+channel,fraction,live)


def signals(plan,values,valid,position,opened,previous,index,out=None):
    b=position.shape[1];u=position.shape[2];values=values.T.contiguous();valid=valid.T.contiguous()
    owners=getattr(plan,'owner_indices',None);owned=owners is not None;owned_k=owners.shape[1] if owned else u
    actions=(torch.zeros if owned else torch.empty)((b,5,u),device=position.device,dtype=torch.bool)
    block=getattr(plan,'cuda_block',128)
    if owned:block=32
    if block not in (32,64,128,256):raise ValueError('Unsupported neural CUDA tile')
    state_kernel[(b,tr.cdiv(u,256))](position,opened,previous,index,plan.feedback,plan.position_states,u,len(STATES),256,enable_fp_fusion=False)
    scheduled=not owned and block in (64,128)
    tiles=tr.cdiv(owned_k,block)
    grid=(b*5*tiles,) if scheduled else (b,5,tiles)
    workspace_kernel[grid](values,valid,plan.sources,plan.weights,plan.edge_counts,plan.operations,plan.bias,plan.counts,plan.offsets,plan.threshold,
        plan.size_source,plan.size_weight,plan.size_bias,plan.position_states,plan.memory,plan.memory_known,plan.ratios,
        plan.workspace,plan.known,plan.buffer_phase,actions,opened,index,owners if owned else plan.sources,owned,owned_k,u,len(plan.columns),len(STATES),FAN_IN,block,
        plan.cuda_network_order,tiles,scheduled,num_warps=2 if scheduled else 4,enable_fp_fusion=False)
    result=out if out is not None else torch.empty((1,b,u),device=position.device,dtype=torch.int16)
    pack[(tr.cdiv(b*u,256),)](actions,result,plan.buffer_phase,b*u,u,256)
    return result

@tr.jit
def pack(A,OUT,PHASE,COUNT:tl.constexpr,U:tl.constexpr,BLOCK:tl.constexpr):
    x=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK);live=x<COUNT;base=(x//U)*5*U+x%U
    bits=tl.full((BLOCK,),0,tl.int32)
    for action in tl.static_range(5):bits=bits | (tl.load(A+base+action*U,live,False).to(tl.int32)<<action)
    tl.store(OUT+x,bits,live)
    # Device-side phase survives every graph replay, including one-clock graphs.
    # Only this program writes it; the next launch waits for the whole kernel.
    if tl.program_id(0)==0:tl.store(PHASE,tl.load(PHASE)+1)
