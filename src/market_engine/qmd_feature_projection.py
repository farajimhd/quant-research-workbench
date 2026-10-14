"""Producer-side projection of the shared market-feature catalog.

Sources are supplied by the certified QMD producer, never by a strategy.
Transport and Backtest only consume the sealed scalar interval product.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import polars as pl

from . import qmd_feature_catalog as catalog
from .candle_feature_projection import encode
from .continuous_indicator_features import initial_state, calculate, ContinuousFeatureProjection
from .causal_market_history import prepare_causal_history
from .causal_context_features import own, NAMES, OWN_NAMES
from .feature_share_basis import price_factor


def asof_rows(source_seconds, targets):
    source_seconds = np.asarray(source_seconds, dtype=np.int64)
    targets = np.asarray(targets, dtype=np.int64)
    if len(source_seconds) and np.any(np.diff(source_seconds) <= 0):
        raise ValueError('Feature source clocks must be strictly increasing')
    return np.searchsorted(source_seconds, targets, side='right') - 1


def project_listing(scope, dense, *, bars, indicators, prior_bars,
                    seed, splits, fundamentals, quotes, activity,
                    continuous_history, context, opening_splits, prior_volume_day):
    """Calculate each listing once; keep unavailable evidence masked.

    `continuous_history` explicitly includes the current session and its ordered
    certified predecessors, with each session's point-in-time split factor.
    `context` is prepared once for the full pinned census, not per candidate.
    """
    from .columnar_history_product import session_clock_anchor_us
    projected=type(continuous_history) is ContinuousFeatureProjection
    if projected:
        if continuous_history.session_date!=scope.session_date or not np.array_equal(continuous_history.buckets,bars['bucket_index'].to_numpy()):
            raise ValueError('Continuous indicator projection differs from current candles')
    elif not continuous_history or continuous_history[-1][0] != scope.session_date:
        raise ValueError('Continuous indicators require the explicit current-session chain')
    days = [] if projected else [item[0] for item in continuous_history]
    if days != sorted(set(days)):
        raise ValueError('Continuous indicator sessions must be unique and ordered')
    origin = session_clock_anchor_us(scope) // 1000000
    targets = origin + dense['bucket_index'].to_numpy().astype(np.int64) + 1
    seconds = origin + bars['bucket_index'].to_numpy().astype(np.int64) + 1
    indices = asof_rows(seconds, targets)
    known = indices >= 0
    safe = indices.clip(0)
    encoded = encode(scope.session_date, bars, indicators, prior_bars, seed, splits, fundamentals) if bars.height else None
    base = (np.concatenate((encoded.scalar, encoded.levels.reshape(-1, 110)), axis=1)
            if encoded is not None else np.empty((0,147),np.float32))
    factor=price_factor(opening_splits,prior_volume_day,scope.session_date)
    known_rvol=base[:,13]==1
    base[known_rvol,12]=np.log1p(np.expm1(base[known_rvol,12].astype(np.float64))*factor)
    today_factor=price_factor(opening_splits,scope.session_date-timedelta(days=1),scope.session_date)
    changed=not np.isclose(today_factor,1.,rtol=1e-12,atol=0.)
    split = float(changed)
    reverse = float(changed and today_factor>1.)
    base = np.column_stack((base, np.full(len(base), split), np.full(len(base), reverse))).astype(np.float32)
    if not len(base):base=np.full((1,len(catalog.BASE)),np.nan,np.float32)
    base_valid = np.isfinite(base)
    for i, field in enumerate(catalog.BASE):
        if field.mask is not None:
            base_valid[:, i] &= base[:, field.mask] == 1
    values = [base[safe]]
    masks = [base_valid[safe] & known[:, None]]
    history = prepare_causal_history(dense, tuple(f.name for f in catalog.HISTORY + list(catalog.RELATIVE)))
    values.append(history.select([f.name for f in (*catalog.HISTORY, *catalog.RELATIVE)]).to_numpy().astype(np.float32))
    masks.append(history.select([f.name+'__valid' for f in (*catalog.HISTORY, *catalog.RELATIVE)]).to_numpy())
    # Quote age advances on the decision clock; activity is the current completed
    # second, not a carried trade count from a previous market observation.
    q = quotes.sort('second')
    qi = asof_rows(q['second'].to_numpy(), targets)
    qknown = qi >= 0
    qs = qi.clip(0)
    if not q.height:
        bid = ask = stamp = np.full(len(targets), np.nan)
    else:
        bid, ask, stamp = (q[n].to_numpy()[qs] for n in ('bid', 'ask', 'quote_us'))
    price = dense['reference_close'].to_numpy()
    age = targets - stamp/1e6
    good_quote = qknown & (stamp > 0) & (age >= 0) & (age <= 1) & (bid > 0) & (ask >= bid)
    a = pl.DataFrame({'second': targets}).join(activity, on='second', how='left', validate='1:1')
    amounts = [a[n].fill_null(0.).to_numpy() for n in ('volume', 'notional', 'trade_count')]
    liquidity = np.column_stack((bid/price-1, ask/price-1, (ask-bid)/price, age,
        *(np.log1p(x) for x in amounts), bid, ask, ask-bid, price)).astype(np.float32)
    liquid_valid = np.isfinite(liquidity) & dense['source_known'].to_numpy()[:, None]
    liquid_valid[:, :3] &= good_quote[:, None]
    liquid_valid[:, 3] &= (stamp > 0) & (age >= 0)
    liquid_valid[:, -4:-1] &= good_quote[:, None]
    liquid_valid[:, -1] &= price > 0
    values.append(liquidity); masks.append(liquid_valid)
    state, counts = initial_state()
    continuous = continuous_history.values if projected else None
    for day, candles, factor in (() if projected else continuous_history):
        if not np.isfinite(factor) or factor <= 0:
            raise ValueError('Continuous indicators require a valid opening split factor')
        emit = np.ones(candles.height, dtype=np.bool_) if day == scope.session_date else np.zeros(candles.height, dtype=np.bool_)
        continuous, state, counts = calculate(
            candles['close_int'].to_numpy()/10000., candles['high_int'].to_numpy()/10000.,
            candles['low_int'].to_numpy()/10000., candles['price_valid'].to_numpy()!=0,
            candles['extremes_valid'].to_numpy()!=0, state, counts, factor, emit)
    if len(continuous) != len(bars):
        raise ValueError('Continuous indicator output differs from current candle clocks')
    if not len(continuous):continuous=np.full((1,len(catalog.VARIANTS)),np.nan,np.float32)
    values.append(continuous[safe]); masks.append(np.isfinite(continuous[safe]) & known[:, None])
    own_frame = bars.select(pl.Series('second', seconds),
        *(pl.col(n+'_int').cast(pl.Float64).truediv(10000.).alias(n) for n in ('open','high','low','close')),
        'volume','trade_count','price_valid','extremes_valid')
    first = int(datetime.combine(scope.session_date,time(4),ZoneInfo('America/New_York')).timestamp())
    enriched = own(own_frame, seconds, first) if len(seconds) else np.empty((0,len(OWN_NAMES)),np.float32)
    if context.height != len(seconds) or not np.array_equal(context['second'].to_numpy(), seconds):
        raise ValueError('Market context differs from the listing feature clocks')
    enriched = np.column_stack((enriched, context.select(NAMES[len(OWN_NAMES):]).to_numpy())).astype(np.float32)
    if not len(enriched):enriched=np.full((1,len(NAMES)),np.nan,np.float32)
    values.append(enriched[safe]); masks.append(np.isfinite(enriched[safe]) & known[:, None])
    result = np.concatenate(values, axis=1)
    valid = np.concatenate(masks, axis=1) & np.isfinite(result)
    if result.shape != (len(targets), len(catalog.CATALOG)):
        raise ValueError('QMD feature projection differs from its complete catalog')
    output = {'bucket_index': dense['bucket_index']}
    for i, field in enumerate(catalog.CATALOG):
        output[field.name] = pl.Series(np.where(valid[:,i],result[:,i],np.nan),dtype=pl.Float32)
        output[field.name+'__valid'] = pl.Series(valid[:,i],dtype=pl.Boolean)
    return pl.DataFrame(output)
