"""Versioned completed-bar MACD supervision, using only certified arte products."""
import polars as pl

from research.rl_trading.v1.common import bounds

VERSION = 'hindsight-phase1-arte-price-action-v4'
LIQUIDATION_SECONDS_BEFORE_CLOSE = 120


def liquidation_time(day):
    return bounds(day)[1] - LIQUIDATION_SECONDS_BEFORE_CLOSE * 1_000_000


def intervals(indicators, terminal):
    if indicators['time_us'].n_unique() != indicators.height or not indicators['time_us'].is_sorted():
        raise ValueError('MACD timestamps must be unique and ordered')
    if indicators.filter(~pl.all_horizontal(pl.col('macd_line','macd_signal').is_finite()).fill_null(False)).height:
        raise ValueError('Non-finite certified MACD')
    changes = indicators.with_columns(
        (pl.col('macd_line')-pl.col('macd_signal')).sign().cast(pl.Int8).alias('direction'))
    changes = changes.filter((pl.col('direction') != pl.col('direction').shift(1)).fill_null(True))
    return changes.select(pl.col('time_us').alias('start_us'),
        pl.col('time_us').shift(-1).fill_null(terminal).alias('end_us'), 'direction'
    ).with_row_index('position_number', offset=1).filter(
        (pl.col('direction') != 0) & (pl.col('end_us') > pl.col('start_us')))


def targets(bars, episodes, lookback=2):
    """Range-join only the bounded entry window; assign exit bars once by ASOF.

    Each bar's high/low is timestamped at its completion. Entry windows include
    both endpoints; exit windows are [MACD start, MACD end), strictly after entry.
    Earliest completed bar wins equal extrema. Same-bar round trips are excluded.
    """
    if not 0 <= lookback <= 30 or int(lookback) != lookback:
        raise ValueError('lookback must be a whole number from 0 through 30')
    prices = bars.filter((pl.col('resolution_ms') == 100) & (pl.col('extremes_valid') == 1)).select('time_us','low','high')
    if prices.filter((pl.col('low') <= 0) | (pl.col('high') < pl.col('low')) | ~pl.all_horizontal(pl.col('low','high').is_finite())).height:
        raise ValueError('Invalid certified extrema')
    prices = prices.sort('time_us')
    windows = episodes.with_columns((pl.col('start_us')-lookback*1_000_000).alias('left_us'))
    entries = prices.join_where(windows, pl.col('time_us') >= pl.col('left_us'), pl.col('time_us') <= pl.col('start_us'))
    entries = entries.with_columns(pl.when(pl.col('direction') == 1).then(pl.col('low')).otherwise(-pl.col('high')).alias('_rank'))
    entries = entries.sort('position_number','_rank','time_us').unique('position_number',keep='first',maintain_order=True).select(
        'position_number',pl.col('time_us').alias('entry_us'),
        pl.when(pl.col('direction') == 1).then(pl.col('low')).otherwise(pl.col('high')).alias('entry_price'))
    exits = prices.join_asof(episodes.sort('start_us'),left_on='time_us',right_on='start_us').filter(pl.col('time_us') < pl.col('end_us'))
    exits = exits.join(entries,on='position_number',how='inner').filter(pl.col('time_us') > pl.col('entry_us'))
    exits = exits.with_columns(pl.when(pl.col('direction') == 1).then(-pl.col('high')).otherwise(pl.col('low')).alias('_rank'))
    exits = exits.sort('position_number','_rank','time_us').unique('position_number',keep='first',maintain_order=True).with_columns(
        pl.when(pl.col('direction') == 1).then(pl.col('high')).otherwise(pl.col('low')).alias('exit_price'))
    valid = exits.filter((pl.col('exit_price')-pl.col('entry_price'))*pl.col('direction') > 0)
    positions = valid.select('position_number',
        pl.when(pl.col('direction') == 1).then(pl.lit('long')).otherwise(pl.lit('short')).alias('direction'),
        (pl.col('entry_us')/1e6).alias('entry_time'),(pl.col('time_us')/1e6).alias('exit_time'),
        'entry_price','exit_price',(pl.col('start_us')/1e6).alias('macd_open'),
        (pl.col('end_us')/1e6).alias('macd_close'),(pl.col('end_us')/1e6).alias('label_available_at')).to_dicts()
    return dict(positions=positions, interval_count=episodes.height, position_count=len(positions),
        interval_rejections=dict(no_entry_price=episodes.height-entries.height,
            no_exit_price=entries.height-exits.height,no_directional_move=exits.height-valid.height),
        target_clock='completed_100ms_bar_end')


