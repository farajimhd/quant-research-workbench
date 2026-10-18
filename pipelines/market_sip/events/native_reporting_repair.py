"""Restart-safe high-bit repair from frozen ingestion-owned original NS evidence.

One call performs one bounded control step. Active server mutations are
returned to the controller, never slept on, canceled or blindly reissued.
No market rows or flatfiles leave ClickHouse.
"""
from datetime import date,datetime,timezone
import json
from pipelines.certified_research.contracts import identifier,literal,digest
from pipelines.certified_research.publication import SourceClaim,insert_rows
from pipelines.certified_research.schema import verify_auxiliary_storage
from . import reporting_evidence as ns
from .trade_reporting_flags import reason_sql,flags_sql,REVISION

HEADER='historical_native_reporting_repair_v1'
VERSION='native-reporting-high-bit-repair-v1'


def desired_flags_sql(evaluated,counts):
    """Build only minority key sets after complete immutable evidence audit."""
    sizes={0:int(counts['unknown']),64:int(counts['timely']),192:int(counts['delayed'])}
    if any(n<0 for n in sizes.values()) or sum(sizes.values())!=int(counts['n']):
        raise ValueError('Reporting class counts do not cover the audited source')
    default=max(sizes,key=lambda flag:(sizes[flag],-flag))
    choices=[]
    for flag,n in sizes.items():
        if flag==default or n==0:continue
        choices.extend((f'(ticker,ordinal) IN (SELECT ticker,ordinal FROM {evaluated} WHERE {flags_sql("expected_reason")}={flag})',str(flag)))
    return 'multiIf('+','.join((*choices,str(default)))+')' if choices else str(default)


def header_ddl(database):
    identifier(database)
    return f"""CREATE TABLE IF NOT EXISTS {database}.{HEADER} (
      source_date Date CODEC(ZSTD(1)),source_hash FixedString(64) CODEC(ZSTD(1)),
      evidence_hash FixedString(64) CODEC(ZSTD(1)),target String CODEC(ZSTD(1)),
      fields Array(String) CODEC(ZSTD(1)),low_fields_hash FixedString(64) CODEC(ZSTD(1)),
      marker FixedString(64) CODEC(ZSTD(1)),created_at DateTime64(6,'UTC') CODEC(Delta(8),ZSTD(1))
    ) ENGINE=MergeTree ORDER BY (source_date,source_hash)
    SETTINGS storage_policy='live_market_ssd',default_compression_codec='ZSTD(1)'"""


def evidence_fingerprint_sql(evidence,day):
    return ns.evidence_fingerprint_sql(evidence,day)


def _fingerprint(source,fields):
    sums=','.join(f'sum(reinterpretAsUInt64(substring(h,{offset},8))) h{i}' for i,offset in enumerate((1,9,17,25)))
    return f'SELECT count() n,{sums} FROM (SELECT SHA256(toString(tuple({fields}))) h FROM ({source}))'


def _hash(query,sql):
    rows=query(sql)
    if len(rows)!=1:raise ValueError('Reporting repair fingerprint is ambiguous')
    return digest({k:int(v) for k,v in rows[0].items()})


