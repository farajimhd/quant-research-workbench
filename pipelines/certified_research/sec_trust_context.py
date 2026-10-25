"""Source-bound SCE trust disclosures; never substitute SCE consolidated facts.

Original issuer, taxonomy and all dimensions remain intact. These are trust
investments and distributions, not the corporate selected-financial catalog.
"""
from .contracts import digest

VERSION = 'reviewed-sce-trust-context-v1'
SOURCE_CIK = '0000827052'
DOCUMENTS = frozenset((
    '21adce1d9c3c0ee33837ca37fc682f15c032e2b782edbd262645f3c42ba9143d',
    '640b36a952eab3df0582ccb5c61bc5e242d93f14e8c51707ae8b988323337d53',
    '9ffc6cadd7eb3eb1d693869bb1e47c887f69edad7ab241c852d964f67ff8d717',
    '8423353d64d73bdce5d4a909e18dd4b12334009b1e632e5cc5df7efef7065210',
    'ea396c22fb22d803e361b6feb17af7c7f260923a4f459880bab88672ee58b775',
))
MEMBERS = {
    'SouthernCaliforniaEdisonTrustIiMember': '0001122392',
    'SCETrustVIMember': '0001709317',
    'SceTrustViiMember': '0001709318',
}
TAGS = ('Investments', 'CommonStockValue',
        'VariableInterestEntitiesDividendIncome',
        'VariableInterestEntitiesDividendDistributions')


def registered(document_hash, cik):
    return cik == SOURCE_CIK and document_hash in DOCUMENTS


def subject(observation):
    """Resolve the disclosed trust, preserving every original dimension."""
    import re
    candidates = []
    for axis, member in observation['dimensions']:
        if (re.fullmatch(r'\{http://fasb.org/(?:srt|us-gaap)/\d{4}(?:-\d{2}-\d{2})?\}ConsolidatedEntitiesAxis', axis)
                or re.fullmatch(r'\{http://www.edison.com/\d{8}\}VariableInterestEntitiesByNameOfEntityAxis', axis)):
            match = re.fullmatch(r'\{http://www.edison.com/\d{8}\}(.+)', member)
            if match and match[1] in MEMBERS:
                candidates.append(MEMBERS[match[1]])
    if len(candidates) > 1:
        raise ValueError('Competing trust subjects')
    return candidates[0] if candidates else None


def extract(payload, expected_hash, cik, *, maximum_bytes, maximum_facts):
    from . import sec_share_context, facts
    if not registered(expected_hash, cik):
        raise ValueError('Unreviewed trust source')
    metrics = facts.catalog()
    existing_tags = {tag for metric in metrics for tag in metric.tags}
    result = sec_share_context.extract(payload, expected_hash, cik,
        maximum_bytes=maximum_bytes, maximum_facts=maximum_facts,
        catalog_tags=tuple(sorted(existing_tags | set(TAGS))), include_security_cover=True)
    # Rejections are retained, so normal qualification still fails closed.
    selected = tuple(o for o in result['observations']
                     if o['concept'].rsplit('}', 1)[-1] in existing_tags or subject(o))
    if not any(subject(o) for o in selected):
        raise ValueError('Reviewed source has no trust contexts')
    result.update(observations=selected, extraction_scope=VERSION,
        whole_document_coverage=False, aggregation='none',
        catalog_hash=digest((VERSION, facts.catalog_hash(metrics), TAGS, MEMBERS)),
        original_vintage_qualified=False, certified=False)
    return dict(result, extraction_hash=digest(result))
