"""Policy-free quantity fills: accepted orders plus available quote liquidity."""
import torch


def fill(kind, remaining, dollars, requested, filled, notional, fees, q,
         bid, ask, quote_valid, capacity, fee_per_share, minimum_order_fee):
    buy=(kind==1)|(kind==2);sell=(kind==3)|(kind==4)
    # Structural validity only; no strategy, policy, or age-based admission.
    fresh = (quote_valid & torch.isfinite(bid) & torch.isfinite(ask) &
             (bid > 0) & (ask >= bid))
    # Dollar intents become fixed-quantity orders at their first executable quote.
    buy_capacity=capacity[...,0];sell_capacity=capacity[...,1]
    activate = buy & (requested == 0) & fresh & torch.isfinite(buy_capacity) & (buy_capacity>1.e-12)
    requested = torch.where(activate, dollars / ask.clamp_min(1.e-12), requested)
    remaining = torch.where(activate, requested, remaining)
    capacity=torch.where(buy,buy_capacity,sell_capacity)
    available = torch.where(fresh & torch.isfinite(capacity), capacity.clamp_min(0), 0.)
    quantity = torch.minimum(remaining.clamp_min(0), available)
    quantity = torch.where(sell, torch.minimum(quantity, q), quantity)
    # Position/episode accounting treats <=1e-12 shares as numerical zero.
    # A consumed quote can leave sub-ulp dust; it must not incur an order fee.
    quantity = torch.where((kind != 0)&(quantity>1.e-12), quantity, 0.)
    executed = quantity > 0
    price = torch.where(buy, ask, bid)
    price = torch.where(fresh, price, 0.)
    value = quantity * price
    new_filled = filled + quantity;new_notional = notional + value
    new_fees = torch.where(executed, torch.maximum(new_filled*fee_per_share,
                          torch.full_like(fees, minimum_order_fee)), fees)
    return requested,remaining,quantity,price,new_filled,new_notional,new_fees,executed
