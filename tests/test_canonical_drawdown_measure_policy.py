from dataclasses import asdict
from datetime import datetime, date, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from types import SimpleNamespace
import asyncio
import math

import pytest

from src.trading_runtime.drawdown_measure_policy import (
    DrawdownMeasurePolicy, canonical_amount, canonical_drawdown, drawdown_exceeds)
from src.trading_runtime.drawdown_measure_authority import resolve_drawdown_policy
from src.trading_runtime.portfolio import (PortfolioManagementEngine, PortfolioAccountProfile,
    PortfolioAccountState, PortfolioPolicy, PortfolioSyncState)
from src.trading_runtime.risk_supervisor import ContinuousRiskSupervisor
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.arte_journal_projection import account_risk_batch
from src.trading_runtime.arte_journal_writer import (publish_typed_batch, load_committed_prefix,
    load_committed_account_risk_page)
from test_arte_journal_writer import MemoryClient, RUN, RECORD, BATCH, ATTEMPT, ZERO
from test_arte_trade_proposal_children import full_result
from src.trading_runtime.arte_trade_proposal_children import project_result_children

POLICY = DrawdownMeasurePolicy()
AT = datetime(2026, 8, 24, 20, tzinfo=timezone.utc)


def engine(peak, net, limit, policy):
    # Real producer and supervisor methods; no arithmetic-method substitutions.
    e = PortfolioManagementEngine.__new__(PortfolioManagementEngine)
    e.drawdown_measure_policy = policy
    e.reservations, e.allocations, e.differences = {}, {}, {}
    profile = PortfolioAccountProfile('primary', 'DU1', 'backtest', 'cash',
        PortfolioPolicy(policy_id='constructed-contract-proof', maximum_drawdown=limit))
    state = PortfolioAccountState(profile, sync_state=PortfolioSyncState.SYNCHRONIZED,
        summary=SimpleNamespace(netliquidation=net, availablefunds=0., buyingpower=0.),
        peak_net_liquidation=peak)
    e.states, e.by_key = {'DU1': state}, {'primary': state}
    e.set_control = lambda *args, **kwargs: None
    return e


def evaluate(e):
    journal = SimpleNamespace(records=[])
    journal.append = lambda **kw: journal.records.append(kw)
    value = asyncio.run(ContinuousRiskSupervisor(e, journal=journal, run_id=RUN,
        mode='backtest').evaluate('DU1', reason='constructed-contract-proof', now=AT))
    return value, journal


def batch(value):
    record = JournalRecord(RECORD, RUN, 1, AT, AT, 'risk', 'continuous_risk_state',
        'DU1', 'DU1', asdict(value))
    return account_risk_batch(record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor='proof', expected_mode='backtest')


def test_real_producer_supervisor_json_and_fenced_typed_cold_roundtrip():
    peak = 10328.515000000001
    net = math.nextafter(peak, -math.inf)
    legacy, _ = evaluate(engine(peak, net, 250000., None))
    assert legacy.metrics['drawdown'].hex() == '0x1.0000000000000p-39'
    with pytest.raises(ValueError):
        batch(legacy)
    value, journal = evaluate(engine(peak, net, 250000., POLICY))
    assert type(value.metrics['drawdown']) is Decimal
    assert value.metrics['drawdown'] == Decimal('1E-12')
    assert journal.records[0]['payload']['metrics']['drawdown'] == Decimal('1E-12')
    assert '"drawdown":"1E-12"' in canonical_json(asdict(value))
    client = MemoryClient()
    publish_typed_batch(client, batch(value))
    rows = load_committed_account_risk_page(client, load_committed_prefix(client, RUN))
    assert Decimal(rows[0]['drawdown']) == Decimal('1E-12')
    assert all(type(v) is float for k, v in value.metrics.items() if k != 'drawdown')


def test_actual_supervisor_equality_changes_only_declared_path():
    old, _ = evaluate(engine(.3, .2, .1, None))
    new, _ = evaluate(engine(.3, .2, .1, POLICY))
    assert str(old.state) == 'normal'
    assert str(new.state) == 'reduce_only'
    assert not drawdown_exceeds(new.metrics['drawdown'], .1, policy=POLICY)
    assert drawdown_exceeds(new.metrics['drawdown'], .1, policy=POLICY, inclusive=True)