def step(query,source,expected_evidence_hash,claim,source_guard,*,database='q_live',table=ns.TABLE,target=None,coverage='historical_trade_reporting_coverage_v1',prepare_only=False):
    """Repair only from a frozen acquisition receipt, not a newly trusted clock snapshot.

    expected_evidence_hash must come from the ingestion-owned reconciliation
    receipt. This adapter does not acquire or establish that source provenance.
    """
    day=date.fromisoformat(str(source['source_date']));identifier(database);identifier(table)
    target=target or f'market_sip_compact.events_{day.year}';target_db,target_table=target.split('.')
    identifier(target_db);identifier(target_table)
    fixture=database.startswith('certified_research_test_') and target_db==database
    if not fixture and (database!='q_live' or target!=f'market_sip_compact.events_{day.year}'
            or table!=ns.TABLE or coverage!='historical_trade_reporting_coverage_v1' or not isinstance(claim,SourceClaim)):
        raise ValueError('Native repair requires canonical source ownership and namespaces')
    if len(expected_evidence_hash)!=64 or any(c not in '0123456789abcdef' for c in expected_evidence_hash):
        raise ValueError('Frozen native evidence fingerprint is required')
    def guard():claim.check();source_guard()
    guard();verify_auxiliary_storage(query,database,(table,))
    policy='live_market_ssd' if fixture else 'sip_raw_ssd'
    routing=query(f'SELECT disk_name FROM system.storage_policies ARRAY JOIN disks AS disk_name WHERE policy_name={literal(policy)}')
    metadata=query(f'SELECT storage_policy FROM system.tables WHERE database={literal(target_db)} AND name={literal(target_table)}')
    parts=query(f'SELECT DISTINCT disk_name FROM system.parts WHERE active AND database={literal(target_db)} AND table={literal(target_table)}')
    if (not routing or any(r['disk_name']!=policy for r in routing)
            or metadata!=[dict(storage_policy=policy)] or any(r['disk_name']!=policy for r in parts)):
        raise ValueError('Canonical repair target is missing or incorrectly placed')
    source_hash=digest({k:v for k,v in source.items() if k!='updated_at'})
    first=int(source['first_sip_timestamp_us']);last=int(source['last_sip_timestamp_us']);expected=int(source['trade_event_rows'])
    marker=digest(dict(version=VERSION,day=day,source=source_hash,evidence=expected_evidence_hash,target=target,revision=REVISION))
    mutations=query(f'SELECT mutation_id,command,is_done,parts_to_do,latest_fail_reason FROM system.mutations '
        f'WHERE database={literal(target_db)} AND table={literal(target_table)} ORDER BY create_time,mutation_id')
    own=[m for m in mutations if marker in m['command']]
    if any(not int(m['is_done']) and marker not in m['command'] for m in mutations):
        raise ValueError('Unrelated canonical mutation must finish before native reporting repair')
    if any(m['latest_fail_reason'] for m in own):raise ValueError('Native reporting mutation requires attention: '+own[-1]['latest_fail_reason'])
    active=[m for m in own if not int(m['is_done'])]
    if active:
        verify_auxiliary_storage(query,database,(HEADER,))
        headers=query(f'SELECT evidence_hash,target,marker FROM {database}.{HEADER} '
            f'WHERE source_date=toDate({literal(day)}) AND source_hash={literal(source_hash)}')
        if headers!=[dict(evidence_hash=expected_evidence_hash,target=target,marker=marker)]:
            raise ValueError('Active native mutation lacks its immutable repair header')
        # Do not rescan a source day on every status poll. Frozen NS and all
        # preserved event fields are fully rechecked after the mutation ends.
        return dict(status='mutating',mutations=[m['mutation_id'] for m in active],parts_remaining=sum(int(m['parts_to_do']) for m in active))
    evidence=database+'.'+table
    if _hash(query,evidence_fingerprint_sql(evidence,day))!=expected_evidence_hash:
        raise ValueError('Native input evidence differs from its frozen acquisition receipt')
    # The source audit also recomputes condition/clock reasons. Ignore only the
    # high flags currently being repaired; SIP association and coverage still
    # must match exactly.
    audit=ns.certification_sql(target,evidence,day,source_hash,first,last,event_date_pruning=not fixture)
    computed=reason_sql("arrayStringConcat(reporting_condition_entries,',')",
        "ifNull(toString(participant_timestamp_ns),'')",'toString(sip_timestamp_ns)')
    flags=flags_sql('r.expected_reason')
    audit=audit.replace(f'OR bitAnd(e.event_meta,192)!={flags}','')
    counts={k:int(v) for k,v in query(audit)[0].items()}
    if counts['n']!=expected or counts['unique_keys']!=expected or counts['bad']:
        raise ValueError('Native evidence lacks exact original-clock/reason/canonical coverage')
    fields=[r['name'] for r in query(f'SELECT name FROM system.columns WHERE database={literal(target_db)} AND table={literal(target_table)} ORDER BY position')]
    if not {'ticker','ordinal','sip_timestamp_us','event_meta'}<=set(fields):raise ValueError('Canonical repair columns are missing')
    for field in fields:identifier(field)
    selected=f'sip_timestamp_us BETWEEN {first} AND {last}'
    if not fixture:selected+=' AND '+ns.event_date_predicate(first,last)
    projection=','.join("if(bitAnd(event_meta,1)=1,bitAnd(event_meta,63),event_meta) AS event_meta" if f=='event_meta' else f for f in fields)
    low_sql=_fingerprint(f'SELECT {projection} FROM {target} WHERE {selected}',','.join(fields))
    low_hash=_hash(query,low_sql)
    guard();query(header_ddl(database),read=False);verify_auxiliary_storage(query,database,(HEADER,))
    where=f'source_date=toDate({literal(day)}) AND source_hash={literal(source_hash)}'
    columns='source_date,source_hash,evidence_hash,target,fields,low_fields_hash,marker'
    wanted=dict(source_date=str(day),source_hash=source_hash,evidence_hash=expected_evidence_hash,target=target,
        fields=fields,low_fields_hash=low_hash,marker=marker)
    old=query(f'SELECT {columns} FROM {database}.{HEADER} WHERE {where}')
    if old and old!=[wanted]:raise ValueError('Native repair source, evidence or preserved fields changed')
    if not old:
        guard();insert_rows(query,database,HEADER,[dict(wanted,created_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))])
        if query(f'SELECT {columns} FROM {database}.{HEADER} WHERE {where}')!=[wanted]:raise ValueError('Native repair header insert is uncertain')
    final={k:int(v) for k,v in query(ns.certification_sql(target,evidence,day,source_hash,first,last,event_date_pruning=not fixture))[0].items()}
    identifier(coverage)
    guard();query(ns.coverage_ddl(database,coverage),read=False);verify_auxiliary_storage(query,database,(coverage,))
    coverage_where=f'source_date=toDate({literal(day)}) AND revision={literal(REVISION)}'
    previous=query(f'SELECT source_digest,status,details FROM {database}.{coverage} FINAL WHERE {coverage_where}')
    if len(previous)>1 or (previous and previous[0]['source_digest']!=source_hash):
        raise ValueError('Native repair cannot supersede a different canonical source certificate')
    transition=dict(source_digest=source_hash,status='native_mutating',
        details=json.dumps(dict(marker=marker,evidence_hash=expected_evidence_hash,version=VERSION),sort_keys=True))
    wanted_complete=dict(source_digest=source_hash,status='complete',
        details=json.dumps(dict(counts=final,evidence_version=ns.VERSION,evidence_hash=expected_evidence_hash),sort_keys=True))
    if previous and previous[0]['status']=='native_mutating' and previous!=[transition]:
        raise ValueError('Another native repair owns the reporting transition')
    if previous!=[wanted_complete] and previous!=[transition]:
        # Invalidate old reporting admission BEFORE starting a mutation. A
        # completed server job with unfinished certification remains blocked.
        guard();insert_rows(query,database,coverage,[dict(source_date=str(day),revision=REVISION,**transition,
            raw_table=evidence,map_table=evidence,updated_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))])
        if query(f'SELECT source_digest,status,details FROM {database}.{coverage} FINAL WHERE {coverage_where}')!=[transition]:
            raise ValueError('Native reporting transition insert is uncertain')
        previous=[transition]
    if final['bad']:
        if own:raise ValueError('Completed native mutation failed its independent flag verification')
        evaluated=f'(SELECT ticker,ordinal,{computed} expected_reason FROM {evidence} WHERE source_date=toDate({literal(day)}))'
        desired=f'toUInt8(bitOr(bitAnd(event_meta,63),{desired_flags_sql(evaluated,counts)}))'
        if prepare_only:
            return dict(status='prepared',marker=marker,target=target,
                predicate=f'({selected}) AND bitAnd(event_meta,1)=1',assignment=desired)
        guard();query(f'ALTER TABLE {target} UPDATE event_meta={desired} WHERE {selected} AND bitAnd(event_meta,1)=1 '
            f'AND {literal(marker)}={literal(marker)} SETTINGS mutations_sync=0',read=False)
        return dict(status='submitted',marker=marker)
    if _hash(query,low_sql)!=wanted['low_fields_hash']:raise ValueError('Native repair changed preserved canonical fields')
    if _hash(query,evidence_fingerprint_sql(evidence,day))!=expected_evidence_hash:
        raise ValueError('Native input evidence changed before certification')
    guard()
    ns._publish_verified(query,database,table,coverage,day,source_hash,expected,final,
        expected_evidence_hash,transition if previous==[transition] else None)
    guard()
    return dict(status='complete',counts=final,marker=marker)


