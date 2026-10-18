"""One fused CUDA financial transition; evaluator.step remains the oracle."""
import torch
import triton
import triton.language as tl


@triton.jit
def kernel(P,A,PRICE,PREVIOUS,OBSERVED,MEMBER,SIGNALS,SWING,POLICY,CLOCK,TERMINAL,RATIOS,OUT,AGG,
           U:tl.constexpr,TILES:tl.constexpr,B:tl.constexpr,PS:tl.constexpr,AS:tl.constexpr,VS:tl.constexpr,
           POLS:tl.constexpr,RS:tl.constexpr,MINIMUM:tl.constexpr,ENTRY:tl.constexpr,ADD:tl.constexpr,COST:tl.constexpr,FEE:tl.constexpr,
           MINFEE:tl.constexpr,DISTANCE:tl.constexpr,BUDGET:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0);session=row//B;candidate=row%B;i=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK);live=i<U
    pp=session*PS[0]+candidate*PS[1]+i*PS[2]
    ap=session*AS[0]+candidate*AS[1]
    q=tl.load(P+pp,live,0);avg=tl.load(P+pp+PS[3],live,0);stop=tl.load(P+pp+2*PS[3],live,0)
    target=tl.load(P+pp+3*PS[3],live,0);water=tl.load(P+pp+4*PS[3],live,0)
    adds=tl.load(P+pp+5*PS[3],live,0);last=tl.load(P+pp+6*PS[3],live,0)
    realized=tl.load(P+pp+7*PS[3],live,0);episode=tl.load(P+pp+8*PS[3],live,0)
    price=tl.load(PRICE+session*VS[0][0]+candidate*VS[0][1]+i*VS[0][2],live,0)
    previous=tl.load(PREVIOUS+session*VS[1][0]+candidate*VS[1][1]+i*VS[1][2],live,0)
    observed=tl.load(OBSERVED+session*VS[2][0]+candidate*VS[2][1]+i*VS[2][2],live,0)
    membership=tl.load(MEMBER+session*VS[3][0]+candidate*VS[3][1]+i*VS[3][2],live,0)
    signals=tl.load(SIGNALS+session*VS[4][0]+candidate*VS[4][1]+i*VS[4][2],live,0)
    swing=tl.load(SWING+session*VS[5][0]+candidate*VS[5][1]+i*VS[5][2],live,0)
    terminal=tl.load(TERMINAL+session*VS[6][0]+candidate*VS[6][1]+i*VS[6][2],live,0)
    pol=POLICY+session*POLS[0]+candidate*POLS[1]+i*POLS[2]
    sf=tl.load(pol);tf=tl.load(pol+POLS[3]);rf=tl.load(pol+2*POLS[3]);trailf=tl.load(pol+3*POLS[3])
    addmin=tl.load(pol+4*POLS[3]);reducemin=tl.load(pol+5*POLS[3]);maxadds=tl.load(pol+6*POLS[3])
    cooldown=tl.load(pol+7*POLS[3]);swing_mode=tl.load(pol+8*POLS[3])>0;clock=tl.load(CLOCK)
    ptr=RATIOS+session*RS[0]+candidate*RS[1]+i*RS[2]
    r0=tl.load(ptr,live,0);r1=tl.load(ptr+RS[3],live,0);r2=tl.load(ptr+2*RS[3],live,0)
    r0=tl.where((r0==r0)&(r0>=0)&(r0<=1),r0,0.);r1=tl.where((r1==r1)&(r1>=0)&(r1<=1),r1,0.);r2=tl.where((r2==r2)&(r2>=0)&(r2<=1),r2,0.)
    entry=ENTRY*r0;addition=ADD*r1
    finite=(price==price)&(tl.abs(price)<float('inf'));held=q>1.e-12
    tradable=observed&finite&(price>0);profit=tl.full((BLOCK,),0.,tl.float64)
    if tl.sum(held.to(tl.int32),0)>0:profit=previous/tl.maximum(avg,1.e-12)-1.
    ratchet=held&((signals&16)!=0)&(previous==previous)&(tl.abs(previous)<float('inf'))
    water=tl.where(ratchet,tl.maximum(water,previous),water)
    stop=tl.where(ratchet,tl.maximum(stop,water*(1.-trailf)),stop)
    exit_now=held&((terminal&finite&(price>0))|((((signals&2)!=0)|(previous<=stop))&tradable))
    managed=(clock-last)>=cooldown;target_hit=held&(previous>=target)
    rf=tl.where(target_hit,rf,r2)
    reduce_now=held&~exit_now&tradable&managed&((((signals&8)!=0)&(profit>=reducemin))|target_hit)
    reduce_now=reduce_now&(rf>0)
    sell=tl.where(exit_now,q,tl.where(reduce_now,q*rf,0.));execution_price=tl.where(finite,price,0.)
    realized=realized+sell*(execution_price-avg)-sell*execution_price*COST
    realized=realized-tl.where(sell>0,tl.maximum(sell*FEE,MINFEE),0.)
    remaining=tl.maximum(q-sell,0.);closed=held&(remaining<=1.e-12);complete_profit=realized-episode
    add_now=held&~exit_now&~reduce_now&tradable&managed&((signals&4)!=0)&(profit>=addmin)&(adds<maxadds)
    add_now=add_now&(addition>0)&(addition>=MINIMUM)
    initial=tl.where(swing_mode,swing,execution_price*(1.-sf))
    entry_now=~held&~terminal&tradable&membership&((signals&1)!=0)&(initial==initial)&(tl.abs(initial)<float('inf'))&(initial>0)&(initial<execution_price)
    entry_now=entry_now&(entry>0)&(entry>=MINIMUM)
    safe_price=tl.maximum(execution_price,1.e-12)
    rejected_entry=tl.full((BLOCK,),False,tl.int1);rejected_add=tl.full((BLOCK,),False,tl.int1)
    # A candidate row has no prospective financial risk when no corresponding
    # action is requested. Skip entire blocks of expensive double arithmetic.
    if tl.sum(entry_now.to(tl.int32),0)>0:
        entry_q=entry/safe_price
        entry_risk=entry_q*tl.maximum(execution_price-initial,0.)+2.*tl.maximum(entry_q*FEE,MINFEE)+entry*COST+entry_q*tl.maximum(initial,0.)*COST
        safe_entry=((execution_price-initial)<=(DISTANCE+1.e-12)*safe_price)&(entry_risk<=BUDGET)
        rejected_entry=entry_now&~safe_entry;entry_now=entry_now&safe_entry
    if tl.sum(add_now.to(tl.int32),0)>0:
        add_q=addition/safe_price;prospective_q=remaining+add_q
        add_risk=tl.maximum(remaining*(avg-stop)+add_q*(execution_price-stop),0.)+tl.maximum(episode-realized,0.)+tl.maximum(add_q*FEE,MINFEE)+addition*COST+tl.maximum(prospective_q*FEE,MINFEE)+prospective_q*tl.maximum(stop,0.)*COST
        basis=remaining*avg+add_q*execution_price
        safe_add=(add_risk<=BUDGET)&(prospective_q*stop>=(1.-DISTANCE-1.e-12)*basis)
        rejected_add=add_now&~safe_add;add_now=add_now&safe_add
    dollars=entry_now.to(tl.float64)*entry+add_now.to(tl.float64)*addition
    bought=tl.full((BLOCK,),0.,tl.float64)
    if tl.sum((entry_now|add_now).to(tl.int32),0)>0:bought=dollars/safe_price
    quantity=remaining+bought
    added_avg=avg
    if tl.sum(add_now.to(tl.int32),0)>0:
        added_avg=avg+(bought/tl.maximum(quantity,1.e-12))*(execution_price-avg)
    new_avg=tl.where(entry_now,execution_price,tl.where(add_now,added_avg,tl.where(quantity>0,avg,0.)))
    episode=tl.where(entry_now,realized,episode)
    realized=realized-dollars*COST-tl.where(bought>0,tl.maximum(bought*FEE,MINFEE),0.)
    stop=tl.where(entry_now,initial,stop)
    target=tl.where(entry_now,execution_price*(1.+tf),tl.where(target_hit&reduce_now,target+avg*tf,target))
    water=tl.where(entry_now,execution_price,water);adds=tl.where(entry_now,0.,adds+add_now.to(tl.float64))
    last=tl.where(entry_now|add_now|reduce_now,clock,last)
    output=(row*U+i)*9
    tl.store(OUT+output,quantity,live);tl.store(OUT+output+1,new_avg,live);tl.store(OUT+output+2,stop,live)
    tl.store(OUT+output+3,target,live);tl.store(OUT+output+4,water,live);tl.store(OUT+output+5,adds,live)
    tl.store(OUT+output+6,last,live);tl.store(OUT+output+7,realized,live);tl.store(OUT+output+8,episode,live)
    equity=tl.sum(tl.where(live,realized+quantity*(execution_price-new_avg),0.),0)
    risk=tl.full((BLOCK,),0.,tl.float64);distance=tl.full((BLOCK,),0.,tl.float64)
    if tl.sum((quantity>0).to(tl.int32),0)>0:
        risk=tl.where(quantity>0,quantity*tl.maximum(new_avg-stop,0.)+tl.maximum(episode-realized,0.)+tl.maximum(quantity*FEE,MINFEE)+quantity*tl.maximum(stop,0.)*COST,0.)
        distance=tl.where(quantity>0,tl.maximum(new_avg-stop,0.)/tl.maximum(new_avg,1.e-12),0.)
    target_a=AGG+(row*TILES+tl.program_id(1))*15
    tl.store(target_a,equity)
    tl.store(target_a+1,tl.sum(tl.where(live,quantity*execution_price,0.),0))
    tl.store(target_a+2,tl.sum(tl.where(live,risk,0.),0))
    tl.store(target_a+3,tl.sum(entry_now.to(tl.float64),0))
    tl.store(target_a+4,tl.sum(add_now.to(tl.float64),0))
    tl.store(target_a+5,tl.sum(reduce_now.to(tl.float64),0))
    tl.store(target_a+6,tl.sum((quantity>0).to(tl.float64),0))
    tl.store(target_a+7,tl.min(tl.where(closed,complete_profit,float('inf')),0))
    tl.store(target_a+8,tl.sum(closed.to(tl.float64),0))
    tl.store(target_a+9,tl.sum((closed&(complete_profit>0)).to(tl.float64),0))
    tl.store(target_a+10,tl.sum(tl.where(live,sell*execution_price+dollars,0.),0))
    tl.store(target_a+11,tl.max(risk,0));tl.store(target_a+12,tl.max(distance,0))
    tl.store(target_a+13,tl.sum(rejected_entry.to(tl.float64),0))
    tl.store(target_a+14,tl.sum(rejected_add.to(tl.float64),0))


