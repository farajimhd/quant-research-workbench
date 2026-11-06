"""Read-only, hash-bound app review of V66 label sidecars."""
from functools import lru_cache
import os
from pathlib import Path
import polars as pl
from research.rl_trading.v6.saved_label_audit import read_json, mapped, runtime, verified_local
from research.rl_trading.v6 import price_action_opportunities as algorithm
from .contract import VERSION, SCOPE


def published():
    registry=Path(os.environ.get('V66_LABEL_REGISTRY',str(runtime()/'v66-active-labels.json')))
    active=read_json(registry)
    if active['version']!=VERSION:raise ValueError('Wrong V66 label registry')
    path=mapped(active['dataset']);dataset=read_json(path,active['sha256'])
    if dataset['version']!=VERSION or not dataset['full_inventory'] or dataset['status']!='verified_review_only':raise ValueError('Incomplete V66 review dataset')
    return active,dataset,path.parent


def catalog():
    active,dataset,_=published()
    return dict(dataset_sha256=active['sha256'],supports_combined=True,scope=dataset['scope'],days=[dict(**d,invalid_price_rows=d['excluded_rows']) for d in dataset['days']])


def session(day):
    active,dataset,root=published()
    entry=next((d for d in dataset['days'] if d['day']==day),None)
    if entry is None:raise ValueError('Session not in published V66 labels')
    folder=root/day;proof=read_json(folder/'complete.json',entry['certificate'])
    if proof['binding']!=dataset['binding'] or proof['day']!=day:raise ValueError('V66 day binding mismatch')
    return active,proof,folder


def listings(day):
    active,proof,_=session(day)
    return dict(dataset_sha256=active['sha256'],listings=[dict(**r,venue='V66 shard',activity_rows=r['source_rows'],has_price_targets=r['valid_rows']>0) for r in proof['listings']])


@lru_cache(maxsize=16)
def frame(path,expected):
    return pl.read_parquet(verified_local(Path(path),expected))


def read_frame(root,proof,name,identity=None):
    value=frame(str(root/name),proof['files'][name]['sha256'])
    return value.filter(pl.col('listing_id')==identity) if identity is not None else value


def product(day,identity,phase='1a'):
    if phase not in ('1a','1b'):raise ValueError('Unknown label phase')
    active,proof,root=session(day)
    item=next((r for r in proof['listings'] if r['listing_id']==identity),None)
    if item is None:raise ValueError('Ticker not in V66 session')
    if not item['valid_rows']:raise ValueError('Ticker has no valid warmed-up label candles; see exclusion count')
    labels=read_frame(root,proof,item['file'])
    if phase=='1a':labels=labels.with_columns(pl.col('action_1a').alias('action'),pl.col('reference_action_1a').alias('reference_action'),pl.col('label_value_1a').alias('label_value'))
    frames=dict(labels=labels,**{name:read_frame(root,proof,name+'.parquet',identity) for name in ('episodes','pairs','trades')})
    decisions=read_frame(root,proof,'decisions.parquet',identity)
    pairs=frames['pairs'].join(decisions.select('listing_id','pair_id','selected','selection_reason','selection_score','allocation_ratio','group_id'),on=['listing_id','pair_id'],how='left',validate='1:1')
    meta=dict(day=day,ticker=identity,listing_id=identity,session='V66 premarket 04:00–09:30 ET',version=VERSION,
        config=proof['config'],selection=proof['selection'],begin_us=proof['begin_us'],finish_us=proof['finish_us'],
        dataset_sha256=active['sha256'],observed_price_candles=labels.height,consumed_activity_rows=item['source_rows'],omitted_invalid_price_rows=item['excluded_rows'],
        absent_second_slots=19800-item['source_rows'],approximate_volume=None,trades=frames['trades'].height,total_price_pnl=float(frames['trades']['price_pnl'].sum()),
        both_opportunities=int(labels['both_opportunities'].sum()),actions=labels.group_by('action').len().sort('action').to_dicts(),
        pairs=pairs.to_dicts(),price_source=str(root/item['file']),scope=SCOPE,
        semantics='Saved '+phase+' targets; V66 session-reset MACD; premarket-only hindsight. Reference price P&L is 1a, not fills or a 1b portfolio.',
        exclusions=read_frame(root,proof,'excluded.parquet',identity).group_by('reason').len().to_dicts())
    return meta,frames


def metadata(day,listing_id,phase='1a'):return product(day,listing_id,phase)[0]


