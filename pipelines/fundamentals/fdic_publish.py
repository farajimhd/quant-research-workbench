"""FDIC authority in the shared normalized tables, with exact decimal readback."""
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from . import schema, fdic_extract
from .producer import names, _different
from pipelines.certified_research.contracts import digest, literal, identifier
from pipelines.certified_research.publication import insert_rows

ISSUERS = {'HIFS':'issuer:ibkr_public:conid:270137',
           'TOWN':'issuer:ibkr_public:conid:30217403',
           'OZK':'issuer:ibkr_public:conid:325898822',
           'PFBC':'issuer:ibkr_public:conid:89596989'}


def availability(report):
    """Date precision is conservative; retrieval never backdates publication."""
    ny=ZoneInfo('America/New_York')
    day=datetime.strptime(report['filing_date'],'%Y-%m-%d').date()
    stamps=[datetime.fromisoformat(report[k].replace('Z','+00:00'))
            for k in ('attachment_created_at','attachment_changed_at')]
    if any(s.tzinfo is None for s in stamps):raise ValueError('Unzoned FDIC attachment time')
    # A later corrected attachment must never inherit an earlier filing date.
    day=max(day,*(s.astimezone(ny).date() for s in stamps))
    return datetime.combine(day+timedelta(days=1),time(),ny).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')


