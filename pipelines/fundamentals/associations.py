"""Retained relationship evidence discovery; candidates are NOT ownership claims."""
from pipelines.certified_research.contracts import literal


def exhibit_sql(ciks):
    if not 1<=len(ciks)<=256:raise ValueError('Use 1..256 CIKs')
    return ('SELECT cik,count() documents,countIf(has_normalized_text=1) text_documents,'
        'min(source_archive_date) first_archive_date,max(source_archive_date) last_archive_date,'
        'argMax(tuple(accession_number,document_id,document_name,document_url,content_sha256),'
        '(source_archive_date,accession_number,document_id)) latest_evidence '
        'FROM q_live.sec_filing_document_v3 FINAL WHERE cik IN ('+','.join(map(literal,ciks))+') '
        "AND (match(upper(document_type),'^EX-21([.]|$)') "
        "OR positionCaseInsensitive(ifNull(description,''),'subsidiar')>0) GROUP BY cik ORDER BY cik")


def cofiling_sql(ciks, partition):
    if not ciks or len(ciks)>100000 or not 0<=partition<64:raise ValueError('Invalid audit scope')
    # One scan per physical partition, not per ticker. A joint filing may be a
    # guarantor, reporting owner or acquisition: do not label it subsidiary.
    return ('SELECT primary_cik,entity_cik,entity_role,uniqExact(accession_number) filings,'
        'min(source_archive_date) first_archive_date,max(source_archive_date) last_archive_date,'
        'argMax(tuple(accession_number,source_header_sha256,entity_name),'
        '(source_archive_date,accession_number,source_header_sha256)) latest_evidence '
        'FROM q_live.sec_filing_entity_v3 FINAL '
        f'WHERE _partition_id={literal(str(partition))} AND primary_cik IN ('
        +','.join(map(literal,ciks))+') AND entity_cik!=primary_cik AND entity_cik!=\'\' '
        'GROUP BY primary_cik,entity_cik,entity_role ORDER BY primary_cik,entity_cik,entity_role')


def coverage(ciks, exhibits, candidates, relationships):
    known={r['parent_cik'] for r in relationships if r['relationship_status']=='active'}
    docs={r['cik']:r for r in exhibits}
    linked={r['primary_cik'] for r in candidates}
    return [dict(cik=c,has_verified_relationship=c in known,
        subsidiary_document_count=docs.get(c,{}).get('documents',0),
        has_cofiling_candidate=c in linked,
        status=('document_review_required' if c in docs else
                'cofiling_review_required' if c in linked else 'no_retained_candidate_found'),
        completeness='unknown') for c in sorted(ciks)]
