"""Rebuild missing symbol intervals from retained Gateway history, without APIs."""
from dataclasses import asdict
from datetime import UTC,date,datetime,time,timedelta
import json,re
from . import ticker_events as gateway
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import verify_sec_write_storage

TABLES=(gateway.TICKER_EVENT_ENTITY_TABLE,gateway.TICKER_EVENT_TABLE,
    gateway.TICKER_EVENT_COVERAGE_TABLE,gateway.SYMBOL_INTERVAL_TABLE)
VOLATILE={'observed_at_utc','source_run_id','inserted_at'}


def semantic(row):return {key:value for key,value in row.items() if key not in VOLATILE}


def compile_intervals(entity,coverage,events,binding,ticker_bindings,context_start,*,correction=None,through=None,origin=None):
    """Only a complete mapped timeline may supply an interval origin."""
    if type(context_start) is not date or context_start<gateway.TICKER_EVENT_HISTORY_START:
        raise ValueError('Retained ticker-history context precedes its supported history')
    through=context_start if through is None else through
    if type(through) is not date or through<context_start:raise ValueError('Invalid retained-history end bound')
    finished=gateway.parse_datetime(coverage.get('last_success_at_utc'))
    updated=gateway.parse_datetime(entity.provider_last_updated_utc)
    if (finished is None or finished<datetime.combine(through+timedelta(days=1),time(),UTC)
            or (updated is not None and updated>finished)):
        raise ValueError('Retained history is stale or does not cover the requested end horizon')
    if (not entity.active or entity.provider_identifier_kind!='composite_figi'
            or not re.fullmatch('[A-Z0-9]{12}',entity.composite_figi)
            or entity.provider_identifier!=entity.composite_figi
            or binding.status!='mapped' or coverage.get('source_status') not in ('completed','covered_empty')
            or coverage.get('mapping_status')!='mapped'
            or (coverage.get('security_id'),coverage.get('listing_id'))!=(binding.security_id,binding.listing_id)):
        raise ValueError('Retained ticker history needs complete active identity mapping; inactive/empty history requires separate origin/lifecycle evidence')
    response_hash=coverage.get('source_response_sha256','')
    if not re.fullmatch('[0-9a-f]{64}',response_hash or ''):
        raise ValueError('Retained ticker-history source proof is absent')
    if len(events)!=int(coverage['active_event_count']) or len(events)!=int(coverage['event_count']):
        raise ValueError('Retained ticker-history counts differ from coverage')
    changes=[];raw=[];ids=set();dates=[]
    for row in events:
        payload=json.loads(row['event_payload_json'])
        if not isinstance(payload,dict):raise ValueError('Retained ticker event is not an object')
        encoded=gateway.canonical_json(payload)
        payload_hash=gateway.sha256_text(encoded);day=gateway.parse_event_date(payload.get('date'))
        kind=str(payload.get('type') or '').strip().lower()
        expected=gateway.stable_id('ticker_event',f'{entity.provider_entity_key}|{day.isoformat()}|{kind}|{payload_hash}')
        if (not kind or row['ticker_event_id']!=expected or expected in ids
                or row['source_content_sha256']!=payload_hash or row['source_response_sha256']!=response_hash
                or row['provider_entity_key']!=entity.provider_entity_key
                or row['security_id']!=binding.security_id or row['listing_id']!=binding.listing_id
                or str(row['event_date'])!=str(day) or row['event_type']!=kind):
            raise ValueError('Retained ticker event changed identity, date, content or source proof')
        ids.add(expected);dates.append(day);raw.append(payload)
        if kind=='ticker_change':
            if not isinstance(payload.get('ticker_change'),dict):raise ValueError('Retained ticker-change payload is incomplete')
            ticker=str((payload.get('ticker_change') or {}).get('ticker') or '').strip().upper()
            if (row.get('ticker') or '')!=ticker:raise ValueError('Retained ticker payload differs from its normalized ticker')
            changes.append((day,ticker,expected))
    if dates and (str(min(dates)),str(max(dates)))!=(str(coverage['min_event_date']),str(coverage['max_event_date'])):
        raise ValueError('Retained history date census differs from coverage')
    if not dates and (coverage['source_status']!='covered_empty' or coverage.get('min_event_date') is not None or coverage.get('max_event_date') is not None):
        raise ValueError('Empty retained history lacks explicit covered-empty evidence')
    if not dates and response_hash not in {
            gateway.sha256_text(gateway.canonical_json(dict(status='OK',name=name,events=[])))
            for name in (entity.entity_name,'')}:
        raise ValueError('Empty history lacks an original successful response signature')
    source_system='massive'
    if correction is not None:
        gateway.validate_provider_signature(correction,raw)
        changes=[(date.fromisoformat(day),ticker,gateway.stable_id('ticker_event_correction',
            f'{correction.correction_id}|{day}|{ticker}')) for day,ticker in correction.canonical_timeline]
        source_system=gateway.CORRECTION_SOURCE_SYSTEM
    origin_event_id=None
    if origin is not None:
        current_type=str(json.loads(entity.source_payload_json).get('type') or '')
        if (not origin.active or not gateway.TICKER_EVENT_HISTORY_START<=origin.as_of<=context_start
                or origin.composite_figi!=entity.composite_figi or origin.share_class_figi!=entity.share_class_figi
                or not origin.cik or not entity.cik or int(origin.cik)!=int(entity.cik)
                or origin.exchange!=entity.primary_exchange or origin.currency!=entity.currency_name.upper()
                or not current_type or origin.provider_type!=current_type
                or origin.ticker not in ticker_bindings
                or (ticker_bindings[origin.ticker].status,ticker_bindings[origin.ticker].security_id,ticker_bindings[origin.ticker].listing_id)
                    !=('mapped',binding.security_id,binding.listing_id)):
            raise ValueError('Dated origin conflicts with canonical issuer, listing, venue or security identity')
        earlier=[change for change in changes if change[0]<=origin.as_of]
        if earlier:
            if max(earlier,key=lambda value:value[0])[1]!=origin.ticker:raise ValueError('Dated origin conflicts with retained timeline')
        else:
            event_id=gateway.stable_id('verified_symbol_origin',origin.evidence_hash+'|'+response_hash)
            changes.append((origin.as_of,origin.ticker,event_id))
            origin_event_id=event_id
    if not changes or min(day for day,_,_ in changes)>context_start:
        raise ValueError('Retained history omits the requested symbol origin; a dated origin is required')
    if max(changes,key=lambda value:value[0])[1]!=entity.current_ticker.strip().upper():
        raise ValueError('Retained timeline conflicts with the active inventory ticker')
    for _,ticker,_ in changes:
        if ticker and (ticker not in ticker_bindings or ticker_bindings[ticker].status!='mapped'
                or ticker_bindings[ticker].security_id!=binding.security_id):
            raise ValueError('Historical alias lacks an unambiguous canonical binding; no alias will be skipped')
    intervals=gateway.build_symbol_intervals(entity,binding,changes,run_id='retained-symbol-repair-v1',
        observed_at=datetime.now(UTC),source_system=source_system,ticker_bindings=ticker_bindings)
    for interval in intervals:
        if interval['source_event_id']==origin_event_id:
            interval['mapping_status']='origin_verified_scope_pending'
            interval['source_system']='verified_dated_symbol_origin_v1'
    return intervals


