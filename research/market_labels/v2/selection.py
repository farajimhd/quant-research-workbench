"""Display-independent spread selection; no database or optimizer access."""
import polars as pl
from research.rl_trading.v6.market_teacher_preview import Config


def quote_book(frame):
    keys=['listing_id','time_us']
    if frame.select('ticker','side','time_us').is_duplicated().any():raise ValueError('Duplicate quote-bar identity')
    sides=[]
    for side in ('bid','ask'):
        rows=frame.filter(pl.col('side')==side).select(pl.col('ticker').alias('listing_id'),'time_us',
            pl.col('close').alias(side),pl.col('close_size').alias(side+'_size'),pl.col('last_event_us').cast(pl.Int64).alias(side+'_us'),
            pl.col('event_count').alias(side+'_events'))
        sides.append(rows)
    book=sides[0].join(sides[1],on=keys,how='full',coalesce=True,validate='1:1')
    valid=pl.lit(True)
    for side in ('bid','ask'):
        valid &= (pl.col(side).is_finite() & (pl.col(side)>0) & pl.col(side+'_size').is_finite() & (pl.col(side+'_size')>=1)
            & (pl.col(side+'_events')>0) & (pl.col(side+'_us')>=pl.col('time_us')-1_000_000) & (pl.col(side+'_us')<pl.col('time_us')))
    return book.with_columns((valid & (pl.col('ask')>=pl.col('bid'))).fill_null(False).alias('spread_quote_valid'))


def price_rows(rows, book, half_life, config=Config()):
    config.validate()
    result=rows.join(book,on=['listing_id','time_us'],how='left',validate='1:1')
    targets=book.select('listing_id',pl.col('time_us').alias('entry_target_us'),pl.col('bid').alias('target_bid'),
        pl.col('ask').alias('target_ask'),pl.col('bid_us').alias('target_bid_us'),pl.col('bid_size').alias('target_bid_size'),
        pl.col('spread_quote_valid').alias('target_quote_valid'))
    result=result.join(targets,on=['listing_id','entry_target_us'],how='left',validate='m:1')
    exit_keys=rows.filter(pl.col('action_1a')=='EXIT').select('listing_id',pl.col('time_us').alias('entry_target_us')).with_columns(pl.lit(True).alias('target_is_1a_exit'))
    result=result.join(exit_keys,on=['listing_id','entry_target_us'],how='left',validate='m:1')
    elapsed=(pl.col('entry_target_us')-pl.col('time_us'))/1e6
    result=result.with_columns((pl.col('target_bid')-pl.col('ask')).alias('executable_gain'),
        ((pl.col('target_bid')-pl.col('ask')) * pl.lit(.5).pow(elapsed/half_life)).alias('spread_discounted_gain'))
    result=result.with_columns(((pl.col('spread_discounted_gain')-2*config.fee_per_share)/(pl.col('ask')+config.fee_per_share)).alias('spread_score'),
        pl.max_horizontal(pl.lit(config.minimum_return),config.minimum_net_fee_multiple*2*config.fee_per_share/(pl.col('ask')+config.fee_per_share)).alias('spread_threshold'))
    reason=pl.when(pl.col('action_1a')!='ENTRY').then(pl.lit('not_1a_entry'))
    reason=reason.when(~pl.col('spread_quote_valid').fill_null(False)).then(pl.lit('entry_quote_unavailable'))
    reason=reason.when(~pl.col('target_quote_valid').fill_null(False)).then(pl.lit('target_quote_unavailable'))
    reason=reason.when(~pl.col('target_is_1a_exit').fill_null(False)).then(pl.lit('target_not_1a_exit'))
    reason=reason.when((pl.col('spread_score')<=config.minimum_net_fee_multiple*2*config.fee_per_share/(pl.col('ask')+config.fee_per_share))
        | (pl.col('spread_score')<config.minimum_return)).then(pl.lit('below_spread_score_threshold')).otherwise(pl.lit('eligible'))
    return result.with_columns(reason.alias('spread_entry_reason')).with_columns((pl.col('spread_entry_reason')=='eligible').alias('spread_entry_eligible'))


