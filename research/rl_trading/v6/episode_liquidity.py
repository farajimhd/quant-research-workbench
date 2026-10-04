"""Causal activity evidence for admission of hindsight MACD opportunities."""
import numpy as np
import polars as pl


def read_activity(client, source, day, ticker, expected_clocks):
    from research.rl_trading.v1 import arte_source, arte_sql
    from research.rl_trading.v1.common import bounds
    from datetime import date
    where = arte_source.selection(source, day, ticker, 'bars')
    proof = arte_sql.query(client, 'SELECT count() AS n,'
        'uniqExact((resolution_ms,bucket_index)) AS unique_keys,'
        'sum(cityHash64(tuple(*))) AS hash FROM arte.bars_v1 WHERE '+where)[0]
    unit = source['units'][str(day)][ticker]['bars']
    if (int(proof['n']) != unit['output_rows'] or int(proof['unique_keys']) != unit['output_rows'] or
            str(proof['hash']) != str(unit['output_hash'])):
        raise ValueError('Exact liquidity source integrity changed')
    frame = arte_source.frame(client, 'SELECT bucket_index,volume,trade_count FROM arte.bars_v1 '
        f'WHERE {where} AND resolution_ms=1000 ORDER BY bucket_index',
        dict(bucket_index=pl.Int64, volume=pl.Float64, trade_count=pl.Int64))
    begin, _ = bounds(date.fromisoformat(str(day)))
    frame = frame.with_columns(((pl.col('bucket_index')-14_400+1)*1_000_000+begin).alias('time_us'))
    if not np.array_equal(frame['time_us'].to_numpy(), expected_clocks):
        raise ValueError('Exact liquidity source differs from certified bank clocks')
    return frame.select('time_us','volume','trade_count')


def evidence(targets, activity, config):
    required = {'time_us', 'volume', 'trade_count'}
    if activity is None or not required <= set(activity.columns):
        raise ValueError('Exact certified activity required for episode liquidity')
    t = activity['time_us'].to_numpy()
    volume = activity['volume'].to_numpy().astype(float)
    trades = activity['trade_count'].to_numpy()
    if (np.any(np.diff(t) <= 0) or not np.isfinite(volume).all() or
            np.any(volume < 0) or np.any(trades < 0) or
            np.any(trades != np.floor(trades))):
        raise ValueError('Invalid exact activity clocks/counts/volume')
    end = np.searchsorted(t, targets, side='left')  # Target close excluded.
    left = np.searchsorted(t, targets-60_000_000, side='left')
    def rolling(values):
        sums = np.r_[0, np.cumsum(values)]
        return sums[end]-sums[left]
    count, shares, seconds = rolling(trades), rolling(volume), rolling(trades > 0)
    active = t[trades > 0]
    index = np.searchsorted(active, targets, side='left')-1
    age = np.full(len(targets), np.nan)
    present = index >= 0
    # A trade lies somewhere in [bucket close - 1s, bucket close).
    # Use the oldest possible trade time: never overstate freshness.
    age[present] = (targets[present]-active[index[present]])/1e6+1.
    eligible = ((count >= config.minimum_trades_60s) &
                (shares >= config.minimum_shares_60s) &
                (seconds >= config.minimum_active_seconds_60s) &
                (age <= config.maximum_inactivity_seconds))
    reason = np.full(len(targets), 'eligible', dtype=object)
    reason[~(age <= config.maximum_inactivity_seconds)] = 'stale_or_absent_trade'
    reason[seconds < config.minimum_active_seconds_60s] = 'insufficient_active_seconds'
    reason[shares < config.minimum_shares_60s] = 'insufficient_share_volume'
    reason[count < config.minimum_trades_60s] = 'insufficient_trade_count'
    return pl.DataFrame(dict(time_us=targets, prior_trades_60s=count,
        prior_shares_60s=shares, prior_active_seconds_60s=seconds,
        prior_trade_age_seconds=[None if np.isnan(v) else float(v) for v in age],
        liquidity_eligible=eligible, liquidity_reason=reason))


def has_gap(activity, start, end, maximum_seconds):
    """Require continuing trade activity through the original pair boundary.

    Include the leading/trailing intervals; missing price rows cannot conceal
    a gap. Future evidence only invalidates hindsight supervision, never inputs.
    """
    times = activity['time_us'].to_numpy()
    left, right = np.searchsorted(times, [start, end])
    active = times[left:right][activity['trade_count'].to_numpy()[left:right] > 0]
    if not len(active):
        return end-start > maximum_seconds*1e6
    # Worst-case interval from an early trade in one bucket to a late
    # trade in the next. Bound both ends of the original pair as well.
    intervals = np.r_[active[0]-start, np.diff(active)+1_000_000,
                      end-active[-1]+1_000_000]
    return bool(np.any(intervals > maximum_seconds*1e6))
