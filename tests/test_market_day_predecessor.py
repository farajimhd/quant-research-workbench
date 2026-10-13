"""Predecessors require exact immutable receipts, not mutable build timestamps."""
from dataclasses import replace
from copy import deepcopy

import pytest

from pipelines.market_sip.events.market_day_checkpoint import CompletedMarketDayLedger
from pipelines.market_sip.events.market_day_predecessor import MarketDayPredecessorLedger
from src.trading_runtime.arte_market_day_cold_preflight import audit_attested_market_day_certificate
from scripts.build_market_day import digest
from test_arte_market_day_certification import DAY, BUILD, PIN
from test_arte_market_day_cold_preflight import fixture
from test_arte_market_day_source_plan import plan as source_plan_fixture

TARGET = '2026-08-19'


def supplied():
    client, keeper = fixture()
    audit = audit_attested_market_day_certificate(client, keeper, BUILD,
        sessions=(DAY,), include_session_hashes=True)
    # Product-value qualification is isolated here; this synthetic fixture
    # exercises native certificate hashes, source storage and Keeper checks.
    audit = replace(audit, market_products_verified=True)
    plan = source_plan_fixture()
    plan['units'][0]['source_date'] = TARGET
    plan['predecessors'] = {TARGET: DAY}
    definition = dict(version='market-day-core-v5', calculation_source=PIN,
        rules_hash=PIN, plan=plan)
    current = CompletedMarketDayLedger(digest(definition), ((TARGET, 'TEST'),), (), ())
    return current, definition, (audit, client.rows['market_day_build_header_v1'][0])


def test_exact_native_parent_receipts_select_without_timestamp_authority():
    current, definition, parent = supplied()
    registry = MarketDayPredecessorLedger(current, definition, (parent,))
    view = registry.for_target(TARGET, 'TEST')
    result = view.candidates(DAY, 'TEST', current.build_id, 'arte', PIN, PIN, 'market-day-core-v5')
    assert result == [dict(build_id=BUILD, attempt_id='attempt', source_hash='source')]
    assert registry.unit(BUILD, DAY, 'TEST', 'bars')['output_rows'] == 0
    assert 'updated_at' not in result[0]
    result[0]['attempt_id'] = 'changed'
    assert view.candidates(DAY, 'TEST', current.build_id, 'arte', PIN, PIN,
        'market-day-core-v5')[0]['attempt_id'] == 'attempt'
    assert registry.predecessor_for(TARGET, 'TEST') == DAY
    with pytest.raises(ValueError, match='before its target'):
        view.candidates(TARGET, 'TEST', current.build_id, 'arte', PIN, PIN, 'market-day-core-v5')


def test_missing_scope_cannot_silently_infer_bootstrap():
    current, definition, _ = supplied()
    registry = MarketDayPredecessorLedger(current, definition, ())
    with pytest.raises(ValueError, match='bootstrap must not be inferred'):
        registry.for_target(TARGET, 'TEST').candidates(DAY, 'TEST', current.build_id,
            'arte', PIN, PIN, 'market-day-core-v5')


@pytest.mark.parametrize('mutation', ['audit', 'header', 'children', 'duplicate', 'calendar', 'scope'])
def test_unverified_or_changed_declared_sources_fail_closed(mutation):
    current, definition, parent = supplied()
    audit, header = parent
    if mutation == 'audit': audit = replace(audit, market_products_verified=False)
    elif mutation == 'header': header = {**header, 'rules_hash': 'c' * 64}
    elif mutation == 'children':
        certificate = replace(audit.certificate, session_families=deepcopy(audit.certificate.session_families))
        certificate.session_families[DAY]['market_day_stage_certificate_v1'][0]['output_rows'] += 1
        audit = replace(audit, certificate=certificate)
    elif mutation == 'calendar':
        definition['plan']['predecessors'][TARGET] = TARGET
        current = CompletedMarketDayLedger(digest(definition), current.scopes, (), ())
    elif mutation == 'scope':
        current = CompletedMarketDayLedger(current.build_id, ((DAY, 'OTHER'),), (), ())
    parents = ((audit, header),) * (2 if mutation == 'duplicate' else 1)
    with pytest.raises(ValueError):
        MarketDayPredecessorLedger(current, definition, parents)
