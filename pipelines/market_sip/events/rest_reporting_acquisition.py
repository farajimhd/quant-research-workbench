"""Bounded original-clock REST acquisition into ingestion-owned ClickHouse staging.

This staging is not canonical authority. Complete canonical reconciliation and
native reporting certification are required before any derived consumer reads it.
"""
from datetime import date,datetime,time,timezone
from decimal import Decimal,InvalidOperation
from urllib.parse import urlsplit,urlunsplit,urlencode,parse_qsl,quote
import re
from uuid import UUID
import json
from pipelines.certified_research import schema
from pipelines.certified_research.contracts import identifier,literal,digest
from pipelines.certified_research.publication import SourceClaim,insert_rows

TABLE='historical_rest_reporting_stage_v1'
RECEIPTS='historical_rest_reporting_receipt_v1'


def receipt_table():
    c=schema.c
    return schema.Table(RECEIPTS,(c('acquisition_id','UUID'),c('source_date','Date'),c('ticker','LowCardinality(String)'),
        c('source_hash','FixedString(64)'),c('provider_hash','FixedString(64)'),c('rows','UInt64'),c('pages','UInt32'),
        c('page_input_hash','FixedString(64)'),c('stored_hash','FixedString(64)'),c('receipt_hash','FixedString(64)'),
        c('completed_at',"DateTime64(6,'UTC')",'Delta,ZSTD(1)')),
        ('acquisition_id','source_date','ticker'),'toYYYYMM(source_date)')


class TransportError(RuntimeError):
    """Public diagnostic category only; URLs, response bodies and keys are excluded."""


def staging_table():
    c=schema.c
    return schema.Table(TABLE,(c('acquisition_id','UUID'),c('source_date','Date'),c('ticker','LowCardinality(String)'),
        c('page_index','UInt32'),c('row_index','UInt16'),c('sip_timestamp_ns','UInt64','Delta,ZSTD(1)'),
        c('participant_timestamp_ns','Nullable(UInt64)'),c('sequence_number','UInt64','Delta,ZSTD(1)'),
        c('conditions','Array(UInt16)'),c('price','String'),c('size','String'),c('exchange','UInt16'),
        c('tape','Nullable(UInt8)'),c('correction','Nullable(UInt8)'),c('trade_id','Nullable(String)'),
        c('trf_id','Nullable(UInt16)'),c('trf_timestamp_ns','Nullable(UInt64)')),
        ('acquisition_id','ticker','page_index','row_index'),'toYYYYMM(source_date)')


def _integer(value,label,maximum,*,nullable=False):
    if value is None and nullable:return None
    if type(value) is not int or not 0<=value<=maximum:raise ValueError('Invalid REST '+label)
    return value


def _number(value,label):
    if isinstance(value,bool) or value is None:raise ValueError('Invalid REST '+label)
    if len(str(value))>128:raise ValueError('REST numeric field exceeds its bound')
    try:number=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('Invalid REST '+label) from None
    if not number.is_finite():raise ValueError('Invalid REST '+label)
    return str(value)


def _fetch(url):
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError,URLError
    try:
        with urlopen(Request(url,headers={'Accept':'application/json','User-Agent':'canonical-reporting-acquisition/1.0'}),timeout=20) as response:
            body=response.read(8*1024**2+1)
    except HTTPError as error:raise TransportError(f'REST reporting acquisition failed: HTTP {error.code}') from None
    except (URLError,TimeoutError):raise TransportError('REST reporting acquisition failed: network or TLS error') from None
    if len(body)>8*1024**2:raise ValueError('REST reporting response exceeds its byte bound')
    return json.loads(body,parse_float=Decimal)


def normalize(item):
    if not isinstance(item,dict):raise ValueError('Invalid REST trade record')
    conditions=item.get('conditions',[])
    if not isinstance(conditions,list) or len(conditions)>256:raise ValueError('Invalid REST full conditions')
    sip=_integer(item.get('sip_timestamp'),'SIP clock',2**64-1)
    if sip==0:raise ValueError('REST SIP clock is missing')
    trade_id=item.get('id')
    if trade_id is not None and (not isinstance(trade_id,str) or len(trade_id)>256):raise ValueError('Invalid REST trade identity')
    return dict(sip_timestamp_ns=sip,
        participant_timestamp_ns=_integer(item.get('participant_timestamp'),'participant clock',2**64-1,nullable=True),
        sequence_number=_integer(item.get('sequence_number'),'sequence',2**64-1),
        conditions=[_integer(v,'condition',65535) for v in conditions],price=_number(item.get('price'),'price'),
        size=_number(item.get('decimal_size',item.get('size')),'size'),
        exchange=_integer(item.get('exchange'),'exchange',65535),
        tape=_integer(item.get('tape'),'tape',255,nullable=True),
        correction=_integer(item.get('correction'),'correction',255,nullable=True),trade_id=trade_id,
        trf_id=_integer(item.get('trf_id'),'TRF identity',65535,nullable=True),
        trf_timestamp_ns=_integer(item.get('trf_timestamp'),'TRF clock',2**64-1,nullable=True))