def scoped_rows(client,database,table,keys,*,maximum_rows=65536,maximum_bytes=32*1024**2):
    literals=','.join(gateway.sql_string(key) for key in keys)
    where=f'provider_entity_key IN ({literals}) AND is_deleted=0'
    payload="length(event_payload_json)" if table==gateway.TICKER_EVENT_TABLE else 'length(ticker)'
    census=gateway.query_json_each_row(client,f'SELECT count() n,sum({payload}) bytes FROM {gateway.table(database,table)} FINAL WHERE {where}')
    if len(census)!=1 or int(census[0]['n'])>maximum_rows or int(census[0]['bytes'])>maximum_bytes:
        raise ValueError('Retained symbol metadata exceeds its bounded row/byte budget')
    rows=gateway.query_json_each_row(client,f'SELECT * FROM {gateway.table(database,table)} FINAL WHERE {where} LIMIT {maximum_rows+1} '
        f"SETTINGS max_result_bytes={maximum_bytes+maximum_rows*2048},result_overflow_mode='throw'")
    actual_bytes=sum(len(str(row['event_payload_json'] if table==gateway.TICKER_EVENT_TABLE else row['ticker']).encode('utf-8')) for row in rows)
    if (len(rows)!=int(census[0]['n']) or actual_bytes!=int(census[0]['bytes'])
            or any(row['provider_entity_key'] not in keys for row in rows)):
        raise ValueError('Retained symbol metadata changed during its bounded read')
    return rows


