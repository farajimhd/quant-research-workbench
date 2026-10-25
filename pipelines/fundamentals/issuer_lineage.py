"""Reviewed legal succession extends a source cohort, never aliases its facts.

A successor's snapshot alone is not permission to discard predecessor history.
The exact retained succession filing and both issuer identities are verified
before admitting a predecessor. This does not assert subsidiary ownership,
backdate the stock bridge, or add overlapping consolidated values together.
"""
from hashlib import sha256
from html.parser import HTMLParser
from datetime import datetime, timezone
from pipelines.certified_research.contracts import digest, literal
from . import retained_sec

VERSION = 'reviewed-sec-issuer-succession-v1'
REVIEWS = ({
    'cik': '0002115436', 'predecessor_cik': '0000034088',
    'predecessor_name': 'EXXON MOBIL CORP',
    'accession': '0001193125-26-291990', 'document': 'd71068d8k12b.htm',
    'content_sha256': '2dd53eef4966062085629a13eaa32b10e752f7fab56bf6558221bf0cab520a18',
    'form': '8-K12B', 'effective_date': '2026-07-01',
    'quotes': (
        'On July 1, 2026, Exxon Mobil Corporation, a New Jersey corporation and the predecessor registrant',
        'establishing ExxonMobil Holdings Corporation as the successor registrant of ExxonMobil’s common stock',
        'The Redomiciliation Merger became effective on July 1, 2026',
    ),
},)


def verify_payload(review, payload, company):
    if sha256(payload.encode('utf-8')).hexdigest() != review['content_sha256']:
        raise ValueError('Succession document bytes changed')
    if (len(company) != 1 or company[0]['entity_name'] != review['predecessor_name']
            or not company[0]['source_file_id']):
        raise ValueError('Predecessor SEC identity is absent or competing')
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
        def handle_data(self, value):
            self.parts.append(value)
    parser = Text(); parser.feed(payload); parser.close()
    text = ' '.join(' '.join(parser.parts).split())
    if any(text.count(quote) != 1 for quote in review['quotes']):
        raise ValueError('Reviewed legal succession statements differ')


def verified_relationships(query, ciks, cutoff):
    """Small reviewed registry; no text search over the filing archive."""
    if cutoff.tzinfo is None:
        raise ValueError('Aware lineage cutoff required')
    result = []
    for review in REVIEWS:
        if review['cik'] not in ciks:
            continue
        proof = retained_sec.inspect(query, review)
        available = datetime.fromisoformat(proof['time_evidence']['available_at']).replace(tzinfo=timezone.utc)
        if available > cutoff:
            continue
        where = (f"cik={literal(review['cik'])} AND accession_number={literal(review['accession'])} "
                 f"AND document_name={literal(review['document'])}")
        rows = query('SELECT source_text FROM q_live.sec_filing_text_v3 PREWHERE '+where+' LIMIT 2')
        company = query('SELECT entity_name,source_file_id FROM sec_core.sec_bulk_mirror_company_v3 FINAL '
                        'WHERE cik='+literal(review['predecessor_cik']))
        if len(rows) != 1:
            raise ValueError('Succession payload is absent or competing')
        verify_payload(review, rows[0]['source_text'], company)
        if retained_sec.inspect(query, review) != proof:
            raise ValueError('Succession evidence changed during verification')
        body = dict(version=VERSION, **review, evidence=proof, predecessor_identity=company,
                    aggregation='none', fact_relabeling=False, historical_stock_bridge=False)
        result.append(dict(body,relationship_hash=digest(body)))
    return result


def extend_population(query, population, cutoff):
    """Return a new manifest. Never mutate an existing campaign's request."""
    families = {k:list(v) for k,v in population['families'].items()}
    ciks = set(families.get('sec_bulk', [])) | set(families.get('sec_reported', []))
    relationships = verified_relationships(query, ciks, cutoff)
    predecessors = {r['predecessor_cik'] for r in relationships}
    for family in ('sec_bulk','sec_reported'):
        if family in families:
            families[family] = sorted(set(families[family]) | predecessors)
    return dict(population, families=families, provenance=dict(population['provenance'],
        issuer_succession=dict(version=VERSION,relationships=relationships,
                              predecessor_ciks=sorted(predecessors),aggregation='none')))