class TradePages:
    def __init__(self,api_key,*,base_url='https://api.massive.com',page_limit=1000,max_pages=10000,fetch=None):
        origin=urlsplit(base_url)
        if (origin.scheme!='https' or not origin.hostname or origin.username or origin.password or origin.query
                or origin.fragment or origin.path not in ('','/')):raise ValueError('Invalid REST provider origin')
        if (not isinstance(api_key,str) or not api_key.strip() or type(page_limit) is not int or type(max_pages) is not int
                or not 1<=page_limit<=4096 or not 1<=max_pages<=10000):raise ValueError('Invalid bounded REST acquisition')
        self.key=api_key;self.base=base_url.rstrip('/');self.origin=(origin.scheme,origin.netloc)
        self.limit=page_limit;self.max_pages=max_pages;self.fetch=fetch

    def pages(self,ticker,day,stop):
        if type(day) is not date or not re.fullmatch(r'[A-Z0-9._-]{1,32}',ticker):raise ValueError('Invalid REST ticker/date')
        path='/v3/trades/'+quote(ticker,safe='')
        first=int(datetime.combine(day,time(),timezone.utc).timestamp())*10**9;end=first+86400*10**9
        url=self.base+path+'?'+urlencode({'timestamp.gte':first,'timestamp.lt':end,'order':'asc','sort':'timestamp','limit':self.limit})
        visited=set();last_sip=0
        for page in range(self.max_pages):
            if stop.is_set():raise InterruptedError('REST reporting acquisition stopped')
            parsed=urlsplit(url)
            if ((parsed.scheme,parsed.netloc)!=self.origin or parsed.path!=path or parsed.fragment
                    or parsed.username or parsed.password):raise ValueError('REST pagination left the configured endpoint')
            params=[(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if k.lower()!='apikey']
            if any(k.lower() in ('api_key','token','authorization') for k,v in params):raise ValueError('Unexpected REST pagination credentials')
            cursor=digest(params)
            if cursor in visited:raise ValueError('REST pagination repeated a cursor')
            visited.add(cursor)
            authenticated=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode([*params,('apiKey',self.key)]),''))
            if self.fetch is None:
                fetch=_fetch
            else:fetch=self.fetch
            try:payload=fetch(authenticated)
            except TransportError:raise
            except Exception:raise RuntimeError('REST reporting page acquisition failed') from None
            if not isinstance(payload,dict) or payload.get('status')!='OK':raise ValueError('REST reporting response was not successful')
            items=payload.get('results',[])
            if not isinstance(items,list) or len(items)>self.limit:raise ValueError('REST reporting page exceeded its row bound')
            rows=[normalize(item) for item in items]
            if any(not first<=row['sip_timestamp_ns']<end for row in rows):raise ValueError('REST reporting page left its UTC source day')
            if any(row['sip_timestamp_ns']<last_sip for row in rows):raise ValueError('REST reporting order regressed')
            if rows:
                if any(a['sip_timestamp_ns']>b['sip_timestamp_ns'] for a,b in zip(rows,rows[1:])):raise ValueError('REST reporting page is unordered')
                last_sip=rows[-1]['sip_timestamp_ns']
            next_url=payload.get('next_url')
            if next_url and (not isinstance(next_url,str) or not rows):raise ValueError('REST reporting pagination is invalid')
            yield page,rows,not bool(next_url)
            if not next_url:return
            url=next_url
        raise ValueError('REST reporting acquisition reached its page bound before completion')


def source_projection(database,acquisition_id,ticker,day):
    identifier(database);UUID(acquisition_id)
    return f"""SELECT ticker,toString(sip_timestamp_ns) sip_timestamp,
      ifNull(toString(participant_timestamp_ns),'') participant_timestamp,toString(sequence_number) sequence_number,
      arrayStringConcat(arrayMap(x->toString(x),conditions),',') conditions,price,size,toString(exchange) exchange,
      ifNull(toString(tape),'') tape,ifNull(toString(correction),'') correction
      FROM {database}.{TABLE} WHERE acquisition_id=toUUID({literal(acquisition_id)})
        AND ticker={literal(ticker)} AND source_date=toDate({literal(day)})"""


