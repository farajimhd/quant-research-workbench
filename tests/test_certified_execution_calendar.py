import json
from copy import deepcopy
from datetime import date

import pytest

from pipelines.certified_research import execution_calendar as ec
from pipelines.certified_research.contracts import digest


def inputs():
    details = dict(pages=1, saturated=False, sha256='a'*64, target_table='market_stock_split_v1')
    coverage = dict(coverage_kind='massive_splits', source_system='massive', source_object='splits',
        status='completed', rows_read=2, rows_written=2, rows_failed=0, source_run_id='run',
        finished_at_utc='2026-03-01 00:00:00', coverage_start_date='2025-12-01',
        coverage_end_date='2026-03-01', details_json=json.dumps(details))
    summary = dict(source='massive_splits', coverage_kind='massive_splits', start_date='2025-12-01',
        end_date='2026-03-01', status='completed', rows_read=2, rows_written=2, rows_failed=0, details=details)
    rows = [dict(stock_split_id=str(i), provider_ticker='ABC', execution_date=d,
        split_from=1, split_to=ratio, source_system='massive', source_run_id='run',
        source_content_sha256='b'*64) for i,d,ratio in [(1,'2026-01-05',2),(2,'2026-02-05',3)]]
    fingerprint = digest(tuple((r['stock_split_id'], r['provider_ticker'], r['execution_date'],
        float(r['split_from']), float(r['split_to']), r['source_content_sha256']) for r in rows))
    mappings = [dict(listing_key=1, security_key=2, ticker='ABC', start='2025-12-01', end_exclusive='2026-03-01')]
    return rows, coverage, summary, fingerprint, mappings


def test_execution_only_and_verified_no_action():
    rows, coverage, summary, fingerprint, mappings = inputs()
    calendar = ec.qualify(rows, coverage, summary, expected_catalog_hash=fingerprint)
    assert ec.resolve(calendar,1,'2026-01-02','2026-01-05',mappings).factor == .5
    assert ec.resolve(calendar,1,'2026-01-05','2026-01-06',mappings).factor == 1
    assert ec.resolve(calendar,1,'2026-01-02','2026-02-05',mappings).factor == pytest.approx(1/6)


@pytest.mark.parametrize('change', ['duplicate','ratio','hash','coverage','summary'])
def test_fail_closed_catalog(change):
    rows, coverage, summary, fingerprint, _ = inputs()
    if change == 'duplicate': rows[1] = deepcopy(rows[0])
    if change == 'ratio': rows[0]['split_to'] = 0
    if change == 'hash': rows[0]['source_content_sha256'] = 'c'*64
    if change == 'coverage': coverage['rows_failed'] = 1
    if change == 'summary': summary['details']['pages'] = 2
    with pytest.raises(ValueError):
        ec.qualify(rows, coverage, summary, expected_catalog_hash=fingerprint)


def test_dated_identity_required_even_without_actions():
    rows, coverage, summary, fingerprint, mappings = inputs()
    calendar = ec.qualify(rows, coverage, summary, expected_catalog_hash=fingerprint)
    with pytest.raises(ValueError): ec.resolve(calendar,1,'2026-01-06','2026-01-07',[])
    mappings.append(dict(mappings[0],listing_key=3))
    with pytest.raises(ValueError): ec.resolve(calendar,1,'2026-01-02','2026-01-05',mappings)
    with pytest.raises(ValueError): ec.resolve(calendar,1,'2026-02-28','2026-03-01',mappings)


def test_same_day_actions_retained_but_not_silently_combined():
    rows, coverage, summary, _, mappings = inputs()
    rows[1]['execution_date'] = rows[0]['execution_date']
    fingerprint = digest(tuple((r['stock_split_id'], r['provider_ticker'], r['execution_date'],
        float(r['split_from']), float(r['split_to']), r['source_content_sha256']) for r in rows))
    calendar = ec.qualify(rows, coverage, summary, expected_catalog_hash=fingerprint)
    with pytest.raises(ValueError, match='same-day reconciliation'):
        ec.resolve(calendar,1,'2026-01-02','2026-01-05',mappings)
    assert ec.resolve(calendar,1,'2026-01-06','2026-01-07',mappings).factor == 1


def test_restore_pinned_immutable_proof():
    from dataclasses import asdict
    rows, coverage, summary, fingerprint, _ = inputs()
    calendar = ec.qualify(rows, coverage, summary, expected_catalog_hash=fingerprint)
    document = json.loads(json.dumps(dict(version=ec.VERSION, calendar=asdict(calendar),
        coverage=coverage, retained_result=summary), default=str))
    assert ec.restore(document, expected_evidence_hash=calendar.evidence_hash) == calendar
    document['calendar']['events'][0][4] = 7
    with pytest.raises(ValueError): ec.restore(document, expected_evidence_hash=calendar.evidence_hash)