def publish(query,database,extracted,receipt,population_hash,guard):
    identifier(database);guard()
    if extracted['issues'] or not extracted['observations']:
        raise ValueError('FDIC extraction must pass all document checks')
    report=receipt['report']
    if extracted['report']!=report or extracted['sha256']!=receipt['sha256']:
        raise ValueError('FDIC extraction and download identities differ')
    issuer=ISSUERS[report['ticker']]
    recipe=digest([fdic_extract.VERSION,Path(fdic_extract.__file__).read_text(encoding='utf-8'),Path(__file__).read_text(encoding='utf-8')])
    source_id=digest(dict(report=report,pdf=receipt['sha256'],observations=extracted['observations']))
    stamp=datetime.fromisoformat(receipt['downloaded_at']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    available=availability(report)
    source=dict(source_id=source_id,family='fdic_report',entity_kind='issuer',entity_id=issuer,
        event_key=f"{report['filing_id']}/{report['attachment_id']}",source_table='official_fdic_securities_filings',
        source_hash=receipt['sha256'],evidence_ref=json.dumps(dict(report=report,extraction_hash=digest(extracted),
            availability_basis='fdic_filing_day_end_or_later_attachment_day_end',exact_publication_time=False),sort_keys=True),
        available_at=available,observed_at=stamp,quality='certified',reason='verified_selected_fdic_statement_rows',recipe_hash=recipe)
    key=digest([source_id,recipe,population_hash]);rows=[]
    for observation in extracted['observations']:
        context=dict(period_start=observation['period_start'],period_end=observation['period_end'],
            taxonomy='fdic',dimensions='{}',unit=observation['unit'])
        rows.append(dict(batch_key=key,**source,context_id=digest(context),**context,
            metric='fdic:'+observation['metric'],value=None,text_value=observation['value']))
    if len({(r['metric'],r['context_id']) for r in rows})!=len(rows):raise ValueError('Duplicate FDIC semantic key')
    expected=digest(rows)
    previous=query(f'SELECT fact_rows,output_hash FROM {database}.reference_batch_receipt_v1 FINAL WHERE batch_key={literal(key)}')
    if previous:
        if len(previous)!=1 or int(previous[0]['fact_rows'])!=len(rows) or previous[0]['output_hash']!=expected:
            raise ValueError('FDIC receipt conflict')
        return dict(state='reused',rows=len(rows),batch_key=key,output_hash=expected,source_id=source_id)
    conflict=query(f"SELECT source_id FROM {database}.reference_source_v1 FINAL WHERE family='fdic_report' "
        f"AND entity_id={literal(issuer)} AND event_key={literal(source['event_key'])} AND source_id!={literal(source_id)} LIMIT 1")
    if conflict:raise ValueError('FDIC previously published version differs; explicit version review required')
    stage=f'{database}.reference_stage_v1 FINAL WHERE batch_key={literal(key)}'
    guard();query(f'ALTER TABLE {database}.reference_stage_v1 DROP PARTITION {literal(key)}',read=False)
    insert_rows(query,database,'reference_stage_v1',rows,maximum_bytes=2*1024**2)
    contexts=f'SELECT DISTINCT {names(schema.CONTEXT)} FROM {stage}'
    facts=f'SELECT DISTINCT entity_kind,entity_id,metric,observed_at ordering_time,source_id,context_id,value,text_value FROM {stage}'
    headers=f'SELECT DISTINCT {names(schema.SOURCE)} FROM {stage}'
    guard()
    query(f'INSERT INTO {database}.reference_context_v1 ({names(schema.CONTEXT)}) {contexts}',read=False)
    query(f'INSERT INTO {database}.reference_fact_v1 ({names(schema.FACT)}) {facts}',read=False)
    _different(query,contexts,f'SELECT {names(schema.CONTEXT)} FROM {database}.reference_context_v1 FINAL WHERE context_id IN (SELECT context_id FROM {stage})')
    _different(query,facts,f"SELECT {names(schema.FACT)} FROM {database}.reference_fact_v1 FINAL WHERE entity_kind='issuer' AND entity_id={literal(issuer)} AND source_id={literal(source_id)}")
    guard();query(f'INSERT INTO {database}.reference_source_v1 ({names(schema.SOURCE)}) {headers}',read=False)
    _different(query,headers,f"SELECT {names(schema.SOURCE)} FROM {database}.reference_source_v1 FINAL WHERE family='fdic_report' AND entity_id={literal(issuer)} AND source_id={literal(source_id)}")
    insert_rows(query,database,'reference_batch_receipt_v1',[dict(batch_key=key,source_signature=source_id,
        recipe_hash=recipe,population_hash=population_hash,source_rows=1,fact_rows=len(rows),output_hash=expected,completed_at=stamp)])
    query(f'ALTER TABLE {database}.reference_stage_v1 DROP PARTITION {literal(key)}',read=False)
    return dict(state='published',rows=len(rows),batch_key=key,output_hash=expected,source_id=source_id)


def financial_sql(issuers,cutoff,*,since=None,known_at=None,database='arte'):
    """All visible periods, latest public vintage; retain competing values."""
    from .queries import _timestamp
    identifier(database)
    if not issuers or not set(issuers)<=set(ISSUERS.values()):raise ValueError('Unreviewed bank issuer')
    ids=','.join(map(literal,sorted(set(issuers))))
    known='' if known_at is None else ' AND observed_at<='+_timestamp(known_at)
    lower='' if since is None else ' AND available_at>='+_timestamp(since)
    return f'''SELECT * EXCEPT(latest) FROM (
      SELECT *,max(available_at) OVER(PARTITION BY issuer_id,feature,period_start,period_end,unit) latest FROM (
      SELECT f.entity_id issuer_id,substring(f.metric,6) feature,c.period_start,c.period_end,c.unit,
        f.text_value value,s.available_at,s.observed_at,s.source_id,s.event_key filing_id
      FROM (SELECT * FROM {database}.reference_fact_v1 FINAL WHERE entity_kind='issuer' AND entity_id IN ({ids}) AND startsWith(metric,'fdic:')) f
      INNER JOIN (SELECT * FROM {database}.reference_source_v1 FINAL WHERE family='fdic_report' AND entity_id IN ({ids}) AND available_at<={_timestamp(cutoff)}{known}) s USING(source_id)
      INNER JOIN (SELECT * FROM {database}.reference_context_v1 FINAL WHERE context_id IN (
        SELECT context_id FROM {database}.reference_fact_v1 FINAL WHERE entity_kind='issuer' AND entity_id IN ({ids}) AND startsWith(metric,'fdic:'))) c USING(context_id)
      WHERE c.taxonomy='fdic' AND c.dimensions='{{}}')) WHERE available_at=latest{lower}
      ORDER BY issuer_id,feature,period_end,period_start,source_id'''