def test_context_is_independent_and_sources_are_not_rounded():
    with localcontext() as hostile:
        hostile.prec, hostile.Emax, hostile.Emin = 2, 1, -1
        hostile.traps[Inexact] = hostile.traps[InvalidOperation] = True
        assert canonical_drawdown(10328.515000000001, 10328.515, policy=POLICY) == Decimal('1E-12')
        assert canonical_amount(Decimal('99999999999999999999.999999999999999999')) == Decimal('99999999999999999999.999999999999999999')


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), Decimal('1E-19'),
    Decimal('1E20'), Decimal('1E100'), object()])
def test_noncanonical_sources_fail_before_projection(value):
    with pytest.raises(ValueError):
        canonical_amount(value)


def test_no_implicit_activation_in_legacy_configuration():
    assert resolve_drawdown_policy('unregistered-manual', 1, {'old_config': True}) is None
    with pytest.raises(ValueError):
        resolve_drawdown_policy('unregistered-manual', 1, {'drawdown_measure_policy': POLICY.payload()})
    with pytest.raises(ValueError):
        resolve_drawdown_policy('unregistered-manual', 1, [])
    with pytest.raises(ValueError):
        DrawdownMeasurePolicy('unknown@3')


def test_real_proposal_children_require_explicit_owner_policy_and_preserve_other_types():
    source = full_result()
    for phase in ('metrics_before', 'metrics_after'):
        source.payload['decision'][phase]['drawdown'] = Decimal('1E-12')
    with pytest.raises(ValueError):
        project_result_children(source)
    result = project_result_children(source, drawdown_measure_policy=POLICY)
    assert result.metrics[0]['drawdown'] == '0.000000000001000000'
    source.payload['decision']['metrics_before']['net_liquidation'] = Decimal('1')
    with pytest.raises(ValueError):
        project_result_children(source, drawdown_measure_policy=POLICY)


def test_actual_v3_proposal_caller_propagates_selected_owner_policy():
    from src.backend.backtest_trade_proposal_v3 import project_trade_proposal_v3
    source = full_result()
    source.category = 'trade_proposal'
    source.entity_type = 'trade_proposal_result'
    source.entity_id = 'p-1'
    source.sequence = 1
    source.account_id = 'DU1'
    source.recorded_at = source.event_time
    for phase in ('metrics_before', 'metrics_after'):
        source.payload['decision'][phase]['drawdown'] = Decimal('1E-12')
    with pytest.raises(ValueError):
        project_trade_proposal_v3(source, attempt_id=ATTEMPT, batch_id=BATCH)
    projected = project_trade_proposal_v3(source, attempt_id=ATTEMPT, batch_id=BATCH,
        drawdown_measure_policy=POLICY)
    from src.backend.backtest_trade_proposal_v3 import TABLE_BY_KIND
    metrics = [row for name, row in projected.rows if name == TABLE_BY_KIND['metrics'].name]
    assert len(metrics) == 2
    assert all(Decimal(row['drawdown']) == Decimal('1E-12') for row in metrics)


def authority_fixture(identity='unregistered-core-proof-a'):
    from dataclasses import replace
    from src.trading_runtime.strategy_registry import NumberedStrategyRelease
    from src.trading_runtime.drawdown_measure_policy import POLICY_ID
    release = NumberedStrategyRelease(999, identity, 1, 'events',
        (POLICY_ID,), (POLICY_ID,), 'Unregistered numeric core contract proof only', '')
    release = replace(release, approved_digest=release.digest())
    contract = SimpleNamespace(strategy_id=release.executor_strategy_id, strategy_number=999,
        drawdown_measure_policy=POLICY)
    strategy = {'strategy_id': release.executor_strategy_id, 'revision': 1, 'strategy_number': 999,
        'numbered_release': {'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
            'drawdown_measure_policy': POLICY.payload()}}
    return release, contract, strategy


