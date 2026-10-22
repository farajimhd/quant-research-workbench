"""Explicit research exclusions; never rewrite retained issuer identities."""
from pipelines.certified_research.contracts import digest

VERSION = 'fundamentals-historical-eligibility-v1'
BLOCKED_CIKS = frozenset(('0001610520', '0001114446'))
BLOCKED_SECURITIES = frozenset(('security:d9dcc9a4dff063081993f594',))
REASON = 'UBS historical identity unresolved; excluded by user instruction'


def blocked(kind, entity):
    if kind == 'issuer' and str(entity).startswith('issuer:cik:'):
        return str(entity).removeprefix('issuer:cik:') in BLOCKED_CIKS
    if kind == 'issuer_cik':
        return str(entity).zfill(10) in BLOCKED_CIKS
    return kind == 'security' and entity in BLOCKED_SECURITIES


def require_eligible(kind, entities):
    if any(blocked(kind, value) for value in entities):
        raise ValueError(REASON)


def policy():
    body = dict(version=VERSION, ciks=sorted(BLOCKED_CIKS),
                securities=sorted(BLOCKED_SECURITIES), reason=REASON)
    return dict(body, policy_hash=digest(body))
