"""Persistent quantity orders and actual-fill accounting, independent of fitness.

One outstanding order per candidate/listing prevents duplicate liquidity use.
Flatten requests are latched behind outstanding orders. The strategy controller
cancels an unfilled entry if its original stop is breached before acceptance.
All operations are tensor expressions so the same oracle can be CUDA compiled.
"""
import torch

VERSION = 'minimal-close-limit-v1'
# kind, remaining, requested dollars, requested qty, filled qty, notional, fee,
# submitted clock, first fill, initial stop, flatten requested, target reduction,
# submitted order count, completed order count, fill count, cancellations, last amendment clock
ORDER_WIDTH = 17
ENTRY, ADD, REDUCE, EXIT = 1, 2, 3, 4


def transition(position, orders, *, signals, ratios, policy, mark, previous,
               membership, observed, swing, bid, ask, quote_valid, capacity,
               clock, terminal, entry_dollars, add_dollars, fee_per_share,
               minimum_order_fee, cost, minimum_order_dollars):
    """Strategy requests then independent fills at the same candle boundary.

Returns position, order state, and completed-order records. Price/quantity
fields are never derived from a later bar than the current execution clock.
"""
    q, avg, stop, target, water, adds, last, realized, episode = position.unbind(-1)
    (kind, remaining, dollars, requested, filled, notional, fees, submitted,
     first, initial, flatten, target_order, submitted_count, completed_count,
     fill_count, cancelled_stops, order_limit) = orders.unbind(-1)
    # Strategy-side request validation and amendment. Fills below use only
    # requested quantity, quote/size, fees and already-owned inventory.
    ratios=torch.where(torch.isfinite(ratios)&(ratios>=0)&(ratios<=1),ratios,0.)
    held=q>1.e-12
    finite=torch.isfinite(previous)&(previous>0)
    stopped=held&finite&(previous<=stop)
    fresh=quote_valid&torch.isfinite(bid)&torch.isfinite(ask)&(bid>0)&(ask>=bid)
    proposed_stop=torch.where(kind==ENTRY,initial,torch.where(policy[...,8]>0,swing,ask*(1-policy[...,0])))
    feasible=fresh&torch.isfinite(proposed_stop)&(proposed_stop>0)&(proposed_stop<ask)&(proposed_stop<ask*(1+policy[...,9]))
    pending_buy=(kind==ENTRY)|(kind==ADD)
    cancelled=(pending_buy & (terminal|stopped|((signals&2)!=0)|((signals&8)!=0))) | ((kind==ENTRY)&(filled==0)&fresh&~feasible)
    cancelled_record=torch.stack((kind,requested,filled,notional,fees,submitted,first,clock.expand_as(q)),-1)
    flatten=torch.where(cancelled&~pending_buy,0.,flatten)
    cancelled_stops=cancelled_stops+cancelled.to(position.dtype)
    completed_count=completed_count+cancelled.to(position.dtype)
    kind=torch.where(cancelled,0.,kind)
    remaining=torch.where(cancelled,0.,remaining)
    requested=torch.where(cancelled,0.,requested)
    dollars=torch.where(cancelled,0.,dollars)
    # The controller leaves standing size unchanged; no model order-management protocol.
    held = q > 1.e-12
    busy = kind != 0
    finite = torch.isfinite(previous) & (previous > 0)
    ratchet = held & ((signals & 16) != 0) & finite & ~terminal
    water = torch.where(ratchet, torch.maximum(water, previous), water)
    stop = torch.where(ratchet, torch.maximum(stop, water * (1-policy[...,3])), stop)
    exit_request = held & (terminal | ((signals & 2) != 0) | (finite & (previous <= stop)))
    flatten = (flatten > 0) | exit_request | (terminal & busy)
    idle = ~busy
    managed = (clock-last) >= policy[...,7]
    gain = torch.where(held & finite, previous / avg.clamp_min(1.e-12)-1, 0.)
    target_hit = held & finite & (previous >= target)
    reduction = torch.where(target_hit, policy[...,2], ratios[...,2]).clamp(0,1)
    exit_new = idle & held & flatten
    reduce_new = (idle & held & ~flatten & ~terminal & ~cancelled & managed & (reduction > 0) &
                  ((((signals & 8) != 0) & (gain >= policy[...,5])) | target_hit))
    add_new = (idle & held & ~flatten & ~reduce_new & ~terminal & managed &
               ((signals & 4) != 0) & (gain >= policy[...,4]) & (adds < policy[...,6]))
    entry_new = idle & ~held & ~terminal & ~cancelled & membership & ((signals & 1) != 0) & ((signals&2)==0) & feasible
    # While flat, last is the final liquidation fill clock. Initial lanes have
    # last=0; partial exits remain held and cannot restart the timer early.
    entry_new = entry_new & ((last <= 0) | ((clock-last) >= 3))
    entry_amount = entry_dollars * ratios[...,0].clamp(0,1)
    add_amount = add_dollars * ratios[...,1].clamp(0,1)
    entry_new = entry_new & (entry_amount > 0) & (entry_amount >= minimum_order_dollars)
    add_new = add_new & (add_amount > 0) & (add_amount >= minimum_order_dollars)
    # An invalid initial stop cannot define an accepted entry request.
    entry_new = entry_new & ((policy[...,8] <= 0) | (torch.isfinite(swing) & (swing > 0) &
        quote_valid & torch.isfinite(ask) & (swing < ask)))
    new_kind = torch.where(exit_new, EXIT, torch.where(reduce_new, REDUCE,
                    torch.where(add_new, ADD, torch.where(entry_new, ENTRY, 0))))
    new = new_kind != 0
    kind = torch.where(new, new_kind, kind)
    dollars = torch.where(entry_new, entry_amount, torch.where(add_new, add_amount, dollars))
    requested = torch.where(exit_new, q, torch.where(reduce_new, q*reduction,
                                torch.where(entry_new|add_new, 0., requested)))
    remaining = torch.where(new, requested, remaining)
    filled = torch.where(new, 0., filled);notional = torch.where(new, 0., notional)
    fees = torch.where(new, 0., fees);submitted = torch.where(new, clock, submitted)
    first = torch.where(new, -1., first)
    initial = torch.where(entry_new, proposed_stop, initial)
    target_order = torch.where(new, (reduce_new & target_hit).to(position.dtype), target_order)
    submitted_count = submitted_count + new.to(position.dtype)
    proposed_limit=torch.where((new_kind==ENTRY)|(new_kind==ADD),ask*(1+policy[...,9]),bid*(1-policy[...,10]))
    proposed_limit=torch.where(terminal,bid,proposed_limit)
    order_limit=torch.where(new,proposed_limit,order_limit)
    buy = (kind == ENTRY) | (kind == ADD)
    sell = (kind == REDUCE) | (kind == EXIT)
    order_limit=torch.where(terminal&sell,bid,order_limit)
    marketable=torch.where(buy,ask<=order_limit,bid>=order_limit)
    from .order_liquidity import fill
    requested,remaining,quantity,price,new_filled,new_notional,new_fees,executed=fill(
        kind,remaining,dollars,requested,filled,notional,fees,q,bid,ask,quote_valid&marketable,capacity,fee_per_share,minimum_order_fee)
    value=quantity*price
    incremental_fee = new_fees-fees
    first_fill = executed & (filled == 0)
    first = torch.where(first_fill, clock, first)
    entry_fill = first_fill & (kind == ENTRY)
    add_fill = first_fill & (kind == ADD)
    episode = torch.where(entry_fill, realized, episode)
    sold = torch.where(sell, quantity, 0.);bought = torch.where(buy, quantity, 0.)
    new_q = q-sold+bought
    new_avg = torch.where(bought > 0, (q*avg+value)/new_q.clamp_min(1.e-12), avg)
    new_avg = torch.where(new_q > 0, new_avg, 0.)
    realized = realized + sold*(price-avg)-incremental_fee-value*cost
    stop = torch.where(entry_fill, initial, stop)
    target = torch.where(entry_fill, price*(1+policy[...,1]), target)
    water = torch.where(entry_fill, price, water)
    adds = torch.where(entry_fill, 0., adds+add_fill.to(position.dtype))
    last = torch.where(executed, clock, last)
    remaining = (remaining-quantity).clamp_min(0)
    complete = (kind != 0) & (requested > 0) & (remaining <= 1.e-12)
    target = torch.where(complete & (target_order > 0), target+avg*policy[...,1], target)
    # An order completion record contains its actual VWAP, never a quoted proxy.
    record = torch.stack((kind, requested, new_filled, new_notional,
                          new_fees, submitted, first, clock.expand_as(q)), -1)
    record=torch.cat((record,cancelled_record,cancelled[...,None].to(position.dtype)),-1)
    completed_count = completed_count+complete.to(position.dtype)
    fill_count = fill_count+executed.to(position.dtype)
    kind = torch.where(complete, 0., kind)
    flatten = torch.where((new_q <= 1.e-12) & (kind == 0), False, flatten)
    out_orders = torch.stack((kind, remaining, dollars, requested, new_filled,
        new_notional, new_fees, submitted, first, initial, flatten.to(position.dtype),
        target_order, submitted_count, completed_count, fill_count, cancelled_stops,order_limit), -1)
    out_position = torch.stack((new_q,new_avg,stop,target,water,adds,last,realized,episode),-1)
    return out_position, out_orders, complete, record


def require_complete(position, orders):
    """A missing execution tail blocks the replay, never candidate selection."""
    pending = orders[...,0] != 0
    held = position[...,0] > 1.e-12
    if not bool(torch.isfinite(position).all() & torch.isfinite(orders).all()):
        raise RuntimeError('Nonfinite execution state; no fitness published')
    if bool((pending | held).any()):
        indexes=(pending|held).nonzero().cpu().tolist()
        examples=[]
        for index in indexes[:8]:
            key=tuple(index)
            examples.append(dict(index=index,held=float(position[key][0]),kind=int(orders[key][0]),remaining=float(orders[key][1])))
        raise RuntimeError(f'Execution coverage exhausted with {len(indexes)} outstanding candidate/listing lanes; no fitness published; examples={examples}')
