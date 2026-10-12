"""Stable normalized IDs and dated, explicit listing-eligibility intervals.

Current conid is an accepted identity anchor, not a source of listing dates.
Historical exchange/status observations must supply the interval evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timezone
from hashlib import sha256
import re

from services.reference_gateway.tradability import is_otc_venue,SUPPORTED_PRODUCT_TYPES
from .contracts import literal
from .publication import insert_rows

ISSUER=1
SECURITY=2
LISTING=3


def identity_key(kind: int,source_id: str) -> int:
    if kind not in (ISSUER,SECURITY,LISTING) or not isinstance(source_id,str) or not source_id or '\x00' in source_id:
        raise ValueError('Invalid canonical identity')
    number=int.from_bytes(sha256(f'research-identity-v1\x00{kind}\x00{source_id}'.encode()).digest()[:8],'big')
    if number==0:raise ValueError('Reserved zero identity; explicit mapping revision required')
    return number


def ensure_identity(query,database,claim,kind,source_id):
    """Read-before-write under the campaign claim; collision/duplicate checks."""
    from .contracts import identifier
    identifier(database);claim.check();key=identity_key(kind,source_id)
    rows=query(f'SELECT entity_key,source_id FROM {database}.research_identity_v1 '
               f'WHERE entity_kind={kind} AND (entity_key={key} OR source_id={literal(source_id)})')
    if rows:
        if len(rows)!=1 or int(rows[0]['entity_key'])!=key or rows[0]['source_id']!=source_id:
            raise ValueError('Identity collision, duplicate mapping or unstable key')
        return key
    insert_rows(query,database,'research_identity_v1',[dict(entity_kind=kind,entity_key=key,source_id=source_id)])
    claim.check()
    rows=query(f'SELECT source_id FROM {database}.research_identity_v1 WHERE entity_kind={kind} AND entity_key={key}')
    if rows!=[dict(source_id=source_id)]:raise ValueError('Identity insertion is uncertain or duplicated')
    return key


def ensure_identities(query,database,claim,identities,*,maximum_batch=4096):
    """Bounded metadata batch; caller holds global dictionary ownership.

    One lookup and one insert/readback per batch replaces per-listing round
    trips. Collision checks run before any insert and again on exact readback.
    """
    from .contracts import identifier
    identifier(database);claim.check()
    values=tuple(sorted(set(identities)))
    if len(values)>maximum_batch:raise ValueError('Identity batch exceeds its bound')
    if not values:return {}
    desired={(kind,identity_key(kind,source_id)):source_id for kind,source_id in values}
    if len(desired)!=len(values):raise ValueError('Identity collision inside batch')
    keys=','.join(f'({kind},{key})' for kind,key in desired)
    ids=','.join(f'({kind},{literal(source_id)})' for kind,source_id in values)
    sql=(f'SELECT entity_kind,entity_key,source_id FROM {database}.research_identity_v1 '
         f'WHERE (entity_kind,entity_key) IN ({keys}) OR (entity_kind,source_id) IN ({ids})')
    rows=query(sql);found={}
    for row in rows:
        pair=(int(row['entity_kind']),int(row['entity_key']))
        if pair in found or desired.get(pair)!=row['source_id']:
            raise ValueError('Identity batch has collisions, duplicates or unstable mappings')
        found[pair]=row['source_id']
    missing=[dict(entity_kind=kind,entity_key=key,source_id=value)
             for (kind,key),value in desired.items() if (kind,key) not in found]
    if missing:
        claim.check();insert_rows(query,database,'research_identity_v1',missing,maximum_rows=maximum_batch)
        claim.check();rows=query(sql)
        actual={(int(r['entity_kind']),int(r['entity_key'])):r['source_id'] for r in rows}
        if len(actual)!=len(rows) or actual!=desired:
            raise ValueError('Identity batch readback is uncertain or duplicated')
    return {(kind,source_id):identity_key(kind,source_id) for kind,source_id in values}


@dataclass(frozen=True)
class ListingInterval:
    listing_id: str
    security_id: str
    issuer_id: str
    ticker: str
    conid: int
    valid_from: datetime
    valid_to: datetime
    exchange_country: str
    exchange_code: str
    currency: str
    product_type: str
    listed: bool
    durable_issuer: bool
    duplicate_issuer: bool
    mapping_issue: bool
    evidence_hash: str

    def __post_init__(self):
        if (not re.fullmatch('[A-Z0-9._-]{1,32}',self.ticker)
                or any(not v for v in (self.listing_id,self.security_id,self.issuer_id))
                or type(self.conid) is not int or not 0<self.conid<2**64
                or self.valid_from.tzinfo is None or self.valid_to.tzinfo is None
                or self.valid_from>=self.valid_to or not re.fullmatch('[0-9a-f]{64}',self.evidence_hash)
                or any(type(v) is not bool for v in (self.listed,self.durable_issuer,self.duplicate_issuer,self.mapping_issue))):
            raise ValueError('Listing interval lacks dated identity/source evidence')

    @property
    def reason(self):
        for condition,reason in ((not self.listed,'inactive_listing'),
              (self.currency!='USD','non_usd_currency'),(self.exchange_country.upper()!='US','non_us_exchange'),
              (is_otc_venue(self.exchange_code),'unsupported_otc_venue'),
              (self.product_type.upper() not in SUPPORTED_PRODUCT_TYPES,'unsupported_product_type'),
              (not self.durable_issuer,'weak_identity'),(self.duplicate_issuer,'duplicate_durable_issuer_identifier'),
              (self.mapping_issue,'open_mapping_issue')):
            if condition:return reason
        return ''

    def row(self,dataset_id):
        from uuid import UUID
        UUID(dataset_id)
        stamp=lambda t:t.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        return dict(dataset_id=dataset_id,listing_key=identity_key(LISTING,self.listing_id),
                    security_key=identity_key(SECURITY,self.security_id),issuer_key=identity_key(ISSUER,self.issuer_id),
                    ticker=self.ticker,conid=self.conid,valid_from=stamp(self.valid_from),valid_to=stamp(self.valid_to),
                    eligible=int(not self.reason),reason=self.reason,evidence_hash=self.evidence_hash)


def validate_intervals(intervals):
    """Metadata-sized intervals, ordered validation; never iterate market rows."""
    by_listing={};by_ticker={}
    for row in intervals:
        by_listing.setdefault(row.listing_id,[]).append(row)
        if not row.reason:by_ticker.setdefault(row.ticker,[]).append(row)
    for rows in (*by_listing.values(),*by_ticker.values()):
        rows=sorted(rows,key=lambda r:r.valid_from)
        for previous,current in zip(rows,rows[1:]):
            if previous.valid_to>current.valid_from:
                raise ValueError('Overlapping historical listing/ticker assignments')
    return tuple(intervals)


def membership_at(query,database,dataset_id,at):
    from .contracts import identifier
    from uuid import UUID
    identifier(database);UUID(dataset_id)
    if at.tzinfo is None:raise ValueError('Membership cutoff must have a timezone')
    cutoff=literal(at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
    rows=query(f'SELECT listing_key,security_key,issuer_key,ticker,conid,evidence_hash '
               f'FROM {database}.research_membership_v1 WHERE dataset_id=toUUID({literal(dataset_id)}) '
               f'AND eligible=1 AND valid_from<=toDateTime64({cutoff},6,\'UTC\') '
               f'AND valid_to>toDateTime64({cutoff},6,\'UTC\') ORDER BY listing_key')
    if len({r['listing_key'] for r in rows})!=len(rows) or len({r['ticker'] for r in rows})!=len(rows):
        raise ValueError('Historical membership is ambiguous')
    return rows
