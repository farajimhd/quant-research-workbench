from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from pipelines.market_sip.events import shared_reference_listing_basis as b
from src.market_engine.opening_price_reference import previous_regular_session


def packet():
    identity = dict(ticker='GORO', symbol_id='symbol', listing_id='gold-resource',
        security_id='resource-security', ibkr_conid='40743577',
        source_run_id='current-capture', source_inserted_at='2026-07-30 07:54:15.592')
    provenance = dict(source_table='q_live.feature_tradable_universe_v1',
        source_event_id='retained', source_evidence_ref='reference:retained',
        source_content_sha256='a' * 64, source_run_id='reference-run',
        inserted_at='2026-07-01 00:00:00')
    keys = dict(provider_ticker='GORO', symbol_id='symbol',
        listing_id='gold-resource', security_id='resource-security')
    routing = [dict(provenance, **keys, routing_fact_id='route',
        ibkr_conid='40743577', broker='ibkr', sec_type='STK', currency_code='USD', routing_status='valid',
        valid_from_utc='2026-07-01 00:00:00', valid_to_utc=None)]
    tradability = [dict(provenance, **keys, tradability_fact_id='tradable',
        effective_at_utc='2026-07-01 00:00:00', observed_at_utc='2026-07-01 00:00:00',
        is_tradable=1, block_status='tradable')]
    retained = [dict(universe_date=day, ticker='GORO', symbol_id='symbol',
        listing_id='gold-resource', security_id='resource-security', ibkr_conid='40743577',
        is_tradable=1, source_run_id=run, inserted_at=stamp)
        for day, run, stamp in (
            ('2026-07-29', 'prior-capture', '2026-07-29 08:02:04.002'),
            ('2026-07-30', 'current-capture', identity['source_inserted_at']))]
    return identity, routing, tradability, retained


def resolve(parts):
    return b.resolve_listing(*parts, day=date(2026, 7, 30),
        regular=previous_regular_session(date(2026, 7, 30)),
        cutoff=datetime(2026, 7, 30, 8, tzinfo=timezone.utc))


def test_prior_capture_after_premarket_but_before_rth_is_not_backdated():
    listing = resolve(packet())
    assert listing.listing_id == 'gold-resource'
    assert listing.valid_from == date(2026, 7, 29)
    assert listing.valid_to_exclusive == date(2026, 7, 31)
    assert listing.identity_interval_id == 'shared-reference:' + listing.identity_source_hash
    assert resolve(packet()) == listing


@pytest.mark.parametrize('case', ['collision', 'conid', 'prior_late', 'current_late',
    'missing_prior', 'duplicate_prior', 'blocked', 'expired', 'late_route', 'changed_capture',
    'currency', 'broker', 'boolean'])
def test_invalid_reference_proof_fails_closed(case):
    parts = deepcopy(packet())
    identity, routes, states, retained = parts
    if case == 'collision': routes[0]['security_id'] = 'goldgroup-security'
    elif case == 'conid': routes[0]['ibkr_conid'] = '587763590'
    elif case == 'prior_late': retained[0]['inserted_at'] = '2026-07-29 13:30:00'
    elif case == 'current_late': retained[1]['inserted_at'] = '2026-07-30 08:00:00'
    elif case == 'missing_prior': retained.pop(0)
    elif case == 'duplicate_prior': retained.append(deepcopy(retained[0]))
    elif case == 'blocked':
        states.append(dict(states[0], tradability_fact_id='blocked', is_tradable=0,
            block_status='blocked', effective_at_utc='2026-07-29 14:00:00',
            observed_at_utc='2026-07-29 14:00:00', inserted_at='2026-07-29 14:00:00'))
    elif case == 'expired': routes[0]['valid_to_utc'] = '2026-07-30 07:59:00'
    elif case == 'late_route': routes[0]['inserted_at'] = '2026-07-30 07:59:00'
    elif case == 'changed_capture': retained[1]['source_run_id'] = 'another-source'
    elif case == 'currency': routes[0]['currency_code'] = 'CAD'
    elif case == 'broker': routes[0]['broker'] = 'another-broker'
    elif case == 'boolean': states[0]['is_tradable'] = True
    with pytest.raises(ValueError): resolve(parts)


