from .authority import MAXIMUM_SPREAD_BPS, INITIAL_STOP_BID_MARGIN
"""Compiled chronological accounting on a dynamic set of ticker obligations.

No feature, network, swing, policy, or indicator is accepted by this module.
Requests already contain prices. Each candidate has an independent quote book.
"""
import numpy as np
from numba import njit,prange
from .request_guard import may_request_add,entry_quantity,whole_quantity,applicable_signals,valid_request_size,reduction_quantity

# Position: quantity, average, stop, target, realized, episode baseline,
# entry clock, last-flat clock, last equity, peak, drawdown, last mark, branch.
P_WIDTH=15  # Last column: strategy adapter's filled-add count for this episode.
# Order: kind, remaining, requested, filled, notional, fee, submitted, first,
# limit, initial stop, target, penalty charged, liquidation latch, rank-loss start+1.
O_WIDTH=15  # Column 14 pins the submitting flat branch through partial fills.
# Diagnostics: submitted, blocked, price, stop, limit, quote, liquidity,
# inventory, partial fills, rank exits, rank exit seconds, cancelled, abstained.
D_WIDTH=23  # Additional cause counters reconcile to the two suppression totals.


@njit(nogil=True)
def _one(candidate,start,slots,signals,ratios,prices,owners,abstain,quotes,
         positions,orders,books,active,lookup,counts,stats,diagnostics,
         entry_dollars,add_dollars,fee_per_share,minimum_fee,cost,terminal_at,labels,supervised,capture_state):
    records=[(0.,)*9 for _ in range(0)]
    episodes=[(0.,)*5 for _ in range(0)]
    p=positions[candidate];o=orders[candidate];book=books[candidate]
    d=diagnostics[candidate];s=stats[candidate];count=counts[candidate]
    score=supervised[candidate]
    for tick in range(len(slots)):
        clock=start+tick;terminal=clock>=terminal_at;any_held=False
        for k in range(5):
            ticker=slots[tick,k]
            if ticker>=0 and lookup[candidate,ticker]<0:
                lookup[candidate,ticker]=count;active[candidate,count]=ticker;count+=1
        index=0
        while index<count:
            u=active[candidate,index];slot=-1;prior_blocked=d[1]
            for k in range(5):
                if slots[tick,k]==u:slot=k
            # Only this dynamic ticker is loaded from the execution-only bank.
            if isinstance(quotes,tuple):
                mark=quotes[0][tick,u];bid=quotes[1][tick,u];ask=quotes[2][tick,u]
                stamp=quotes[3][tick,u];bid_size=quotes[4][tick,u];ask_size=quotes[5][tick,u];valid=quotes[6][tick,u]
            else:
                mark,bid,ask,stamp,bid_size,ask_size,valid=quotes[tick,u]
            q=p[u,0];held=q>1e-12;kind=int(o[u,0])
            branch=np.int64(candidate)
            if slot>=0:
                branch=int(p[u,12]) if held else (int(o[u,14]) if kind==1 else int(owners[tick,candidate,slot]))
            sig=0 if slot<0 or terminal else signals[tick,branch,slot]
            sig=applicable_signals(sig,held)
            action=0;pair=-1;deadline=-1
            if clock<len(labels[0]):
                action=int(labels[0][clock,u]);pair=int(labels[1][clock,u]);deadline=int(labels[2][clock,u])
            # Charges describe requests, never change their execution or ownership.
            edge=sig & 15
            prior=int(score[u,4]) if pair==int(score[u,5]) else 0
            new_edge=edge & ~prior
            score[u,4]=edge;score[u,5]=pair
            aligned=action==2 and pair>=0 and (not held or pair==int(score[u,0]))
            if action>0 and (new_edge & (1|4))!=0 and not aligned:score[u,7]+=1
            # Accrue occupied capital after the paired exit window, including tail.
            if held and score[u,1]>=0 and score[u,13]>=0:
                elapsed=max(0.,clock-max(score[u,13],score[u,1]))
                score[u,9]+=elapsed*q*p[u,1]
            score[u,13]=clock
            if slot>=0:d[12]+=abstain[tick,branch,slot]
            fresh=valid>0 and np.isfinite(bid) and np.isfinite(ask) and bid>0 and ask>=bid
            if stamp>book[u,0]:
                book[u,0]=stamp;book[u,1]=max(0.,ask_size);book[u,2]=max(0.,bid_size)
            spread_ok=fresh and (ask-bid)<=bid*(MAXIMUM_SPREAD_BPS/10000.)
            rank_loss=slot<0 and held and not terminal
            stopped=held and np.isfinite(mark) and mark<=p[u,2]
            targeted=held and np.isfinite(mark) and mark>=p[u,3]
            lifecycle=rank_loss or terminal or stopped or targeted or o[u,12]>0
            flatten=lifecycle or (sig&2)!=0
            strategy_exit=((not lifecycle and (new_edge & (2|8))!=0) or
                           (targeted and not rank_loss and not terminal and not stopped and o[u,12]==0))
            if held and action>0 and strategy_exit:
                if action!=4 or pair!=int(score[u,0]):score[u,8]+=1
            if rank_loss and o[u,13]==0:
                d[9]+=1;o[u,13]=clock+1
            if lifecycle and held:o[u,12]=1.
            if (kind==1 or kind==2) and (flatten or slot<0 or (held and (sig&8)!=0)):
                records.append((float(u),float(kind),o[u,2],o[u,3],o[u,4],o[u,5],o[u,6],o[u,7],float(clock)))
                d[11]+=1;o[u,0]=0.;kind=0
            # A candidate's explicit stop update is applied verbatim if valid.
            if held and (sig&16)!=0 and slot>=0:
                update=prices[tick,branch,slot,4]
                if np.isfinite(update) and update>0 and np.isfinite(mark) and update<mark:
                    d[0]+=1;p[u,2]=update
                else:
                    d[15]+=1
                    if not np.isfinite(update) or update<=0:d[20]+=1
                    else:d[21]+=1
            if kind==0:
                new_kind=0;qty=0.;limit=0.;stop=0.;target=0.
                if held and flatten:
                    new_kind=4;qty=q
                    # Lifecycle/conditional marketable liquidation is explicit.
                    limit=max(bid,1e-300) if lifecycle else prices[tick,branch,slot,1]
                elif slot>=0 and not terminal:
                    if held and (sig&8)!=0:
                        new_kind=3;qty=reduction_quantity(q,ratios[tick,branch,slot,2]);limit=prices[tick,branch,slot,1]
                    elif held and (sig&4)!=0:
                        if may_request_add(p[u,14]):
                            new_kind=2;limit=prices[tick,branch,slot,0]
                            qty=entry_quantity(add_dollars,limit)
                        else:d[13]+=1
                    elif not held and (sig&1)!=0 and (sig&2)==0 and (p[u,7]<0 or clock-p[u,7]>=3):
                        new_kind=1;limit=prices[tick,branch,slot,0]
                        stop=prices[tick,branch,slot,2];target=prices[tick,branch,slot,3]
                        qty=entry_quantity(entry_dollars,limit)
                if new_kind in (1,2) and not spread_ok:
                    d[22]+=1;d[1]+=1;new_kind=0
                if new_kind!=0:
                    # Requests and inventory use whole shares, never display-only rounding.
                    qty=whole_quantity(qty)
                    # Candidate request boundary precedes submission and accounting.
                    if not valid_request_size(limit,qty):
                        if not np.isfinite(limit) or limit<=0:d[16]+=1
                        elif new_kind==1:d[17]+=1
                        elif new_kind==2:d[18]+=1
                        else:d[19]+=1
                        d[14]+=1;new_kind=0
                if new_kind!=0:
                    d[0]+=1
                    invalid=not np.isfinite(limit) or limit<=0 or not np.isfinite(qty) or qty<=1e-12
                    invalid_stop=new_kind==1 and (not np.isfinite(stop) or stop<=0 or stop>=limit or (not fresh or stop>bid-INITIAL_STOP_BID_MARGIN) or not np.isfinite(target) or target<=limit)
                    if invalid or invalid_stop:
                        d[1]+=1
                        if invalid:d[2]+=1
                        if invalid_stop:d[3]+=1
                    else:
                        if new_kind in (1,2):
                            score[u,10]=pair if aligned else -1
                            score[u,11]=deadline if aligned else -1
                            score[u,12]=1. if aligned else 0.
                        latch=o[u,12];rank_started=o[u,13]
                        for j in range(O_WIDTH):o[u,j]=0.
                        o[u,0]=new_kind;o[u,1]=qty;o[u,2]=qty;o[u,6]=clock;o[u,7]=-1
                        o[u,8]=limit;o[u,9]=stop;o[u,10]=target;o[u,12]=latch;o[u,13]=rank_started;o[u,14]=branch;kind=new_kind
            if kind==1 and fresh and o[u,9]>bid-INITIAL_STOP_BID_MARGIN:
                if o[u,11]==0:d[1]+=1;d[3]+=1
                records.append((float(u),float(kind),o[u,2],o[u,3],o[u,4],o[u,5],o[u,6],o[u,7],float(clock)))
                d[11]+=1;o[u,0]=0;kind=0
            if kind!=0:
                buy=kind==1 or kind==2
                # A rank-loss liquidation stays marketable; strategy limits stay fixed.
                if not buy and o[u,12]>0:o[u,8]=bid
                marketable=fresh and (ask<=o[u,8] if buy else bid>=o[u,8])
                if buy and not spread_ok:
                    if o[u,11]==0:d[22]+=1
                    marketable=False
                capacity=book[u,1] if buy else book[u,2]
                inventory=buy or q>1e-12
                blocked=not fresh or not marketable or capacity<1. or not inventory
                if blocked and o[u,11]==0 and o[u,3]<=1e-12:
                    d[1]+=1;o[u,11]=1
                    if not fresh:d[5]+=1
                    elif not marketable:d[4]+=1
                    elif not inventory:d[7]+=1
                    else:d[6]+=1
                quantity=np.floor(min(o[u,1],capacity)) if marketable and inventory else 0.
                if not buy:quantity=min(quantity,q)
                if quantity>1e-12:
                    price=ask if buy else bid;value=quantity*price
                    first=o[u,3]<=1e-12
                    if first:o[u,7]=clock
                    if kind==1 and first:
                        p[u,14]=0.
                        score[u,0]=score[u,10] if action==2 and pair==int(score[u,10]) else -1
                        score[u,1]=score[u,11] if score[u,0]>=0 else -1
                        if capture_state is not None:
                            if (score[u,0]>=0 and clock<terminal_at
                                and capture_state[3][int(o[u,6]),u] and capture_state[3][clock,u]):
                                key=np.int64(u)*np.int64(4294967296)+np.int64(score[u,0])
                                if key in capture_state[0]:
                                    captured_index=capture_state[0][key]
                                    if not capture_state[1][candidate,captured_index]:
                                        capture_state[1][candidate,captured_index]=True
                                        capture_state[2][candidate,u]+=1
                        score[u,2]=0.;score[u,3]=0.
                        p[u,5]=p[u,4];p[u,6]=clock;p[u,2]=o[u,9];p[u,3]=o[u,10]
                        p[u,12]=o[u,14]
                    total_fee=max(minimum_fee,(o[u,3]+quantity)*fee_per_share)
                    delta_fee=total_fee-o[u,5]
                    if buy:
                        score[u,2]+=value
                        if score[u,12]>0 and score[u,0]>=0 and action==2 and pair==int(score[u,0]):score[u,3]+=value
                        p[u,1]=(q*p[u,1]+value)/(q+quantity);p[u,0]=q+quantity;book[u,1]-=quantity
                    else:
                        p[u,4]+=quantity*(price-p[u,1]);p[u,0]=max(0.,q-quantity);book[u,2]-=quantity
                    p[u,4]-=delta_fee+value*cost
                    o[u,1]=max(0.,o[u,1]-quantity);o[u,3]+=quantity;o[u,4]+=value;o[u,5]=total_fee
                    s[4]+=1;s[11]+=value
                    if kind==2 and first:s[5]+=1;p[u,14]+=1
                    if kind==3 and first:s[6]+=1
                    if o[u,1]>1e-12:d[8]+=1
                    if held and p[u,0]<=1e-12:
                        profit=p[u,4]-p[u,5]
                        fraction=score[u,3]/score[u,2] if score[u,2]>0 else 0.
                        score[u,6]+=min(profit,0.)+max(profit,0.)*fraction
                        episodes.append((profit,float(u),p[u,12],p[u,6],float(clock)))
                        s[8]=profit if s[9]==0 else min(s[8],profit)
                        s[9]+=1;s[10]+=profit>0
                        p[u,7]=clock;p[u,1]=0.;p[u,14]=0.
                    if o[u,1]<=1e-12 or (not buy and p[u,0]<=1e-12):
                        records.append((float(u),float(kind),o[u,2],o[u,3],o[u,4],o[u,5],o[u,6],o[u,7],float(clock)))
                        if o[u,13]>0:d[10]+=clock-(o[u,13]-1)
                        o[u,0]=0
                        if p[u,0]<=1e-12:o[u,12]=0;o[u,13]=0
            if np.isfinite(mark) and mark>0:p[u,11]=mark
            equity=p[u,4]+p[u,0]*(p[u,11]-p[u,1])
            s[0]+=equity-p[u,8];p[u,8]=equity;p[u,9]=max(p[u,9],equity);p[u,10]=max(p[u,10],p[u,9]-equity)
            s[2]+=p[u,0]*p[u,11];risk=p[u,0]*max(0.,p[u,1]-p[u,2]);s[3]+=risk;s[12]=max(s[12],risk)
            if p[u,0]>1e-12 and p[u,1]>0:s[13]=max(s[13],max(0.,1-p[u,2]/p[u,1]))
            p[u,13]+=d[1]-prior_blocked
            any_held=any_held or p[u,0]>1e-12
            if slot<0 and p[u,0]<=1e-12 and o[u,0]==0:
                count-=1;other=active[candidate,count];active[candidate,index]=other
                lookup[candidate,other]=index;lookup[candidate,u]=-1
            else:index+=1
        s[16]=max(s[16],s[0]);s[1]=max(s[1],s[16]-s[0])
        s[17]+=count
        s[14]+=not any_held
        s[15]+=1
    counts[candidate]=count
    return records,episodes