def encoded_projection(args,database,acquisition_id,ticker,day):
    from pipelines.market_sip.flatfiles.download_update_events import raw_event_union_sql
    return raw_event_union_sql(args,None,trade_only=True,include_reporting_reason=True,include_reporting_evidence=True,
        trade_source_sql=source_projection(database,acquisition_id,ticker,day))


def _verify_table(query,database,table):
    schema.verify_auxiliary_storage(query,database,(table.name,))
    compact=lambda value:value.replace(' ','').replace('`','')
    metadata=query(f'SELECT engine,sorting_key,partition_key FROM system.tables WHERE database={literal(database)} AND name={literal(table.name)}')
    if (len(metadata)!=1 or metadata[0]['engine']!='MergeTree' or compact(metadata[0]['sorting_key'])!=','.join(table.order)
            or compact(metadata[0]['partition_key'])!=compact(table.partition)):
        raise ValueError('REST reporting table engine/key differs')
    columns=query(f'SELECT name,type,compression_codec FROM system.columns WHERE database={literal(database)} AND table={literal(table.name)} ORDER BY position')
    if [(r['name'],compact(r['type']),compact(r['compression_codec'])) for r in columns]!=[
            (c.name,compact(c.type),'CODEC('+compact(c.codec)+')') for c in table.columns]:
        raise ValueError('REST reporting table schema/codec differs')


def _stored(query,database,where):
    fields=','.join(c.name for c in staging_table().columns)
    sums=','.join(f'sum(reinterpretAsUInt64(substring(h,{offset},8))) h{i}' for i,offset in enumerate((1,9,17,25)))
    rows=query(f'SELECT count() n,uniqExact(tuple(page_index,row_index)) unique_rows,{sums} '
        f'FROM (SELECT page_index,row_index,SHA256(toString(tuple({fields}))) h FROM {database}.{TABLE} WHERE {where})')
    if len(rows)!=1:raise ValueError('REST reporting stored fingerprint is ambiguous')
    evidence={k:int(v) for k,v in rows[0].items()}
    if evidence['n']!=evidence['unique_rows']:raise ValueError('REST reporting staging is duplicated')
    return evidence['n'],digest(evidence)


