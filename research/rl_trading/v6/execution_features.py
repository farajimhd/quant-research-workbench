"""Versioned unit adapter and causal, size-conditioned execution estimates.

Stored certified candle banks remain unchanged. Bps are reporting units;
training-only mean/std normalization is applied at the encoder boundary.
Absolute log(close/USD) remains explicitly an absolute-price channel.
"""
import numpy as np
import torch
from research.rl_trading.v6.features import SCALAR_NAMES, LEVEL_NAMES
from research.rl_trading.v6.tensor_broker import order_fee

VERSION = 'rl-v6-bps-execution-features-v1'
PROBE_BUDGETS = (100., 1000., 10000.)
EXECUTION_NAMES = ('spread_bps','buy_slippage_bps','quote_age_seconds',
    'quote_valid','volume_valid', 'fill_fraction_100','fill_fraction_1000','fill_fraction_10000',
    'roundtrip_fee_bps_100','roundtrip_fee_bps_1000','roundtrip_fee_bps_10000')
EXECUTION_SCALE = (100.,100.,1.,1.,1.,1.,1.,1.,100.,100.,100.)


def bps_input(scalar, levels):
    """[C,37], [C,2,5,11] -> [C,147], differentiable and row-local.

    OH/L are bps from the same completed candle close. MACD/ATR and
    VWAP/EMA distances are bps from close. RSI is already a fraction.
    Level geometry is bps from close; counts/ages remain log quantities.
    No current-row transformation reads any next candle.
    """
    result=scalar.clone()
    close=scalar[:,SCALAR_NAMES.index('log_close')]
    valid=scalar[:,SCALAR_NAMES.index('bar_price_valid')].bool()
    for name in ('log_open','log_high','log_low'):
        i=SCALAR_NAMES.index(name)
        result[:,i]=torch.where(valid,torch.expm1(scalar[:,i]-close)*10000,0.)
    for name in ('bar_vwap_rel','session_vwap_rel','macd_line_rel','macd_signal_rel',
                 'atr_14_rel','ema_7_rel','ema_26_rel'):
        i=SCALAR_NAMES.index(name);result[:,i]=scalar[:,i]*10000
    geometry=levels.clone();geometry[:,:,:,:3]=levels[:,:,:,:3]*10000
    return torch.cat((result,geometry.flatten(1)),dim=1)


def execution_estimates(reference,bid,ask,vwap,volume,quote_valid,volume_valid,
                        quote_age_seconds,*,participation=.1,capacity=None):
    """All inputs [N] -> [N,11] causal estimates for three dollar budgets.

    Consume completed trailing evidence only. VWAP adverse move is an
    explicit participation-model slippage proxy, not an order-book impact
    forecast. No synthetic cost is supplied when quote/liquidity is missing.
    Fees use the broker's actual minimum/cap/regulatory fee function.
    """
    if not 0<participation<=1:raise ValueError('Invalid participation')
    good=quote_valid.bool() & (bid>0) & (ask>=bid) & (reference>0) & (quote_age_seconds>=0) & (quote_age_seconds<=1)
    liquid=volume_valid.bool() & (volume>=0) & ((vwap>0)|(volume==0))
    mid=(bid+ask)*.5
    safe=mid.clamp_min(1e-12)
    spread=torch.where(good,(ask-bid)/safe*10000,0.)
    adverse=torch.where(good&liquid,(vwap/safe-1).clamp_min(0)*10000,0.)
    price=torch.where(volume>0,vwap+(ask-bid)*.5,ask).clamp_min(1e-12)
    budgets=reference.new_tensor(PROBE_BUDGETS)[None,:]
    requested=affordable_buy_quantity(price[:,None],budgets)
    capacity=torch.floor(volume*participation) if capacity is None else capacity
    fill=torch.minimum(requested,capacity[:,None]).clamp_min(0)
    fill=torch.where((good&liquid)[:,None],fill,0.)
    fraction=torch.where(requested>0,fill/requested.clamp_min(1),0.)
    notional=fill*price[:,None]
    fees=order_fee(fill,notional,False)+order_fee(fill,notional,True)
    fee_bps=torch.where((good&liquid)[:,None]&(notional>0),fees/notional.clamp_min(1e-12)*10000,0.)
    age=torch.where(good,quote_age_seconds,0.)
    return torch.cat((spread[:,None],adverse[:,None],age[:,None],good[:,None],liquid[:,None],fraction,fee_bps),dim=1).float()


def affordable_buy_quantity(price,budget):
    """Broadcast [N,B] prices/budgets -> integer-valued quantities, no loop.

    The canonical buy commission has three affine regimes (cap, minimum,
    per-share) plus its per-share regulatory charge. Test each regime's
    root with the actual fee function; only affordable roots may survive.
    """
    roots=torch.stack((torch.floor(budget/(price*1.01+.000003)),
        torch.floor((budget-1)/(price+.000003)),
        torch.floor(budget/(price+.005+.000003))),dim=-1).clamp_min(0)
    total=roots*price[...,None]+order_fee(roots,roots*price[...,None],False)
    return torch.where(total<=budget[...,None],roots,0.).amax(-1)


def net_execution_bps(entry_price,exit_price,entry_capacity,exit_capacity,valid,*,budget=1000.):
    """[K] future execution estimates -> [K] net bps / proposed capital.

    Both sides must have certified evidence. Only matched round-trip capacity
    contributes; the remaining budget earns zero. This is a hypothetical
    participation/VWAP estimate, not realized P&L or a claim of full execution.
    Missing evidence is NaN, while known zero capacity has score zero.
    """
    if not np.isfinite(budget) or budget<=0:raise ValueError('Invalid probe budget')
    usable=valid.bool()&(entry_price>0)&(exit_price>0)
    q=torch.minimum(affordable_buy_quantity(entry_price.clamp_min(1e-12),entry_price.new_tensor(budget)),
        torch.minimum(entry_capacity,exit_capacity)).clamp_min(0)
    buy=q*entry_price;sell=q*exit_price
    net=sell-buy-order_fee(q,buy,False)-order_fee(q,sell,True)
    known_zero=valid.bool()&((entry_capacity==0)|(exit_capacity==0))
    return torch.where(known_zero,0.,torch.where(usable,net/budget*10000,torch.full_like(net,float('nan'))))
