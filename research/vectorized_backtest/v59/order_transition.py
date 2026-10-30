"""Fused persistent-order CUDA transition; orders.transition is the oracle."""
import torch
import triton
import triton.language as tl


@triton.jit
def kernel(P,O,S,R,POL,PRE,MEM,SW,BID,ASK,V,CAP,CLOCK,TERM,PO,OO,DONE,REC,
           U:tl.constexpr,B:tl.constexpr,VS:tl.constexpr,PS:tl.constexpr,RS:tl.constexpr,
           ED:tl.constexpr,AD:tl.constexpr,FEE:tl.constexpr,MINFEE:tl.constexpr,COST:tl.constexpr,MINIMUM:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0);i=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK);live=i<U
    at=row*U+i;pp=P+at*9;op=O+at*16
    q=tl.load(pp,live,0.);avg=tl.load(pp+1,live,0.);stop=tl.load(pp+2,live,0.)
    target=tl.load(pp+3,live,0.);water=tl.load(pp+4,live,0.);adds=tl.load(pp+5,live,0.)
    last=tl.load(pp+6,live,0.);realized=tl.load(pp+7,live,0.);episode=tl.load(pp+8,live,0.)
    kind=tl.load(op,live,0.);remaining=tl.load(op+1,live,0.);dollars=tl.load(op+2,live,0.)
    requested=tl.load(op+3,live,0.);filled=tl.load(op+4,live,0.);notional=tl.load(op+5,live,0.)
    fees=tl.load(op+6,live,0.);submitted=tl.load(op+7,live,0.);first=tl.load(op+8,live,0.)
    initial=tl.load(op+9,live,0.);flatten=tl.load(op+10,live,0.)>0;target_order=tl.load(op+11,live,0.)
    submitted_count=tl.load(op+12,live,0.);completed_count=tl.load(op+13,live,0.)
    fill_count=tl.load(op+14,live,0.);last_completed=tl.load(op+15,live,0.)
    session=row//B;candidate=row%B
    sig=tl.load(S+session*VS[0][0]+candidate*VS[0][1]+i*VS[0][2],live,0)
    prev=tl.load(PRE+session*VS[1][0]+candidate*VS[1][1]+i*VS[1][2],live,0.)
    member=tl.load(MEM+session*VS[2][0]+candidate*VS[2][1]+i*VS[2][2],live,False)
    swing=tl.load(SW+session*VS[3][0]+candidate*VS[3][1]+i*VS[3][2],live,0.)
    bid=tl.load(BID+session*VS[4][0]+candidate*VS[4][1]+i*VS[4][2],live,0.)
    ask=tl.load(ASK+session*VS[5][0]+candidate*VS[5][1]+i*VS[5][2],live,0.)
    valid=tl.load(V+session*VS[6][0]+candidate*VS[6][1]+i*VS[6][2],live,False)
    capacity=tl.load(CAP+session*VS[7][0]+candidate*VS[7][1]+i*VS[7][2],live,0.)
    terminal=tl.load(TERM);clock=tl.load(CLOCK)
    pol=POL+session*PS[0]+candidate*PS[1]+i*PS[2]
    sf=tl.load(pol,live,0.);tf=tl.load(pol+PS[3],live,0.);rf=tl.load(pol+2*PS[3],live,0.)
    trail=tl.load(pol+3*PS[3],live,0.);addmin=tl.load(pol+4*PS[3],live,0.);redmin=tl.load(pol+5*PS[3],live,0.)
    maxadds=tl.load(pol+6*PS[3],live,0.);cooldown=tl.load(pol+7*PS[3],live,0.);swingmode=tl.load(pol+8*PS[3],live,0.)>0
    ratios=R+session*RS[0]+candidate*RS[1]+i*RS[2]
    r0=tl.load(ratios,live,0.);r1=tl.load(ratios+RS[3],live,0.);r2=tl.load(ratios+2*RS[3],live,0.)
    r0=tl.where((r0>=0)&(r0<=1),r0,0.);r1=tl.where((r1>=0)&(r1<=1),r1,0.);r2=tl.where((r2>=0)&(r2<=1),r2,0.)
    held=q>1.e-12;busy=kind!=0;finite=(prev>0)&(tl.abs(prev)<float('inf'))
    ratchet=held&((sig&16)!=0)&finite&~terminal
    water=tl.where(ratchet,tl.maximum(water,prev),water);stop=tl.where(ratchet,tl.maximum(stop,water*(1-trail)),stop)
    flatten=flatten|(held&(terminal|((sig&2)!=0)|(finite&(prev<=stop))))|(terminal&busy)
    managed=(clock-last)>=cooldown;gain=tl.full((BLOCK,),0.,tl.float64)
    if tl.sum((held&finite).to(tl.int32),0)>0:gain=tl.where(held&finite,prev/tl.maximum(avg,1.e-12)-1,0.)
    hit=held&finite&(prev>=target);reduction=tl.minimum(tl.maximum(tl.where(hit,rf,r2),0.),1.)
    exit_new=~busy&held&flatten
    reduce_new=~busy&held&~flatten&~terminal&managed&(reduction>0)&((((sig&8)!=0)&(gain>=redmin))|hit)
    add_new=~busy&held&~flatten&~reduce_new&~terminal&managed&((sig&4)!=0)&(gain>=addmin)&(adds<maxadds)
    entry_new=~busy&~held&~terminal&member&((sig&1)!=0)
    ea=ED*r0;aa=AD*r1
    entry_new=entry_new&(ea>0)&(ea>=MINIMUM)&(~swingmode|((swing>0)&(tl.abs(swing)<float('inf'))))
    add_new=add_new&(aa>0)&(aa>=MINIMUM)
    new_kind=tl.where(exit_new,4,tl.where(reduce_new,3,tl.where(add_new,2,tl.where(entry_new,1,0))))
    new=new_kind!=0;kind=tl.where(new,new_kind,kind)
    dollars=tl.where(entry_new,ea,tl.where(add_new,aa,dollars))
    requested=tl.where(exit_new,q,tl.where(reduce_new,q*reduction,tl.where(entry_new|add_new,0.,requested)))
    remaining=tl.where(new,requested,remaining);filled=tl.where(new,0.,filled);notional=tl.where(new,0.,notional)
    fees=tl.where(new,0.,fees);submitted=tl.where(new,clock,submitted);first=tl.where(new,-1.,first)
    initial=tl.where(entry_new,tl.where(swingmode,swing,0.),initial);target_order=tl.where(new,(reduce_new&hit).to(tl.float64),target_order)
    submitted_count+=new.to(tl.float64);buy=(kind==1)|(kind==2);sell=(kind==3)|(kind==4)
    fresh=valid&(bid>0)&(ask>=bid)&(tl.abs(bid)<float('inf'))&(tl.abs(ask)<float('inf'))
    capfinite=tl.abs(capacity)<float('inf');activate=buy&(requested==0)&fresh&capfinite&(capacity>0)
    if tl.sum(activate.to(tl.int32),0)>0:requested=tl.where(activate,dollars/tl.maximum(ask,1.e-12),requested)
    remaining=tl.where(activate,requested,remaining)
    available=tl.where(fresh&capfinite,tl.maximum(capacity,0.),0.)
    quantity=tl.minimum(tl.maximum(remaining,0.),available);quantity=tl.where(sell,tl.minimum(quantity,q),quantity)
    quantity=tl.where(kind!=0,quantity,0.);executed=quantity>0
    price=tl.where(fresh,tl.where(buy,ask,bid),0.);value=quantity*price
    new_filled=filled+quantity;new_notional=notional+value
    new_fees=tl.where(executed,tl.maximum(new_filled*FEE,MINFEE),fees);fee_delta=new_fees-fees
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
    tl.store(REC+at*8,kind,live);tl.store(REC+at*8+1,requested,live);tl.store(REC+at*8+2,new_filled,live)
    tl.store(REC+at*8+3,new_notional,live);tl.store(REC+at*8+4,new_fees,live);tl.store(REC+at*8+5,submitted,live)
    tl.store(REC+at*8+6,first,live);tl.store(REC+at*8+7,clock,live);tl.store(DONE+at,complete,live)
    completed_count+=complete.to(tl.float64);fill_count+=executed.to(tl.float64)
    last_completed=tl.where(complete,clock,last_completed);kind=tl.where(complete,0.,kind)
    flatten=tl.where((new_q<=1.e-12)&(kind==0),False,flatten)
    output=PO+at*9
    tl.store(output,new_q,live);tl.store(output+1,new_avg,live);tl.store(output+2,stop,live);tl.store(output+3,target,live)
    tl.store(output+4,water,live);tl.store(output+5,adds,live);tl.store(output+6,last,live);tl.store(output+7,realized,live);tl.store(output+8,episode,live)
    out=OO+at*16
    tl.store(out,kind,live);tl.store(out+1,remaining,live);tl.store(out+2,dollars,live);tl.store(out+3,requested,live)
    tl.store(out+4,new_filled,live);tl.store(out+5,new_notional,live);tl.store(out+6,new_fees,live);tl.store(out+7,submitted,live)
    tl.store(out+8,first,live);tl.store(out+9,initial,live);tl.store(out+10,flatten.to(tl.float64),live);tl.store(out+11,target_order,live)
    tl.store(out+12,submitted_count,live);tl.store(out+13,completed_count,live);tl.store(out+14,fill_count,live);tl.store(out+15,last_completed,live)


