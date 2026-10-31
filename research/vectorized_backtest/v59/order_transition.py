"""Fused persistent-order CUDA transition; orders.transition is the oracle."""
import torch
import triton
import triton.language as tl
from .order_liquidity_cuda import fill


@triton.jit
def order_fill_kernel(P,O,S,R,POL,PRE,MEM,SW,BID,ASK,V,CAP,CLOCK,TERM,PO,OO,DONE,REC,
           U:tl.constexpr,B:tl.constexpr,VS:tl.constexpr,PS:tl.constexpr,RS:tl.constexpr,
           ED:tl.constexpr,AD:tl.constexpr,FEE:tl.constexpr,MINFEE:tl.constexpr,COST:tl.constexpr,MINIMUM:tl.constexpr,BLOCK:tl.constexpr,
           FUSED:tl.constexpr=False,BOOK=None,MARK=None,OPENED=None,TICKER=None,ACTIVE=None,
           CLOSED=None,PROFIT=None,ENTRIES=None,PART=None,MS:tl.constexpr=None,TILES:tl.constexpr=0):
    row=tl.program_id(0);i=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK);live=i<U
    at=row*U+i;pp=P+at*9;op=O+at*17
    q=tl.load(pp,live,0.);avg=tl.load(pp+1,live,0.);stop=tl.load(pp+2,live,0.)
    target=tl.load(pp+3,live,0.);water=tl.load(pp+4,live,0.);adds=tl.load(pp+5,live,0.)
    last=tl.load(pp+6,live,0.);realized=tl.load(pp+7,live,0.);episode=tl.load(pp+8,live,0.)
    old_episode=episode
    kind=tl.load(op,live,0.);remaining=tl.load(op+1,live,0.);dollars=tl.load(op+2,live,0.)
    requested=tl.load(op+3,live,0.);filled=tl.load(op+4,live,0.);notional=tl.load(op+5,live,0.)
    fees=tl.load(op+6,live,0.);submitted=tl.load(op+7,live,0.);first=tl.load(op+8,live,0.)
    initial=tl.load(op+9,live,0.);flatten=tl.load(op+10,live,0.)>0;target_order=tl.load(op+11,live,0.)
    submitted_count=tl.load(op+12,live,0.);completed_count=tl.load(op+13,live,0.)
    fill_count=tl.load(op+14,live,0.);cancelled_stops=tl.load(op+15,live,0.)
    amended_clock=tl.load(op+16,live,0.)
    session=row//B;candidate=row%B
    sig=tl.load(S+session*VS[0][0]+candidate*VS[0][1]+i*VS[0][2],live,0)
    prev=tl.load(PRE+session*VS[1][0]+candidate*VS[1][1]+i*VS[1][2],live,0.)
    member=tl.load(MEM+session*VS[2][0]+candidate*VS[2][1]+i*VS[2][2],live,False)
    swing=tl.load(SW+session*VS[3][0]+candidate*VS[3][1]+i*VS[3][2],live,0.)
    bid=tl.load(BID+session*VS[4][0]+candidate*VS[4][1]+i*VS[4][2],live,0.)
    ask=tl.load(ASK+session*VS[5][0]+candidate*VS[5][1]+i*VS[5][2],live,0.)
    valid=tl.load(V+session*VS[6][0]+candidate*VS[6][1]+i*VS[6][2],live,False)
    cp=CAP+session*VS[7][0]+candidate*VS[7][1]+i*VS[7][2]
    buy_capacity=tl.load(cp,live,0.);sell_capacity=tl.load(cp+VS[7][3],live,0.)
    if FUSED:
        stamp=tl.load(cp+2*VS[7][3],live,0.);old_stamp=tl.load(BOOK+at*3,live,0.)
        newer=stamp>old_stamp
        buy_capacity=tl.maximum(tl.where(newer,buy_capacity,tl.load(BOOK+at*3+1,live,0.)),0.)
        sell_capacity=tl.maximum(tl.where(newer,sell_capacity,tl.load(BOOK+at*3+2,live,0.)),0.)
    terminal=tl.load(TERM);clock=tl.load(CLOCK)
    pol=POL+session*PS[0]+candidate*PS[1]+i*PS[2]
    sf=tl.load(pol,live,0.);tf=tl.load(pol+PS[3],live,0.);rf=tl.load(pol+2*PS[3],live,0.)
    trail=tl.load(pol+3*PS[3],live,0.);addmin=tl.load(pol+4*PS[3],live,0.);redmin=tl.load(pol+5*PS[3],live,0.)
    maxadds=tl.load(pol+6*PS[3],live,0.);cooldown=tl.load(pol+7*PS[3],live,0.);swingmode=tl.load(pol+8*PS[3],live,0.)>0
    ratios=R+session*RS[0]+candidate*RS[1]+i*RS[2]
    r0=tl.load(ratios,live,0.);r1=tl.load(ratios+RS[3],live,0.);r2=tl.load(ratios+2*RS[3],live,0.)
    r0=tl.where((r0>=0)&(r0<=1),r0,0.);r1=tl.where((r1>=0)&(r1<=1),r1,0.);r2=tl.where((r2>=0)&(r2<=1),r2,0.)
    finite=(prev>0)&(tl.abs(prev)<float('inf'));stopped=(q>1.e-12)&finite&(prev<=stop)
    fresh=valid&(bid>0)&(ask>=bid)&(tl.abs(ask)<float('inf'))&(tl.abs(bid)<float('inf'))
    proposed_stop=tl.where(swingmode,tl.where(kind==1,initial,swing),ask*(1-sf))
    feasible=fresh&(tl.abs(proposed_stop)<float('inf'))&(proposed_stop>0)&(proposed_stop<ask)&(bid>proposed_stop)&finite&(prev>proposed_stop)
    intended_quantity=ED*r0/tl.maximum(ask,1.e-12)
    expected_cost=(ask-bid)*intended_quantity+2*tl.maximum(intended_quantity*FEE,MINFEE)
    feasible=feasible&(ask*tf*intended_quantity>expected_cost)
    pending_buy=(kind==1)|(kind==2)
    cancelled=(pending_buy&(terminal|stopped|((sig&2)!=0)|((sig&8)!=0)))|((kind==1)&(filled==0)&fresh&~feasible)
    cancelled=cancelled|(((kind==3)|(kind==4))&((sig&4)!=0)&~terminal&~stopped&((sig&2)==0))
    flatten=flatten&~(cancelled&~pending_buy)
    # Preserve cancelled partial fills before replacing the request this candle.
    tl.store(REC+at*17+8,kind,live);tl.store(REC+at*17+9,requested,live)
    tl.store(REC+at*17+10,filled,live);tl.store(REC+at*17+11,notional,live)
    tl.store(REC+at*17+12,fees,live);tl.store(REC+at*17+13,submitted,live)
    tl.store(REC+at*17+14,first,live);tl.store(REC+at*17+15,clock,live)
    cancel_active=cancelled
    if FUSED:cancel_active=cancelled&tl.load(ACTIVE)
    tl.store(REC+at*17+16,cancel_active.to(tl.float64),live)
    cancelled_stops+=cancelled.to(tl.float64);completed_count+=cancelled.to(tl.float64)
    kind=tl.where(cancelled,0.,kind);remaining=tl.where(cancelled,0.,remaining)
    requested=tl.where(cancelled,0.,requested);dollars=tl.where(cancelled,0.,dollars)
    extra=AD*r1
    amend=pending_buy&~cancelled&~terminal&((sig&4)!=0)&fresh&((clock-amended_clock)>=cooldown)&(adds<maxadds)&(extra>0)&(extra>=MINIMUM)
    extra_quantity=tl.where(amend&(requested>0),extra/tl.maximum(ask,1.e-12),0.)
    remaining+=extra_quantity;requested+=extra_quantity;dollars=tl.where(amend,dollars+extra,dollars)
    adds+=amend.to(tl.float64);amended_clock=tl.where(amend,clock,amended_clock)
    held=q>1.e-12;busy=kind!=0;finite=(prev>0)&(tl.abs(prev)<float('inf'))
    ratchet=held&((sig&16)!=0)&finite&~terminal
    water=tl.where(ratchet,tl.maximum(water,prev),water);stop=tl.where(ratchet,tl.maximum(stop,water*(1-trail)),stop)
    flatten=flatten|(held&(terminal|((sig&2)!=0)|(finite&(prev<=stop))))|(terminal&busy)
    managed=(clock-last)>=cooldown;gain=tl.full((BLOCK,),0.,tl.float64)
    if tl.sum((held&finite).to(tl.int32),0)>0:gain=tl.where(held&finite,prev/tl.maximum(avg,1.e-12)-1,0.)
    hit=held&finite&(prev>=target);reduction=tl.minimum(tl.maximum(tl.where(hit,rf,r2),0.),1.)
    exit_new=~busy&held&flatten
    reduce_new=~busy&held&~flatten&~terminal&~cancelled&managed&(reduction>0)&((((sig&8)!=0)&(gain>=redmin))|hit)
    add_new=~busy&held&~flatten&~reduce_new&~terminal&managed&((sig&4)!=0)&(gain>=addmin)&(adds<maxadds)
    entry_new=~busy&~held&~terminal&~cancelled&member&((sig&1)!=0)&((sig&2)==0)&feasible
    entry_new=entry_new&((last<=0)|((clock-last)>=3))
    ea=ED*r0;aa=AD*r1
    entry_new=entry_new&(ea>0)&(ea>=MINIMUM)&(~swingmode|((swing>0)&(tl.abs(swing)<float('inf'))&valid&(tl.abs(ask)<float('inf'))&(swing<ask)))
    add_new=add_new&(aa>0)&(aa>=MINIMUM)
    new_kind=tl.where(exit_new,4,tl.where(reduce_new,3,tl.where(add_new,2,tl.where(entry_new,1,0))))
    new=new_kind!=0;kind=tl.where(new,new_kind,kind)
    amended_clock=tl.where(new,clock,amended_clock)
    dollars=tl.where(entry_new,ea,tl.where(add_new,aa,dollars))
    requested=tl.where(exit_new,q,tl.where(reduce_new,q*reduction,tl.where(entry_new|add_new,0.,requested)))
    remaining=tl.where(new,requested,remaining);filled=tl.where(new,0.,filled);notional=tl.where(new,0.,notional)
    fees=tl.where(new,0.,fees);submitted=tl.where(new,clock,submitted);first=tl.where(new,-1.,first)
    initial=tl.where(entry_new,tl.where(swingmode,swing,0.),initial);target_order=tl.where(new,(reduce_new&hit).to(tl.float64),target_order)
    submitted_count+=new.to(tl.float64);buy=(kind==1)|(kind==2);sell=(kind==3)|(kind==4)
    requested,remaining,quantity,price,new_filled,new_notional,new_fees,executed=fill(
        kind,remaining,dollars,requested,filled,notional,fees,q,bid,ask,valid,buy_capacity,sell_capacity,FEE,MINFEE)
    value=quantity*price;fee_delta=new_fees-fees
    first_fill=executed&(filled==0);first=tl.where(first_fill,clock,first)
    entry_fill=first_fill&(kind==1);add_fill=first_fill&(kind==2);episode=tl.where(entry_fill,realized,episode)
    sold=tl.where(sell,quantity,0.);bought=tl.where(buy,quantity,0.);new_q=q-sold+bought
    new_avg=avg
    if tl.sum((bought>0).to(tl.int32),0)>0:new_avg=tl.where(bought>0,(q*avg+value)/tl.maximum(new_q,1.e-12),avg)
    new_avg=tl.where(new_q>0,new_avg,0.);realized=realized+sold*(price-avg)-fee_delta-value*COST
    stop=tl.where(entry_fill,tl.where(swingmode,initial,price*(1-sf)),stop)
    target=tl.where(entry_fill,price*(1+tf),target);water=tl.where(entry_fill,price,water)
    adds=tl.where(entry_fill,0.,adds+add_fill.to(tl.float64));last=tl.where(executed,clock,last)
    remaining=tl.maximum(remaining-quantity,0.);complete=(kind!=0)&(requested>0)&(remaining<=1.e-12)
    target=tl.where(complete&(target_order>0),target+avg*tf,target)
    if FUSED:
        active=tl.load(ACTIVE);closed=(q>1.e-12)&(new_q<=1.e-12)&live
        profit=realized-old_episode
        mark=tl.load(MARK+session*MS[0]+candidate*MS[1]+i*MS[2],live,0.)
        mark=tl.where(tl.abs(mark)<float('inf'),mark,0.)
        equity=realized+new_q*(mark-new_avg)
        risk=new_q*tl.maximum(new_avg-stop,0.);distance=tl.full((BLOCK,),0.,tl.float64)
        if tl.sum((new_q>0).to(tl.int32),0)>0:distance=tl.where(new_q>0,tl.maximum(new_avg-stop,0.)/tl.maximum(new_avg,1.e-12),0.)
        # Match the separate accounting oracle's subtraction and reduction order.
        old_filled=tl.load(op+4,live,0.);old_notional=tl.load(op+5,live,0.)
        delta_qty=tl.where(new,new_filled,new_filled-old_filled)
        delta_value=tl.where(new,new_notional,new_notional-old_notional)
        first_actual=executed&(new_filled==delta_qty)
        part=PART+(row*TILES+tl.program_id(1))*15
        tl.store(part,tl.sum(tl.where(live,equity,0.),0))
        tl.store(part+1,tl.sum(tl.where(live,new_q*mark,0.),0));tl.store(part+2,tl.sum(risk,0))
        tl.store(part+3,tl.sum((first_actual&(kind==1)).to(tl.float64),0))
        tl.store(part+4,tl.sum((first_actual&(kind==2)).to(tl.float64),0))
        tl.store(part+5,tl.sum((first_actual&(kind==3)).to(tl.float64),0))
        tl.store(part+6,tl.sum((new_q>1.e-12).to(tl.float64),0))
        tl.store(part+7,tl.min(tl.where(closed,profit,float('inf')),0))
        tl.store(part+8,tl.sum(closed.to(tl.float64),0));tl.store(part+9,tl.sum((closed&(profit>0)).to(tl.float64),0))
        tl.store(part+10,tl.sum(tl.where(executed,delta_value,0.),0))
        tl.store(part+11,tl.max(risk,0));tl.store(part+12,tl.max(distance,0));tl.store(part+13,0.);tl.store(part+14,0.)
        entry_clock=tl.load(OPENED+at,live,0.)
        tl.store(ENTRIES+at,entry_clock,live);tl.store(CLOSED+at,closed&active,live);tl.store(PROFIT+at,profit,live)
        tl.store(OPENED+at,tl.where((q<=1.e-12)&(new_q>1.e-12),clock,entry_clock),live&active)
        peak=tl.maximum(tl.load(TICKER+at*2,live,0.),equity)
        dd=tl.maximum(tl.load(TICKER+at*2+1,live,0.),peak-equity)
        tl.store(TICKER+at*2,peak,live&active);tl.store(TICKER+at*2+1,dd,live&active)
        delta=new_q-q
        tl.store(BOOK+at*3,tl.where(newer,stamp,old_stamp),live&active)
        tl.store(BOOK+at*3+1,tl.maximum(buy_capacity-tl.maximum(delta,0.),0.),live&active)
        tl.store(BOOK+at*3+2,tl.maximum(sell_capacity-tl.maximum(-delta,0.),0.),live&active)
    tl.store(REC+at*17,kind,live);tl.store(REC+at*17+1,requested,live);tl.store(REC+at*17+2,new_filled,live)
    tl.store(REC+at*17+3,new_notional,live);tl.store(REC+at*17+4,new_fees,live);tl.store(REC+at*17+5,submitted,live)
    tl.store(REC+at*17+6,first,live);tl.store(REC+at*17+7,clock,live);tl.store(DONE+at,complete,live)
    if FUSED:
        tl.store(DONE+at,complete&active,live)
        live=live&active
    completed_count+=complete.to(tl.float64);fill_count+=executed.to(tl.float64)
    kind=tl.where(complete,0.,kind)
    flatten=tl.where((new_q<=1.e-12)&(kind==0),False,flatten)
    output=PO+at*9
    tl.store(output,new_q,live);tl.store(output+1,new_avg,live);tl.store(output+2,stop,live);tl.store(output+3,target,live)
    tl.store(output+4,water,live);tl.store(output+5,adds,live);tl.store(output+6,last,live);tl.store(output+7,realized,live);tl.store(output+8,episode,live)
    out=OO+at*17
    tl.store(out,kind,live);tl.store(out+1,remaining,live);tl.store(out+2,dollars,live);tl.store(out+3,requested,live)
    tl.store(out+4,new_filled,live);tl.store(out+5,new_notional,live);tl.store(out+6,new_fees,live);tl.store(out+7,submitted,live)
    tl.store(out+8,first,live);tl.store(out+9,initial,live);tl.store(out+10,flatten.to(tl.float64),live);tl.store(out+11,target_order,live)
    tl.store(out+12,submitted_count,live);tl.store(out+13,completed_count,live);tl.store(out+14,fill_count,live);tl.store(out+15,cancelled_stops,live)
    tl.store(out+16,amended_clock,live)


