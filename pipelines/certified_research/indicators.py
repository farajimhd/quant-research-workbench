"""Continuous, lossless Wilder/EMA state for certified session calculations."""
from math import isfinite,log
from pipelines.market_sip.events import market_day_sql as core
from .contracts import literal

STATE_FIELDS=tuple('ema_'+str(p) for p in core.EMAS)+(
    'macd_signal','close','atr_14','avg_gain','avg_loss','sample_count')


def validate_state(prior,frames=core.FRAMES):
    if not frames or tuple(sorted(set(frames)))!=tuple(frames):raise ValueError('Invalid indicator frames')
    if prior and set(prior)!=set(frames):raise ValueError('Incomplete continuous indicator frames')
    for row in prior.values():
        if set(row)!=set(STATE_FIELDS) or any(not isfinite(float(v)) for v in row.values()):
            raise ValueError('Incomplete or nonfinite continuous indicator state')
        if (type(row['sample_count']) is not int or not 0<=row['sample_count']<2**64
                or (row['sample_count']>0 and row['close']<=0)
                or (row['sample_count']==0 and any(row.values()))
                or any(row[n]<0 for n in ('atr_14','avg_gain','avg_loss'))):
            raise ValueError('Invalid continuous indicator predecessor')


def technical_sql(database,unit,source,prior,*,frames=core.FRAMES):
    """One vectorized warehouse pass; warmup sums survive session boundaries.

    avg_gain/loss and atr_14 store partial seed sums divided by 14 during
    warmup. Readiness controls visibility; once ready they are Wilder means.
    No fixed warmup length is substituted for certified predecessor state.
    """
    validate_state(prior,frames)
    keys=','.join(map(str,frames))
    def state(name):
        values=','.join(str(prior.get(f,{}).get(name,0)) if name=='sample_count'
            else format(prior.get(f,{}).get(name,0),'.17g') for f in frames)
        default='toUInt64(0)' if name=='sample_count' else '0.'
        return f'transform(resolution_ms,[{keys}],[{values}],{default})'
    ordered='PARTITION BY resolution_ms ORDER BY session_date,bucket_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW'
    decay=13/14;half=log(.5)/log(decay)
    def wilder(value,counter,previous_counter,name):
        exponent=f'if({counter}>14,toUInt64({counter}-14),toUInt64(0))'
        previous=f'if({previous_counter}>14,toUInt64({previous_counter}-14),toUInt64(0))'
        seed=f'sumIf({value},{counter}<=14) OVER w / 14.'
        tail=f'exponentialMovingAverage({half:.17g})(if({counter}>14,{value},0.),n) OVER w'
        return f'({state(name)})*pow({decay:.17g},({exponent})-({previous}))+({seed})*pow({decay:.17g},{exponent})+({tail})'
    return f"""INSERT INTO {core.table(database,'technical',{'technical':'research_stage_indicators_v1'})}
      WITH numbered AS (SELECT *,close_int/10000. close,high_int/10000. high,low_int/10000. low,
        row_number() OVER ({ordered}) n,toUInt64({state('sample_count')}) prior_samples,
        toUInt8({state('sample_count')}>0) has_prior,lagInFrame(close_int/10000.,1,{state('close')}) OVER ({ordered}) prev
        FROM ({source}) WHERE resolution_ms IN ({keys})),
      averages AS (SELECT *,n+prior_samples samples,if(prior_samples>0,toUInt64(prior_samples-1),toUInt64(0)) prior_changes,
        n+prior_samples-1 changes,greatest(close-prev,0.) gain,greatest(prev-close,0.) loss,
        if(prev>0,greatest(high-low,abs(high-prev),abs(low-prev)),high-low) tr,
        {','.join(core.ema('close',p,prior=state('ema_'+str(p)) if prior else None)+' AS ema_'+str(p) for p in core.EMAS)}
        FROM numbered WINDOW w AS ({ordered})),
      smoothed AS (SELECT *,ema_12-ema_26 line,
        {wilder('if(prev>0,gain,0.)','changes','prior_changes','avg_gain')} ag,
        {wilder('if(prev>0,loss,0.)','changes','prior_changes','avg_loss')} al,
        {wilder('tr','samples','prior_samples','atr_14')} atr FROM averages WINDOW w AS ({ordered})),
      signals AS (SELECT *,{core.ema('line',9,prior=state('macd_signal') if prior else None)} signal
        FROM smoothed WINDOW w AS ({ordered}))
      SELECT {literal(unit.dataset_id)},session_date,{literal(unit.ticker)},toUUID({literal(unit.attempt_id)}),
        resolution_ms,bucket_index,{','.join('ema_'+str(p) for p in core.EMAS)},line,signal,line-signal,
        if(changes<14,0.,if(al<=0,100.,100.-100./(1.+ag/al))),atr,
        toUInt8(changes>=14),toUInt8(samples>=14),prev,samples,ag,al
      FROM signals WHERE session_date=toDate({literal(unit.day)})"""
