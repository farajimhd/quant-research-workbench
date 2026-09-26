"""Pinned IBKR US SmartRouted Fixed cost scenario, distinct from market slippage.

Published schedule observed 2026-09-26. Applied as a current-cost replay scenario,
not a claim to reconstruct historical invoices, taxes, or routing-specific fees.
"""
import math

PROFILE = 'ibkr_us_fixed_20260926'
SCHEDULE = dict(profile=PROFILE,as_of='2026-09-26',currency='USD',
    commission_per_share=.005,commission_minimum=1.,commission_notional_cap=.01,
    sec_sell_notional_ratio=.0000206,finra_taf_sell_per_share=.000195,
    finra_taf_maximum=9.79,cat_per_share=.000003,
    scope='US exchange-listed whole-share stocks; SmartRouting; current-cost replay scenario',
    rounding='unrounded components; invoice aggregation/rounding not reconstructed',
    source='https://www.interactivebrokers.com/en/pricing/commissions-stocks.php',
    sec_source='https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2')


def charges(quantity, price, side, config):
    if (not math.isfinite(quantity) or quantity < 0 or not math.isfinite(price)
            or price <= 0 or side not in (-1,1)):
        raise ValueError('Invalid commission inputs')
    components = dict(commission=0.,sec=0.,taf=0.,cat=0.,venue=0.)
    if not quantity:
        return components
    notional = quantity*price
    if config.commission_model == 'research':
        components['commission'] = max(config.minimum_fee,
            notional*config.fee_ratio+quantity*config.fee_per_share)
    elif config.commission_model == PROFILE:
        components['commission'] = min(max(1.,quantity*.005),notional*.01)
        if side == -1:
            components['sec'] = notional*.0000206
            components['taf'] = min(9.79,quantity*.000195)
        components['cat'] = quantity*.000003
    else:
        raise ValueError('Unknown commission model')
    components['venue'] = quantity*config.extra_venue_fee_per_share
    return components
