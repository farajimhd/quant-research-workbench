"""ClickHouse-native SEC projection with explicit public-vintage availability.

No ingestion-clock fallback and no inference of missing duration start dates.
Aliases use the frozen app tag order; source identities and ambiguity survive
through provenance and confidence rather than being silently overwritten.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID
import re

from .contracts import identifier,literal
from .facts import catalog,UNITS,CURRENCY_UNITS
from .publication import evidence_sql
from .schema import TABLES


def sec_projection(dataset_id,attempt_id,entity_key,cik,start: date,end: date,*,metrics=None,provenance=False,wide=False):
    UUID(dataset_id);UUID(attempt_id)
    if (type(entity_key) is not int or not 0<entity_key<2**64 or not re.fullmatch(r'0*[1-9][0-9]{0,9}',cik)
            or type(start) is not date or type(end) is not date or start>end):
        raise ValueError('Invalid SEC projection scope')
    metrics=tuple(m for m in (metrics or catalog()) if m.tags)
    ciks=','.join(literal(v) for v in sorted({str(int(cik)),str(int(cik)).zfill(10)}))
    mappings=[]
    for metric in metrics:
        for priority,tag in enumerate(metric.tags):
            mappings.append(f'({literal(tag)},toUInt32({metric.key}),toUInt16({UNITS[metric.unit]}),toUInt16({priority}))')
    if not mappings:raise ValueError('No SEC metrics selected')
    tags=','.join(literal(t) for t in sorted({t for m in metrics for t in m.tags}))
    currencies=','.join(literal(k) for k in CURRENCY_UNITS)
    native_units=[]
    for symbol,key in UNITS.items():
        if symbol in CURRENCY_UNITS or symbol.endswith('/share'):
            native_units.extend([(symbol,key),(symbol.replace('/share','/shares'),key)])
    source_units=','.join(literal(s) for s,_ in native_units)
    output_units=','.join(str(k) for _,k in native_units)
    share_units=','.join(literal(s) for s,_ in native_units if '/' in s)
    prefix=f"""WITH raw_context AS (
      SELECT lower(hex(SHA256(concat('sec-xbrl-company-fact|',cik,'|',taxonomy,'|',tag,'|',unit,'|',
          ifNull(toString(start_date),''),'|',ifNull(toString(end_date),''),'|',ifNull(accession_number,''),'|',ifNull(frame,''))))) company_fact_id,
        cik,taxonomy,tag,unit unit_code,toFloat64(value) value,fy fiscal_year,fp fiscal_period,
        toDateTime64(filed_date,3,'UTC') filed_at_utc,start_date period_start_date,end_date period_end_date,
        accession_number,toUInt8(if(start_date IS NULL,1,2)) source_context_kind,
        concat('sec_core.sec_bulk_mirror_xbrl_fact_v3/',fact_id) source_record,
        lower(hex(SHA256(toJSONString(tuple(fact_id,cik,taxonomy,tag,unit,value,start_date,end_date,
          filed_date,fy,fp,form_type,frame,accession_number,dimensions_json,source_file_id))))) source_content_sha256,
        [lower(hex(SHA256(toJSONString(['sec-xbrl-company-fact',toString(toUInt64OrZero(cik)),taxonomy,tag,unit,
          ifNull(toString(start_date),''),ifNull(toString(end_date),''),ifNull(accession_number,''),ifNull(frame,'')])))),
         lower(hex(SHA256(toJSONString(['sec-xbrl-company-fact',cik,taxonomy,tag,unit,
          ifNull(toString(start_date),''),ifNull(toString(end_date),''),ifNull(accession_number,''),ifNull(frame,'')]))))] live_ids
      FROM sec_core.sec_bulk_mirror_xbrl_fact_v3 FINAL
      PREWHERE cik IN ({ciks}) AND tag IN ({tags})
      WHERE value IS NOT NULL AND end_date IS NOT NULL AND filed_date IS NOT NULL
        AND notEmpty(ifNull(accession_number,'')) AND dimensions_json IN ('','{{}}')
    ), raw_keys AS (SELECT arrayJoin(arrayConcat([company_fact_id],live_ids)) company_fact_id FROM raw_context),
    source_facts AS (
      SELECT * EXCEPT live_ids FROM raw_context
      UNION ALL SELECT f.company_fact_id,f.cik,f.taxonomy,f.tag,f.unit_code,f.value,f.fiscal_year,f.fiscal_period,
        f.filed_at_utc,CAST(NULL AS Nullable(Date)) period_start_date,f.period_end_date,f.accession_number,
        toUInt8(0) source_context_kind,concat('q_live.sec_xbrl_company_fact_v3/',f.company_fact_id) source_record,
        f.source_content_sha256
      FROM q_live.sec_xbrl_company_fact_v3 f FINAL
      LEFT ANTI JOIN raw_keys r ON r.company_fact_id=f.company_fact_id
      PREWHERE f.cik IN ({ciks}) AND f.tag IN ({tags})
    ), mapping AS (SELECT tupleElement(v,1) tag,tupleElement(v,2) metric_key,
      tupleElement(v,3) unit_key,tupleElement(v,4) priority FROM (SELECT arrayJoin([{','.join(mappings)}]) v)),
    filings AS (
      SELECT accession_number,uniqExact(tuple(accepted_at_utc,accepted_at_source)) versions,
        tupleElement(argMin(tuple(accepted_at_utc,accepted_at_source,source_content_sha256),
          tuple(source_content_sha256,accepted_at_source,accepted_at_utc)),1) accepted,
        tupleElement(argMin(tuple(accepted_at_utc,accepted_at_source,source_content_sha256),
          tuple(source_content_sha256,accepted_at_source,accepted_at_utc)),2) accepted_source,
        tupleElement(argMin(tuple(accepted_at_utc,accepted_at_source,source_content_sha256),
          tuple(source_content_sha256,accepted_at_source,accepted_at_utc)),3) filing_hash
      FROM q_live.sec_filing_v3 FINAL WHERE cik IN ({ciks})
      GROUP BY accession_number
    ), eligible AS (
      SELECT f.*,g.versions,m.metric_key,
        if(m.unit_key IN (1,5),transform(f.unit_code,[{source_units}],[{output_units}],m.unit_key),m.unit_key) unit_key,
        m.priority,
        if(g.versions=1 AND g.accepted IS NOT NULL AND notEmpty(g.accepted_source),
          fromUnixTimestamp64Micro(intDiv(toUnixTimestamp64Nano(g.accepted)+999,1000)),
          toDateTime64(toDate(f.filed_at_utc)+1,6,'America/New_York')) AS public_at,
        if(g.versions=1 AND g.accepted IS NOT NULL AND notEmpty(g.accepted_source),1,2) AS public_precision,
        toJSONString(tuple(f.company_fact_id,f.cik,f.source_content_sha256,f.accession_number,f.tag,f.taxonomy,
              f.unit_code,f.value,f.fiscal_year,f.fiscal_period,f.period_start_date,f.period_end_date,f.source_context_kind,
              g.filing_hash,g.accepted,g.accepted_source,public_at,public_precision)) source_proof,
        lower(hex(SHA256(source_proof))) proof_hash
      FROM source_facts f
      INNER JOIN mapping m ON m.tag=f.tag
      LEFT JOIN filings g ON g.accession_number=ifNull(f.accession_number,'')
      WHERE f.filed_at_utc IS NOT NULL AND notEmpty(ifNull(f.accession_number,''))
        AND (f.unit_code=transform(m.unit_key,[1,2,3,5,6],['USD','shares','pure','USD/shares','pure'],'')
             OR (m.unit_key=1 AND f.unit_code IN ({currencies}))
             OR (m.unit_key=5 AND f.unit_code IN ({share_units})))
    ), checked AS (
      SELECT *,uniqExact(tuple(value,source_context_kind)) OVER
        (PARTITION BY metric_key,unit_key,period_start_date,period_end_date,public_at,priority) value_versions
      FROM eligible WHERE toDate(public_at) BETWEEN toDate({literal(start)}) AND toDate({literal(end)})
    ), ranked AS (
      SELECT * FROM checked
      ORDER BY metric_key,period_end_date,public_at,priority,company_fact_id,source_content_sha256,source_record
      LIMIT 1 BY metric_key,unit_key,period_start_date,period_end_date,public_at
    ) """
    if provenance:
        return prefix+"""SELECT DISTINCT proof_hash evidence_hash,'sec' source_system,
          source_record,
          source_content_sha256 source_hash,
          source_proof proof FROM ranked"""
    extras=(",'sec' source_system,source_record,"
            "source_content_sha256 source_hash,source_proof proof") if wide else ''
    return prefix+f"""SELECT toUUID({literal(dataset_id)}) dataset_id,toUUID({literal(attempt_id)}) attempt_id,
      toUInt8(1) entity_kind,toUInt64({entity_key}) entity_key,metric_key,
      period_start_date period_start,period_end_date period_end,source_context_kind context_kind,
      toDateTime64(public_at,6,'UTC') available_at,toUInt32(0) revision,
      if(isFinite(r.value) AND r.versions<=1 AND r.value_versions=1,toNullable(r.value),CAST(NULL AS Nullable(Float64))) value,
      unit_key,toUInt8(if(isFinite(r.value) AND r.versions<=1 AND r.value_versions=1,1,3)) status,toUInt8(public_precision) precision,
      toUInt8(if(public_precision=1,90,70)) confidence,
      multiIf(r.versions>1,'ambiguous_acceptance_clock',r.value_versions>1,'conflicting_source_values',NOT isFinite(r.value),'nonfinite_source_value',
              source_context_kind=0,'period_start_not_preserved_by_source','') reason,proof_hash evidence_hash{extras}
    FROM ranked r"""


def sec_insert(database,*args,**kw):
    identifier(database)
    names=','.join(c.name for c in next(t for t in TABLES if t.name=='research_facts_v1').columns)
    return f'INSERT INTO {database}.research_facts_v1 ({names}) '+sec_projection(*args,**kw)


def sec_expected(*args,**kw):
    table=next(t for t in TABLES if t.name=='research_facts_v1')
    return evidence_sql(table,sec_projection(*args,**kw),'tuple(metric_key,unit_key,period_start,period_end,available_at,revision)')


def sec_provenance_insert(database,*args,**kw):
    identifier(database)
    projection=sec_projection(*args,**kw,provenance=True)
    return (f'INSERT INTO {database}.research_provenance_v1 '
            '(evidence_hash,source_system,source_record,source_hash,proof) '
            f'SELECT p.* FROM ({projection}) p LEFT ANTI JOIN {database}.research_provenance_v1 existing '
            'ON existing.evidence_hash=p.evidence_hash')
