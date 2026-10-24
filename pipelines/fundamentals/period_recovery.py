"""Recover omitted SEC period metadata by exact original identity matching.

No calendar guess is published. The live SEC importer hashes start and frame
into company_fact_id using JSON string arrays. Only a unique SHA256 preimage
matching that retained identifier is evidence. Values are never reconstructed.
"""
from .v1 import FINANCIAL,predicate
from pipelines.certified_research.contracts import literal,identifier

VERSION='sec-live-period-identity-v1'
COLUMNS=(('company_fact_id','String'),('source_hash','String'),('period_start','Date'),
         ('frame','String'),('method','LowCardinality(String)'),('completed_at',"DateTime64(6,'UTC')"))


def candidates_sql(ciks,database='arte',*,exclude_completed=True):
    identifier(database)
    if not 1<=len(ciks)<=128:raise ValueError('Bound issuer cohorts to 128')
    from .eligibility import require_eligible
    require_eligible('issuer_cik',ciks)
    ids=','.join(map(literal,ciks));tags=','.join(literal(t) for kind,ts in FINANCIAL.values() if kind=='duration' for t in ts)
    exclusion=f'''AND (r.company_fact_id,r.source_content_sha256) NOT IN
          (SELECT company_fact_id,source_hash FROM {database}.reference_period_evidence_v1 FINAL)''' if exclude_completed else ''
    return f'''SELECT r.company_fact_id,r.source_content_sha256 source_hash,r.cik,r.taxonomy,r.tag,r.unit_code,
      r.period_end_date,r.accession_number FROM q_live.sec_xbrl_company_fact_v3 AS r FINAL
      LEFT JOIN (SELECT cik,max(filed_date) latest FROM sec_core.sec_bulk_mirror_xbrl_fact_v3 FINAL
        PREWHERE cik IN ({ids}) AND {predicate('sec_bulk')} GROUP BY cik) b ON r.cik=b.cik
      PREWHERE r.cik IN ({ids}) AND r.taxonomy IN ('us-gaap','ifrs-full','dei') AND r.tag IN ({tags})
      WHERE isNotNull(r.period_end_date) AND isNotNull(r.filed_at_utc)
        AND r.period_end_date<=toDate(r.filed_at_utc)
        AND (isNull(b.latest) OR toDate(r.filed_at_utc)>b.latest)
        {exclusion}
        AND (r.company_fact_id,r.source_content_sha256) IN
          (SELECT event_key,source_hash FROM {database}.reference_source_v1 FINAL
           WHERE family='sec_reported' AND entity_kind='issuer_cik' AND entity_id IN ({ids}))'''


def recovery_sql(candidates,*,maximum_days=731):
    if not 1<=maximum_days<=731:raise ValueError('Bounded period search required')
    # A finite search is not an inference: unmatched IDs remain unresolved.
    frames="arrayConcat([''],arrayFlatten(arrayMap(y -> arrayMap(s -> concat('CY',toString(y),s),['','Q1','Q2','Q3','Q4','Q1I','Q2I','Q3I','Q4I']),[toYear(period_end_date)-1,toYear(period_end_date),toYear(period_end_date)+1])))"
    return f'''SELECT company_fact_id,source_hash,any(start) period_start,any(frame) recovered_frame,
       {literal(VERSION)} method,now64(6,'UTC') completed_at FROM
       (SELECT *,subtractDays(assumeNotNull(period_end_date),day_offset) start FROM ({candidates})
         ARRAY JOIN range({maximum_days}) AS day_offset)
       ARRAY JOIN {frames} AS frame
       WHERE lower(hex(SHA256(toJSONString(['sec-xbrl-company-fact',cik,taxonomy,tag,unit_code,
          toString(start),toString(period_end_date),assumeNotNull(accession_number),frame]))))=company_fact_id
       GROUP BY company_fact_id,source_hash HAVING uniqExact(tuple(start,frame))=1'''