@torch.library.custom_op('vector_v59::persistent_orders',mutates_args=())
def transition(p:torch.Tensor,o:torch.Tensor,signals:torch.Tensor,ratios:torch.Tensor,policy:torch.Tensor,
               previous:torch.Tensor,membership:torch.Tensor,swing:torch.Tensor,bid:torch.Tensor,ask:torch.Tensor,
               valid:torch.Tensor,capacity:torch.Tensor,clock:torch.Tensor,terminal:torch.Tensor,
               entry:float,addition:float,fee:float,minimum_fee:float,cost:float,minimum:float)->tuple[torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor]:
    s,b,u,_=p.shape
    if not p.is_contiguous() or not o.is_contiguous():raise ValueError('Contiguous persistent execution buffers required')
    vectors=[x.expand(s,b,u) for x in (signals,previous,membership,swing,bid,ask,valid)]+[capacity.expand(s,b,u,2)]
    policy=policy.expand(s,b,u,9)
    out=torch.empty_like(p);orders=torch.empty_like(o);complete=torch.empty(p.shape[:-1],device=p.device,dtype=torch.bool)
    record=torch.empty((*p.shape[:-1],17),device=p.device,dtype=p.dtype)
    order_fill_kernel[(s*b,triton.cdiv(u,128))](p,o,vectors[0],ratios,policy,*vectors[1:],clock,terminal,out,orders,complete,record,
        u,b,tuple(x.stride() for x in vectors),policy.stride(),ratios.stride(),entry,addition,fee,minimum_fee,cost,minimum,
        128,num_warps=4,enable_fp_fusion=False)
    return out,orders,complete,record


@transition.register_fake
def fake(p,o,signals,ratios,policy,previous,membership,swing,bid,ask,valid,capacity,clock,terminal,entry,addition,fee,minimum_fee,cost,minimum):
    return torch.empty_like(p),torch.empty_like(o),torch.empty(p.shape[:-1],device=p.device,dtype=torch.bool),torch.empty((*p.shape[:-1],17),device=p.device,dtype=p.dtype)
