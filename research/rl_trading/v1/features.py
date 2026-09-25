"""Vectorized one-second features from pinned arte and as-of reference products."""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import polars as pl

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v1.arte_source import frame
from research.rl_trading.v1.common import bounds
from src.backend.fixed_v7_stream import FixedV7Stream

SECONDS = 57_601
FIRST_BUCKET = 14_400
FEATURE_NAMES = (
    'log_price','bar_return','bar_range','log_volume','log_trades',
    'log_volume_60s','log_trades_60s','price_age','price_present','price_available',
    'macd_line_rel','macd_signal_rel','macd_hist_rel','rsi_14','atr_14_rel',
    'ema_7_rel','ema_26_rel','indicator_age',
    'v7_level_count','v7_support_count','v7_resistance_count',
    'v7_support_distance','v7_resistance_distance','v7_age','v7_present',
    'log_float_shares','float_present','log_shares_outstanding','shares_present',
    'log_days_since_split','split_present','log_days_since_reverse_split','reverse_split_present',
)


def _indices(frame: pl.DataFrame) -> np.ndarray:
    index = frame['bucket_index'].to_numpy().astype(np.int64) - FIRST_BUCKET + 1
    if (np.any(index < 1) or np.any(index >= SECONDS) or
            len(np.unique(index)) != len(index) or np.any(np.diff(index) <= 0)):
        raise ValueError('Arte one-second buckets are out of order, duplicate, or outside session')
    return index


def _carry(indices: np.ndarray, values: np.ndarray, default: float = 0.) -> tuple[np.ndarray, np.ndarray]:
    output = np.full(SECONDS, default, dtype=np.float64)
    age = np.full(SECONDS, SECONDS, dtype=np.float64)
    if len(indices):
        where = np.searchsorted(indices, np.arange(SECONDS), side='right') - 1
        present = where >= 0
        output[present] = values[where[present]]
        age[present] = np.arange(SECONDS)[present] - indices[where[present]]
    return output, age


def _rolling(values: np.ndarray, window: int) -> np.ndarray:
    cumulative = np.concatenate(([0.], np.cumsum(values, dtype=np.float64)))
    index = np.arange(len(values))
    return cumulative[index+1]-cumulative[np.maximum(0,index-window+1)]


def _v7_features(seed: dict, splits: list[dict], bars: pl.DataFrame,
                 prices: np.ndarray, day: date) -> np.ndarray:
    stream = FixedV7Stream(seed,ticker=seed['ticker'],session=day,
                           splits=splits,consume_seed=True)
    result = np.zeros((SECONDS,7),dtype=np.float32)
    rows = bars.to_dicts()
    positions = _indices(bars)
    left,_ = bounds(day)
    cursor = 0
    cached_revision = None
    supports = np.empty(0,dtype=np.float64)
    resistances = np.empty(0,dtype=np.float64)
    last_input = 0
    for second in range(SECONDS):
        at = datetime.fromtimestamp(left/1_000_000+second,timezone.utc)
        while cursor < len(rows) and positions[cursor] == second:
            row = rows[cursor]
            if row['price_valid'] == 1 and row['extremes_valid'] == 1:
                stream.update_second(row,at=at)
                last_input = second
            cursor += 1
        context = stream.context(as_of=at,price=float(prices[second]))
        if float(context['v7_max_input_timestamp']) > at.timestamp()+1e-6:
            raise ValueError('Streaming V7 consumed a future bar')
        revision = getattr(stream.engine,'_projection_revision',0)
        if revision != cached_revision:
            levels = context['qmd_structure_unified_levels']
            supports = np.sort(np.asarray([float(x['price']) for x in levels if x['side'] == 1],dtype=np.float64))
            resistances = np.sort(np.asarray([float(x['price']) for x in levels if x['side'] == -1],dtype=np.float64))
            result[second,0:3] = (np.log1p(len(levels)),np.log1p(len(supports)),np.log1p(len(resistances)))
            cached_revision = revision
        elif second:
            result[second,0:3] = result[second-1,0:3]
        price = prices[second]
        if price > 0:
            if len(supports):
                index = np.searchsorted(supports,price,side='right')-1
                if index >= 0:
                    result[second,3] = (price-supports[index])/price
            if len(resistances):
                index = np.searchsorted(resistances,price,side='left')
                if index < len(resistances):
                    result[second,4] = (resistances[index]-price)/price
        result[second,5] = min(second-last_input,600)/600
        result[second,6] = 1.
    return result


