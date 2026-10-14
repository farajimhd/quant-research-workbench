"""Dated original-cover to canonical-security candidates; no allocation inference."""
from datetime import date,datetime,time,timezone
import re
from zoneinfo import ZoneInfo

from . import identity,sec_public_archive_time
from .contracts import digest


def resolve(cover,witness,qualification,receipt,snapshot,issuer_ciks,exchange_aliases):
    body=dict(cover);sealed=body.pop('cover_hash',None)
    if sealed!=digest(body):raise ValueError('SEC cover fingerprint differs')
    if witness['document_hash']!=cover['source_text_hash']:
        raise ValueError('SEC class cover differs from original archive document')
    if sec_public_archive_time.qualify(witness,receipt,mode=qualification['mode'])!=qualification:
        raise ValueError('SEC class mapping public-time evidence differs')
    if (snapshot.get('ready') is not True or not re.fullmatch('[0-9a-f]{64}',snapshot.get('source_hash',''))
            or len(snapshot.get('intervals',()))>100000 or len(cover['candidates'])>10000):
        raise ValueError('SEC class mapping requires a bounded admitted dated universe')
    if (not isinstance(issuer_ciks,dict) or len(issuer_ciks)>100000
            or not isinstance(exchange_aliases,dict) or len(exchange_aliases)>10000):
        raise ValueError('SEC class mapping requires explicit issuer and exchange dictionaries')
    for values in issuer_ciks.values():
        if not isinstance(values,(tuple,list)) or len(values)>16 or any(not re.fullmatch('0*[1-9][0-9]{0,9}',str(v)) for v in values):
            raise ValueError('Invalid bounded canonical CIK bridge')
    issuers={issuer for issuer,values in issuer_ciks.items()
        if len(values)==1 and str(int(values[0]))==cover['cik']}
    if len(issuers)!=1:raise ValueError('SEC class mapping CIK bridge is missing or ambiguous')
    issuer=next(iter(issuers));intervals=identity.validate_intervals(tuple(snapshot['intervals']))
    aliases={}
    for name,codes in exchange_aliases.items():
        normalized=name.strip().casefold()
        if (not normalized or not isinstance(codes,(tuple,list)) or not 1<=len(codes)<=16
                or any(not isinstance(code,str) or not re.fullmatch('[A-Z0-9._-]{1,32}',code) for code in codes)):
            raise ValueError('Invalid canonical exchange alias evidence')
        aliases.setdefault(normalized,set()).update(codes)
    by_ticker={}
    for row in intervals:
        if row.issuer_id==issuer and not row.reason:by_ticker.setdefault(row.ticker,[]).append(row)
    ny=ZoneInfo('America/New_York');links=[]
    for candidate in cover['candidates']:
        reason=candidate['reason'];matches=[]
        day=date.fromisoformat(candidate['period_end'])
        start=datetime.combine(day,time(4),ny).astimezone(timezone.utc)
        end=datetime.combine(day,time(20),ny).astimezone(timezone.utc)
        if not reason:
            ticker=candidate['fields']['TradingSymbol'][0]
            exchange=candidate['fields']['SecurityExchangeName'][0]
            codes=aliases.get(exchange.strip().casefold(),set())
            if len(codes)!=1:reason='missing_or_ambiguous_exchange_alias'
            else:
                matches=[row for row in by_ticker.get(ticker,()) if row.exchange_code in codes
                    and row.valid_from<=start and row.valid_to>=end]
                if not matches:reason='missing_dated_issuer_ticker_exchange_mapping'
                elif len(matches)!=1:reason='ambiguous_dated_security_mapping'
        row=matches[0] if len(matches)==1 and not reason else None
        links.append(dict(dimensions=candidate['dimensions'],period_end=str(day),positions=candidate['positions'],
            status='mapped_candidate' if row else 'unresolved',reason=reason,listing_id=row.listing_id if row else None,
            security_id=row.security_id if row else None,issuer_id=issuer,evidence_hash=row.evidence_hash if row else None,
            available_at=qualification['available_at'],precision=qualification['precision']))
    result=dict(version='sec-dated-cover-security-candidate-v1',cover_hash=sealed,witness_hash=witness['witness_hash'],
        qualification_hash=qualification['qualification_hash'],universe_source_hash=snapshot['source_hash'],
        identity_dictionary_hash=digest(dict(issuer_ciks=issuer_ciks,exchange_aliases=exchange_aliases)),links=tuple(links),
        class_completeness_proven=False,allocation_certified=False)
    return dict(result,mapping_hash=digest(result))
