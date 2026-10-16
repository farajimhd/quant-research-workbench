"""Append continuous period variants without changing any existing feature ID."""
from research.vectorized_backtest.v7.features import *
from research.vectorized_backtest.v7.features import CATALOG as ORIGINAL
from dataclasses import replace
ORIGINAL=tuple(replace(f,name=f.name.replace(".relative_to_close",".fraction_of_close"),lower=0.,upper=.5) if f.name.startswith("history.average_move.") and f.name.endswith(".relative_to_close") else f for f in ORIGINAL)
FEATURE_SEMANTICS="v21-magnitude-ratio-v1"

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
from .enriched_math import NAMES
EXTRA=tuple(Feature('enriched.'+n,'ratio',0. if any(k in n for k in ('volatility','range','location','fraction','coverage','percentile','log_','dispersion')) and 'change' not in n else -.5,
    30. if 'log_' in n else 100. if 'fee_ratio' in n else 1.) for n in NAMES)
CATALOG=(*LEGACY,*EXTRA)
# Original indicator certificates retain their immutable mathematical version.
VERSION='v16-continuous-period-bank-v1'
