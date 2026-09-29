"""Causal features for actual completed one-second candles.

The candle axis contains persisted bars, not 57,601 padded clock seconds.
Exact close clocks are retained as int64 coordinates; the start is one second
earlier. V7 slots are a separate, masked [candles, below/above, 5, fields]
tensor so a model can share one level encoder across all ten levels.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math

import numpy as np
import polars as pl

from research.rl_trading.v1.common import bounds
from src.backend.fixed_v7_stream import FixedV7Stream


VERSION = 'rl-trading-actual-candles-features-v6'
FIRST_BUCKET = 14_400
CLOCK_SECONDS = 57_601
CONTEXT_CANDLES = 120
LEVELS_PER_SIDE = 5
SCALAR_NAMES = (
    'log_open', 'log_high', 'log_low', 'log_close',
    'bar_vwap_rel', 'session_vwap_rel', 'bar_vwap_valid',
    'session_vwap_valid', 'log_volume', 'log_trades',
    'log_volume_60s', 'log_trades_60s',
    'log_rvol_10s_prev_session', 'rvol_10s_available',
    'macd_line_rel', 'macd_signal_rel', 'rsi_14', 'atr_14_rel',
    'ema_7_rel', 'ema_26_rel', 'indicator_available',
    'time_of_day_sin', 'time_of_day_cos',
    'premarket', 'regular', 'after_hours', 'log_inter_candle_gap',
    'log_float_shares', 'float_present',
    'log_shares_outstanding', 'shares_present',
    'log_days_since_split', 'split_present',
    'log_days_since_reverse_split', 'reverse_split_present',
    'bar_price_valid', 'bar_extremes_valid',
)
LEVEL_NAMES = (
    'center_distance_rel', 'lower_distance_rel', 'upper_distance_rel',
    'log_observation_count', 'log_today_observation_count',
    'log_age_since_confirmation', 'role_support', 'role_resistance',
    'role_transition', 'historical_origin', 'present',
)
FUNDAMENTAL_NAMES = SCALAR_NAMES[27:35]


@dataclass(frozen=True)
class CandleFeatures:
    close_us: np.ndarray  # [C], int64; start_us is close_us - 1_000_000.
    scalar: np.ndarray  # [C, 37], float32.
    levels: np.ndarray  # [C, 2, 5, 11], float32; axis 1 is below, above.

    def validate(self) -> None:
        candles = len(self.close_us)
        if (self.close_us.dtype != np.int64 or
                self.scalar.shape != (candles, len(SCALAR_NAMES)) or
                self.levels.shape != (candles, 2, LEVELS_PER_SIDE,
                                      len(LEVEL_NAMES)) or
                self.scalar.dtype != np.float32 or
                self.levels.dtype != np.float32 or
                (candles and np.any(np.diff(self.close_us) <= 0)) or
                not np.isfinite(self.scalar).all() or
                not np.isfinite(self.levels).all()):
            raise ValueError('Malformed actual-candle feature contract')


def _clock_volume(bars: pl.DataFrame) -> np.ndarray:
    """Map certified observed 1s volume onto the clock for causal rolling sums."""
    result = np.zeros(CLOCK_SECONDS, dtype=np.float64)
    if bars.is_empty():
        return result
    indices = bars['bucket_index'].to_numpy().astype(np.int64) - FIRST_BUCKET + 1
    volume = bars['volume'].to_numpy().astype(np.float64)
    if (np.any((indices < 1) | (indices >= CLOCK_SECONDS)) or
            len(np.unique(indices)) != len(indices) or
            not np.isfinite(volume).all() or np.any(volume < 0)):
        raise ValueError('Invalid or duplicate one-second volume')
    result[indices] = volume
    return result


def _rolling(values: np.ndarray, length: int) -> np.ndarray:
    cumulative = np.concatenate(([0.], np.cumsum(values, dtype=np.float64)))
    end = np.arange(len(values)) + 1
    return cumulative[end] - cumulative[np.maximum(0, end - length)]


def _level_slots(levels: list[dict], price: float, now_us: int,
                 session: str, engine_rows: list[dict]) -> np.ndarray:
    """Reference renderer used in tests; production reuses a revision index."""
    return _LevelIndex(levels, session, engine_rows).render(price, now_us)


class _LevelIndex:
    """Sort one V7 projection revision once, then select ten nearby slots."""

    def __init__(self, levels: list[dict], session: str,
                 engine_rows: list[dict]):
        current_counts = {
            row['id']: sum(obs.get('session') == session
                           for obs in row.get('observations', ()))
            for row in engine_rows
        }
        indexed = []
        for level in levels:
            center = float(level['price'])
            lower = float(level['lower'])
            upper = float(level['upper'])
            confirmed_us = int(level['confirmed_at_ms']) * 1_000
            role = level['role']
            count = int(level['observation_count'])
            if (not all(math.isfinite(v) for v in (center, lower, upper)) or
                    center <= 0 or lower <= 0 or lower > center or center > upper or
                    count < 0 or
                    role not in ('support', 'resistance', 'transition')):
                raise ValueError('Invalid or future V7 level geometry')
            indexed.append((center, str(level['unified_level_id']), lower, upper,
                            confirmed_us, count,
                            current_counts.get(level['unified_level_id'], 0),
                            role, bool(level['historical'])))
        indexed.sort(key=lambda row: (row[0], row[1]))
        self.rows = indexed
        self.centers = np.asarray([row[0] for row in indexed], dtype=np.float64)

    def render(self, price: float, now_us: int) -> np.ndarray:
        out = np.zeros((2, LEVELS_PER_SIDE, len(LEVEL_NAMES)), dtype=np.float32)
        if price <= 0:
            return out
        cut = np.searchsorted(self.centers, price, side='right')
        nearby = (list(reversed(self.rows[max(0, cut-LEVELS_PER_SIDE):cut])),
                  self.rows[cut:cut+LEVELS_PER_SIDE])
        for side, selected in enumerate(nearby):
            for slot, (center, _, lower, upper, confirmed_us, count,
                       today, role, historical) in enumerate(selected):
                if confirmed_us > now_us:
                    raise ValueError('V7 level confirmation is in the future')
                out[side, slot] = (
                    (center-price)/price, (lower-price)/price, (upper-price)/price,
                    math.log1p(count), math.log1p(today),
                    math.log1p((now_us-confirmed_us)/1_000_000),
                    float(role == 'support'), float(role == 'resistance'),
                    float(role == 'transition'), float(historical), 1.,
                )
        return out


def encode(day: date, bars: pl.DataFrame, indicators: pl.DataFrame,
           prior_bars: pl.DataFrame, seed: dict, splits: list[dict],
           fundamentals: dict, *, prior_close_us: int | None = None,
           ) -> CandleFeatures:
    """Encode one listing once, using only completed data available by each close.

    `prior_bars` must be from the immediately preceding certified session.
    It supplies same-clock 10s RVOL; the builder separately binds the last
    120 actual prior candles as warm-up without copying overlapping windows.
    """
    required = {'resolution_ms', 'bucket_index', 'open_int', 'high_int',
                'low_int', 'close_int', 'notional', 'volume', 'trade_count',
                'price_valid', 'extremes_valid'}
    if not required <= set(bars.columns) or not {'bucket_index', 'volume'} <= set(prior_bars.columns):
        raise ValueError('Missing certified one-second candle fields')
    if any(name not in fundamentals for name in FUNDAMENTAL_NAMES):
        raise ValueError('Missing point-in-time reference features')
    if bars.is_empty() or not (bars['resolution_ms'] == 1000).all():
        raise ValueError('Expected nonempty one-second candle series')
    index = bars['bucket_index'].to_numpy().astype(np.int64) - FIRST_BUCKET + 1
    if np.any((index < 1) | (index >= CLOCK_SECONDS)) or np.any(np.diff(index) <= 0):
        raise ValueError('Candle buckets must be unique and ordered')
    left, right = bounds(day)
    close_us = left + index * 1_000_000
    if np.any(close_us > right) or (prior_close_us is not None and prior_close_us >= close_us[0]):
        raise ValueError('Candle clocks or preceding session are invalid')
    current_volume = _clock_volume(bars)
    prior_volume = _clock_volume(prior_bars)
    current_10 = _rolling(current_volume, 10)[index]
    prior_10 = _rolling(prior_volume, 10)[index]
    volume_60 = _rolling(current_volume, 60)[index]
    trades = bars['trade_count'].to_numpy().astype(np.float64)
    if not np.isfinite(trades).all() or np.any(trades < 0):
        raise ValueError('Invalid certified trade counts')
    clock_trades = np.zeros(CLOCK_SECONDS, dtype=np.float64)
    clock_trades[index] = trades
    trades_60 = _rolling(clock_trades, 60)[index]
    rvol_ok = prior_10 > 0
    rvol = np.zeros(len(index), dtype=np.float64)
    rvol[rvol_ok] = np.log1p(current_10[rvol_ok] / prior_10[rvol_ok])
    raw = np.column_stack([bars[name].to_numpy().astype(np.float64) / 10_000
                           for name in ('open_int', 'high_int', 'low_int', 'close_int')])
    price_ok = (bars['price_valid'].to_numpy() == 1) & (raw > 0).all(axis=1)
    extrema_ok = ((bars['extremes_valid'].to_numpy() == 1) & price_ok &
                  (raw[:, 1] >= np.maximum(raw[:, 0], raw[:, 3])) &
                  (raw[:, 2] <= np.minimum(raw[:, 0], raw[:, 3])))
    log_prices = np.zeros_like(raw)
    log_prices[price_ok] = np.log(raw[price_ok])
    notional = bars['notional'].to_numpy().astype(np.float64)
    if not np.isfinite(notional).all() or np.any(notional < 0):
        raise ValueError('Invalid certified notional')
    volume = bars['volume'].to_numpy().astype(np.float64)
    bar_vwap_ok = price_ok & (volume > 0) & (notional > 0)
    bar_vwap = np.zeros(len(index))
    bar_vwap[bar_vwap_ok] = notional[bar_vwap_ok] / volume[bar_vwap_ok] / raw[bar_vwap_ok, 3] - 1
    cumulative_volume = np.cumsum(volume)
    cumulative_notional = np.cumsum(notional)
    session_vwap_ok = price_ok & (cumulative_volume > 0) & (cumulative_notional > 0)
    session_vwap = np.zeros(len(index))
    session_vwap[session_vwap_ok] = (cumulative_notional[session_vwap_ok] /
        cumulative_volume[session_vwap_ok] / raw[session_vwap_ok, 3] - 1)
    indicator_names = ('macd_line', 'macd_signal', 'rsi_14', 'atr_14', 'ema_7', 'ema_26')
    if not {'bucket_index', *indicator_names} <= set(indicators.columns):
        raise ValueError('Missing certified one-second indicator fields')
    if indicators['bucket_index'].n_unique() != indicators.height:
        raise ValueError('Duplicate indicator bucket')
    aligned = bars.select('bucket_index').join(
        indicators.select('bucket_index', *indicator_names), on='bucket_index',
        how='left', validate='1:1')
    values = aligned.select(indicator_names).to_numpy().astype(np.float64)
    indicator_ok = price_ok & np.isfinite(values).all(axis=1)
    normalized = np.zeros((len(index), 6), dtype=np.float64)
    normalized[indicator_ok, 0] = values[indicator_ok, 0] / raw[indicator_ok, 3]
    normalized[indicator_ok, 1] = values[indicator_ok, 1] / raw[indicator_ok, 3]
    normalized[indicator_ok, 2] = values[indicator_ok, 2] / 100
    normalized[indicator_ok, 3] = values[indicator_ok, 3] / raw[indicator_ok, 3]
    normalized[indicator_ok, 4:6] = values[indicator_ok, 4:6] / raw[indicator_ok, 3, None] - 1
    seconds_of_day = bars['bucket_index'].to_numpy().astype(np.float64) + 1
    radians = 2 * np.pi * seconds_of_day / 86_400
    gaps = np.diff(close_us, prepend=prior_close_us if prior_close_us is not None else close_us[0]) / 1_000_000
    if np.any(gaps < 0):
        raise ValueError('Negative inter-candle gap')
    refs = np.broadcast_to(np.asarray([fundamentals[name] for name in FUNDAMENTAL_NAMES],
                                      dtype=np.float64), (len(index), len(FUNDAMENTAL_NAMES)))
    scalar = np.column_stack((
        log_prices, bar_vwap, session_vwap, bar_vwap_ok, session_vwap_ok,
        np.log1p(volume), np.log1p(trades), np.log1p(volume_60),
        np.log1p(trades_60), rvol, rvol_ok, normalized, indicator_ok,
        np.sin(radians), np.cos(radians), seconds_of_day < 34_200,
        (seconds_of_day >= 34_200) & (seconds_of_day < 57_600),
        seconds_of_day >= 57_600, np.log1p(gaps), refs,
        price_ok, extrema_ok,
    )).astype(np.float32)
    stream = FixedV7Stream(seed, ticker=seed['ticker'], session=day,
                           splits=splits, consume_seed=True)
    level_tensor = np.zeros((len(index), 2, LEVELS_PER_SIDE,
                             len(LEVEL_NAMES)), dtype=np.float32)
    session = day.isoformat()
    cached_revision = None
    level_index = None
    for position, (row, end_us, good, close) in enumerate(zip(
            bars.iter_rows(named=True), close_us, extrema_ok, raw[:, 3])):
        at = datetime.fromtimestamp(end_us / 1_000_000, timezone.utc)
        if good:
            stream.update_second(row, at=at)
        context = stream.context(as_of=at, price=float(close) if good else 0.)
        if float(context['v7_max_input_timestamp']) * 1_000_000 > end_us + 1:
            raise ValueError('V7 consumed a future completed candle')
        revision = getattr(stream.engine, '_projection_revision', 0)
        if revision != cached_revision:
            level_index = _LevelIndex(context['qmd_structure_unified_levels'],
                                      session, stream.engine.rows)
            cached_revision = revision
        level_tensor[position] = level_index.render(
            float(close) if good else 0., int(end_us))
    result = CandleFeatures(close_us.astype(np.int64), scalar, level_tensor)
    result.validate()
    return result
