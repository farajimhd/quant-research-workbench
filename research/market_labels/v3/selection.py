"""Price preserved opportunities with the certified execution book, not event bars."""
import polars as pl
from research.market_labels.v2 import selection as economics
from research.rl_trading.v6.market_teacher_preview import Config

select = economics.select


def execution_book(execution, clocks):
    """One session only. Keep invalid timestamped updates; never fill valid-only."""
    if execution.select('ticker','time_us').is_duplicated().any():
        raise ValueError('Duplicate execution identity')
    fields=['bid','ask','bid_size','ask_size','book_valid','quote_us']
    if execution.select(pl.any_horizontal([~pl.col(c).is_finite() | pl.col(c).is_null() for c in fields]).any()).item():
        raise ValueError('Nonfinite execution book')
    if execution.filter((pl.col('bid_size')<0)|(pl.col('ask_size')<0)|
                        (pl.col('quote_us')>=pl.col('time_us'))).height:
        raise ValueError('Noncausal execution timestamp or negative displayed size')
    updates=execution.filter(pl.col('quote_us')>0).select(
        pl.col('ticker').alias('listing_id'),pl.col('time_us').alias('book_available_us'),*fields)
    book=clocks.sort('time_us').join_asof(updates.sort('book_available_us'),
        left_on='time_us',right_on='book_available_us',by='listing_id',strategy='backward',check_sortedness=False)
    reason=pl.when(pl.col('quote_us').is_null()).then(pl.lit('no_prior_book'))
    reason=reason.when((pl.col('book_valid')<=0)|(pl.col('bid')<=0)|(pl.col('ask')<pl.col('bid'))).then(pl.lit('invalid_book'))
    reason=reason.when((pl.col('bid_size')<1)|(pl.col('ask_size')<1)).then(pl.lit('no_displayed_size')).otherwise(pl.lit('valid'))
    return book.with_columns(reason.alias('quote_book_reason'),
        pl.col('quote_us').alias('bid_us'),pl.col('quote_us').alias('ask_us'),
        ((pl.col('time_us')-pl.col('quote_us'))/1e6).alias('quote_age_seconds'),
        pl.lit('execution_liquidity').alias('quote_source')).with_columns(
            (pl.col('quote_book_reason')=='valid').alias('spread_quote_valid'))


def price_rows(rows, book, half_life, config=Config()):
    result=economics.price_rows(rows,book,half_life,config)
    return result.join(book.select('listing_id',pl.col('time_us').alias('entry_target_us'),
        pl.col('quote_age_seconds').alias('target_quote_age_seconds'),
        pl.col('quote_book_reason').alias('target_quote_book_reason')),
        on=['listing_id','entry_target_us'],how='left',validate='m:1')


def copy_targets(rows, decisions, half_life, minimum_seconds, config=Config()):
    result=economics.copy_targets(rows,decisions,half_life,minimum_seconds,config)
    unknown=(~pl.col('spread_quote_valid').fill_null(False)) | pl.col('spread_entry_reason').is_in(
        ['entry_quote_unavailable','target_quote_unavailable','target_not_1a_exit'])
    return result.with_columns((~unknown).alias('economic_label_valid'),
        pl.when(unknown).then(pl.lit('UNLABELLED')).otherwise(pl.col('action')).alias('action'),
        pl.when(unknown).then(None).otherwise(pl.col('label_value')).alias('label_value'),
        pl.when(unknown).then(pl.lit([0.,0.,0.,0.])).otherwise(pl.col('teacher_probabilities')).alias('teacher_probabilities'),
        (pl.col('allocation_loss_mask') & ~unknown).alias('allocation_loss_mask'))
