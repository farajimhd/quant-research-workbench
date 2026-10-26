"""Retained filing order within an unchanged conservative public-time bucket.

Companyfacts is entity-wide (SEC EDGAR API contract). Ordering two filings does
not reconcile alternative concepts, subsidiaries, currencies, or genuine ties.
"""
from pipelines.certified_research.contracts import literal,identifier


def sql(observations,ciks,key,*,metadata_database='sec_core',reported_metadata_database='q_live'):
    identifier(metadata_database);identifier(reported_metadata_database)
    ids=','.join(map(literal,sorted(set(ciks))))
    iso="match(ifNull(acceptance_datetime_raw,''),'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]+)?Z$')"
    sources=f'''SELECT cik,accession_number,accepted_at_utc,acceptance_datetime_raw,
        filing_date,accepted_at_source,source_file_id
        FROM {metadata_database}.sec_bulk_mirror_filing_v3 FINAL PREWHERE cik IN ({ids})
        WHERE {iso} AND isNotNull(accepted_at_utc) AND length(source_file_id)=64
        AND accepted_at_source IN ('submissions_bulk','submissions_bulk_fragment')
        UNION ALL SELECT cik,accession_number,
        parseDateTime64BestEffortOrNull(ifNull(acceptance_datetime_raw,''),9,'UTC') accepted_at_utc,
        acceptance_datetime_raw,filing_date,'retained_sec_submissions' accepted_at_source,
        source_content_sha256 source_file_id
        FROM {reported_metadata_database}.sec_filing_v3 FINAL PREWHERE cik IN ({ids})
        WHERE {iso} AND length(source_content_sha256)=64'''
    from .filing_order_witnesses import source_sql
    witness=source_sql(ciks)
    if witness:sources+=' UNION ALL '+witness
    metadata=f'''SELECT cik,accession_number,any(toNullable(accepted_at_utc)) accepted,
        any(filing_date) filing_day FROM ({sources})
        GROUP BY cik,accession_number
        HAVING uniqExact(tuple(accepted_at_utc,filing_date))=1
        AND countIf(isNull(accepted_at_utc) OR isNull(acceptance_datetime_raw)
          OR NOT match(ifNull(acceptance_datetime_raw,''),'^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}(?:[.][0-9]+)?Z$')
          OR accepted_at_source NOT IN ('submissions_bulk','submissions_bulk_fragment','retained_sec_submissions','retained_header_clock_order')
          OR length(source_file_id)!=64
          OR parseDateTime64BestEffortOrNull(ifNull(acceptance_datetime_raw,''),9,'UTC')!=accepted_at_utc
          OR dateDiff('day',toDate(accepted_at_utc),filing_date) NOT BETWEEN 0 AND 7)=0'''
    joined=f'''SELECT o.*,m.accepted accepted_order,
        isNotNull(m.accepted) AND o.family IN ('sec_bulk','sec_reported','sec_original_qualified','sec_companyfacts_context','sec_ingested_original')
        AND o.available_at>=m.accepted
        AND toDate(o.available_at,'America/New_York')>=m.filing_day order_verified
        FROM ({observations}) o LEFT JOIN ({metadata}) m
        ON o.cik=m.cik AND o.accession=m.accession_number'''
    # If even one competing record lacks matching chronology, preserve the
    # original tie. Known metadata must never silently beat unknown metadata.
    return f'''SELECT * EXCEPT(accepted_order,order_verified),
        if(min(order_verified) OVER (PARTITION BY {key},available_at,authority_priority),
           ifNull(toUnixTimestamp64Nano(accepted_order),0),0) filing_order
        FROM ({joined})'''