def encode(day: date, bars: pl.DataFrame, indicators: pl.DataFrame,
           seed: dict, splits: list[dict], fundamental: dict) -> tuple[np.ndarray,np.ndarray]:
    """Return [second, feature] and causal completed-minute share volume."""
    b = _indices(bars)
    i = _indices(indicators)
    volume = np.zeros(SECONDS,dtype=np.float64)
    trades = np.zeros(SECONDS,dtype=np.float64)
    volume[b] = bars['volume'].to_numpy()
    trades[b] = bars['trade_count'].to_numpy()
    if (np.any(~np.isfinite(volume)) or np.any(volume < 0) or
            np.any(~np.isfinite(trades)) or np.any(trades < 0)):
        raise ValueError('Arte activity must be finite and nonnegative')
    volume60 = _rolling(volume,60)
    trades60 = _rolling(trades,60)
    valid = bars['price_valid'].to_numpy() == 1
    price_index = b[valid]
    close = bars['close_int'].to_numpy()[valid].astype(np.float64)/10_000
    if np.any(~np.isfinite(close)) or np.any(close <= 0):
        raise ValueError('Invalid price-eligible arte close')
    price,price_age = _carry(price_index,close)
    bar_close = bars['close_int'].to_numpy().astype(np.float64)/10_000
    bar_open = bars['open_int'].to_numpy().astype(np.float64)/10_000
    bar_high = bars['high_int'].to_numpy().astype(np.float64)/10_000
    bar_low = bars['low_int'].to_numpy().astype(np.float64)/10_000
    bar_return = np.zeros(SECONDS)
    bar_range = np.zeros(SECONDS)
    active = valid & (bar_open > 0)
    bar_return[b[active]] = np.log(bar_close[active]/bar_open[active])
    bar_range[b[active]] = (bar_high[active]-bar_low[active])/bar_open[active]
    base = np.maximum(price,1e-8)
    indicator_age = np.full(SECONDS,SECONDS,dtype=np.float64)
    indicator = {}
    for name in ('macd_line','macd_signal','rsi_14','atr_14','ema_7','ema_26'):
        indicator[name],indicator_age = _carry(i,indicators[name].to_numpy().astype(np.float64))
    features = np.column_stack((
        np.log(np.maximum(price,1e-6)),bar_return,bar_range,np.log1p(volume),np.log1p(trades),
        np.log1p(volume60),np.log1p(trades60),np.minimum(price_age,600)/600,
        (price_age == 0).astype(np.float32),(price_age < SECONDS).astype(np.float32),
        indicator['macd_line']/base,indicator['macd_signal']/base,
        (indicator['macd_line']-indicator['macd_signal'])/base,
        indicator['rsi_14']/100,indicator['atr_14']/base,
        indicator['ema_7']/base-1,indicator['ema_26']/base-1,
        np.minimum(indicator_age,600)/600,
        _v7_features(seed,splits,bars,price,day),
        np.broadcast_to(np.asarray(list(fundamental.values()),dtype=np.float32),(SECONDS,8)),
    )).astype(np.float32)
    if features.shape != (SECONDS,len(FEATURE_NAMES)) or not np.isfinite(features).all():
        raise ValueError('Causal market feature tensor is malformed')
    return features,volume60


def read_arte_seconds(client, source, day, ticker):
    """Project only completed 1s bars and indicators from pinned attempts."""
    bar_where = arte_sql.selection(source['build_id'],day,ticker,
        source['units'][str(day)][ticker]['bars']['attempt_id'])
    indicator_where = arte_sql.selection(source['build_id'],day,ticker,
        source['units'][str(day)][ticker]['technical']['attempt_id'])
    bars = frame(client, f'SELECT resolution_ms,bucket_index,open_int,high_int,low_int,close_int,'
        f'price_valid,extremes_valid,volume,trade_count FROM arte.bars_v1 WHERE {bar_where} '
        'AND resolution_ms=1000 ORDER BY bucket_index',
        {'resolution_ms':pl.Int64,'bucket_index':pl.Int64,'open_int':pl.Int64,'high_int':pl.Int64,
         'low_int':pl.Int64,'close_int':pl.Int64,'price_valid':pl.Int64,
         'extremes_valid':pl.Int64,
         'volume':pl.Float64,'trade_count':pl.Int64})
    indicators = frame(client, f'SELECT bucket_index,macd_line,macd_signal,rsi_14,'
        f'atr_14,ema_7,ema_26 FROM arte.indicators_v1 WHERE {indicator_where} '
        'AND resolution_ms=1000 ORDER BY bucket_index',
        {'bucket_index':pl.Int64,'macd_line':pl.Float64,'macd_signal':pl.Float64,
         'rsi_14':pl.Float64,'atr_14':pl.Float64,'ema_7':pl.Float64,'ema_26':pl.Float64})
    return bars,indicators