def batch_step(query,units,claim,source_guard,**kwargs):
    """One monthly mutation; retain independent daily headers and certification.

    Units are (source receipt, frozen evidence hash). The batch is bounded to
    one canonical month. Interruption never cancels or blindly resubmits work:
    each day marker is present in the shared server mutation command.
    """
    if not units or len(units)>31:
        raise ValueError('Native repair batch requires 1 to 31 source days')
    days=[date.fromisoformat(str(source['source_date'])) for source,_ in units]
    if len(set(days))!=len(days) or len({(d.year,d.month) for d in days})!=1:
        raise ValueError('Native repair batch must contain unique days in one month')
    target=kwargs.get('target') or f'market_sip_compact.events_{days[0].year}'
    target_db,target_table=target.split('.')
    identifier(target_db);identifier(target_table)
    active=query(f'SELECT command FROM system.mutations WHERE database={literal(target_db)} '
        f'AND table={literal(target_table)} AND NOT is_done')
    for source,frozen in units:
        day=date.fromisoformat(str(source['source_date']))
        source_hash=digest({k:v for k,v in source.items() if k!='updated_at'})
        marker=digest(dict(version=VERSION,day=day,source=source_hash,evidence=frozen,target=target,revision=REVISION))
        if any(marker in row.get('command','') for row in active):
            # Visit a day actually owned by the mutation before any already
            # certified day whose marker was not needed in that command.
            return step(query,source,frozen,claim,source_guard,prepare_only=True,**kwargs)
    prepared=[];completed=[]
    for source,frozen in units:
        result=step(query,source,frozen,claim,source_guard,prepare_only=True,**kwargs)
        if result['status']=='mutating':
            return result
        if result['status']=='complete':completed.append(str(source['source_date']))
        elif result['status']=='prepared':prepared.append(result)
        else:raise ValueError('Unexpected native batch preparation state')
    if not prepared:return dict(status='complete',days=completed)
    targets={r['target'] for r in prepared}
    if len(targets)!=1:raise ValueError('Native repair batch has different canonical targets')
    branches=[]
    for result in prepared:branches.extend((result['predicate'],result['assignment']))
    assignment='multiIf('+','.join((*branches,'event_meta'))+')'
    selected=' OR '.join('('+r['predicate']+')' for r in prepared)
    markers=' AND '.join(f'{literal(r["marker"])}={literal(r["marker"])}' for r in prepared)
    claim.check();source_guard()
    query(f'ALTER TABLE {prepared[0]["target"]} UPDATE event_meta={assignment} '
        f'WHERE ({selected}) AND {markers} SETTINGS mutations_sync=0',read=False)
    return dict(status='submitted',days=[str(d) for d in days],completed=completed)