def rebuild(client,*,database,identifiers,context_start,claim,execute=False,guard=lambda:None,through=None,origin_loader=lambda:{}):
    identifiers=gateway.bounded_reference_keys(identifiers)
    if not identifiers:raise ValueError('Retained symbol repair requires explicit bounded identities')
    def check():claim.check();guard();verify_sec_write_storage(client,database,TABLES)
    def snapshot():
        check()
        selected=','.join(gateway.sql_string(value.upper()) for value in identifiers)
        inventory_census=gateway.query_json_each_row(client,f'SELECT count() n,sum(length(source_payload_json)) bytes '
            f'FROM {gateway.table(database,gateway.TICKER_EVENT_ENTITY_TABLE)} FINAL WHERE is_deleted=0 '
            f'AND upper(provider_identifier) IN ({selected})')
        if (len(inventory_census)!=1 or int(inventory_census[0]['n'])>1000
                or int(inventory_census[0]['bytes'])>32*1024**2):
            raise ValueError('Retained inventory exceeds bounded metadata limits')
        entities=gateway.load_ticker_event_inventory(client,database=database,only_identifiers=identifiers)
        if (len(entities)!=int(inventory_census[0]['n'])
                or sum(len(entity.source_payload_json.encode('utf-8')) for entity in entities)!=int(inventory_census[0]['bytes'])):
            raise ValueError('Retained inventory changed during bounded read')
        found={entity.provider_identifier.upper() for entity in entities}
        if found!={value.upper() for value in identifiers} or len(found)!=len(entities):
            raise ValueError('Requested retained identities are missing or competing')
        keys=tuple(entity.provider_entity_key for entity in entities)
        coverage=gateway.load_ticker_event_coverage(client,database=database,entity_keys=keys)
        events=scoped_rows(client,database,gateway.TICKER_EVENT_TABLE,keys)
        bindings=gateway.load_canonical_bindings(client,database=database,entities=entities)
        tickers=gateway.load_ticker_bindings(client,database=database,entities=entities,entity_bindings=bindings)
        corrections=gateway.load_ticker_event_corrections()
        origins=origin_loader()
        proof=gateway.sha256_text(gateway.canonical_json(dict(entities=[asdict(entity) for entity in entities],
            coverage=coverage,events=sorted(events,key=lambda row:row['ticker_event_id']),
            bindings={key:asdict(value) for key,value in bindings.items()},
            tickers={key:{ticker:asdict(value) for ticker,value in values.items()} for key,values in tickers.items()},
            corrections=[asdict(value) for value in corrections],origins={key:asdict(value) for key,value in origins.items()})))
        desired=[];by_entity={key:[] for key in keys}
        for row in events:by_entity[row['provider_entity_key']].append(row)
        for entity in entities:
            key=entity.provider_entity_key
            if key not in coverage or key not in bindings:raise ValueError('Retained identity coverage/binding is missing')
            desired.extend(compile_intervals(entity,coverage[key],by_entity[key],
                bindings[key],tickers.get(key,{}),context_start,correction=gateway.correction_for_entity(entity,corrections),
                through=through,origin=origins.get(entity.composite_figi)))
        return proof,keys,desired
    proof,keys,desired=snapshot()
    wanted={row['symbol_interval_id']:semantic(row) for row in desired}
    if len(wanted)!=len(desired):raise ValueError('Retained timeline generated duplicate interval identities')
    def missing():
        actual=scoped_rows(client,database,gateway.SYMBOL_INTERVAL_TABLE,keys);seen=set()
        for row in actual:
            key=row['symbol_interval_id']
            if key in seen or wanted.get(key)!=semantic(row):
                raise ValueError('Existing symbol intervals conflict with retained repair; explicit reconciliation required')
            seen.add(key)
        return [row for row in desired if row['symbol_interval_id'] not in seen]
    pending=missing()
    if execute:
        if snapshot()[0]!=proof:raise ValueError('Retained source changed before interval publication')
        for offset in range(0,len(pending),1000):
            check();gateway.insert_json_rows(client,database,gateway.SYMBOL_INTERVAL_TABLE,pending[offset:offset+1000])
        if missing():raise ValueError('Rebuilt symbol intervals did not read back exactly')
        if snapshot()[0]!=proof:raise ValueError('Retained source changed after interval publication')
    check()
    return dict(status='repaired' if execute else 'planned',intervals=len(desired),
        inserted=len(pending) if execute else 0,pending=len(pending),source_hash=proof)