def select(rows,pairs):
    entries=rows.filter(pl.col('action_1a')=='ENTRY')
    fields=['listing_id','pair_id','time_us','entry_target_us','close','ask','bid','target_bid','target_ask','bid_us','ask_us','target_bid_us',
        'spread_score','spread_threshold','spread_discounted_gain','executable_gain','spread_entry_reason']
    best=entries.sort(['listing_id','pair_id','spread_score','time_us'],descending=[False,False,True,False],nulls_last=True).unique(['listing_id','pair_id'],keep='first',maintain_order=True)
    chosen=entries.filter(pl.col('spread_entry_eligible')).sort('time_us').unique(['listing_id','pair_id'],keep='first',maintain_order=True)
    chosen=chosen.with_columns(pl.lit(True).alias('selected')).select(*fields,'selected')
    audit=best.select(*[pl.col(c).alias('audit_'+c) if c not in ('listing_id','pair_id') else pl.col(c) for c in fields])
    result=pairs.join(chosen,on=['listing_id','pair_id'],how='left',validate='1:1').join(audit,on=['listing_id','pair_id'],how='left',validate='1:1')
    result=result.with_columns(pl.col('selected').fill_null(False),*[pl.coalesce(c,'audit_'+c).alias(c) for c in fields if c not in ('listing_id','pair_id')])
    return result.with_columns(pl.col('spread_score').alias('best_score'),
        pl.when(pl.col('selected')).then(pl.col('spread_score')).otherwise(None).alias('selection_score'),
        pl.col('spread_threshold').alias('score_threshold'),pl.col('spread_discounted_gain').alias('entry_gain'),
        pl.when(pl.col('selected')).then(pl.lit('selected')).otherwise(pl.col('spread_entry_reason').fill_null('no_1a_entry')).alias('selection_reason'))


def copy_targets(rows,decisions,half_life,minimum_seconds,config=Config()):
    joined=rows.join(decisions.select('listing_id','pair_id',pl.col('selected').alias('episode_selected'),'group_id','allocation_ratio','active_score_sum','active_count',
        pl.col('time_us').alias('selected_entry_us'),pl.col('entry_target_us').alias('selected_target_us'),pl.col('ask').alias('selected_entry_ask')),
        on=['listing_id','pair_id'],how='left',validate='m:1')
    elapsed=(pl.col('time_us')-pl.col('selected_entry_us'))/1e6
    score=((pl.col('bid')-pl.col('selected_entry_ask'))*pl.lit(.5).pow(elapsed/half_life)-2*config.fee_per_share)/(pl.col('selected_entry_ask')+config.fee_per_share)
    exit_ok=((pl.col('action_1a')=='EXIT') & pl.col('spread_quote_valid') & (elapsed>=minimum_seconds)
        & (score>config.minimum_net_fee_multiple*2*config.fee_per_share/(pl.col('selected_entry_ask')+config.fee_per_share)) & (score>=config.minimum_return)).fill_null(False)
    joined=joined.with_columns(score.alias('spread_exit_score'),exit_ok.alias('spread_exit_eligible'))
    selected=pl.col('episode_selected').fill_null(False)
    action=pl.when(~selected).then(pl.lit('WAIT')).when(~pl.col('spread_quote_valid').fill_null(False)).then(pl.lit('UNLABELLED'))
    action=action.when(pl.col('spread_entry_eligible')).then(pl.lit('ENTRY')).when(pl.col('spread_exit_eligible')).then(pl.lit('EXIT'))
    action=action.when(pl.col('time_us').is_between(pl.col('selected_entry_us'),pl.col('selected_target_us'),closed='none')).then(pl.lit('HOLD')).otherwise(pl.lit('WAIT'))
    joined=joined.with_columns(action.alias('action'))
    ref=pl.when(~selected).then(pl.lit('WAIT')).when(pl.col('time_us')==pl.col('selected_entry_us')).then(pl.lit('ENTRY'))
    ref=ref.when(pl.col('time_us')==pl.col('selected_target_us')).then(pl.lit('EXIT')).when(pl.col('time_us').is_between(pl.col('selected_entry_us'),pl.col('selected_target_us'),closed='none')).then(pl.lit('HOLD')).otherwise(pl.lit('WAIT'))
    joined=joined.with_columns(ref.alias('reference_action'),(pl.col('action')=='ENTRY').alias('allocation_loss_mask'),
        pl.when(pl.col('action')=='ENTRY').then(pl.col('allocation_ratio')).otherwise(0.).alias('allocation_ratio'),
        pl.when(pl.col('action')=='ENTRY').then(pl.col('entry_quality')).when(pl.col('action')=='EXIT').then(pl.col('exit_quality')).when(pl.col('action')=='HOLD').then(1-pl.col('exit_quality').fill_null(0)).otherwise(1.).alias('label_value'))
    q=pl.col('label_value');a=pl.col('action')
    return joined.with_columns(pl.concat_list(pl.when(a=='ENTRY').then(q).otherwise(0.),pl.when(a=='WAIT').then(1.).when(a=='ENTRY').then(1-q).otherwise(0.),
        pl.when(a=='HOLD').then(q).when(a=='EXIT').then(1-q).otherwise(0.),pl.when(a=='EXIT').then(q).when(a=='HOLD').then(1-q).otherwise(0.)).alias('teacher_probabilities'))
