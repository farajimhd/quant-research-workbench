"""Vectorized elapsed-time activity windows over certified compact liquidity."""
from __future__ import annotations

from .contracts import identifier,literal,digest
from .publication import Publisher
from pipelines.market_sip.events.market_day_sql import bounds

VERSION='certified-elapsed-activity-v1'
STAGING='research_stage_activity_v1'


def staging_table():
    from .schema import TABLES,Table
    output=next(t for t in TABLES if t.name=='research_activity_v1')
    return Table(STAGING,output.columns,output.order,output.partition)


def windows_contract(windows):
    if (not isinstance(windows,tuple) or not windows or tuple(sorted(set(windows)))!=windows
            or len(windows)>32 or any(type(w) is not int or not 0<w<2**32 for w in windows)):
        raise ValueError('Activity windows must be bounded unique positive UInt32 seconds')
    return windows


def source_parents(query,database,claim,scopes):
    if not scopes or len(scopes)>1024:raise ValueError('Activity context requires bounded certified parents')
    publisher=Publisher(query,database,claim);parents=[]
    for scope in sorted(scopes,key=lambda p:p.session_date):
        if scope.product!='broker_100ms':raise ValueError('Activity requires compact liquidity authority')
        receipt=publisher.receipt(scope)
        if receipt is None:raise ValueError('Activity input is not certified')
        parents.append((scope,receipt))
    return parents


def parent_hash(windows,parents):
    windows_contract(windows)
    return digest(dict(version=VERSION,windows=windows,parents=[dict(day=p.session_date,
        listing=p.listing_key,hash=r['output_hash']) for p,r in parents]))


def projection(scope,attempt,windows,parents,database='arte'):
    """One input scan and shared ordering; closed periods contribute no rows."""
    identifier(database);windows_contract(windows)
    if not parents or len(parents)>1024:raise ValueError('Activity context is absent or too large')
    if any(p.dataset_id!=scope.dataset_id or p.listing_key!=scope.listing_key or p.session_date>scope.session_date
            for p,_ in parents) or len({p.session_date for p,_ in parents})!=len(parents):
        raise ValueError('Activity parent identity/date is inconsistent')
    from .source import exchange_sessions
    if set(exchange_sessions(min(p.session_date for p,_ in parents),scope.session_date))!={p.session_date for p,_ in parents}:
        raise ValueError('Activity context has missing exchange sessions')
    filters=' OR '.join('('+p.where(r['attempt_id'])+')' for p,r in parents)
    origin=bounds(min(p.session_date for p,_ in parents),'04:00:00')
    values=[];sums=[]
    fields=('volume','notional','trade_count','execution_volume','execution_notional')
    for window in windows:
        frame=f'ORDER BY close_us RANGE BETWEEN {window*1000000-1} PRECEDING AND CURRENT ROW'
        for field in fields:sums.append(f'sum({field}) OVER ({frame}) {field}_{window}')
        values.append(f'tuple(toUInt32({window}),'+','.join(f'{f}_{window}' for f in fields)+')')
    return f"""WITH calculated AS (
      SELECT *,{','.join(sums)} FROM (
        SELECT *,{bounds_expr()} close_us FROM {database}.research_liquidity_v1
        WHERE resolution_ms=100 AND ({filters})
      )
    ) SELECT toUUID({literal(scope.dataset_id)}) dataset_id,session_date,toUInt64({scope.listing_key}) listing_key,
      toUUID({literal(attempt)}) attempt_id,resolution_ms,bucket_index,w.1 window_seconds,
      if(context_ready,w.2,CAST(NULL AS Nullable(Float64))) volume,
      if(context_ready,w.3,CAST(NULL AS Nullable(Float64))) notional,
      if(context_ready,w.4,CAST(NULL AS Nullable(UInt64))) trade_count,
      if(context_ready,w.5,CAST(NULL AS Nullable(Float64))) execution_volume,
      if(context_ready,w.6,CAST(NULL AS Nullable(Float64))) execution_notional,
      toUInt8(close_us>={origin} AND close_us-{origin}>=toUInt64(w.1)*1000000) context_ready
    FROM calculated ARRAY JOIN [{','.join(values)}] AS w WHERE session_date=toDate({literal(scope.session_date)})"""


def bounds_expr():
    return "toUInt64(toUnixTimestamp64Micro(toDateTime64(toString(session_date),6,'America/New_York')))+(toUInt64(bucket_index)+1)*100000"


def run(scope,windows,parent_scopes,query,claim,source_guard,*,database='arte'):
    from .retired_market import require_available
    require_available(database)
    from uuid import uuid4
    from .schema import TABLES
    from .publication import evidence_sql
    claim.check();source_guard()
    if scope.product!='activity':raise ValueError('Wrong activity output scope')
    parents=source_parents(query,database,claim,parent_scopes)
    if scope.parent_hash!=parent_hash(windows,parents):raise ValueError('Activity dependencies changed')
    publisher=Publisher(query,database,claim);old=publisher.receipt(scope)
    if old:return old
    attempt=str(uuid4());sql=projection(scope,attempt,windows,parents,database)
    from .schema import verify_auxiliary_storage
    verify_auxiliary_storage(query,database,(STAGING,))
    columns=query(f'SELECT name,type,compression_codec FROM system.columns WHERE database={literal(database)} '
        f'AND table={literal(STAGING)} ORDER BY position')
    actual=[(r['name'],r['type'].replace(' ',''),r['compression_codec'].replace(' ','')) for r in columns]
    expected_columns=[(c.name,c.type.replace(' ',''),'CODEC('+c.codec.replace(' ','')+')') for c in staging_table().columns]
    if actual!=expected_columns:raise ValueError('Activity staging column/codec contract differs')
    query(f'INSERT INTO {database}.{STAGING} '+sql,read=False)
    sql=f'SELECT * FROM {database}.{STAGING} WHERE '+scope.where(attempt)
    table=next(t for t in TABLES if t.name=='research_activity_v1')
    expected={k:int(v) for k,v in query(evidence_sql(table,sql,'tuple(window_seconds,resolution_ms,bucket_index)'))[0].items()}
    claim.check();source_guard()
    query(f'INSERT INTO {database}.research_activity_v1 '+sql,read=False)
    claim.check();source_guard()
    if source_parents(query,database,claim,parent_scopes)!=parents:raise ValueError('Activity parent authority changed')
    return publisher.publish(scope,attempt,expected=expected,empty_proof=scope.parent_hash)
