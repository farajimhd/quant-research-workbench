"""Persistable Phase 1 opportunities; no portfolio policy or account filters."""
from array import array
from datetime import datetime, time
from hashlib import sha256
import json
from zoneinfo import ZoneInfo
import polars as pl
from src.market_engine.hindsight import PriceMacdLabels

VERSION = 'hindsight-phase1-macd-v1'
NY = ZoneInfo('America/New_York')


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def bounds(day):
    return tuple(int(datetime.combine(day,time(h),NY).timestamp()*1_000_000) for h in (4,20))


def targets_from_extrema(records, intervals, lookback=2):
    # Whole-second boundaries and their exact-time trades are separate SQL bins.
    # Min/max suffices for every base-label interval and integer lookback window.
    points={}
    for row in records:
        if not int(row.get('prices',row['trades'])):continue
        for key in ('lo','hi'):
            at,ordinal,price=row[key]
            points[(int(at),int(ordinal))]=float(price)
    points=sorted(points.items())
    oracle=PriceMacdLabels()
    oracle.times=array('d',(key[0]/1e6 for key,_ in points))
    oracle.prices=array('d',(price for _,price in points))
    oracle.trades=sum(int(r['trades']) for r in records)
    result=oracle.result(intervals,lookback)
    result['compressed_price_points']=len(points)
    return result


def opportunities(day, records, quotes, targets):
    left,right=bounds(day)
    # Exactly one decision per second, including the terminal unavailable row.
    grid=pl.DataFrame({'time_us':pl.int_range(left,right+1,1_000_000,eager=True)})
    schema={'time_us':pl.Int64,'quote_us':pl.Int64,'ask':pl.Float64,'bid':pl.Float64,'ask_size':pl.Float64,'bid_size':pl.Float64}
    q=quotes.select(list(schema)).cast(schema) if isinstance(quotes,pl.DataFrame) else pl.DataFrame(quotes,schema=schema)
    q=q.with_columns(((pl.col('quote_us')>0)&((pl.col('time_us')-pl.col('quote_us')).is_between(0,1_000_000))&
                     (pl.col('bid')>0)&(pl.col('ask')>=pl.col('bid'))&(pl.col('bid_size')>0)&(pl.col('ask_size')>0)&pl.all_horizontal(pl.col('bid','ask','bid_size','ask_size').is_finite())).fill_null(False).alias('quote_valid'))
    grid=grid.join(q,on='time_us',how='left',validate='1:1')
    if grid['quote_us'].null_count():raise ValueError('Quote query omitted decision seconds')
    activity=records.select('time_us','trades','volume') if isinstance(records,pl.DataFrame) else pl.DataFrame({'time_us':[int(r['decision_us']) for r in records],
                           'trades':[int(r['trades']) for r in records],
                           'volume':[float(r['volume']) for r in records]},schema={'time_us':pl.Int64,'trades':pl.Int64,'volume':pl.Float64})
    activity=activity.group_by('time_us').agg(pl.col('trades').sum(),pl.col('volume').sum())
    grid=grid.join(activity,on='time_us',how='left').sort('time_us').with_columns(pl.col('trades','volume').fill_null(0))
    grid=grid.with_columns(pl.col('trades').rolling_sum(10,min_samples=1).alias('trades_10s'),
                          pl.col('volume').rolling_sum(10,min_samples=1).alias('volume_10s'),
                          pl.col('volume').cum_sum().alias('session_eligible_volume'),
                          pl.when(pl.col('quote_valid')).then((pl.col('ask')-pl.col('bid'))/((pl.col('ask')+pl.col('bid'))/2)*10000).alias('spread_bps'))
    for side in ('long','short'):
        selected=[p for p in targets if p['direction']==side]
        data=pl.DataFrame({'target_us':[round(p['exit_time']*1e6) for p in selected],
                           'target_id':[p['position_number'] for p in selected],
                           'available_us':[round(p['label_available_at']*1e6) for p in selected]},
                          schema={'target_us':pl.Int64,'target_id':pl.Int64,'available_us':pl.Int64}).sort('target_us')
        data=data.join(q.select(pl.col('time_us').alias('target_us'),pl.col('ask','bid','quote_valid','quote_us','bid_size','ask_size')),on='target_us',how='left',validate='m:1')
        data=data.rename({c:f'{side}_{c}' for c in data.columns})
        grid=grid.with_columns((pl.col('time_us')+1).alias('_next')).join_asof(data,left_on='_next',right_on=f'{side}_target_us',strategy='forward').drop('_next')
        entry='ask' if side=='long' else 'bid';exit_col=f'{side}_'+('bid' if side=='long' else 'ask')
        entry_depth='ask_size' if side=='long' else 'bid_size';exit_depth=f'{side}_'+('bid_size' if side=='long' else 'ask_size')
        valid=pl.col('quote_valid')&(pl.col(entry_depth)>=1)&pl.col(f'{side}_quote_valid').fill_null(False)&(pl.col(exit_depth)>=1)
        profit=(pl.col(exit_col)-pl.col(entry))*(1 if side=='long' else -1)
        grid=grid.with_columns(pl.when(valid).then(profit).alias(f'{side}_gross_profit'),
             ((pl.col(f'{side}_target_us')-pl.col('time_us'))/1e6).alias(f'{side}_hold_seconds'),
             pl.when(pl.col(f'{side}_target_id').is_null()).then(pl.lit('no_future_macd_target'))
               .when(~pl.col('quote_valid')|(pl.col(entry_depth)<1)).then(pl.lit('entry_quote_unavailable'))
               .when(~pl.col(f'{side}_quote_valid').fill_null(False)|(pl.col(exit_depth)<1)).then(pl.lit('target_quote_unavailable'))
               .otherwise(pl.lit('available')).alias(f'{side}_status'))
    if grid.height!=57601 or grid['time_us'].n_unique()!=grid.height:raise ValueError('Decision grid integrity failure')
    return grid
