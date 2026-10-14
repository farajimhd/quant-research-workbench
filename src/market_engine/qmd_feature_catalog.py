"""Shared QMD market-feature identities; no strategy or research-bank dependency.

IDs 0..956 match the certified search catalog. IDs, units and masks are stable;
selection never changes calculation identity or the pinned source population.
"""
from dataclasses import dataclass
from .causal_market_history import SWING_WINDOWS

SCALARS=('log_open','log_high','log_low','log_close','bar_vwap_rel','session_vwap_rel','bar_vwap_valid','session_vwap_valid','log_volume','log_trades','log_volume_60s','log_trades_60s','log_rvol_10s_prev_session','rvol_10s_available','macd_line_rel','macd_signal_rel','rsi_14','atr_14_rel','ema_7_rel','ema_26_rel','indicator_available','time_of_day_sin','time_of_day_cos','premarket','regular','after_hours','log_inter_candle_gap','log_float_shares','float_present','log_shares_outstanding','shares_present','log_days_since_split','split_present','log_days_since_reverse_split','reverse_split_present','bar_price_valid','bar_extremes_valid')
LEVELS=('center_distance_rel','lower_distance_rel','upper_distance_rel','log_observation_count','log_today_observation_count','log_age_since_confirmation','role_support','role_resistance','role_transition','historical_origin','present')

@dataclass(frozen=True)
class Feature:
    name:str
    unit:str
    lower:float
    upper:float
    mask:int|None=None

def catalog():
    out=[]
    for i,name in enumerate(SCALARS):
        mask=None;unit='ratio';lo=-2.;hi=2.
        if name in ('log_open','log_high','log_low','log_close'):unit='log_price';lo=-5.;hi=10.;mask=35 if i in (0,3) else 36
        elif name.endswith(('_valid','_available','_present')) or name in ('premarket','regular','after_hours'):unit='bool';lo=0.;hi=1.
        elif name.startswith('log_'):
            unit='log_'+('shares' if 'volume' in name or 'shares' in name else 'count' if 'trades' in name else 'duration');lo=0.;hi=30.
        if i in (4,5):mask=6 if i==4 else 7
        elif i==12:mask=13;lo=0.;hi=20.
        elif 14<=i<=19:mask=20
        elif i in (27,29,31,33):mask=i+1
        if i==16:lo=0.;hi=1.
        out.append(Feature(name,unit,lo,hi,mask))
    for side in ('below','above'):
        for slot in range(1,6):
            start=len(out)
            for i,name in enumerate(LEVELS):
                unit='ratio' if i<3 else 'log_count' if i<5 else 'log_duration' if i==5 else 'bool'
                out.append(Feature(f'v7.{side}.{slot}.{name}',unit,-10. if i<3 else 0.,10. if i<3 else 30. if i<6 else 1.,None if i==10 else start+10))
    # Derived consumer inputs; the original 147 bank channels keep their IDs.
    out.extend(Feature(name,'bool',0.,1.) for name in ('split_this_session','reverse_split_this_session'))
    return tuple(out)

BASE=catalog()


HISTORY=[]
for family,windows in [('recent_high',range(1,61)),('momentum_close',range(1,61)),
                       ('attention_mean',range(1,61)),('average_move',range(2,33))]:
    for window in windows:
        unit='dollars' if family=='attention_mean' else 'price'
        HISTORY.append(Feature(f'history.{family}.{window}',unit,0.,1e7 if unit=='dollars' else 1e3))
for left in SWING_WINDOWS:
    for right in SWING_WINDOWS:
        HISTORY.append(Feature(f'history.swing_low.{left}.{right}','price',0.,1e3))
# Relative versions avoid absolute-price thresholds while raw values remain accessible.
RELATIVE=tuple(Feature(f'{f.name}.relative_to_close','ratio',-.5,.5) for f in HISTORY if f.unit=='price')
LIQUIDITY=(Feature('market.bid_relative','ratio',-.1,.1),Feature('market.ask_relative','ratio',-.1,.1),
           Feature('market.spread_fraction','ratio',0.,.1),Feature('market.quote_age_seconds','duration',0.,3600.),
           Feature('market.log_execution_volume','log_shares',0.,30.),Feature('market.log_execution_notional','log_dollars',0.,30.),
           Feature('market.log_execution_trade_count','log_count',0.,20.),
           Feature('market.bid','price',0.,1000.),Feature('market.ask','price',0.,1000.),
           Feature('market.spread','price',0.,100.),Feature('market.close','price',0.,1000.))
ORIGINAL=(*BASE,*HISTORY,*RELATIVE,*LIQUIDITY)

PERIODS=(2,3,5,7,9,10,12,14,15,20,26,30,45,50,60)
FAST=(3,7,12);SLOW=(15,26,50);SIGNAL=(5,9,20)
VARIANTS=[];SPECS=[]
for kind in ('ema','rsi','atr'):
    for p in PERIODS:
        for relative in ((False,True) if kind!='rsi' else (False,)):
            unit='ratio' if relative or kind=='rsi' else 'price'
            name=f'indicator.{kind}.{p}'+('.relative' if relative else '')
            limits=(-.5,.5) if kind=='ema' and relative else (0.,1.) if unit=='ratio' else (0.,1000.)
            VARIANTS.append(Feature(name,unit,*limits));SPECS.append((kind,p,0,0,relative))
for f in FAST:
    for s in SLOW:
        VARIANTS.append(Feature(f'indicator.macd_line.{f}.{s}.relative','ratio',-.5,.5));SPECS.append(('macd_line',f,s,0,True))
        for q in SIGNAL:
            for kind in ('macd_signal','macd_histogram'):
                VARIANTS.append(Feature(f'indicator.{kind}.{f}.{s}.{q}.relative','ratio',-.5,.5));SPECS.append((kind,f,s,q,True))
LEGACY=(*ORIGINAL,*VARIANTS)
from .causal_context_features import NAMES
EXTRA=tuple(Feature('enriched.'+n,'ratio',0. if any(k in n for k in ('volatility','range','location','fraction','coverage','percentile','log_','dispersion')) and 'change' not in n else -.5,
    30. if 'log_' in n else 100. if 'fee_ratio' in n else 1.) for n in NAMES)
CATALOG=(*LEGACY,*EXTRA)

BY_NAME={f.name:(i,f) for i,f in enumerate(CATALOG)}