@torch.library.custom_op('vector_v59::persistent_orders',mutates_args=())
def transition(p:torch.Tensor,o:torch.Tensor,signals:torch.Tensor,ratios:torch.Tensor,policy:torch.Tensor,
               previous:torch.Tensor,membership:torch.Tensor,swing:torch.Tensor,bid:torch.Tensor,ask:torch.Tensor,
               valid:torch.Tensor,capacity:torch.Tensor,clock:torch.Tensor,terminal:torch.Tensor,
               entry:float,addition:float,fee:float,minimum_fee:float,cost:float,minimum:float)->tuple[torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor]:
    s,b,u,_=p.shape
    if not p.is_contiguous() or not o.is_contiguous():raise ValueError('Contiguous persistent execution buffers required')
    vectors=[x.expand(s,b,u) for x in (signals,previous,membership,swing,bid,ask,valid,capacity)]
    policy=policy.expand(s,b,u,9)
    out=torch.empty_like(p);orders=torch.empty_like(o);complete=torch.empty(p.shape[:-1],device=p.device,dtype=torch.bool)
    record=torch.empty((*p.shape[:-1],8),device=p.device,dtype=p.dtype)
    kernel[(s*b,triton.cdiv(u,128))](p,o,vectors[0],ratios,policy,*vectors[1:],clock,terminal,out,orders,complete,record,
        u,b,tuple(x.stride() for x in vectors),policy.stride(),ratios.stride(),entry,addition,fee,minimum_fee,cost,minimum,
        128,num_warps=4,enable_fp_fusion=False)
    return out,orders,complete,record


@transition.register_fake
def fake(p,o,signals,ratios,policy,previous,membership,swing,bid,ask,valid,capacity,clock,terminal,entry,addition,fee,minimum_fee,cost,minimum):
    return torch.empty_like(p),torch.empty_like(o),torch.empty(p.shape[:-1],device=p.device,dtype=torch.bool),torch.empty((*p.shape[:-1],8),device=p.device,dtype=p.dtype)