@pytest.mark.parametrize('identity', ['unregistered-core-proof-a', 'unregistered-core-proof-b'])
def test_pure_binding_requires_paired_declarations_and_exact_manifest(identity):
    from dataclasses import replace
    from src.trading_runtime.drawdown_measure_authority import declared_drawdown_policy
    release, contract, strategy = authority_fixture(identity)
    assert declared_drawdown_policy(release, contract, strategy) == POLICY
    release = replace(release, input_contracts=())
    with pytest.raises(ValueError):
        declared_drawdown_policy(release, contract, strategy)
    release, contract, strategy = authority_fixture(identity)
    release = replace(release, rule_set_contracts=release.rule_set_contracts * 2)
    with pytest.raises(ValueError):
        declared_drawdown_policy(release, contract, strategy)
    release, contract, strategy = authority_fixture(identity)
    strategy['numbered_release']['drawdown_measure_policy']['typed_scale'] = 17
    with pytest.raises(ValueError):
        declared_drawdown_policy(release, contract, strategy)
    release, contract, strategy = authority_fixture(identity)
    strategy['revision'] = 2
    with pytest.raises(ValueError):
        declared_drawdown_policy(release, contract, strategy)


def test_injected_foreign_policy_rejected_before_durable_campaign_bind():
    from unittest.mock import MagicMock
    from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
    control = MagicMock()
    config = RunConfig(mode=RunMode.PAPER, strategy_id='', strategy_revision=0,
        account_ids=('DU1',), anchor_date=date(2026, 8, 24))
    with pytest.raises(ValueError, match='Injected Portfolio'):
        TradingRuntime(config, MagicMock(), None, MagicMock(),
            portfolio=SimpleNamespace(drawdown_measure_policy=POLICY), control_plane=control)
    control.campaigns.bind_durable_authority.assert_not_called()


def test_real_portfolio_admission_and_reprice_use_declared_strict_threshold():
    from dataclasses import replace
    from test_adaptive_execution_risk import PortfolioAndContinuousRiskTests, adaptive_intent
    fixture = PortfolioAndContinuousRiskTests()
    fixture.setUp()
    try:
        policy = PortfolioPolicy(policy_id='numeric-boundary-proof', maximum_drawdown=.1,
            maximum_position_fraction=1, maximum_ticker_fraction=1,
            maximum_planned_risk_fraction=1, maximum_open_risk_fraction=1,
            maximum_order_notional=1000000)
        legacy = fixture.engine(policy)
        state = legacy.states['DU1']
        state.summary = replace(state.summary, netliquidation=100000.2)
        state.peak_net_liquidation = 100000.3
        legacy._persist_state(state)
        old_decision, old_intent = asyncio.run(legacy.approve(adaptive_intent(quantity=1), account_id='DU1'))
        assert 'drawdown_limit' in old_decision.reasons
        assert old_intent is None
        selected = fixture.engine(policy)
        selected.drawdown_measure_policy = POLICY
        state = selected.states['DU1']
        state.summary = replace(state.summary, netliquidation=100000.2)
        state.peak_net_liquidation = 100000.3
        selected._persist_state(state)
        decision, approved = asyncio.run(selected.approve(adaptive_intent(quantity=1), account_id='DU1'))
        assert 'drawdown_limit' not in decision.reasons
        assert decision.metrics_before['drawdown'] == Decimal('.1')
        assert approved is not None
        assert asyncio.run(selected.authorize_entry_reprice(approved, 'DU1', 100., 1.))
        selected.drawdown_measure_policy = None
        assert not asyncio.run(selected.authorize_entry_reprice(approved, 'DU1', 100., 1.))
    finally:
        fixture.tearDown()


@pytest.mark.parametrize('field,contracts', [
    ('input_contracts', ('legacy-input@1',)),
    ('rule_set_contracts', ('legacy-rule@1',)),
    ('input_contracts', ('portfolio.drawdown.unknown@3',)),
    ('rule_set_contracts', ('portfolio.drawdown.unknown@3',)),
])
def test_paired_binder_rejects_structurally_valid_resealed_foreign_contracts(field, contracts):
    from dataclasses import replace
    from src.trading_runtime.drawdown_measure_authority import declared_drawdown_policy
    release, contract, strategy = authority_fixture()
    release = replace(release, **{field: contracts})
    release = replace(release, approved_digest=release.digest())
    release.verify()
    strategy['numbered_release']['contract'] = release.canonical_payload()
    strategy['numbered_release']['approved_digest'] = release.approved_digest
    with pytest.raises(ValueError, match='paired exact'):
        declared_drawdown_policy(release, contract, strategy)


