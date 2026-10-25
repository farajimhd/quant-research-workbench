"""Exact retained SEC header witnesses for a mixed-format acceptance cohort.

The encoded clock below is an ordering ordinal, never a UTC publication claim.
Two ISO submission clocks were checked against their original 14-digit headers;
all three headers must remain present before the legacy third clock is admitted.
"""
from pipelines.certified_research.contracts import literal

CIK='0001963685'
WITNESSES=(
 ('0001213900-26-086751','20260807164555','1a96c1f495de2083a9f44674fa22f1830be4efec6d185f42df7e9e77a0ab5c8b'),
 ('0001213900-26-086761','20260807165346','bc4b3a82856c8d2ab91758bdcafed4e31d191694f2195b64694b0bae0cf3b155'),
 ('0001213900-26-086762','20260807165445','cbd2ee71bbaa4a2ef91b726c539932b098894e20959fe3a40337b4a25ad60b78'),
)


def inventory(ciks):
    if CIK not in ciks:return None
    keys=','.join('('+literal(a)+','+literal(h)+')' for a,_,h in WITNESSES)
    return f'''SELECT DISTINCT primary_cik cik,accession_number,acceptance_datetime_raw,
        filing_date,source_content_sha256,public_document_count,private_to_public,pac_event_id
        FROM q_live.sec_filing_archive_accession_v3
        WHERE primary_cik={literal(CIK)} AND (accession_number,source_content_sha256) IN ({keys})'''


def source_sql(ciks):
    source=inventory(ciks)
    if source is None:return None
    pins=','.join('('+','.join(map(literal,(a,raw,h)))+')' for a,raw,h in WITNESSES)
    verified=f'''SELECT * FROM ({source}) WHERE
        (accession_number,acceptance_datetime_raw,source_content_sha256) IN ({pins})
        AND filing_date=toDate('2026-08-07') AND public_document_count>0
        AND NOT private_to_public AND empty(ifNull(pac_event_id,''))'''
    raw='a.acceptance_datetime_raw'
    clock=f"concat(substring({raw},1,4),'-',substring({raw},5,2),'-',substring({raw},7,2),'T',substring({raw},9,2),':',substring({raw},11,2),':',substring({raw},13,2),'Z')"
    return f'''SELECT cik,accession_number,parseDateTime64BestEffortOrNull({clock},9,'UTC') accepted_at_utc,
        {clock} acceptance_datetime_raw,filing_date,'retained_header_clock_order' accepted_at_source,
        source_content_sha256 source_file_id FROM ({verified}) a
        WHERE (SELECT uniqExact(accession_number) FROM ({verified}))=3'''
