"""Versioned completed-bar MACD supervision, using only certified arte products."""
import polars as pl

from src.market_engine.hindsight_phase1 import bounds, opportunities

VERSION = 'hindsight-phase1-arte-100ms-v1'


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


def decision_values(day, bars, quotes, selected_targets):
    activity = bars.filter(pl.col('resolution_ms') == 1000).select('time_us',pl.col('trade_count').alias('trades'),'volume')
    return opportunities(day,activity,quotes,selected_targets)


def label_coverage(values):
    return {side: values.group_by(side+'_status').len().sort(side+'_status').to_dicts() for side in ('long','short')}