def test_legacy_installed_resolver_does_not_require_additional_manifest_verification():
    from unittest.mock import patch
    from src.trading_runtime.strategy_registry import numbered_strategy
    release = numbered_strategy(50)
    with patch('src.backend.backtest_strategy_one_configuration.is_numbered_fixed_configuration') as verify:
        assert resolve_drawdown_policy(release.executor_strategy_id, release.executor_revision,
            {'legacy-config-shape': True}) is None
        verify.assert_not_called()
    with pytest.raises(ValueError):
        resolve_drawdown_policy(release.executor_strategy_id, release.executor_revision,
            {'drawdown_measure_policy': POLICY.payload()})


@pytest.fixture
def unregistered_installed_fixture(monkeypatch):
    # Only installed catalog/full-manifest verifier seams stand in for a future
    # consuming release. No registration/publication or native-source claim.
    from hashlib import sha256
    from src.trading_runtime import numbered_fixed_strategy as contracts
    from src.trading_runtime import strategy_registry as registry
    from src.backend import backtest_strategy_one_configuration as reader
    release, _, strategy = authority_fixture()
    contract = contracts.NumberedFixedStrategyContract(release.number, release.executor_strategy_id)
    old_is, old_resolve, old_release = contracts.is_numbered_fixed_strategy, contracts.resolve_numbered_fixed_strategy, registry.numbered_strategy
    monkeypatch.setattr(contracts, 'is_numbered_fixed_strategy',
        lambda sid, rev: (sid, rev) == (release.executor_strategy_id, release.executor_revision) or old_is(sid, rev))
    monkeypatch.setattr(contracts, 'resolve_numbered_fixed_strategy',
        lambda sid, rev: contract if (sid, rev) == (release.executor_strategy_id, release.executor_revision) else old_resolve(sid, rev))
    monkeypatch.setattr(registry, 'numbered_strategy',
        lambda number: release if number == release.number else old_release(number))
    checks = []
    def verify(payload):
        checks.append(payload)
        release.verify()
        assert payload['strategy']['numbered_release']['contract'] == release.canonical_payload()
        assert payload['strategy']['numbered_release']['approved_digest'] == release.approved_digest
        return True
    monkeypatch.setattr(reader, 'is_numbered_fixed_configuration', verify)
    payload = {'strategy': strategy}
    digest = sha256(canonical_json(payload).encode()).hexdigest()
    revision = {'revision_id': 'unregistered-numeric-core-fixture', 'content_hash': digest, 'payload': payload}
    flat = {'strategy_id': release.executor_strategy_id, 'strategy_revision': release.executor_revision}
    return revision, flat, checks


def test_run_bound_owner_requires_exact_configuration_hash_run_and_release(unregistered_installed_fixture):
    from dataclasses import replace
    from src.trading_runtime.drawdown_measure_authority import bind_run_drawdown_authority
    revision, flat, checks = unregistered_installed_fixture
    authority = bind_run_drawdown_authority(run_id=RUN, expected_config=flat,
        configuration_hash=revision['content_hash'], configuration_revision=revision)
    assert authority.policy == POLICY and checks
    with pytest.raises(ValueError):
        authority.verify(run_id='foreign-run', expected_config=flat)
    with pytest.raises(ValueError):
        authority.verify(run_id=RUN, expected_config={**flat, 'strategy_revision': 2})
    with pytest.raises(ValueError):
        bind_run_drawdown_authority(run_id=RUN, expected_config=flat,
            configuration_hash='0' * 64, configuration_revision=revision)
    with pytest.raises(ValueError):
        bind_run_drawdown_authority(run_id=RUN, expected_config=flat,
            configuration_hash=revision['content_hash'])
    with pytest.raises(ValueError):
        replace(authority, configuration_revision_json=authority.configuration_revision_json.replace('typed_scale', 'unknown_scale'))


