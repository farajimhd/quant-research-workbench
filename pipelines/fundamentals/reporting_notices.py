"""Reviewed reporting-delay evidence, never a substitute for financial facts.

These are historical statements as of the notice, not permanent exemptions.
New retained evidence invalidates the recovery queue through its source hash.
"""
from pipelines.certified_research.contracts import digest, literal

NOTICES = (
    dict(cik='0001979484',accession='0001104659-26-046054',document='tm2612302d1_ex99-1.htm',
         content_sha256='06bbca7c23664e68d4200d33c9cfc0801928465a4d03caf2e20a6958291266f6',
         period_start='2024-02-01',period_end='2025-01-31',
         quotes=('the fiscal year ended January 31, 2025',
                 'At this time, the audit of the Company’s Fiscal 2025 consolidated financial statements remains ongoing.')),
    dict(cik='0001979484',accession='0001104659-26-017026',document='tm266684d1_ex99-1.htm',
         content_sha256='10e8a7074c6bf057845f81f8b745f2d5fabae35b8d6b66081e11e5790a77780d',
         period_start='2025-02-01',period_end='2025-07-31',
         quotes=('due to the delay in filing its interim financial statements for the six-month period ended July 31, 2025.',
                 'The interim financial statements for the six-month period ended July 31, 2025 have already been completed, and will be filed within one week following the filing of the audited annual financial statements included in the Company’s Form 20-F.')),
)


def contract_hash():
    return digest(NOTICES)


def inspect_notice(query, doc):
    from html.parser import HTMLParser
    from .retained_sec import inspect
    from pipelines.certified_research.sec_document_payload import load
    scope='cik='+literal(doc['cik'])+' AND accession_number='+literal(doc['accession'])
    filings=query('SELECT DISTINCT primary_document FROM q_live.sec_filing_v3 FINAL WHERE '+scope+' LIMIT 2')
    if len(filings)!=1:raise ValueError('Notice parent is missing or competing')
    name=filings[0]['primary_document']
    columns='document_name,document_type,content_sha256,source_version_key,source_archive_date'
    rows=query(f'SELECT DISTINCT {columns} FROM q_live.sec_filing_text_v3 WHERE '+scope+
               ' AND document_name IN ('+literal(name)+','+literal(doc['document'])+') LIMIT 3')
    members=query(f'SELECT DISTINCT {columns} FROM q_live.sec_filing_document_v3 WHERE '+scope+
                  ' AND document_name IN ('+literal(name)+','+literal(doc['document'])+') LIMIT 3')
    key=lambda row:row['document_name']
    if (len(rows)!=2 or len({r['document_name'] for r in rows})!=2
            or sorted(rows,key=key)!=sorted(members,key=key)):
        raise ValueError('Notice exact document membership differs')
    indexed={r['document_name']:r for r in rows}
    if indexed[doc['document']]['content_sha256']!=doc['content_sha256']:
        raise ValueError('Notice source hash differs')
    parent=dict(cik=doc['cik'],accession=doc['accession'],document=name,
                content_sha256=indexed[name]['content_sha256'])
    evidence=inspect(query,parent)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.hrefs=[]
        def handle_starttag(self,tag,attrs):
            if tag=='a':self.hrefs.append(dict(attrs).get('href',''))
    links=Links();links.feed(load(query,parent['cik'],parent['accession'],name,parent['content_sha256'],maximum_bytes=4*1024**2));links.close()
    if doc['document'] not in links.hrefs:
        raise ValueError('Retained primary filing does not link the exact notice')
    return dict(parent=evidence,members=sorted(members,key=key),time_evidence=evidence['time_evidence'])


def inspect_notices(query, ciks, cutoff):
    """Read only registered small exhibits for changed issuer cases."""
    from datetime import datetime, timezone
    from pipelines.certified_research.sec_document_payload import load
    from pipelines.certified_research.sec_html_context import parse, normalize
    result={c:[] for c in ciks}
    for notice in NOTICES:
        if notice['cik'] not in result:continue
        doc={k:notice[k] for k in ('cik','accession','document','content_sha256')}
        before=inspect_notice(query,doc)
        at=datetime.fromisoformat(before['time_evidence']['available_at']).replace(tzinfo=timezone.utc)
        if at>cutoff:continue
        payload=load(query,doc['cik'],doc['accession'],doc['document'],doc['content_sha256'],maximum_bytes=4*1024**2)
        text=normalize(' '.join(parse(payload,doc['content_sha256'],maximum_bytes=4*1024**2).text))
        if any(text.count(q)!=1 for q in notice['quotes']):
            raise ValueError('Reviewed reporting-delay notice text differs')
        if inspect_notice(query,doc)!=before:raise ValueError('Reporting-delay source changed during review')
        result[doc['cik']].append(dict(notice,available_at=before['time_evidence']['available_at'],
            lineage_hash=digest(before),status='filing_delayed_as_of_notice',
            financial_coverage_exemption=False,meaning='Historical explanation only; later financial statements remain eligible.'))
    return result
