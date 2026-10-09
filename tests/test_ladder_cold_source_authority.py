"""Cold declared-source and real three-lot OMS recovery contracts."""
import asyncio
from dataclasses import asdict, replace
from datetime import timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.backend.backtest_ladder_source_authority import (
    DeclaredLadderSourceAuthority, declared_ladder_policy, declared_source_end,
    declared_population_exclusions,
)
from src.trading_runtime.arte_intent_projection import RecoveredIntent, strategy_intent_batch
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
from src.trading_runtime.arte_oms_projection import (
    RecoveredOmsGroupState, reconstruct_strategy_one_oms_lineage,
    load_recovered_strategy_one_oms_lineage,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.squeeze_ladder_automatic import submit_automatic_ladder
from tests.test_squeeze_ladder_automatic import source, runtime_fixture, RUN, TICKER


class Reader:
    base_url = 'http://cold-test.invalid'
    user = 'readonly-fixture'
    password = 'test-fixture-only'
    def execute(self, query):
        if query == "SELECT getSetting('readonly')":
            return '1'
        assert query.startswith('SELECT count() AS count FROM arte.trading_strategy_assignment_command_v1 ')
        return '{"count":"0"}'


def declared(context):
    payload = context.configuration.payload.copy()
    strategy = dict(payload['strategy'])
    release = dict(strategy['numbered_release'])
    release['automatic_market_policy'] = {'gate': asdict(context.gate_policy),
        'tick_int':100, 'stop_buffer_ticks':1, 'break_buffer_ticks':1,
        'source_through_boundary_rule':'extended_session_end',
        'source_through_boundary_ms_by_session':{'premarket':19800000, 'afterhours':57600000}}
    strategy['numbered_release'] = release
    payload['strategy'] = strategy
    return replace(context.configuration, payload=payload,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest())


def authority(context, *, reader=None, max_parents=4096):
    config = declared(context)
    native = {'run_id':RUN, 'mode':'backtest', 'configuration_hash':config.payload_hash,
        'strategy_revision':config.strategy_number,
        'strategy_id':config.payload['strategy']['strategy_id'], 'evaluation_interval_ms':100,
        'market_plan_token':context.market.token, 'session_date':context.session_date.isoformat(),
        'account_ids':('DU1',)}
    saved = {'definition':{'final_session_date':context.session_date.isoformat(),
        'start_local_ms':14400000, 'end_local_ms':34200000, 'ticker_population_mode':'explicit'},
        'tickers':({'ticker':TICKER},)}
    return DeclaredLadderSourceAuthority(reader or Reader(), RUN, config, native,
        saved, context.market, max_parents=max_parents)


def test_source_end_binds_fenced_full_session_and_exact_declared_keysets():
    context, *_ = source()
    config = declared(context)
    market = config.payload['strategy']['numbered_release']['automatic_market_policy']
    assert declared_ladder_policy(config) is not None
    assert declared_source_end(market, {'start_local_ms':14400000, 'end_local_ms':34200000}) == 19800000
    assert declared_source_end(market, {'start_local_ms':57600000, 'end_local_ms':72000000}) == 57600000
    for changed in ({**market, 'source_through_boundary_ms':19800000},
                    {**market, 'source_through_boundary_ms_by_session':{'premarket':19800000}}):
        with pytest.raises(ValueError, match='exact declared'):
            declared_source_end(changed, {'start_local_ms':14400000, 'end_local_ms':34200000})
    with pytest.raises(ValueError, match='fenced full'):
        declared_source_end(market, {'start_local_ms':14400000, 'end_local_ms':18000000})
    unknown = declared(context)
    unknown.payload['strategy']['numbered_release']['automatic_entry_policy'] = {}
    with pytest.raises(ValueError, match='Unknown declared'):
        declared_ladder_policy(unknown)


def test_context_scope_and_batch_bounds_fail_before_source_reads():
    context, *_ = source()
    cold = authority(context)
    with pytest.raises(ValueError, match='fenced saved membership'):
        cold.context_for('FOREIGN')
    for scopes in ((), ('ZZZ', TICKER), (TICKER, TICKER), tuple(str(i) for i in range(9))):
        with pytest.raises(ValueError, match='one to eight'):
            cold.contexts_for(scopes)


def test_context_rejects_unqualified_prior_seed_before_v7_or_pivot_reads(monkeypatch):
    from src.backend import structural_v7_seed, backtest_declared_ladder_seed
    from src.backend import backtest_strategy_one_v7_interval_store
    context, *_ = source()
    cold = authority(context)
    cold._scan = object()
    seeds = object()
    monkeypatch.setattr(structural_v7_seed, 'certified_seed_plan', lambda market, client: seeds)
    def reject(market, loaded):
        assert market is cold.market and loaded is seeds
        raise ValueError('Declared ladder seed is not the exact prior NYSE session')
    monkeypatch.setattr(backtest_declared_ladder_seed, 'verify_declared_ladder_seed_plan', reject)
    monkeypatch.setattr(backtest_strategy_one_v7_interval_store, 'certify_v7_interval_plan',
        lambda *args, **kwargs: pytest.fail('V7 read preceded declared seed certification'))
    with pytest.raises(ValueError, match='exact prior NYSE session'):
        cold.context_for(TICKER)
    assert not cold._contexts


def test_real_financial_ladder_recovers_all_nine_orders_from_proved_parent(monkeypatch):
    async def run():
        runtime, context, policy, assignment, decision, event = await runtime_fixture()
        try:
            await submit_automatic_ladder(runtime, decision, assignment=assignment,
                market_context=context, policy=policy)
            group, = runtime.order_manager._groups.values()
            intent = replace(group.intent, metadata={}, quantity=0.)
            # The actual source proposal remains zero quantity until Portfolio sizes it.
            parent = strategy_intent_batch(intent, run_id=RUN,
                run_month=context.session_date.replace(day=1), account_id='DU1',
                attempt_id=RUN, batch_id=str(uuid4()), prior_batch_id=str(uuid4()),
                sequence=2, source_cursor='boundary', run_status='running', recorded_at=event.ts)
            previous = V4CommittedPrefix(RUN, 1, parent.prior_batch_id, 'boundary', 'running', (parent.prior_batch_id,))
            cold = authority(context)
            cold.record_verified_parent(previous, parent, intent)
            group_batch = str(uuid4())
            prefix = V4CommittedPrefix(RUN, 3, group_batch, 'boundary', 'running',
                (*previous.batch_ids, parent.batch_id, group_batch))
            recovered = RecoveredIntent(2, 'DU1', parent.events[0]['record_id'], parent.batch_id, intent)
            proved = cold.source_parent(recovered, prefix)
            orders = tuple(replace(order, raw={}, strategyParameters={}) for order in group.orders)
            state = RecoveredOmsGroupState(3, recovered.record_id,
                {'run_id':RUN, 'batch_id':group_batch, 'account_id':'DU1', 'group_id':group.group_id,
                 'strategy_id':cold.native['strategy_id'], 'strategy_revision':1,
                 'strategy_intent_id':intent.intent_id,
                 # Recovery consumes ClickHouse's UTC DateTime64 text.
                 'updated_at':group.updated_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')},
                orders, tuple(0 for _ in orders),
                tuple(group.plan.order_slice_ids), (), (), ())
            history = CompleteProtectionHistory(RUN, 3, prefix.batch_ids, ())
            meta = group.intent.metadata
            reservation = {'account_id':'DU1', 'intent_id':intent.intent_id,
                'decision_id':meta['portfolio_decision_id'], 'reservation_id':meta['portfolio_reservation_id'],
                'account_key':meta['portfolio_account_key'], 'assignment_id':assignment.assignment_id,
                'quantity':str(group.intent.quantity)}
            admission = {'decision_id':reservation['decision_id'], 'reservation_id':reservation['reservation_id'],
                'account_key':reservation['account_key'], 'status':'approved',
                'policy_id':'default', 'policy_revision':1, 'requested_quantity':str(meta['requested_quantity'])}
            rebuilt = reconstruct_strategy_one_oms_lineage(state, proved, history,
                admission_reservation=reservation, admission_decision=admission,
                automatic_ladder_sources=cold)
            assert len(rebuilt) == 9
            assert sum(order.side == 'BUY' for order in rebuilt) == 3
            assert sorted(order.price for order in rebuilt if order.side == 'SELL' and order.orderType == 'LMT') == [10.99,11.99,12.99]
            assert {order.raw['canonical_strategy_id'] for order in rebuilt} == {cold.native['strategy_id']}
            assert {order.raw['canonical_metadata']['portfolio_reservation_id'] for order in rebuilt} == {reservation['reservation_id']}
            lifecycle = tuple(row for row in runtime.journal._records
                if row.category == 'protection' and row.entity_type == 'protection_change')
            assert lifecycle
            actual = replace(lifecycle[0], sequence=3)
            assert reconstruct_strategy_one_oms_lineage(state, proved, replace(history, records=(actual,)),
                admission_reservation=reservation, admission_decision=admission,
                automatic_ladder_sources=cold) == rebuilt
            with pytest.raises(ValueError, match='protection interventions'):
                reconstruct_strategy_one_oms_lineage(state, proved,
                    replace(history, records=(replace(actual, payload={**actual.payload,
                        'action':'replace_protective_stop'}),)),
                    admission_reservation=reservation, admission_decision=admission,
                    automatic_ladder_sources=cold)
            changed_orders = tuple(replace(order, auxPrice=9.0) if order.orderType == 'STP' else order
                                   for order in state.orders)
            with pytest.raises(ValueError, match='frozen confirmed swing low'):
                reconstruct_strategy_one_oms_lineage(replace(state, orders=changed_orders), proved, history,
                    admission_reservation=reservation, admission_decision=admission,
                    automatic_ladder_sources=cold)
            from src.trading_runtime import arte_oms_projection as oms, arte_intent_projection as intents
            monkeypatch.setattr(oms, 'load_latest_committed_oms_groups', lambda *a, **k: (state,))
            monkeypatch.setattr(oms, 'load_committed_oms_admission_page', lambda *a, **k: {3:reservation})
            monkeypatch.setattr(oms, 'load_committed_oms_decision_page', lambda *a, **k: {3:admission})
            def page(*a, **kwargs):
                assert kwargs['include_source_batch'] is False
                return (recovered,)
            monkeypatch.setattr(intents, 'load_committed_strategy_intent_page', page)
            result = load_recovered_strategy_one_oms_lineage(cold.client, prefix,
                allowed_accounts=frozenset({'DU1'}), protection_history=history,
                automatic_ladder_sources=cold)
            assert result[0].orders == rebuilt
            assert result[0].approved_intent.quantity == group.intent.quantity
            with pytest.raises(ValueError, match='proved committed prefix'):
                cold.source_parent(replace(recovered, account_id='FOREIGN'), prefix)
            with pytest.raises(ValueError, match='exact proved source parent'):
                reconstruct_strategy_one_oms_lineage(replace(state, group={**state.group,'strategy_revision':49}),
                    proved, history, admission_reservation=reservation, admission_decision=admission,
                    automatic_ladder_sources=cold)
            with pytest.raises(ValueError, match='not been cold certified'):
                cold.source_parent(replace(recovered, record_id=str(uuid4())), prefix)
        finally:
            await runtime.order_manager.close()
            runtime.journal.close()
    asyncio.run(run())


def test_full_population_uses_certified_market_membership_only():
    context, *_ = source()
    cold = authority(context)
    saved = {'definition':{'final_session_date':context.session_date.isoformat(),
        'start_local_ms':14400000, 'end_local_ms':34200000,
        'ticker_population_mode':'market_plan'}, 'tickers':()}
    full = DeclaredLadderSourceAuthority(cold.client, RUN, cold.configuration,
        cold.native, saved, cold.market)
    assert full.tickers == frozenset(context.market.tickers)
    for changed in ({**saved, 'tickers':({'ticker':TICKER},)},
                    {**saved, 'definition':{**saved['definition'], 'ticker_population_mode':'explicit'}}):
        with pytest.raises(ValueError, match='fenced selection mode'):
            DeclaredLadderSourceAuthority(cold.client, RUN, cold.configuration,
                cold.native, changed, cold.market)


def test_declared_population_exclusions_are_exact_and_context_bound():
    context, *_ = source()
    config = declared(context)
    context = replace(context, source_through_boundary_ms=19800000,
        observations=replace(context.observations,
            gate=replace(context.observations.gate, certified_history_through_ms=19800000)))
    policy = config.payload['strategy']['numbered_release']['automatic_market_policy']
    assert declared_population_exclusions(policy) == ()
    assert 'population_exclusions' not in replace(context, configuration=config).market_policy_payload()
    for invalid in (('LGHL',), ['lghl'], [' LGHL'], ['LGHL', 'LGHL'],
                    ['ZZZ', 'LGHL'], ['A'] * 101, [None]):
        with pytest.raises(ValueError, match='population exclusions'):
            declared_source_end({**policy, 'population_exclusions':invalid},
                {'start_local_ms':14400000, 'end_local_ms':34200000})
    policy['population_exclusions'] = ['LGHL']
    config = replace(config, payload_hash=sha256(canonical_json(config.payload).encode()).hexdigest())
    selected = replace(context, configuration=config)
    assert selected.market_policy_payload()['population_exclusions'] == ['LGHL']
    selected.verify_policy(declared_ladder_policy(config))
    excluded_market = replace(context.market, tickers=tuple(sorted((*context.market.tickers, 'LGHL'))))
    with pytest.raises(ValueError, match='declared population exclusion'):
        replace(selected, market=excluded_market).market_policy_payload()


def test_full_population_accepts_6086_without_truncation_and_rejects_over_8192():
    context, *_ = source()
    cold = authority(context)
    saved = {'definition':{'final_session_date':context.session_date.isoformat(),
        'start_local_ms':14400000, 'end_local_ms':34200000,
        'ticker_population_mode':'market_plan'}, 'tickers':()}
    symbols = tuple(f'T{index:04d}' for index in range(6086))
    market = replace(cold.market, tickers=symbols)
    full = DeclaredLadderSourceAuthority(cold.client, RUN, cold.configuration,
        cold.native, saved, market)
    assert full.tickers == frozenset(symbols) and len(full.tickers) == 6086
    assert (full.max_contexts, full.max_context_bytes, full.max_parents) == (8, 512*1024*1024, 4096)
    with pytest.raises(ValueError, match='exact bounded scope'):
        DeclaredLadderSourceAuthority(cold.client, RUN, cold.configuration, cold.native, saved,
            replace(market, tickers=tuple(f'T{index:04d}' for index in range(8193))))


def test_cold_population_cannot_locally_remove_excluded_certified_members():
    context, *_ = source()
    cold = authority(context)
    policy = cold.configuration.payload['strategy']['numbered_release']['automatic_market_policy']
    policy['population_exclusions'] = ['LGHL']
    config = replace(cold.configuration,
        payload_hash=sha256(canonical_json(cold.configuration.payload).encode()).hexdigest())
    native = {**cold.native, 'configuration_hash':config.payload_hash}
    saved = {'definition':{'final_session_date':context.session_date.isoformat(),
        'start_local_ms':14400000, 'end_local_ms':34200000,
        'ticker_population_mode':'explicit'}, 'tickers':({'ticker':TICKER},)}
    with pytest.raises(ValueError, match='declared population exclusion'):
        DeclaredLadderSourceAuthority(cold.client, RUN, config, native, saved,
            replace(cold.market, tickers=tuple(sorted((*cold.market.tickers, 'LGHL')))))


def test_from_run_reuses_configuration_selected_market_and_exact_native_token(monkeypatch):
    from src.trading_runtime import arte_journal_writer, arte_backtest_definition
    from src.backend import backtest_strategy_one_configuration, backtest_market_data
    context, *_ = source()
    cold = authority(context)
    config = cold.configuration
    config.payload['strategy']['numbered_release']['automatic_market_policy']['population_exclusions'] = ['LGHL']
    config = replace(config, payload_hash=sha256(canonical_json(config.payload).encode()).hexdigest())
    native = {**cold.native, 'configuration_hash':config.payload_hash}
    saved = {'definition':{'final_session_date':context.session_date.isoformat(),
        'configuration_revision_id':config.revision()['revision_id'],
        'start_local_ms':14400000, 'end_local_ms':34200000,
        'ticker_population_mode':'market_plan'}, 'tickers':()}
    monkeypatch.setattr(arte_journal_writer, 'load_typed_run_context', lambda client, run_id:native)
    monkeypatch.setattr(backtest_strategy_one_configuration, 'certify_numbered_configuration',
        lambda client, revision:config)
    monkeypatch.setattr(arte_backtest_definition, 'load_backtest_definition',
        lambda client, run_id, **kwargs:saved)
    def selected(**kwargs):
        assert kwargs['configuration'] is config.payload
        assert kwargs['sessions'] == (context.session_date.isoformat(),)
        assert 'LGHL' not in kwargs['tickers']
        return cold.market
    monkeypatch.setattr(backtest_market_data, 'certified_market_plan_from_arte', selected)
    loaded = DeclaredLadderSourceAuthority.from_run(cold.client, RUN)
    assert loaded.tickers == frozenset(cold.market.tickers)
    assert loaded.market.token == native['market_plan_token']
    native['market_plan_token'] = 'different-token'
    with pytest.raises(ValueError, match='sealed native run authority'):
        DeclaredLadderSourceAuthority.from_run(cold.client, RUN)


def test_terminal_snapshot_cold_verification_keeps_complete_source_scope(monkeypatch):
    from src.trading_runtime import arte_backtest_snapshot_anchor as anchor
    context, *_ = source()
    cold = authority(context)
    batch = str(uuid4())
    prefix = V4CommittedPrefix(RUN, 10, batch, 'terminal', 'completed', (batch,))
    calls = []
    def verified(client, run_id, **kwargs):
        assert client is cold.client and run_id == RUN
        calls.append(kwargs)
        return prefix
    monkeypatch.setattr(anchor, 'load_verified_v4_prefix', verified)
    monkeypatch.setattr(anchor, 'load_typed_run_context', lambda *a: cold.native)
    assert anchor._verify_current_prefix(cold.client, prefix,
        automatic_ladder_sources=cold) is cold.native
    assert calls == [{'automatic_ladder_sources':cold}]


def test_saved_review_dispatches_by_declared_policy_with_complete_cold_scope(monkeypatch):
    from src.backend import backtest_v4_saved_review as review
    from src.backend import backtest_market_data as markets
    from src.backend import backtest_strategy_one_configuration as configurations
    from src.backend.typed_backtest_review_core import AuditedSessionCache
    context, *_ = source()
    cold = authority(context)
    native = {**cold.native, 'strategy_revision':49}
    reader = SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(review, 'load_typed_run_context', lambda *a:native)
    monkeypatch.setattr(review, 'is_numbered_fixed_strategy', lambda *a:True)
    # This test owns cold-source dispatch, not installed release/read-principal
    # authorization. The synthetic source identity has no installed profile.
    monkeypatch.setattr(review, '_require_declared_read_profile', lambda *a,**k:{})
    monkeypatch.setattr(markets, 'readonly_clickhouse_client', lambda **k:reader)
    monkeypatch.setattr(configurations, 'certify_numbered_configuration', lambda *a:cold.configuration)
    monkeypatch.setattr(DeclaredLadderSourceAuthority, 'from_run', lambda *a:cold)
    calls = []
    def verified(client, run, **kwargs):
        assert run == RUN
        calls.append(kwargs)
        return None
    monkeypatch.setattr(review, 'load_verified_v4_prefix', verified)
    monkeypatch.setattr(review, '_saved_twenty_price_source', lambda *a:pytest.fail('Inherited entry source selected for ladder'))
    with pytest.raises(ValueError, match='cold-verified terminal'):
        review._terminal_attestation(cold.client, RUN, AuditedSessionCache())
    assert calls == [{'automatic_ladder_sources':cold}]



@pytest.mark.parametrize('mutation', [None, 'quantity', 'quote', 'checkpoint', 'pending_exit'])
def test_session_exit_uses_exact_native_bits_quote_and_predecessor(monkeypatch, mutation):
    """Real bit-sealed broker image; native/source reader boundaries are mocked."""
    import pyarrow as pa
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.numbered_session_exit import numbered_session_exit_intent
    from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
    from src.trading_runtime import arte_journal_projection as cursors, arte_portfolio_snapshot as portfolios
    from src.trading_runtime import strategy_one_broker_match_snapshot as brokers, arte_oms_projection as oms
    context, *_ = source()
    config = declared(context)
    config.payload['strategy']['strategy_number'] = 49
    config = replace(config, payload_hash=sha256(canonical_json(config.payload).encode()).hexdigest())
    cold = authority(replace(context, configuration=config))
    boundary = 19740000
    at = market_day_boundary(context.session_date, boundary)
    previous, identity = str(uuid4()), str(uuid4())
    prefix = V4CommittedPrefix(RUN, 10, previous, 'cursor', 'running', (previous,))
    intent = numbered_session_exit_intent(session_date=context.session_date, account_id='DU1',
        assignment_id=f'strategy-49:DU1:{TICKER}', ticker=TICKER, boundary_ms=boundary,
        quantity=91. if mutation == 'quantity' else 90., bid=10.22, strategy_number=49)
    batch = strategy_intent_batch(intent, run_id=RUN, run_month=context.session_date.replace(day=1),
        account_id='DU1', attempt_id=RUN, batch_id=identity, prior_batch_id=previous,
        sequence=11, source_cursor='cursor', run_status='running', recorded_at=at)
    state = dict(schema_version=4, bar_mode=True, initial_time=market_day_boundary(context.session_date, 0),
        account_ids=('DU1',), cash={'DU1':9000.}, realized_pnl={'DU1':0.},
        positions={'DU1':[dict(conid=123,ticker=TICKER,quantity=90.,avg_cost=10.23,realized_pnl=0.)]},
        orders=(),next_order_id=1,next_execution_id=1,
        performance_extrema=dict(complete=False,as_of=None,unrealized=0.,market_value=0.,
            peak_unrealized=0.,worst_unrealized=0.,equity_peak=0.,maximum_drawdown=0.))
    image = project_broker_match_snapshot(run_id=RUN,session_date=context.session_date,
        checkpoint_sequence=9 if mutation == 'checkpoint' else 10,boundary_ms=boundary,state=state)
    table = pa.Table.from_pylist([{'ticker':TICKER,'boundary_ms':boundary,'quote_valid':1,
        'quote_timestamp_us':int(at.timestamp()*1000000),
        'bid_int':102100 if mutation == 'quote' else 102200,'ask_int':102300}])
    cold.context_for = lambda ticker:SimpleNamespace(observations=SimpleNamespace(completed_source=table))
    monkeypatch.setattr(cursors,'load_latest_backtest_cursor',lambda *a:{
        'event_sequence':10,'batch_id':previous,'session_date':context.session_date.isoformat(),'boundary_ms':boundary})
    monkeypatch.setattr(portfolios,'load_portfolio_snapshot',lambda *a,**k:{
        'state_revision':10,'snapshot_at':at.isoformat(),'state':{'pending_entry_requests':{}}})
    monkeypatch.setattr(brokers,'load_unattested_broker_match_snapshot',lambda *a,**k:image)
    owned = SimpleNamespace(state=SimpleNamespace(group={'account_id':'DU1',
        'state':'working' if mutation == 'pending_exit' else 'filled','remaining_quantity':'0',
        'filled_quantity':'90'}), orders=(SimpleNamespace(conid=123),),
        approved_intent=SimpleNamespace(ticker=TICKER,action='exit' if mutation == 'pending_exit' else 'enter_long'))
    monkeypatch.setattr(oms,'load_recovered_strategy_one_oms_lineage',lambda *a,**k:(owned,))
    rows = {'trading_event_v1':batch.events,'trading_strategy_intent_v1':batch.intents}
    metadata = dict(run_month=batch.run_month.isoformat(),attempt_id=batch.attempt_id,
        batch_id=batch.batch_id,prior_batch_id=batch.prior_batch_id,first_sequence=11,last_sequence=11,
        status='running',source_cursor=batch.source_cursor)
    if mutation is None:
        assert cold.verify_session_exit_families(rows,verified_prior_prefix=prefix,
            batch_metadata=metadata,stored_utc=False) == intent
        recovered = RecoveredIntent(11,'DU1',batch.events[0]['record_id'],identity,intent)
        head = V4CommittedPrefix(RUN,11,identity,'cursor','running',(previous,identity))
        assert cold.source_parent(recovered,head).source_batch.intents == batch.intents
        assert cold.verify_session_exit_families(rows,verified_prior_prefix=prefix,
            batch_metadata=metadata,stored_utc=False) == intent
    else:
        with pytest.raises(ValueError):
            cold.verify_session_exit_families(rows,verified_prior_prefix=prefix,
                batch_metadata=metadata,stored_utc=False)
        assert cold._parents == {}

def test_writer_snapshot_uses_dedicated_full_cold_source_not_warm_head(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commit
    context, *_ = source()
    cold = authority(context)
    writer = SimpleNamespace(automatic_ladder_profile=True,
        automatic_ladder_read_client=cold.client, backtest_v4_lease=object())
    calls = []
    monkeypatch.setattr(DeclaredLadderSourceAuthority, 'from_native', lambda reader, run:
        cold if reader is cold.client and run == RUN else pytest.fail('Wrong dedicated source reader'))
    def verified(reader, run, **kwargs):
        assert reader is cold.client and run == RUN
        calls.append(kwargs)
        return None
    monkeypatch.setattr(commit,'load_verified_v4_prefix',verified)
    assert commit.load_writer_v4_snapshot_prefix(writer,RUN) is None
    assert calls == [{'max_commits':100000,'automatic_ladder_sources':cold}]
    writer.automatic_ladder_read_client = writer
    with pytest.raises(ValueError,match='dedicated SELECT-only'):
        commit.load_writer_v4_snapshot_prefix(writer,RUN)


def test_opt_in_base_writer_retry_verifies_original_predecessor_before_inserts(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commit
    from src.trading_runtime.arte_journal_writer import TypedJournalBatch
    context, *_ = source()
    config = declared(context)
    config.payload['strategy']['strategy_number'] = 49
    config = replace(config,payload_hash=sha256(canonical_json(config.payload).encode()).hexdigest())
    cold = authority(replace(context,configuration=config))
    previous, identity = str(uuid4()), str(uuid4())
    prior = V4CommittedPrefix(RUN,1,previous,'cursor','running',(previous,))
    head = V4CommittedPrefix(RUN,2,identity,'cursor','running',(previous,identity))
    batch = TypedJournalBatch(RUN,context.session_date.replace(day=1),RUN,identity,previous,
        2,2,'cursor','running',())
    writer = SimpleNamespace(automatic_ladder_profile=True,
        automatic_ladder_read_client=cold.client,typed_insert_dispatch=object())
    calls = []
    monkeypatch.setattr(DeclaredLadderSourceAuthority,'from_native',lambda *a:cold)
    def verified(reader,run,**kwargs):
        assert reader is cold.client and kwargs == {'automatic_ladder_sources':cold}
        calls.append('full-prefix')
        return head
    def predecessor(reader,prefix,selected):
        assert reader is cold.client and prefix is head and selected == identity
        calls.append('original-predecessor')
        return prior
    monkeypatch.setattr(commit,'load_verified_v4_prefix',verified)
    monkeypatch.setattr(commit,'verified_batch_predecessor',predecessor)
    original = cold.verify_immutable_prefix
    def immutable(prefix):
        assert prefix is prior
        calls.append('immutable-predecessor')
        original(prefix)
    cold.verify_immutable_prefix = immutable
    class BeforeInsert(Exception):pass
    monkeypatch.setattr(commit,'prepare_commit_v4',lambda **k:(_ for _ in ()).throw(BeforeInsert()))
    with pytest.raises(BeforeInsert):
        commit._publish_sealed_batch_v4(writer,batch,
            (('trading_strategy_intent_v1',()),('trading_event_v1',())),())
    assert calls == ['full-prefix','original-predecessor','immutable-predecessor']