def test_actual_v4_owner_fresh_resume_and_fenced_metric_cold_recovery(unregistered_installed_fixture):
    from src.backend.backtest_fixed_journal_bootstrap import FixedV4JournalPreflightToken, _assemble_v4_writer_lane
    from src.trading_runtime.drawdown_measure_authority import bind_run_drawdown_authority
    revision, flat, _ = unregistered_installed_fixture
    authority = bind_run_drawdown_authority(run_id=RUN, expected_config=flat,
        configuration_hash=revision['content_hash'], configuration_revision=revision)
    token = FixedV4JournalPreflightToken(RUN, ('DU1',), date(2026, 8, 1),
        revision['content_hash'], 'a' * 64, 'b' * 64)
    created = []
    def writer_factory(client, **kw):
        created.append(kw)
        return SimpleNamespace(run_id=kw['run_id'], run_mode='backtest',
            journal_profile='backtest_v4', coalesce_batches=False,
            max_events_per_commit=kw['max_events_per_commit'], close=lambda: None)
    def assemble(initial=0, prior=ZERO, owner=authority):
        return _assemble_v4_writer_lane(None, token, attempt_id=ATTEMPT,
            expected_config=flat, fixed_market_parent_plan=None,
            fixed_market_execution_plan=None, expected_market_start=AT,
            writer_factory=writer_factory, batch_size=16, queue_capacity=1,
            initial_sequence=initial, prior_batch_id=prior, drawdown_authority=owner)
    client = MemoryClient()
    fresh = assemble()
    value, _ = evaluate(engine(.3, .2, .1, POLICY))
    fresh.journal.append(run_id=RUN, category='risk', entity_type='continuous_risk_state',
        entity_id='DU1', account_id='DU1', event_time=AT, payload=asdict(value))
    unit, = fresh.publisher._prepare_batches(1)
    publish_typed_batch(client, unit)
    resumed = assemble(1, unit.batch_id)
    resumed.journal.append(run_id=RUN, category='risk', entity_type='continuous_risk_state',
        entity_id='DU1', account_id='DU1', event_time=AT, payload=asdict(value))
    suffix, = resumed.publisher._prepare_batches(2)
    assert suffix.prior_batch_id == unit.batch_id
    publish_typed_batch(client, suffix)
    prefix = load_committed_prefix(client, RUN)
    assert prefix.last_sequence == 2
    assert all(Decimal(row['drawdown']) == Decimal('.1')
        for row in load_committed_account_risk_page(client, prefix))
    before = len(created)
    with pytest.raises(ValueError):
        assemble(owner=None)
    assert len(created) == before


def test_actual_v3_prefix_owner_policy_is_not_record_metadata(unregistered_installed_fixture):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    from src.backend.backtest_trade_proposal_v3 import TABLE_BY_KIND
    from src.trading_runtime.drawdown_measure_authority import bind_run_drawdown_authority
    revision, flat, _ = unregistered_installed_fixture
    authority = bind_run_drawdown_authority(run_id=RUN, expected_config=flat,
        configuration_hash=revision['content_hash'], configuration_revision=revision)
    source = full_result()
    for phase in ('metrics_before', 'metrics_after'):
        source.payload['decision'][phase]['drawdown'] = Decimal('1E-12')
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category='trade_proposal', entity_type='trade_proposal_result',
        entity_id='p-1', account_id='DU1', event_time=AT, payload=source.payload)
    kwargs = dict(attempt_id=ATTEMPT, run_month=date(2026, 8, 1), prior_sequence=0,
        expected_market_plan_token='a' * 64, expected_query_sha256='b' * 64)
    with pytest.raises(ValueError):
        project_pending_backtest_v3_prefix(journal, expected_config=flat, **kwargs)
    with pytest.raises(ValueError):
        project_pending_backtest_v3_prefix(journal, **kwargs)
    unit, = project_pending_backtest_v3_prefix(journal, expected_config=flat,
        drawdown_authority=authority, **kwargs)
    metrics = [row for name, row in unit.trade_proposal_rows if name == TABLE_BY_KIND['metrics'].name]
    assert len(metrics) == 2 and all(Decimal(row['drawdown']) == Decimal('1E-12') for row in metrics)
    with pytest.raises(ValueError):
        project_pending_backtest_v3_prefix(journal, expected_config={**flat, 'strategy_revision': 2},
            drawdown_authority=authority, **kwargs)


