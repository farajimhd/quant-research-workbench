"""Persistent quantity orders and actual-fill accounting, independent of fitness.

One outstanding order per candidate/listing prevents duplicate liquidity use.
Flatten requests are latched behind outstanding orders; none are cancelled.
All operations are tensor expressions so the same oracle can be CUDA compiled.
"""
import torch

VERSION = 'persistent-liquidity-orders-v1'
# kind, remaining, requested dollars, requested qty, filled qty, notional, fee,
# submitted clock, first fill, initial stop, flatten requested, target reduction,
# submitted order count, completed order count, fill count, last completion clock
ORDER_WIDTH = 16
ENTRY, ADD, REDUCE, EXIT = 1, 2, 3, 4


def transition(position, orders, *, signals, ratios, policy, mark, previous,
               membership, observed, swing, bid, ask, quote_valid, capacity,
               clock, terminal, entry_dollars, add_dollars, fee_per_share,
               minimum_order_fee, cost, minimum_order_dollars):
    """Submit at the decision boundary and fill from the subsequent bar.

Returns position, order state, and completed-order records. Price/quantity
fields are never derived from a later bar than the current execution clock.
"""
    q, avg, stop, target, water, adds, last, realized, episode = position.unbind(-1)
    (kind, remaining, dollars, requested, filled, notional, fees, submitted,
     first, initial, flatten, target_order, submitted_count, completed_count,
     fill_count, last_completed) = orders.unbind(-1)
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
    ratios=torch.where(torch.isfinite(ratios)&(ratios>=0)&(ratios<=1),ratios,0.)
    reduction = torch.where(target_hit, policy[...,2], ratios[...,2]).clamp(0,1)
    exit_new = idle & held & flatten
    reduce_new = (idle & held & ~flatten & ~terminal & managed & (reduction > 0) &
                  ((((signals & 8) != 0) & (gain >= policy[...,5])) | target_hit))
    add_new = (idle & held & ~flatten & ~reduce_new & ~terminal & managed &
               ((signals & 4) != 0) & (gain >= policy[...,4]) & (adds < policy[...,6]))
    entry_new = idle & ~held & ~terminal & membership & ((signals & 1) != 0)
    entry_amount = entry_dollars * ratios[...,0].clamp(0,1)
    add_amount = add_dollars * ratios[...,1].clamp(0,1)
    entry_new = entry_new & (entry_amount > 0) & (entry_amount >= minimum_order_dollars)
    add_new = add_new & (add_amount > 0) & (add_amount >= minimum_order_dollars)
    # A missing swing stop cannot define an entry request. Later quote changes
    # never silently discard an already accepted order.
    entry_new = entry_new & ((policy[...,8] <= 0) | (torch.isfinite(swing) & (swing > 0)))
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
    initial = torch.where(entry_new, swing, initial)
    target_order = torch.where(new, (reduce_new & target_hit).to(position.dtype), target_order)
    submitted_count = submitted_count + new.to(position.dtype)
    buy = (kind == ENTRY) | (kind == ADD)
    sell = (kind == REDUCE) | (kind == EXIT)
    fresh = (quote_valid & torch.isfinite(bid) & torch.isfinite(ask) &
             (bid > 0) & (ask >= bid))
    # Dollar intents become fixed-quantity orders at their first executable quote.
    activate = buy & (requested == 0) & fresh & torch.isfinite(capacity) & (capacity>0)
    requested = torch.where(activate, dollars / ask.clamp_min(1.e-12), requested)
    remaining = torch.where(activate, requested, remaining)
    available = torch.where(fresh & torch.isfinite(capacity), capacity.clamp_min(0), 0.)
    quantity = torch.minimum(remaining.clamp_min(0), available)
    quantity = torch.where(sell, torch.minimum(quantity, q), quantity)
    quantity = torch.where(kind != 0, quantity, 0.)
    executed = quantity > 0
    price = torch.where(buy, ask, bid)
    price = torch.where(fresh, price, 0.)
    value = quantity * price
    new_filled = filled + quantity;new_notional = notional + value
    new_fees = torch.where(executed, torch.maximum(new_filled*fee_per_share,
                          torch.full_like(fees, minimum_order_fee)), fees)
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
    stop = torch.where(entry_fill, torch.where(policy[...,8] > 0, initial, price*(1-policy[...,0])), stop)
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
    completed_count = completed_count+complete.to(position.dtype)
    fill_count = fill_count+executed.to(position.dtype)
    last_completed = torch.where(complete, clock, last_completed)
    kind = torch.where(complete, 0., kind)
    flatten = torch.where((new_q <= 1.e-12) & (kind == 0), False, flatten)
    out_orders = torch.stack((kind, remaining, dollars, requested, new_filled,
        new_notional, new_fees, submitted, first, initial, flatten.to(position.dtype),
        target_order, submitted_count, completed_count, fill_count, last_completed), -1)
    out_position = torch.stack((new_q,new_avg,stop,target,water,adds,last,realized,episode),-1)
    return out_position, out_orders, complete, record


def require_complete(position, orders):
    """A missing execution tail blocks the replay, never candidate selection."""
    pending = orders[...,0] != 0
    held = position[...,0] > 1.e-12
    if bool((pending | held).any()):
        raise RuntimeError('Execution coverage exhausted with outstanding orders or positions; no fitness published')