def test_changed_provenance_changes_proof_and_duplicate_latest_state_is_rejected():
    parts = deepcopy(packet())
    before = resolve(parts)
    parts[1][0]['source_content_sha256'] = 'b' * 64
    assert resolve(parts).identity_source_hash != before.identity_source_hash
    parts[2].append(deepcopy(parts[2][0]))
    with pytest.raises(ValueError, match='ambiguous'): resolve(parts)


def test_bounded_loader_pins_identity_and_cold_reloads_exact_sources():
    parts = packet()
    market = SimpleNamespace(sessions=('2026-07-30',), tickers=('GORO',))
    seen = []
    def read(reader, query, *, bound):
        assert reader is owner
        seen.append(query)
        assert bound == 4097 and 'LIMIT 4097' in query
        assert 'gold-resource' in query and 'resource-security' in query and 'symbol' in query
        if 'security_routing_fact_v1' in query: return parts[1]
        if 'security_tradability_fact_v1' in query: return parts[2]
        if 'feature_tradable_universe_v1' in query: return parts[3]
        raise AssertionError(query)
    owner = object()
    expected = (resolve(parts),)
    assert b.load_listings(owner, market, {'GORO': parts[0]}, read_rows=read) == expected
    assert b.load_listings(owner, market, {'GORO': parts[0]}, read_rows=read) == expected
    assert len(seen) == 6
    parts[2][0]['block_status'] = 'blocked'
    with pytest.raises(ValueError, match='tradability'):
        b.load_listings(owner, market, {'GORO': parts[0]}, read_rows=read)


def test_native_opening_builder_reverifies_shared_basis_and_retains_parent_checks(monkeypatch):
    from dataclasses import asdict
    from pipelines.market_sip.events import opening_price_reference_producer as p
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
    from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
    from src.trading_runtime.historical_reference_identity import ReferencePin
    uid = '00000000-0000-0000-0000-000000000001'
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), 'current', 'a'*64,
        ('2026-07-30',), ('GORO',), (), (1000,), 'b'*64)
    unit = MarketDayUnit('prior', '2026-07-29', 'GORO', 'bars', uid, 'c'*64, 2, '123')
    prior = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), 'prior', 'd'*64,
        ('2026-07-29',), ('GORO',), (unit,), (1000,), 'e'*64)
    identity = CertifiedIdentityPlan('current', '2026-07-30', uid, market.token,
        ('GORO',), (40743577,), 'f'*64, '1'*64)
    pin = ReferencePin('snapshot', 'revision', '2'*64,
        '2026-07-30 07:55:00', '2026-07-30 08:00:00')
    parts = packet()
    monkeypatch.setattr(p, 'certify_identity_plan', lambda *args, **kwargs: identity)
    monkeypatch.setattr(p, 'load_reference_pin', lambda *args: pin)
    monkeypatch.setattr(p, '_identity_rows', lambda *args: {'GORO': parts[0]})
    parent_reads = []
    monkeypatch.setattr(p, '_verify_market_batch', lambda *args: parent_reads.append(args[3]))
    def read(reader, query, *, bound=25001):
        if 'FROM system.tables' in query:
            names = [('arte','bars_v1'), ('q_live','id_symbol_interval_v1'),
                ('q_live','market_stock_split_v1'), *[('q_live', name) for name in b.TABLES]]
            return [dict(database=db, name=name, engine='MergeTree', storage_policy='live_market_ssd')
                for db,name in names]
        if 'FROM system.parts' in query: return []
        if 'security_routing_fact_v1' in query: return parts[1]
        if 'security_tradability_fact_v1' in query: return parts[2]
        if 'feature_tradable_universe_v1' in query: return parts[3]
        raise AssertionError(query)
    monkeypatch.setattr(p, '_rows', read)
    source = p.certified_opening_input(object(), market, prior, listing_basis=b.BASIS)
    assert source.population_reference_hash == p.contract.content_hash(asdict(pin))
    assert source.listings == (resolve(parts),)
    assert parent_reads == ['2026-07-29']
    source.verify(object())
    assert parent_reads == ['2026-07-29', '2026-07-29']
    parts[2][0]['block_status'] = 'blocked'
    with pytest.raises(ValueError, match='tradability'): source.verify(object())
