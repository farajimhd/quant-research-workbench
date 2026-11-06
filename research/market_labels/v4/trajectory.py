"""Deterministic trajectory labels; never reinterpret an opportunity as a fill."""
import polars as pl


def label(rows, decisions):
    keys=['listing_id','time_us']
    if rows.select(keys).is_duplicated().any():raise ValueError('Duplicate candle identity')
    positions=decisions.filter(pl.col('selected')).sort(keys)
    if positions.select('listing_id','pair_id').is_duplicated().any():raise ValueError('Duplicate position')
    if positions.filter(pl.col('time_us').is_null() | pl.col('entry_target_us').is_null() |
        (pl.col('time_us')>=pl.col('entry_target_us')) |
        (pl.col('time_us')%1_000_000!=0) | (pl.col('entry_target_us')%1_000_000!=0)).height:
        raise ValueError('Invalid reference endpoints')
    previous=positions.with_columns(pl.col('entry_target_us').shift().over('listing_id').alias('_previous_exit'))
    if previous.filter(pl.col('time_us')<=pl.col('_previous_exit')).height:
        raise ValueError('Overlapping or shared-boundary positions cannot have one action per candle')
    for endpoint in ['time_us','entry_target_us']:
        wanted=positions.select('listing_id',pl.col(endpoint).alias('time_us'))
        if wanted.join(rows.select(keys),on=keys,how='anti').height:raise ValueError('Reference endpoint candle missing')
    source=positions.select('listing_id',pl.col('pair_id').alias('position_id_1c'),
        pl.col('time_us').alias('position_entry_us'),pl.col('entry_target_us').alias('position_exit_us'))
    out=rows.with_row_index('_order').sort('time_us').join_asof(source.sort('position_entry_us'),
        left_on='time_us',right_on='position_entry_us',by='listing_id',strategy='backward',check_sortedness=False)
    active=pl.col('time_us').is_between(pl.col('position_entry_us'),pl.col('position_exit_us'),closed='both').fill_null(False)
    out=out.with_columns(*[pl.when(active).then(pl.col(c)).otherwise(None).alias(c)
        for c in ['position_id_1c','position_entry_us','position_exit_us']])
    entry=pl.col('time_us').eq(pl.col('position_entry_us')).fill_null(False)
    exit_=pl.col('time_us').eq(pl.col('position_exit_us')).fill_null(False)
    held=(pl.col('time_us')>pl.col('position_entry_us')).fill_null(False)
    action=pl.when(entry).then(pl.lit('ENTRY')).when(exit_).then(pl.lit('EXIT')).when(held).then(pl.lit('HOLD')).otherwise(pl.lit('WAIT'))
    out=out.with_columns(action.alias('action_1c'),held.cast(pl.Int8).alias('position_state_before'),
        (entry | (held & ~exit_)).cast(pl.Int8).alias('position_state_after'),
        pl.when(held).then((pl.col('time_us')-pl.col('position_entry_us'))//1_000_000).otherwise(0).alias('position_age_seconds'),
        ((pl.col('position_exit_us')-pl.col('time_us'))//1_000_000).alias('remaining_duration_seconds'),
        pl.col('position_id_1c').is_not_null().alias('remaining_duration_valid'),pl.lit(True).alias('trajectory_label_valid'))
    out=out.with_columns(pl.col('position_age_seconds').alias('position_age_candles'),
        pl.col('remaining_duration_seconds').alias('remaining_duration_candles'),
        pl.lit(1.).alias('label_value_1c'),pl.concat_list(*[(pl.col('action_1c')==a).cast(pl.Float64) for a in ['ENTRY','WAIT','HOLD','EXIT']]).alias('teacher_probabilities_1c'))
    for action in ['ENTRY','EXIT']:
        witnesses=out.filter(pl.col('action_1c')==action)
        if witnesses.height!=positions.height or witnesses.select('listing_id','position_id_1c').is_duplicated().any():
            raise ValueError('Trajectory must have exactly one '+action+' per accepted position')
    out=out.sort('_order').drop('_order')
    if not out.select(rows.columns).equals(rows):raise ValueError('Original 1a/1b rows changed')
    return out
