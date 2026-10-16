"""Retrospective executed-split authority, independent of public-as-of facts.

Inputs are complete retained catalog rows and their acquisition receipt, not
announcements. The normalized row fingerprint is distinct from the provider
response fingerprint, whose original bytes may no longer be retained.
No ClickHouse publication or original-public-vintage certification occurs here.
"""
from dataclasses import dataclass
from datetime import date
from math import isfinite, prod
import re

from .contracts import digest
from .adjustments import Adjustment

VERSION = 'retrospective-executed-split-calendar-v1'


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Invalid execution-calendar evidence hash')
    return value


def _day(value):
    return value if type(value) is date else date.fromisoformat(value)


@dataclass(frozen=True)
class Calendar:
    start: date
    end_exclusive: date
    # event id, ticker, execution date, from, to, original normalized-row hash
    events: tuple
    catalog_hash: str
    evidence_hash: str


def qualify(rows, coverage, retained_result, *, expected_catalog_hash):
    """Qualify the entire acquisition range, never a filtered subset.

    expected_catalog_hash must be frozen by the caller's immutable source plan.
    Coverage and retained run summary must agree exactly on acquisition identity,
    counts, range, pagination and original provider response fingerprint.
    """
    import json
    details = json.loads(coverage['details_json'])
    count = int(coverage['rows_read'])
    if (coverage['coverage_kind'] != 'massive_splits' or coverage['source_system'] != 'massive'
            or coverage['source_object'] != 'splits' or coverage['status'] != 'completed'
            or count <= 0 or int(coverage['rows_written']) != count or int(coverage['rows_failed'])
            or len(rows) != count or not coverage['source_run_id'] or not coverage['finished_at_utc']
            or details.get('saturated') is not False or type(details.get('pages')) is not int
            or details['pages'] < 1 or details.get('target_table') != 'market_stock_split_v1'):
        raise ValueError('Incomplete split execution acquisition coverage')
    _hash(details['sha256'])
    expected = dict(source='massive_splits', coverage_kind='massive_splits',
        start_date=coverage['coverage_start_date'], end_date=coverage['coverage_end_date'],
        status=coverage['status'], rows_read=count, rows_written=count, rows_failed=0, details=details)
    if retained_result != expected:
        raise ValueError('Retained acquisition summary differs from coverage')
    start, end = _day(expected['start_date']), _day(expected['end_date'])
    if start >= end:
        raise ValueError('Invalid execution coverage bounds')
    events = []
    ids = set()
    for row in rows:
        event_id, ticker, day = row['stock_split_id'], row['provider_ticker'], _day(row['execution_date'])
        before, after = float(row['split_from']), float(row['split_to'])
        if (not event_id or not ticker or not start <= day < end
                or row['source_system'] != 'massive' or row['source_run_id'] != coverage['source_run_id']
                or not isfinite(before) or not isfinite(after) or before <= 0 or after <= 0
                or event_id in ids):
            raise ValueError('Invalid, duplicate or conflicting split execution')
        ids.add(event_id)
        events.append((event_id, ticker, str(day), before, after, _hash(row['source_content_sha256'])))
    events = tuple(sorted(events))
    fingerprint = digest(events)
    if fingerprint != _hash(expected_catalog_hash):
        raise ValueError('Split execution catalog fingerprint changed')
    proof = digest(dict(version=VERSION, coverage=coverage, retained_result=retained_result,
                        normalized_catalog_hash=fingerprint))
    return Calendar(start, end, events, fingerprint, proof)


def restore(document, *, expected_evidence_hash):
    """Read back a frozen authority against the launch plan's pinned proof."""
    if document['version'] != VERSION:
        raise ValueError('Unknown execution calendar version')
    saved, coverage = document['calendar'], document['coverage']
    rows = [dict(stock_split_id=e[0], provider_ticker=e[1], execution_date=e[2],
        split_from=e[3], split_to=e[4], source_content_sha256=e[5], source_system='massive',
        source_run_id=coverage['source_run_id']) for e in saved['events']]
    calendar = qualify(rows, coverage, document['retained_result'],
                       expected_catalog_hash=saved['catalog_hash'])
    if (calendar.evidence_hash != _hash(expected_evidence_hash)
            or saved['evidence_hash'] != calendar.evidence_hash
            or _day(saved['start']) != calendar.start or _day(saved['end_exclusive']) != calendar.end_exclusive):
        raise ValueError('Frozen execution calendar proof changed')
    return calendar


def resolve(calendar, listing_key, previous, day, mappings):
    """Adjust continuity only for executions in (previous, day].

    mappings contain listing_key, security_key, ticker, start, end_exclusive;
    dates describe certified dated identity, not current-symbol substitution.
    The caller freezes and verifies the mapping source alongside this proof.
    """
    previous, day = _day(previous), _day(day)
    if (type(listing_key) is not int or listing_key <= 0 or previous >= day
            or previous < calendar.start or day >= calendar.end_exclusive
            or digest(calendar.events) != calendar.catalog_hash):
        raise ValueError('Execution calendar does not cover continuation or changed')
    normalized = []
    for row in mappings:
        a, b = _day(row['start']), _day(row['end_exclusive'])
        if a >= b or not row['ticker'] or int(row['security_key']) <= 0 or int(row['listing_key']) <= 0:
            raise ValueError('Invalid dated execution identity')
        normalized.append((int(row['listing_key']), int(row['security_key']), row['ticker'], a, b))
    relevant = sorted((r for r in normalized if r[0] == listing_key and r[3] <= day and r[4] > previous), key=lambda r:r[3])
    cursor = previous
    security = None
    for identity in relevant:
        if identity[3] > cursor or (security is not None and identity[1] != security):
            raise ValueError('Dated listing identity has a gap or security change')
        if identity[3] < cursor and cursor != previous:
            raise ValueError('Dated listing identity overlaps')
        security = identity[1]
        cursor = identity[4]
    if cursor <= day or security is None:
        raise ValueError('Dated listing identity does not cover continuation')
    applied = []
    applied_days = set()
    for event in calendar.events:
        effective = _day(event[2])
        if not previous < effective <= day:
            continue
        owners = [r for r in normalized if r[2] == event[1] and r[3] <= effective < r[4]]
        if not any(r[0] == listing_key for r in owners):
            continue
        if len(owners) != 1:
            raise ValueError('Split ticker has ambiguous dated listing identity')
        if effective in applied_days:
            raise ValueError('Multiple split executions require independent same-day reconciliation')
        applied_days.add(effective)
        applied.append(event)
    factor = prod(event[3] / event[4] for event in applied)
    evidence = digest(dict(version=VERSION, listing_key=listing_key, previous=previous, day=day,
        mapping_hash=digest(sorted(normalized)), executions=applied, factor=factor,
        calendar_proof=calendar.evidence_hash, semantics='retrospective-execution-calendar; not original-public availability'))
    return Adjustment(factor, evidence, calendar.evidence_hash)
