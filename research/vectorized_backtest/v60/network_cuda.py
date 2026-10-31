"""Market-only feed-forward graphs with exact ordered edge arithmetic."""
import torch
import triton as tr
import triton.language as tl
import triton.language.extra.cuda.libdevice as libdevice
from .config import FAN_IN,STATES

@tr.jit
def fetch_value(V,VALID,POSITION_STATES,OLD,OLD_KNOWN,CURRENT,CURRENT_KNOWN,base,member,source,enabled,u,live,U:tl.constexpr,C:tl.constexpr,STATES:tl.constexpr):
    # Construction forbids reserved state slots and delayed sources. Only
    # current market channels and preceding nodes can reach this kernel.
    market=source<3*C
    offset=tl.where(market,source,base+source-3*C-STATES)
    pointer=tl.where(market,V,CURRENT)
    value=tl.load(pointer+offset*U+u,enabled&live,0)
    known_pointer=tl.where(market,VALID,CURRENT_KNOWN)
    known_offset=tl.where(market,source%C,offset)
    known=tl.load(known_pointer+known_offset*U+u,enabled&live,False)&(~market|(source<2*C))
    return value,known


@tr.jit
def workspace_kernel(V,VALID,SOURCES,WEIGHTS,EDGE_COUNTS,OPS,BIAS,COUNTS,OFFSETS,THRESHOLD,SIZE_SOURCE,SIZE_WEIGHT,SIZE_BIAS,POSITION_STATES,ENTRY_ALLOWED,ENTRY_REP,MEMORY,MEMORY_KNOWN,RATIOS,WORK,KNOWN,PHASE,OUT,OPENED,CLOCK,OWNER_IDS,OWNED:tl.constexpr,OWNED_K:tl.constexpr,U:tl.constexpr,C:tl.constexpr,STATES:tl.constexpr,FAN:tl.constexpr,BLOCK:tl.constexpr,ORDER,TILES:tl.constexpr,SORTED:tl.constexpr,TIME_COUNT:tl.constexpr=1,MEMBERS:tl.constexpr=0,NODES:tl.constexpr=0,VALUE_WIDTH:tl.constexpr=0,KNOWN_WIDTH:tl.constexpr=0,PROGRAMS:tl.constexpr=0):
    """One clock of scratch, directly indexed; no N-way register gathers."""
    time_index=0
    program=tl.program_id(0)
    if TIME_COUNT>1:
        time_index=program//(PROGRAMS*TILES)
        program=program%(PROGRAMS*TILES)
        V+=time_index*VALUE_WIDTH*U;VALID+=time_index*KNOWN_WIDTH*U
        WORK+=time_index*NODES*U;KNOWN+=time_index*NODES*U
        MEMORY=WORK;MEMORY_KNOWN=KNOWN
        RATIOS+=time_index*MEMBERS*U*3;OUT+=time_index*MEMBERS*5*U
        ENTRY_ALLOWED+=time_index*U
    if SORTED:
        net_order=tl.load(ORDER+program//TILES)
        member=net_order//5;action=net_order%5
        j=(program%TILES)*BLOCK+tl.arange(0,BLOCK)
    else:
        member=tl.program_id(0);action=tl.program_id(1);j=tl.program_id(2)*BLOCK+tl.arange(0,BLOCK)
    if OWNED:
        u=tl.load(OWNER_IDS+member*OWNED_K+j,j<OWNED_K,-1);live=(j<OWNED_K)&(u>=0)&(u<U)
    else:
        u=j;live=u<U
    net=member*5+action;ncount=tl.load(COUNTS+net);base=tl.load(OFFSETS+net)
    phase=tl.load(PHASE)&1
    old=MEMORY;current=WORK
    old_known=MEMORY_KNOWN;current_known=KNOWN
    # No position reads: every management head is market-only.
    entry_allowed=tl.load(ENTRY_ALLOWED+u,live,False)
    representative=tl.load(ENTRY_REP+member*5+action)
    active=live&(representative==member)&tl.where(action==0,entry_allowed,True)
    tl.store(OUT+(member*5+action)*U+u,False,live)
    if tl.sum(active.to(tl.int32),0)>0:
        # V60 has no delayed edges or action feedback. Only eligible entry
        # listings and all market-only management heads are evaluated.
        # Keep arithmetic/order identical on active lanes; avoid scratch/feature
        # traffic for the other listings in an otherwise active CUDA tile.
        live=active
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
                    values=();knowns=();weights=()
                    for k in tl.static_range(4):
                        source=tl.load(SOURCES+(base+i)*FAN+first+k,first+k<ec,0)
                        edge_weight=tl.load(WEIGHTS+(base+i)*FAN+first+k,first+k<ec,0)
                        enabled=(first+k<ec)&((edge_weight!=0)|((op>=1)&(op<=6)))
                        value,known=fetch_value(V,VALID,POSITION_STATES,old,old_known,current,current_known,base,member,source,enabled,u,live,U,C,STATES)
                        values+=(value,);knowns+=(known,);weights+=(edge_weight,)
                    for k in tl.static_range(4):
                        e=first+k
                        w=weights[k]
                        if e<ec and (w!=0 or (op>=1 and op<=6)):
                            x=values[k]
                            valid=knowns[k]
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
            if op==17:
                before=tl.full((BLOCK,),0,tl.float32)+tl.load(BIAS+base+i);now=before;ok=live
                for edge in range(tl.load(EDGE_COUNTS+base+i)):
                    source=tl.load(SOURCES+(base+i)*FAN+edge);weight=tl.load(WEIGHTS+(base+i)*FAN+edge)
                    now=libdevice.add_rn(now,libdevice.mul_rn(tl.load(V+source*U+u,live,0),weight))
                    before=libdevice.add_rn(before,libdevice.mul_rn(tl.load(V+(3*C+source)*U+u,live,0),weight))
                    ok=ok&tl.load(VALID+source*U+u,live,False)&tl.load(VALID+(C+source)*U+u,live,False)
                h=tl.minimum(1.,tl.maximum(-1.,tl.minimum(now,-before)))
            elif op>=9:
                source=tl.load(SOURCES+(base+i)*FAN)-3*C-STATES
                x0=tl.load(current+(base+source)*U+u,live,0)
                k0=tl.load(current_known+(base+source)*U+u,live,False)
                if op==9:
                    threshold=tl.load(WEIGHTS+(base+i)*FAN)
                    h=(k0&(x0>threshold)).to(tl.float32);ok=k0
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
    # No state projection kernel: model input is exclusively market data.
    scheduled=not owned and block in (32,64,128)
    tiles=tr.cdiv(owned_k,block)
    grid=(plan.cuda_network_order.numel()*tiles,) if scheduled else (b,5,tiles)
    workspace_kernel[grid](values,valid,plan.sources,plan.weights,plan.edge_counts,plan.operations,plan.bias,plan.counts,plan.offsets,plan.threshold,
        plan.size_source,plan.size_weight,plan.size_bias,plan.position_states,plan.entry_allowed,plan.action_representatives,plan.memory,plan.memory_known,plan.ratios,
        plan.workspace,plan.known,plan.buffer_phase,actions,opened,index,owners if owned else plan.sources,owned,owned_k,u,len(plan.columns),len(STATES),FAN_IN,block,
        plan.cuda_network_order,tiles,scheduled,num_warps=(1 if block==32 else 2) if scheduled else 4,enable_fp_fusion=False)
    result=out if out is not None else torch.empty((1,b,u),device=position.device,dtype=torch.int16)
    pack[(tr.cdiv(b*u,256),)](actions,result,plan.buffer_phase,plan.action_representatives,plan.ratios,b*u,u,256)
    return result

@tr.jit
def pack(A,OUT,PHASE,ENTRY_REP,RATIOS,COUNT:tl.constexpr,U:tl.constexpr,BLOCK:tl.constexpr,MEMBERS:tl.constexpr=0):
    x=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK);live=x<COUNT;base=(x//U)*5*U+x%U
    bits=tl.full((BLOCK,),0,tl.int32)
    time_offset=0;member=x//U
    if MEMBERS>0:
        time_offset=(member//MEMBERS)*MEMBERS
        member=member%MEMBERS
    for action in tl.static_range(5):
        representative=tl.load(ENTRY_REP+member*5+action,live,0)
        address=((representative+time_offset)*5+action)*U+x%U
        bits=bits | (tl.load(A+address,live,False).to(tl.int32)<<action)
        if action==0 or action==2 or action==3:
            channel=0 if action==0 else 1 if action==2 else 2
            ratio=tl.load(RATIOS+((representative+time_offset)*U+x%U)*3+channel,live,1.)
            tl.store(RATIOS+x*3+channel,ratio,live&(representative!=member))
    tl.store(OUT+x,bits,live)
    # Device-side phase survives every graph replay, including one-clock graphs.
    # Only this program writes it; the next launch waits for the whole kernel.
    if MEMBERS==0 and tl.program_id(0)==0:tl.store(PHASE,tl.load(PHASE)+1)


def market_batch(plan,values,valid,membership,workspace,known):
    """Independent candle axis; one GPU graph traversal for an entire block."""
    t,u,width=values.shape;b=len(plan.members)
    values=values.transpose(1,2).contiguous();valid=valid.transpose(1,2).contiguous()
    actions=torch.empty((t,b,5,u),device=values.device,dtype=torch.bool)
    ratios=torch.ones((t,b,u,3),device=values.device,dtype=torch.float64)
    block=64;tiles=tr.cdiv(u,block)
    workspace_kernel[(t*plan.cuda_network_order.numel()*tiles,)](values,valid,plan.sources,plan.weights,plan.edge_counts,plan.operations,plan.bias,plan.counts,plan.offsets,plan.threshold,
        plan.size_source,plan.size_weight,plan.size_bias,plan.position_states,membership,plan.action_representatives,workspace,known,ratios,
        workspace,known,plan.buffer_phase,actions,plan.buffer_phase,plan.buffer_phase,plan.sources,False,u,u,len(plan.columns),len(STATES),FAN_IN,block,
        plan.cuda_network_order,tiles,True,t,b,plan.total_nodes,width,valid.shape[1],plan.cuda_network_order.numel(),num_warps=2,enable_fp_fusion=False)
    out=torch.empty((t,b,u),device=values.device,dtype=torch.int16)
    pack[(tr.cdiv(t*b*u,256),)](actions,out,plan.buffer_phase,plan.action_representatives,ratios,t*b*u,u,256,b)
    return out,ratios