def test_actual_fresh_v4_public_bootstrap_binds_before_context_write(unregistered_installed_fixture, monkeypatch):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap, backtest_fixed_market_authority
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    revision, flat, _ = unregistered_installed_fixture
    run_id = '00000000-0000-0000-0000-000000000a01'
    keeper = object()
    context_client = SimpleNamespace(typed_insert_strict=True, typed_insert_dispatch=TypedInsertDispatch(keeper))
    writer_client = SimpleNamespace(typed_insert_strict=True, typed_insert_dispatch=TypedInsertDispatch(keeper))
    read, terminal = object(), object()
    market = SimpleNamespace(token='a' * 64)
    run = dict(run_id=run_id, run_month='2026-08-01', mode='backtest', evaluation_interval_ms=100,
        session_date='2026-08-24', configuration_hash=revision['content_hash'], code_hash='d' * 64,
        market_plan_token=market.token, started_at=AT.isoformat())
    config = dict(flat, anchor_date='2026-08-24', run_plan_id='', safety_supervisor_enabled=True,
        checkpoint_interval_events=100, write_progress_checkpoints=False)
    published = dict(run, **config, account_ids=('DU1',))
    calls = []
    monkeypatch.setattr(backtest_fixed_market_authority, '_validate_plans', lambda *_: None)
    monkeypatch.setattr(bootstrap, 'is_numbered_fixed_strategy', lambda sid, rev: (sid, rev) == (flat['strategy_id'], flat['strategy_revision']))
    for name in ('fixed_backtest_v2_preflight', '_v4_cold_reader_preflight', '_v4_preflight'):
        monkeypatch.setattr(bootstrap, name, lambda *_: None)
    monkeypatch.setattr(bootstrap, 'publish_fixed_run_context', lambda *args, **kwargs: calls.append('context-write') or published)
    monkeypatch.setattr(bootstrap, 'load_typed_run_context', lambda *_: published)
    def writer_factory(client, **kw):
        calls.append('writer')
        return SimpleNamespace(run_id=kw['run_id'], run_mode='backtest', journal_profile='backtest_v4',
            coalesce_batches=False, max_events_per_commit=kw['max_events_per_commit'], close=lambda: None)
    kwargs = dict(run=run, config=config, account_ids=('DU1',), attempt_id=ATTEMPT,
        expected_config=config, fixed_market_parent_plan=market, fixed_market_execution_plan=market,
        expected_market_start=AT, projection_certifier=lambda: 'b' * 64,
        writer_factory=writer_factory, configuration_revision=revision)
    assembly = bootstrap.publish_and_assemble_fixed_v4_journal(context_client, read, writer_client, terminal, **kwargs)
    assert assembly.publisher.drawdown_authority.policy == POLICY
    assert calls == ['context-write', 'writer']
    assembly.journal.close()
    calls.clear()
    with pytest.raises(ValueError):
        bootstrap.publish_and_assemble_fixed_v4_journal(context_client, read, writer_client, terminal,
            **{**kwargs, 'config': {**config, 'strategy_revision': 2}})
    assert calls == []
    with pytest.raises(ValueError):
        bootstrap.publish_and_assemble_fixed_v4_journal(context_client, read, writer_client, terminal,
            **{**kwargs, 'configuration_revision': None})
    assert calls == []


