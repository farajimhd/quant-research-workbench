from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.test_strategy_one_identity_publication import _market, _row
from pipelines.strategy_one.reference_identity_publication import match_retained_identities
from src.trading_runtime.historical_reference_identity import ReferencePin, ReferenceIdentityError, pin_from_scopes


def fixture():
    market = replace(_market(), sessions=('2026-08-20',))
    pin = ReferencePin('a' * 64, 'preopen-tradable-carry-forward-v1', '2',
                       '2026-08-19 07:00:00', '2026-08-20 08:00:00')
    retained = [_row(t, inserted='2026-08-19 06:00:00.000') for t in market.tickers]
    snapshot = [{**r, 'source_universe_date': '2026-08-19',
                 'captured_at_utc': r['source_inserted_at'], 'is_tradable': 1,
                 'row_hash': 1} for r in retained]
    return snapshot, retained, market, pin


def test_carried_identity_uses_retained_date_and_exact_tuple():
    snap, retained, market, pin = fixture()
    day, result = match_retained_identities(snap, retained, market, pin)
    assert day == '2026-08-19'
    assert [r['ibkr_conid'] for r in result] == [101, 101]
    assert all(r['source_inserted_at'] == '2026-08-19 06:00:00.000' for r in result)


@pytest.mark.parametrize('mutation', ['hash', 'missing', 'duplicate', 'listing', 'run', 'clock', 'conid', 'late', 'future'])
def test_rejects_mismatched_reference(mutation):
    snap, retained, market, pin = fixture()
    if mutation == 'hash': snap[0]['row_hash'] = 7
    if mutation == 'missing': retained.pop()
    if mutation == 'duplicate': retained.append(deepcopy(retained[0]))
    if mutation == 'listing': retained[0]['listing_id'] = 'new-listing'
    if mutation == 'run': retained[0]['source_run_id'] = 'new-run'
    if mutation == 'clock': retained[0]['source_inserted_at'] = '2026-08-19 06:01:00'
    if mutation == 'conid': retained[0]['ibkr_conid'] = ''
    if mutation == 'late': pin = replace(pin, available_at='2026-08-19 05:00:00')
    if mutation == 'future':
        for row in snap: row['source_universe_date'] = '2026-08-21'
    with pytest.raises(ReferenceIdentityError):
        match_retained_identities(snap, retained, market, pin)


def test_exact_reference_and_market_pin():
    snap, retained, market, pin = fixture()
    market = replace(market, sessions=('2026-08-19',))
    pin = replace(pin, reference_revision='preopen-tradable-snapshot-v3', cutoff_at='2026-08-19 08:00:00')
    assert match_retained_identities(snap, retained, market, pin)[0] == '2026-08-19'
    scope = [dict(build_id=market.build_id,session_date=market.sessions[0],ticker=t,
        population_snapshot_id=pin.snapshot_id,population_revision=pin.reference_revision,
        population_source_hash=pin.population_source_hash,population_available_at=pin.available_at,
        population_cutoff_at=pin.cutoff_at) for t in market.tickers]
    assert pin_from_scopes(scope,market) == pin
    scope[0]['population_snapshot_id'] = 'other'
    with pytest.raises(ReferenceIdentityError): pin_from_scopes(scope,market)


def test_legacy_identity_keeps_existing_token():
    from tests.test_backtest_strategy_one_identity import Client, _market as legacy_market
    from src.backend.backtest_strategy_one_identity import certify_identity_plan
    with patch('src.backend.backtest_reference_identity.certify_reference_identity', side_effect=AssertionError):
        a = certify_identity_plan(legacy_market(), client=Client())
        b = certify_identity_plan(legacy_market(), client=Client())
    assert a.token == b.token


