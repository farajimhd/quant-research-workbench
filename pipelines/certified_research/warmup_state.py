"""Bounded finite December seed using the shared warehouse indicator kernel."""
from datetime import date
import json
from uuid import uuid4

from . import indicators,indicator_warmup,market
from .contracts import digest,literal


def calculate(query,selected,adjustment_for_day,*,dataset_id,listing_key,ticker,target_day=date(2026,1,2)):
    """Rebase only the selected observations, then run the existing SQL kernel.

    Synthetic ordering is local to this read-only computation. No synthetic
    bars or warmup indicators are published as January market observations.
    """
    frames=market.selected_frames(tuple(sorted(selected)))
    required=indicator_warmup.requirements(frames)
    rows=[];closes={};proofs=[]
    for frame in frames:
        observations=tuple(sorted(selected[frame],key=lambda r:(str(r['session_date']),int(r['bucket_index']))))
        if len(observations)!=required['bars_per_frame'][frame]:
            raise ValueError('Finite warmup must contain exactly the required observations per frame')
        seen=set()
        for index,row in enumerate(observations):
            day=date.fromisoformat(str(row['session_date']));key=(day,int(row['bucket_index']))
            if (day.year!=2025 or day.month!=12 or day>=target_day or key in seen
                    or int(row['resolution_ms'])!=frame
                    or not 0<int(row['low_int'])<=int(row['close_int'])<=int(row['high_int'])):
                raise ValueError('Invalid, repeated or out-of-range December warmup observation')
            seen.add(key);adjustment=adjustment_for_day(day,target_day)
            values={name:float(row[name])*adjustment.factor for name in ('close_int','high_int','low_int')}
            rows.append(dict(session_date=str(target_day),resolution_ms=frame,bucket_index=index,**values))
            closes[frame]=values['close_int']/10000.
            proofs.append(dict(frame=frame,day=day,bucket_index=key[1],adjustment=adjustment.evidence_hash))
    payload='\n'.join(json.dumps(row,separators=(',',':'),allow_nan=False) for row in rows)
    if len(payload.encode())>256*1024:raise ValueError('Warmup metadata exceeds its bounded transport')
    constants=','.join('tuple('+','.join((literal(row['session_date']),str(row['resolution_ms']),
        str(row['bucket_index']),repr(row['close_int']),repr(row['high_int']),repr(row['low_int'])))+')' for row in rows)
    source=('SELECT toDate(x.1) session_date,toUInt32(x.2) resolution_ms,toUInt32(x.3) bucket_index,'
            'toFloat64(x.4) close_int,toFloat64(x.5) high_int,toFloat64(x.6) low_int '
            'FROM (SELECT arrayJoin(['+constants+']) x)')
    unit=market.MarketUnit(dataset_id,target_day,ticker,listing_key,str(uuid4()))
    statement=indicators.technical_sql('arte',unit,source,{},frames=frames)
    prefix='INSERT INTO arte.research_stage_indicators_v1'
    if not statement.startswith(prefix):raise ValueError('Shared indicator projection contract changed')
    columns={name:name for name in indicators.STATE_FIELDS if name!='close'}
    columns.update(macd_signal='signal',atr_14='atr',avg_gain='ag',avg_loss='al',sample_count='samples')
    sql='SELECT resolution_ms,'+','.join(f'argMax({column},bucket_index) AS {name}' for name,column in columns.items())
    calculated=query(sql+' FROM ('+statement[len(prefix):]+') GROUP BY resolution_ms')
    state={}
    for row in calculated:
        frame=int(row['resolution_ms'])
        if frame not in closes or frame in state:raise ValueError('Unexpected or duplicate warmup frame')
        state[frame]={name:int(row[name]) if name=='sample_count' else float(row[name]) for name in columns}
        state[frame]['close']=closes[frame]
    indicators.validate_state(state,frames)
    if set(state)!=set(frames) or any(row['sample_count']!=50 for row in state.values()):
        raise ValueError('Shared kernel did not produce the complete finite seed')
    body=dict(version='shared-kernel-finite-december-seed-v1',policy=required,target_day=target_day,
              dataset_id=dataset_id,listing_key=listing_key,inputs=selected,adjustments=proofs,state=state)
    return dict(body,certificate_hash=digest(body))