@triton.jit
def aggregate_kernel(PART,A,OUT,TILES:tl.constexpr,B:tl.constexpr,AS:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0);i=tl.arange(0,BLOCK);live=i<TILES
    source=PART+(row*TILES+i)*15;old=A+(row//B)*AS[0]+(row%B)*AS[1];out=OUT+row*16
    equity=tl.sum(tl.load(source,live,0.),0);peak=tl.maximum(tl.load(old),equity)
    tl.store(out,peak);tl.store(out+1,tl.maximum(tl.load(old+AS[2]),peak-equity))
    for field in tl.static_range(2,7):
        value=tl.sum(tl.load(source+(field-1),live,0.),0)
        tl.store(out+field,tl.load(old+field*AS[2])+value)
    held=tl.sum(tl.load(source+6,live,0.),0)>0
    tl.store(out+7,tl.load(old+7*AS[2])+(~held).to(tl.float64))
    worst=tl.min(tl.load(source+7,live,float('inf')),0)
    tl.store(out+8,tl.minimum(tl.load(old+8*AS[2]),worst))
    for field in tl.static_range(9,12):
        value=tl.sum(tl.load(source+(field-1),live,0.),0)
        tl.store(out+field,tl.load(old+field*AS[2])+value)
    tl.store(out+12,tl.maximum(tl.load(old+12*AS[2]),tl.max(tl.load(source+11,live,0.),0)))
    tl.store(out+13,tl.maximum(tl.load(old+13*AS[2]),tl.max(tl.load(source+12,live,0.),0)))
    for field in tl.static_range(14,16):
        value=tl.sum(tl.load(source+(field-1),live,0.),0)
        tl.store(out+field,tl.load(old+field*AS[2])+value)


@torch.library.custom_op('vector_v31::protected_transition',mutates_args=())
def transition(p:torch.Tensor,a:torch.Tensor,price:torch.Tensor,previous:torch.Tensor,observed:torch.Tensor,
               membership:torch.Tensor,signals:torch.Tensor,swing:torch.Tensor,policy:torch.Tensor,
               clock:torch.Tensor,terminal:torch.Tensor,entry_dollars:float,add_dollars:float,cost:float,
               fee:float,minimum_fee:float,distance:float,budget:float,ratios:torch.Tensor,minimum_dollars:float)->tuple[torch.Tensor,torch.Tensor]:
    s,b,u,_=p.shape
    if p.device.type!='cuda' or p.dtype!=torch.float64 or a.shape!=(s,b,16):raise ValueError('Protected double CUDA state required')
    vectors=[v.expand(s,b,u) for v in (price,previous,observed,membership,signals,swing,terminal)]
    policy=policy.expand(s,b,u,9);out=torch.empty(p.shape,device=p.device,dtype=p.dtype);agg=torch.empty(a.shape,device=a.device,dtype=a.dtype)
    width=min(128,triton.next_power_of_2(u));tiles=triton.cdiv(u,width)
    partial=torch.empty((s*b,tiles,15),device=p.device,dtype=p.dtype)
    kernel[(s*b,tiles)](p,a,*vectors[:6],policy,clock,vectors[6],ratios,out,partial,u,tiles,b,p.stride(),a.stride(),tuple(v.stride() for v in vectors),policy.stride(),ratios.stride(),minimum_dollars,entry_dollars,add_dollars,cost,fee,minimum_fee,distance,budget,width,num_warps=4,enable_fp_fusion=False)
    aggregate_kernel[(s*b,)](partial,a,agg,tiles,b,a.stride(),triton.next_power_of_2(tiles),num_warps=4,enable_fp_fusion=False)
    return out,agg


@transition.register_fake
def fake(p,a,price,previous,observed,membership,signals,swing,policy,clock,terminal,entry_dollars,add_dollars,cost,fee,minimum_fee,distance,budget,ratios,minimum_dollars):
    return torch.empty_like(p),torch.empty_like(a)