def test_publication_withholds_coverage_after_child_mismatch(monkeypatch):
    from pipelines.strategy_one import reference_identity_publication as subject
    snap, retained, market, pin = fixture()
    resolved = match_retained_identities(snap, retained, market, pin)
    monkeypatch.setattr(subject, 'resolve_retained_identities', lambda *a: resolved)
    writes = []
    class Client:
        def execute(self, sql):
            if sql.startswith('INSERT'):
                writes.append(sql)
            return ''
    with pytest.raises(ReferenceIdentityError, match='readback'):
        subject.publish_reference_identity(Client(), Client(), market, pin)
    assert len(writes) == 1 and 'identity_coverage_v2' not in writes[0]


def test_publication_withholds_coverage_when_source_changes(monkeypatch):
    import json
    from pipelines.strategy_one import reference_identity_publication as subject
    snap, retained, market, pin = fixture()
    resolved = match_retained_identities(snap, retained, market, pin)
    changed = deepcopy(resolved)
    changed[1][0]['ibkr_conid'] += 1
    outcomes = iter([resolved, changed])
    monkeypatch.setattr(subject, 'resolve_retained_identities', lambda *a: next(outcomes))
    writes = []
    class Client:
        def execute(self, sql):
            if sql.startswith('INSERT'):
                writes.append(sql)
                return ''
            if 'identity_coverage_' in sql: return ''
            return '\n'.join(json.dumps(r) for r in resolved[1])
    with pytest.raises(ReferenceIdentityError, match='changed during'):
        subject.publish_reference_identity(Client(), Client(), market, pin)
    assert len(writes) == 1


def test_v2_reader_checks_pin_and_full_child_hash(monkeypatch):
    import json
    from dataclasses import asdict
    from src.backend import backtest_reference_identity as subject
    from src.backend.backtest_strategy_one_identity import identity_content_hash
    snap, retained, market, pin = fixture()
    source_date, facts = match_retained_identities(snap, retained, market, pin)
    seal = dict(identity_attempt_id='11111111-1111-1111-1111-111111111111',
                source_universe_date=source_date, **asdict(pin), ticker_count=2,
                content_hash=identity_content_hash(facts), reference_hash=pin.digest(source_date))
    monkeypatch.setattr(subject, 'verify_tables', lambda c: None)
    class Client:
        def execute(self, sql):
            if 'system.tables' in sql: result = [{'name': subject.COVERAGE.name}]
            elif 'identity_coverage_v2' in sql: result = [seal]
            else: result = facts
            return '\n'.join(json.dumps(r) for r in result)
    assert subject.certify_reference_identity(market, client=Client(), pin=pin).conid_for('AAA') == 101
    seal['snapshot_id'] = 'b' * 64
    with pytest.raises(ReferenceIdentityError, match='provenance'):
        subject.certify_reference_identity(market, client=Client(), pin=pin)
    seal['snapshot_id'] = pin.snapshot_id
    facts[0]['ibkr_conid'] += 1
    with pytest.raises(ReferenceIdentityError, match='publication seal'):
        subject.certify_reference_identity(market, client=Client(), pin=pin)


def test_reader_and_provisioner_share_reference_grants(monkeypatch):
    from src.backend import backtest_fixed_v3_preflight as preflight
    from scripts.clickhouse.provision_fixed_backtest_v3_principals import desired_plan
    from src.trading_runtime.historical_reference_identity import TABLES
    seen = {}
    monkeypatch.setattr(preflight, 'storage_preflight', lambda c, **kw: seen.update(storage=kw['tables']))
    monkeypatch.setattr(preflight, 'journal_permission_preflight', lambda c, **kw: seen.update(kw))
    monkeypatch.setattr(preflight, '_exact_grants', lambda *a: None)
    preflight.read_v3_preflight(object())
    desired = next(p for p in desired_plan() if p.role == 'read')
    for table in TABLES:
        assert table in seen['storage']
        assert table.name in seen['read_only_tables']
        assert table.name in desired.select_arte
        assert table.name not in desired.insert_arte