@njit(nogil=True,parallel=True)
def advance(start,slots,signals,ratios,prices,owners,abstain,quotes,positions,orders,books,
            active,lookup,counts,stats,diagnostics,entry,addition,fee,minimum,cost,terminal,labels,supervised,capture_state=None):
    b=len(positions)
    records=[[(0.,)*9 for _ in range(0)] for _ in range(b)]
    episodes=[[(0.,)*5 for _ in range(0)] for _ in range(b)]
    for i in prange(b):
        records[i],episodes[i]=_one(i,start,slots,signals,ratios,prices,owners,abstain,quotes,
            positions,orders,books,active,lookup,counts,stats,diagnostics,entry,addition,fee,minimum,cost,terminal,labels,supervised,capture_state)
    return records,episodes


def state(candidates,listings):
    p=np.zeros((candidates,listings,P_WIDTH));p[:,:,7]=-1.
    books=np.zeros((candidates,listings,3));books[:,:,0]=-1.
    return (p,np.zeros((candidates,listings,O_WIDTH)),books,
            np.zeros((candidates,listings),np.int32),np.full((candidates,listings),-1,np.int32),
            np.zeros(candidates,np.int32),np.zeros((candidates,18)),np.zeros((candidates,D_WIDTH),np.int64))


def supervision_state(candidates,listings):
    result=np.zeros((candidates,listings,14))
    result[:,:,[0,1,5,10,11,13]]=-1.
    return result


def warmup():
    """Compile native accounting while the worker loads its first market session."""
    from time import perf_counter
    from numba import set_num_threads
    started=perf_counter();set_num_threads(4)
    slots=np.arange(5,dtype=np.int32)[None];shape=(1,1,5)
    quotes=tuple(np.ones((1,5),dtype=bool if i==6 else np.float64) for i in range(7))
    labels=(np.zeros((1,5),np.int8),np.full((1,5),-1,np.int32),np.full((1,5),-1,np.int32))
    for array in labels:array.flags.writeable=False
    advance(0,slots,np.zeros(shape,np.int16),np.ones((*shape,3)),np.ones((*shape,5)),
        np.zeros(shape,np.int64),np.zeros(shape,bool),quotes,*state(1,5),1000.,200.,0.,0.,0.,1,
        labels,supervision_state(1,5))
    from .price_references import confirmed_selected
    source=np.ones((3,5));rows,columns=np.nonzero(np.isfinite(source))
    confirmed_selected(source,np.tile(slots,(3,1)),rows,columns,1,1,True)
    return perf_counter()-started