def acquire(query,database,claim,guard,provider,acquisition_id,ticker,day,source_hash,stop,*,progress=None):
    """Seal acquired inputs, then resume using SQL fingerprints without HTTP.

    A sealed input cannot be automatically healed after tampering. Source hash
    must identify the frozen canonical source-day certificate, not provider keys.
    This receipt still does not certify reporting coverage or repaired events.
    """
    identifier(database);UUID(acquisition_id)
    if (not isinstance(claim,SourceClaim) or (database!='q_live' and not database.startswith('certified_research_test_'))
            or not isinstance(source_hash,str) or not re.fullmatch('[0-9a-f]{64}',source_hash) or type(day) is not date
            or not re.fullmatch(r'[A-Z0-9._-]{1,32}',ticker)):
        raise ValueError('Invalid REST acquisition ownership or source binding')
    if stop.is_set():raise InterruptedError('REST reporting acquisition stopped')
    claim.check();guard()
    for table in (staging_table(),receipt_table()):_verify_table(query,database,table)
    provider_hash=digest(dict(version='original-rest-reporting-v1',origin=provider.base,
        page_limit=provider.limit,max_pages=provider.max_pages,columns=[vars(c) for c in staging_table().columns]))
    where=f'acquisition_id=toUUID({literal(acquisition_id)}) AND source_date=toDate({literal(day)}) AND ticker={literal(ticker)}'
    def read():
        receipts=query(f'SELECT * FROM {database}.{RECEIPTS} WHERE {where}')
        if not receipts:return None
        if len(receipts)!=1:raise ValueError('REST reporting acquisition receipt is duplicated')
        receipt=receipts[0]
        proof=dict(receipt);fingerprint=proof.pop('receipt_hash')
        if (digest(proof)!=fingerprint or receipt['source_hash']!=source_hash or receipt['provider_hash']!=provider_hash):
            raise ValueError('REST reporting acquisition receipt binding differs')
        count,stored_hash=_stored(query,database,where)
        if count!=int(receipt['rows']) or stored_hash!=receipt['stored_hash']:
            raise ValueError('Sealed REST reporting inputs changed')
        claim.check();guard()
        return dict(receipt,canonical_certified=False)
    existing=read()
    if existing is not None:
        if progress:progress(dict(status='reused',rows=int(existing['rows']),pages=int(existing['pages']),sealed_input=True))
        return existing
    staged=stage(query,database,claim,guard,provider,acquisition_id,ticker,day,stop,progress=progress)
    count,stored_hash=_stored(query,database,where)
    if count!=staged['rows']:raise ValueError('REST reporting input count changed before sealing')
    proof=dict(acquisition_id=acquisition_id,source_date=str(day),ticker=ticker,source_hash=source_hash,
        provider_hash=provider_hash,rows=count,pages=staged['pages'],page_input_hash=staged['input_hash'],stored_hash=stored_hash,
        completed_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
    claim.check();guard()
    if stop.is_set():raise InterruptedError('REST reporting stopped before acquisition seal')
    insert_rows(query,database,RECEIPTS,[dict(proof,receipt_hash=digest(proof))])
    verified=read()
    if verified is None:raise ValueError('REST reporting acquisition receipt insert is uncertain')
    return verified


def stage(query,database,claim,guard,provider,acquisition_id,ticker,day,stop,*,progress=None):
    """Immutable pages resume after uncertain writes by independently reading back.

    Return an input fingerprint, never a canonical reporting certificate.
    Stage installation is explicit and must already have passed placement checks.
    """
    identifier(database);UUID(acquisition_id)
    if not isinstance(claim,SourceClaim) or (database!='q_live' and not database.startswith('certified_research_test_')):
        raise ValueError('REST reporting staging requires canonical source ownership and namespace')
    claim.check();guard();schema.verify_auxiliary_storage(query,database,(TABLE,))
    table=staging_table()
    metadata=query(f'SELECT engine,sorting_key,partition_key FROM system.tables WHERE database={literal(database)} AND name={literal(TABLE)}')
    compact=lambda value:value.replace(' ','').replace('`','')
    if (len(metadata)!=1 or metadata[0]['engine']!='MergeTree'
            or compact(metadata[0]['sorting_key'])!=','.join(table.order)
            or compact(metadata[0]['partition_key'])!=compact(table.partition)):
        raise ValueError('REST reporting staging engine/key differs')
    columns=query(f'SELECT name,type,compression_codec FROM system.columns WHERE database={literal(database)} '
                  f'AND table={literal(TABLE)} ORDER BY position')
    if [(r['name'],compact(r['type']),compact(r['compression_codec'])) for r in columns]!=[
            (c.name,compact(c.type),'CODEC('+compact(c.codec)+')') for c in table.columns]:
        raise ValueError('REST reporting staging schema/codec differs')
    total=0;hashes=[];final_seen=False
    for page,rows,complete in provider.pages(ticker,day,stop):
        if (page!=len(hashes) or final_seen or type(complete) is not bool or not isinstance(rows,list)
                or len(rows)>4096):raise ValueError('REST reporting page sequence is invalid')
        final_seen=complete
        claim.check();guard()
        desired=[dict(acquisition_id=acquisition_id,source_date=str(day),ticker=ticker,page_index=page,row_index=index,**row)
                 for index,row in enumerate(rows)]
        where=f'acquisition_id=toUUID({literal(acquisition_id)}) AND ticker={literal(ticker)} AND source_date=toDate({literal(day)}) AND page_index={page}'
        actual=query(f'SELECT * FROM {database}.{TABLE} WHERE {where} ORDER BY row_index')
        found=set()
        for item in actual:
            index=int(item['row_index'])
            if index in found or not 0<=index<len(desired) or item!=desired[index]:raise ValueError('REST reporting page changed or duplicated')
            found.add(index)
        missing=[row for index,row in enumerate(desired) if index not in found]
        if stop.is_set():raise InterruptedError('REST reporting stopped before publication')
        if missing:claim.check();guard();insert_rows(query,database,TABLE,missing)
        if missing and query(f'SELECT * FROM {database}.{TABLE} WHERE {where} ORDER BY row_index')!=desired:
            raise ValueError('REST reporting page did not read back exactly')
        total+=len(desired);hashes.append(digest(desired))
        if progress:progress(dict(status='published' if missing else 'reused',page=page,rows=len(desired),total_rows=total,final_page=complete))
    if not final_seen:raise ValueError('REST reporting acquisition has no final page proof')
    claim.check();guard();schema.verify_auxiliary_storage(query,database,(TABLE,))
    census=query(f'SELECT count() n,uniqExact(tuple(page_index,row_index)) unique_rows,max(page_index) last_page '
                 f'FROM {database}.{TABLE} WHERE acquisition_id=toUUID({literal(acquisition_id)}) AND ticker={literal(ticker)} AND source_date=toDate({literal(day)})')[0]
    if int(census['n'])!=total or int(census['unique_rows'])!=total or (total and int(census['last_page'])>=len(hashes)):
        raise ValueError('REST reporting staging contains unplanned pages')
    return dict(rows=total,pages=len(hashes),input_hash=digest(dict(day=str(day),ticker=ticker,pages=hashes)),
                canonical_certified=False)
