"""Versioned US equity order-fee proxy shared by teacher and replay.

The published IBKR Pro Tiered base schedule is known; the eventual venue,
monthly volume tier, order fills, and spread are not. This model fixes the
first monthly tier and zero venue fee, and records those assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math


VERSION = 'ibkr-pro-tiered-us-base-2026-09-v1'


@dataclass(frozen=True)
class OrderCosts:
    version: str = VERSION
    per_share: float = .0035
    minimum_order: float = .35
    maximum_fraction_of_value: float = .01
    fractional_minimum: float = .01
    fractional_fraction_of_value: float = .01
    clearing_per_share: float = .0002
    clearing_max_fraction_of_value: float = .005
    cat_per_share: float = .000003
    sec_fraction_of_sale_value: float = .0000206
    taf_per_share_sold: float = .000195
    taf_max_per_trade: float = 9.79
    nyse_pass_through_fraction: float = .000175
    finra_pass_through_fraction: float = .00056
    venue_fee_per_share: float = 0.

    def plan(self) -> dict:
        return {**asdict(self), 'monthly_tier_assumption': 'first tier, <=300000 shares',
            'routing_assumption': 'SmartRouted venue unknown; venue fee fixed at zero',
            'fractional_assumption': '1% applies only to fractional-share component',
            'rounding': 'unrounded research estimate',
            'fill_assumption': 'one complete fill per order at completed-bar price'}

    def fee(self, quantity: float, price: float, *, side: str) -> float:
        if (side not in ('buy', 'sell') or not math.isfinite(quantity) or quantity <= 0
                or not math.isfinite(price) or price <= 0):
            raise ValueError('Order fee requires a positive finite quantity and price')
        whole = math.floor(quantity + 1e-10)
        fraction = max(0., quantity-whole)
        whole_value = whole*price
        whole_commission = (min(max(whole*self.per_share,self.minimum_order),
            whole_value*self.maximum_fraction_of_value) if whole else 0.)
        fractional_commission = (max(self.fractional_minimum,
            fraction*price*self.fractional_fraction_of_value) if fraction > 1e-9 else 0.)
        commission = whole_commission+fractional_commission
        trade_value = quantity*price
        clearing = min(quantity*self.clearing_per_share,
            trade_value*self.clearing_max_fraction_of_value)
        regulatory = quantity*self.cat_per_share
        if side == 'sell':
            regulatory += trade_value*self.sec_fraction_of_sale_value
            regulatory += min(quantity*self.taf_per_share_sold,self.taf_max_per_trade)
        pass_through = commission*(self.nyse_pass_through_fraction+
            self.finra_pass_through_fraction)
        return (commission+clearing+regulatory+pass_through+
            quantity*self.venue_fee_per_share)

    def buy_for_budget(self, price: float, budget: float) -> tuple[float,float]:
        """A feasible fractional quantity with total debit within one unit."""
        if not math.isfinite(price) or price <= 0 or not math.isfinite(budget) or budget <= 0:
            raise ValueError('Buy price and budget must be finite and positive')
        quantity = budget/price
        for _ in range(32):
            fee = self.fee(quantity,price,side='buy')
            affordable = (budget-fee)/price
            if affordable <= 0:
                raise ValueError('Order minimum exceeds the buy budget')
            if quantity*price+fee <= budget+1e-9:
                break
            quantity = min(quantity,affordable)*(1-1e-12)
        fee = self.fee(quantity,price,side='buy')
        if quantity <= 0 or quantity*price+fee > budget+1e-8:
            raise ValueError('No affordable positive buy quantity')
        return quantity,fee


def from_plan(plan: dict | None) -> OrderCosts | None:
    if plan is None:
        return None
    if plan.get('version') != VERSION:
        raise ValueError('Unknown order-cost model version')
    model = OrderCosts(**{key:plan[key] for key in OrderCosts.__dataclass_fields__})
    if plan != model.plan():
        raise ValueError('Order-cost plan differs from the certified fee contract')
    return model