def decision_values(day, bars, selected_targets, *, liquidation_us=None):
    """Price-action labels; neither quotes nor hypothetical execution eligibility.

    The decision reference is the latest completed eligible 100 ms trade close.
    Sparse periods carry that observed close, retaining its timestamp and age.
    Future swing extrema are label-side prices, never current observations.
    """
    left,right = bounds(day)
    if liquidation_us is not None:
        if not left < liquidation_us < right or liquidation_us % 1_000_000:
            raise ValueError('Liquidation must be an interior whole-second session boundary')
        bars = bars.filter(pl.col('time_us') <= liquidation_us)
        selected_targets = [p for p in selected_targets if round(p['exit_time']*1e6) < liquidation_us]
    grid = pl.DataFrame({'time_us':pl.int_range(left,right+1,1_000_000,eager=True)})
    prices = bars.filter((pl.col('resolution_ms') == 100) & (pl.col('price_valid') == 1)).select(
        pl.col('time_us').alias('price_us'),pl.col('close').alias('decision_price')).sort('price_us')
    if prices['price_us'].n_unique() != prices.height or prices.filter(
            ~((pl.col('decision_price') > 0) & pl.col('decision_price').is_finite()).fill_null(False)).height:
        raise ValueError('Invalid or duplicate certified trade closes')
    terminal_price = prices['decision_price'][-1] if liquidation_us is not None and prices.height else None
    terminal_price_us = prices['price_us'][-1] if liquidation_us is not None and prices.height else None
    grid = grid.join_asof(prices,left_on='time_us',right_on='price_us').with_columns(
        pl.col('decision_price').is_not_null().alias('price_valid'),
        ((pl.col('time_us')-pl.col('price_us'))/1e6).alias('price_age_seconds'))
    activity = bars.filter(pl.col('resolution_ms') == 1000).select('time_us',pl.col('trade_count').alias('trades'),'volume')
    grid = grid.join(activity,on='time_us',how='left',validate='1:1').sort('time_us').with_columns(
        pl.col('trades','volume').fill_null(0)).with_columns(
        pl.col('trades').rolling_sum(10,min_samples=1).alias('trades_10s'),
        pl.col('volume').rolling_sum(10,min_samples=1).alias('volume_10s'),
        pl.col('volume').cum_sum().alias('session_eligible_volume'))
    for side,sign in (('long',1),('short',-1)):
        selected = [p for p in selected_targets if p['direction'] == side]
        data = pl.DataFrame({f'{side}_target_us':[round(p['exit_time']*1e6) for p in selected],
            f'{side}_entry_us':[round(p['entry_time']*1e6) for p in selected],
            f'{side}_target_id':[p['position_number'] for p in selected],
            f'{side}_available_us':[round(p['label_available_at']*1e6) for p in selected],
            f'{side}_target_price':[p['exit_price'] for p in selected]},schema={
                f'{side}_target_us':pl.Int64,f'{side}_entry_us':pl.Int64,f'{side}_target_id':pl.Int64,
                f'{side}_available_us':pl.Int64,f'{side}_target_price':pl.Float64}).sort(f'{side}_target_us')
        grid = grid.with_columns((pl.col('time_us')+1).alias('_next')).join_asof(
            data,left_on='_next',right_on=f'{side}_target_us',strategy='forward').drop('_next')
        if liquidation_us is not None:
            fallback = pl.col(f'{side}_target_us').is_null()
            grid = grid.with_columns(
                pl.when(fallback).then(pl.lit(liquidation_us)).otherwise(pl.col(f'{side}_target_us')).alias(f'{side}_target_us'),
                pl.when(fallback).then(pl.lit(None,dtype=pl.Int64)).otherwise(pl.col(f'{side}_entry_us')).alias(f'{side}_entry_us'),
                pl.when(fallback).then(pl.lit(0)).otherwise(pl.col(f'{side}_target_id')).alias(f'{side}_target_id'),
                pl.when(fallback).then(pl.lit(liquidation_us)).otherwise(pl.col(f'{side}_available_us')).alias(f'{side}_available_us'),
                pl.when(fallback).then(pl.lit(terminal_price,dtype=pl.Float64)).otherwise(pl.col(f'{side}_target_price')).alias(f'{side}_target_price'),
                pl.when(fallback).then(pl.lit('session_liquidation')).otherwise(pl.lit('macd_swing')).alias(f'{side}_target_kind'))
        grid = grid.with_columns(
            pl.when((pl.col(f'{side}_target_id') == 0) |
                    ((pl.col(f'{side}_target_id') > 0) & (pl.col('time_us') >= pl.col(f'{side}_entry_us'))))
                .then((pl.col(f'{side}_target_price')-pl.col('decision_price'))*sign).alias(f'{side}_gross_profit'),
            ((pl.col(f'{side}_target_us')-pl.col('time_us'))/1e6).alias(f'{side}_hold_seconds'),
            pl.when(pl.col(f'{side}_target_id').is_null()).then(pl.lit('no_future_macd_target'))
                .when(~pl.col('price_valid')).then(pl.lit('current_price_unavailable'))
                .when(pl.col(f'{side}_target_id') == 0).then(pl.lit('no_active_macd_swing'))
                .when(pl.col('time_us') < pl.col(f'{side}_entry_us')).then(pl.lit('waiting_for_macd_entry'))
                .otherwise(pl.lit('available')).alias(f'{side}_status'))
        if liquidation_us is not None:
            grid = grid.with_columns(
                pl.col(f'{side}_hold_seconds').clip(lower_bound=0).alias(f'{side}_hold_seconds'),
                pl.when(pl.col(f'{side}_target_price').is_null()).then(pl.lit('terminal_price_unavailable'))
                    .when(pl.col('time_us') >= liquidation_us).then(pl.lit('session_liquidation'))
                    .otherwise(pl.col(f'{side}_status')).alias(f'{side}_status'))
    if liquidation_us is not None:
        grid = grid.with_columns((pl.col('time_us') >= liquidation_us).alias('session_terminal'),
            pl.lit(liquidation_us).alias('liquidation_us'),
            pl.lit(terminal_price_us,dtype=pl.Int64).alias('liquidation_price_us'))
    return grid


def label_coverage(values):
    return {side: values.group_by(side+'_status').len().sort(side+'_status').to_dicts() for side in ('long','short')}
