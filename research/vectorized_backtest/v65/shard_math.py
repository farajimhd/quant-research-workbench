"""V65 scalar features from completed ARTE bars, with explicit session warmup."""
import numpy as np
import polars as pl
from .features import BASE, VARIANTS
from .indicator_math import calculate, initial_state
from .enriched_math import own


def rolling_sum(values, width):
    cumulative = np.r_[0., np.cumsum(values, dtype=np.float64)]
    return cumulative[1:] - cumulative[np.maximum(0, np.arange(len(values))+1-width)]


def scalar_features(frame, clocks, targets, origin):
    """One ticker at a time; no interpolation of missing price observations."""
    n = len(clocks)
    dense = {k: np.zeros(n, dtype=np.float64) for k in
             ('open','high','low','close','volume','trade_count','notional')}
    observed = np.zeros(n, bool); extremes = np.zeros(n, bool)
    ix = (frame['time_us'].to_numpy()//1000000-clocks[0]).astype(np.int64)
    if np.any((ix < 0) | (ix >= n)) or len(np.unique(ix)) != len(ix):
        raise ValueError('Trade rows outside unique completed-second grid')
    for k in dense: dense[k][ix] = frame[k].to_numpy()
    observed[ix] = frame['price_valid'].to_numpy() == 1
    extremes[ix] = frame['extremes_valid'].to_numpy() == 1
    observed &= np.isfinite(dense['close']) & (dense['close'] > 0)
    state, counts = initial_state()
    indicators, _, _ = calculate(dense['close'], dense['high'], dense['low'],
        observed, extremes, state, counts, 1., np.ones(n, bool))
    base = np.full((n, len(BASE)), np.nan, dtype=np.float32)
    names = {f.name:i for i,f in enumerate(BASE)}
    def put(name, value): base[:, names[name]] = value
    for k in ('open','high','low','close'):
        mask = extremes if k in ('high','low') else observed
        put('log_'+k, np.where(mask & (dense[k]>0), np.log(np.maximum(dense[k],1e-30)), np.nan))
    close = np.where(observed, dense['close'], np.nan)
    volume, notional = dense['volume'], dense['notional']
    bar = np.divide(notional, volume, out=np.full(n,np.nan), where=volume>0)
    cumulative = np.cumsum(volume)
    session = np.divide(np.cumsum(notional), cumulative, out=np.full(n,np.nan), where=cumulative>0)
    put('bar_vwap_rel',bar/close-1);put('session_vwap_rel',session/close-1)
    put('bar_vwap_valid',np.isfinite(bar));put('session_vwap_valid',np.isfinite(session))
    for k, short in (('volume','volume'),('trade_count','trades')):
        put('log_'+short,np.log1p(dense[k]))
        put('log_'+short+'_60s',np.log1p(rolling_sum(dense[k],60)))
    lookup = {f.name:i for i,f in enumerate(VARIANTS)}
    # Exact period-bank equations; V65 explicitly starts state at session open.
    mappings = {'ema_7_rel':'indicator.ema.7.relative',
                'ema_26_rel':'indicator.ema.26.relative',
                'rsi_14':'indicator.rsi.14', 'atr_14_rel':'indicator.atr.14.relative',
                'macd_line_rel':'indicator.macd_line.12.26.relative',
                'macd_signal_rel':'indicator.macd_signal.12.26.9.relative'}
    for key, variant in mappings.items():
        if variant not in lookup: raise ValueError('Unknown period feature '+variant)
        put(key, indicators[:,lookup[variant]])
    put('indicator_available',np.isfinite(base[:,[names[k] for k in mappings]]).all(1))
    put('bar_price_valid',observed);put('bar_extremes_valid',extremes)
    put('rvol_10s_available',np.zeros(n))
    # RVOL requires same-clock prior-session evidence, supplied separately.
    enriched = own(frame.with_columns((pl.col('time_us')//1000000).alias('second')),
                   clocks[targets], origin)
    return base[targets], indicators[targets], enriched, dense, observed, extremes