def chart(day,listing_id,start_us=None,seconds=900,view='combined',candle_offset=0,phase='1a',segment_end_us=None):
    if view not in ('combined','flat','held','reference'):raise ValueError('Invalid label view')
    proof,frames=product(day,listing_id,phase)
    labels=frames['labels'];n=labels.height
    offset=int(labels['time_us'].search_sorted(start_us)) if start_us is not None else candle_offset or 0
    offset=max(0,min(offset,max(0,n-1)))
    if segment_end_us is not None:
        if start_us is None or segment_end_us<start_us:raise ValueError('Segment requires ordered start and end clocks')
        # Preserve the whole saved ENTRY-to-target interval, including sparse
        # periods. Context counts observed candles, never fabricated seconds.
        stop=int(labels['time_us'].search_sorted(segment_end_us,side='right'))
        offset=max(0,offset-30);stop=min(n,stop+30)
        window=labels.slice(offset,stop-offset)
    else:window=labels.slice(offset,120)
    start=int(window['time_us'][0]);end=int(window['time_us'][-1])+1_000_000
    # Render only saved labels. classify is a display projection, no future search.
    shown=algorithm.classify(window,proof['config']['quality_threshold'],view)
    if phase=='1b':
        shown=shown.with_columns(pl.when(pl.col('episode_selected')==False).then(pl.lit('WAIT')).otherwise(pl.col('action')).alias('action'),
            pl.when(pl.col('episode_selected')==False).then(pl.lit(1.)).otherwise(pl.col('label_value')).alias('label_value'))
    if view=='held':
        shown=shown.with_columns(pl.when(pl.col('exit_gain').is_null() | ((pl.col('episode_selected')!=True).fill_null(True) if phase=='1b' else pl.lit(False))).then(pl.lit('UNLABELLED')).otherwise(pl.col('action')).alias('action'))
    candles=[dict(time=r['time_us']//1_000_000-1,endTime=r['time_us']//1_000_000,isClosed=True,**{k:r[k] for k in ('open','high','low','close')}) for r in window.iter_rows(named=True)]
    oscillator=[]
    for column,name,color in [('macd_line','MACD','var(--primary)'),('macd_signal','Signal','var(--warning)'),('macd_histogram','Histogram','var(--muted-foreground)')]:
        values=window['macd_line']-window['macd_signal'] if column=='macd_histogram' else window[column]
        oscillator.append(dict(column=column,label=name,paneKey='macd',style='histogram' if column=='macd_histogram' else 'line',color=color,lineWidth=1,data=[dict(time=int(t)//1_000_000-1,value=float(v)) for t,v in zip(window['time_us'],values)]))
    regions=[dict(start=r['start_us']//1_000_000-1,end=r['end_us']//1_000_000-1,color='var(--success)' if r['direction']==1 else 'var(--danger)',label='') for r in frames['episodes'].filter((pl.col('start_us')<end)&(pl.col('end_us')>start)).iter_rows(named=True)]
    return dict(ticker=listing_id,candles=candles,labels=shown.to_dicts(),oscillator_series=oscillator,regions=regions,start_us=start,end_us=end,
        context_candles=0,session_candles=window.height,offset=offset,previous_offset=max(0,offset-120),next_offset=offset+window.height,
        previous_available=offset>0,next_available=offset+window.height<n,dataset_sha256=proof['dataset_sha256'],quality_threshold=.9)


def market(day,search='',selection='all',offset=0,group_id=None):
    active,proof,root=session(day);rows=read_frame(root,proof,'decisions.parquet')
    if search:rows=rows.filter(pl.col('listing_id').str.to_uppercase().str.contains(search.upper(),literal=True))
    if selection!='all':rows=rows.filter(pl.col('selected')==(selection=='selected'))
    if group_id is not None:rows=rows.filter(pl.col('group_id')==group_id)
    return dict(dataset_sha256=active['sha256'],groups=proof['groups'],total=rows.height,rows=rows.sort('time_us','listing_id',nulls_last=True).slice(offset,100).to_dicts())


def timeline_rows(rows,groups,begin,finish,start_us=None,seconds=300,search='',include_rejected=False):
    """Project saved half-open ENTRY/target intervals; no label recomputation."""
    if not 60<=seconds<=3600:raise ValueError('Timeline window must be 60..3600 seconds')
    selected=rows.filter(pl.col('selected'))
    score_sum=float(selected['selection_score'].sum() or 0.)
    positive=rows.filter(pl.coalesce('selection_score','best_score')>0)
    first=positive['time_us'].min()
    start=max(begin,start_us if start_us is not None else first or begin)
    end=start+seconds*1_000_000
    if not include_rejected:positive=positive.filter(pl.col('selected'))
    if search:positive=positive.filter(pl.col('listing_id').str.to_uppercase().str.contains(search.upper(),literal=True))
    unavailable=positive.filter(pl.col('entry_target_us').is_null() & pl.col('time_us').is_between(start,end,closed='left')).height
    visible=positive.filter((pl.col('time_us')<end)&(pl.col('entry_target_us')>start)).sort('time_us','listing_id','pair_id')
    visible=visible.with_columns(pl.col('listing_id').alias('ticker'))
    visible_groups=[dict(g,target_end_us=g['end_us']) for g in groups if g['start_us']<end and g['end_us']>start]
    return dict(start_us=start,end_us=end,begin_us=begin,finish_us=finish,rows=visible.head(5000).to_dicts(),groups=visible_groups,
        total=visible.height,unavailable=unavailable,truncated=visible.height>5000,session_score_sum=score_sum)


def timeline(day,start_us=None,seconds=300,search='',include_rejected=False):
    active,proof,root=session(day)
    result=timeline_rows(read_frame(root,proof,'decisions.parquet'),proof['groups'],proof['begin_us'],proof['finish_us'],start_us,seconds,search,include_rejected)
    return dict(result,dataset_sha256=active['sha256'])