def test_actual_resumed_v4_public_bootstrap_retains_exact_policy_identity(unregistered_installed_fixture, monkeypatch):
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap, backtest_fixed_running_anchor, backtest_v4_running_recovery
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.strategy_one_campaign_snapshot import project_campaign_snapshot
    from src.trading_runtime import arte_journal_reader
    from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
    revision, flat, _ = unregistered_installed_fixture
    run_id = '00000000-0000-0000-0000-000000000a01'
    prior = '00000000-0000-0000-0000-000000000a03'
    token = bootstrap.FixedV4JournalPreflightToken(run_id, ('DU1',), date(2026, 8, 1),
        revision['content_hash'], 'a' * 64, 'b' * 64)
    market = CertifiedMarketDayPlan(ExecutionInterval.parse('100ms'), 'build', 'definition',
        ('2026-08-24',), ('AAA',), (), (100,), token.market_plan_token)
    lease = BacktestV4KeeperLease(SimpleNamespace(is_current=lambda *a, **kw: True), run_id, 'owner', 1)
    writer_client = SimpleNamespace(backtest_v4_lease=lease, typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(object()))
    anchor = SimpleNamespace(journal_sequence=7, batch_id=prior, source_cursor='2026-08-24:100')
    monkeypatch.setattr(backtest_fixed_running_anchor, 'cold_verify_v4_resume_anchor', lambda *a, **kw: anchor)
    monkeypatch.setattr(backtest_v4_running_recovery, 'verify_v4_recovery_at_anchor', lambda *a: None)
    monkeypatch.setattr(arte_journal_reader, 'load_complete_typed_protection_history',
        lambda *a: CompleteProtectionHistory(run_id, 7, (prior,), ()))
    context = dict(flat, mode='backtest', account_ids=('DU1',), configuration_hash=token.configuration_hash,
        market_plan_token=token.market_plan_token, code_hash='d' * 64)
    monkeypatch.setattr(bootstrap, 'load_typed_run_context', lambda *a: context)
    monkeypatch.setattr(bootstrap, '_v4_preflight', lambda *a: None)
    calls = []
    def writer_factory(client, **kw):
        calls.append('writer')
        return SimpleNamespace(run_id=kw['run_id'], run_mode='backtest', journal_profile='backtest_v4',
            coalesce_batches=False, max_events_per_commit=kw['max_events_per_commit'], close=lambda: None)
    campaign = project_campaign_snapshot(run_id=run_id, session_date=date(2026, 8, 24),
        checkpoint_sequence=7, boundary_ms=100, journal_batch_id=prior, ownership=())
    kwargs = dict(attempt_id=ATTEMPT, expected_config=flat, fixed_market_parent_plan=market,
        fixed_market_execution_plan=market, expected_market_start=AT, code_hash='d' * 64,
        recovery_evidence=SimpleNamespace(campaign=campaign, oms=(), prefix=object()),
        writer_factory=writer_factory, configuration_revision=revision)
    assembly, recovered_anchor = bootstrap.assemble_resumed_fixed_v4_journal(object(), writer_client, object(), token, **kwargs)
    assert recovered_anchor is anchor
    assert assembly.publisher.drawdown_authority.policy == POLICY
    assert assembly.publisher.fenced_sequence == 7
    assembly.journal.close()
    calls.clear()
    context['strategy_revision'] = 2
    with pytest.raises(ValueError):
        bootstrap.assemble_resumed_fixed_v4_journal(object(), writer_client, object(), token, **kwargs)
    assert calls == []


def test_actual_runtime_constructor_selects_only_verified_future_fixture(unregistered_installed_fixture):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
    from src.trading_runtime.control_plane import TradingControlPlane
    revision, flat, _ = unregistered_installed_fixture
    config = RunConfig(mode=RunMode.BACKTEST, strategy_id=flat['strategy_id'],
        strategy_revision=flat['strategy_revision'], account_ids=('DU1',),
        anchor_date=date(2026, 8, 24), run_id=RUN)
    strategy = SimpleNamespace(strategy_id=flat['strategy_id'], revision=flat['strategy_revision'], automatic=True)
    journal = BacktestMemoryJournal(run_id=RUN)
    runtime = TradingRuntime(config, SimpleNamespace(), strategy, journal,
        control_plane=TradingControlPlane(), strategy_configuration=revision['payload']['strategy'])
    assert runtime.portfolio.drawdown_measure_policy == POLICY
    assert runtime.risk_supervisor.portfolio is runtime.portfolio
    with pytest.raises(ValueError):
        TradingRuntime(config, SimpleNamespace(), strategy, BacktestMemoryJournal(run_id=RUN),
            control_plane=TradingControlPlane())
    journal.close()


def test_resumed_v4_malformed_token_rejected_before_binding_or_writer(monkeypatch):
    from unittest.mock import Mock
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from src.trading_runtime import drawdown_measure_authority
    bind = Mock(side_effect=AssertionError('Malformed token must not reach authority binding'))
    writer = Mock(side_effect=AssertionError('Malformed token must not create writer'))
    monkeypatch.setattr(drawdown_measure_authority, 'bind_run_drawdown_authority', bind)
    with pytest.raises(ValueError, match='V4 cold journal lacks exact owner, plan, or bounds'):
        bootstrap.assemble_resumed_fixed_v4_journal(object(), object(), object(), object(),
            attempt_id=ATTEMPT, expected_config={}, fixed_market_parent_plan=object(),
            fixed_market_execution_plan=object(), expected_market_start=AT,
            code_hash='d' * 64, recovery_evidence=object(), writer_factory=writer)
    bind.assert_not_called()
    writer.assert_not_called()
