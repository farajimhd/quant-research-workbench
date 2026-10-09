"""Decision-scope cleanup controls; external frontier seam is explicit."""
from types import SimpleNamespace
from weakref import WeakKeyDictionary

import pytest

from src.backend import backtest_fixed_lot_management_reuse as reuse
from src.trading_runtime.fixed_lot_management_reuse_policy import FixedLotManagementReusePolicy


class Request:
    intents = ()


def owner_and_proof(monkeypatch):
    owner = SimpleNamespace(operation=SimpleNamespace(source=object()),
                            _management_reads=WeakKeyDictionary())
    frontier = (object(),)
    monkeypatch.setattr(reuse, 'installed_management_reuse_policy', lambda _: FixedLotManagementReusePolicy())
    monkeypatch.setattr(reuse, '_frontier', lambda *args: frontier)
    monkeypatch.setattr(reuse, '_normalized_context_snapshot', lambda *args: frontier)
    proof = reuse._Decision(owner, frontier)
    proof.normalized_snapshot = (*frontier,None,None,None,())
    monkeypatch.setattr(reuse, "_same_normalized_snapshot", lambda old, new: old[0] == new[0])
    reuse._DECISIONS.add(proof)  # Component issuer seam; no installed authority claim.
    request = Request()
    owner._management_reads[request] = proof
    return owner, proof, request


def test_unissued_decision_constructor_cannot_grant_reads(monkeypatch):
    monkeypatch.setattr(reuse, '_frontier', lambda _: ())
    with pytest.raises(ValueError, match='not issued'):
        reuse._Decision(object(), ()).require()


def test_original_authority_loader_can_verify_historical_roster_without_owner_cache(monkeypatch):
    owner,proof,_=owner_and_proof(monkeypatch)
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    owner.client=object();proof.prefix=V4CommittedPrefix('run',3,'batch','cursor','running',('batch',))
    proof.prefix_content=reuse._prefix_binding(proof.prefix)
    historical_prefix=V4CommittedPrefix('run',2,'prior','cursor','running',('prior',))
    entry=SimpleNamespace(source=owner.operation.source)
    frontier=object();checked=[]
    monkeypatch.setattr(reuse,'_context_frontier',lambda actual:frontier)
    token=reuse._ACTIVE.set(proof)
    def original():
        assert reuse._CONTEXT_OWNER.get()==(owner,frontier)
        return reuse.roster_read(owner.client,historical_prefix,entry,(),
            lambda:checked.append('complete historical authority') or 'verified roster')
    try:
        assert reuse._owner_read_context(owner,original)=='verified roster'
        assert checked==['complete historical authority'] and not proof.rosters
        assert reuse._ACTIVE.get() is proof
    finally:reuse._ACTIVE.reset(token)


@pytest.mark.parametrize('kind',('authority-error','frontier','normalized-content'))
def test_original_authority_still_rejects_changes_and_restores_scope(monkeypatch,kind):
    owner,proof,_=owner_and_proof(monkeypatch)
    frontier=[object()]
    monkeypatch.setattr(reuse,'_context_frontier',lambda actual:frontier[0])
    token=reuse._ACTIVE.set(proof)
    def original():
        assert reuse._ACTIVE.get() is None
        if kind=='authority-error':raise ValueError('original verifier rejects')
        if kind=='frontier':frontier[0]=object()
        else:monkeypatch.setattr(reuse,'_normalized_context_snapshot',lambda *a:('changed',))
        return 'untrusted'
    try:
        with pytest.raises(ValueError):reuse._owner_read_context(owner,original)
        assert reuse._ACTIVE.get() is proof and reuse._CONTEXT_OWNER.get() is None
        assert not proof.rosters and not proof.groups
    finally:reuse._ACTIVE.reset(token)


def test_direct_decision_roster_still_rejects_foreign_prefix(monkeypatch):
    from dataclasses import replace
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    owner,proof,_=owner_and_proof(monkeypatch)
    owner.client=object();proof.prefix=V4CommittedPrefix('run',3,'batch','cursor','running',('batch',))
    proof.prefix_content=reuse._prefix_binding(proof.prefix)
    entry=SimpleNamespace(source=owner.operation.source)
    token=reuse._ACTIVE.set(proof)
    try:
        with pytest.raises(ValueError,match='crosses issued ownership'):
            reuse.roster_read(owner.client,replace(proof.prefix,last_sequence=2),entry,(),
                lambda:pytest.fail('Foreign direct decision roster reached authority loader'))
    finally:reuse._ACTIVE.reset(token)


def test_admission_failure_removes_stale_owner_proof(monkeypatch):
    owner, _, request = owner_and_proof(monkeypatch)
    monkeypatch.setattr(reuse, '_normalized_context_snapshot', lambda *args: ('advanced',))
    @reuse.management_read_scope
    def verify_request(owner, request):
        raise AssertionError('must reject before body')
    with pytest.raises(ValueError, match='head or context changed'):
        verify_request(owner, request)
    assert request not in owner._management_reads
    assert reuse._ACTIVE.get() is None


def test_cancelled_confirm_clears_issued_proof(monkeypatch):
    import asyncio
    owner, _, request = owner_and_proof(monkeypatch)
    @reuse.management_read_scope
    async def confirm(owner, request):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(confirm(owner, request))
    assert request not in owner._management_reads
    assert reuse._ACTIVE.get() is None


def test_commandful_verify_discards_reads_before_submit(monkeypatch):
    owner, _, request = owner_and_proof(monkeypatch)
    request.intents = (object(),)
    @reuse.management_read_scope
    def verify_request(owner, request):
        assert reuse._ACTIVE.get() is not None
    verify_request(owner, request)
    assert request not in owner._management_reads
    assert reuse._ACTIVE.get() is None


def prepared_native_operation(monkeypatch):
    """Real session/profile issuance; external prepared configuration seam only."""
    from test_fixed_structural_lot_configuration_routing import source_fixture
    import test_fixed_structural_lot_native as base_fixture
    from test_fixed_structural_lot_checkpoint_reader_profile import published
    from src.trading_runtime import fixed_structural_lot_release_v19 as release
    from src.trading_runtime.strategy_one_hundred_one_release import derive_strategy_one_hundred_one_configuration
    from src.trading_runtime.strategy_registry import numbered_strategy
    parent = source_fixture()
    monkeypatch.setattr(base_fixture, 'declarations', lambda: (parent, None, None, numbered_strategy(42)))
    original_derive = release.derive_fixed_structural_lot_release
    def derive(actual_parent, **options):
        # Existing fixture captured this wrapper; restore inherited real99 compiler
        # before the actual101 compiler imports and calls it.
        monkeypatch.setattr(release, 'derive_fixed_structural_lot_release', original_derive)
        return derive_strategy_one_hundred_one_configuration(actual_parent,
            approved_code_commit=options['approved_code_commit'],
            approved_code_fingerprint=options['approved_code_fingerprint'],
            approval_reference=options['approval_reference'])
    monkeypatch.setattr(release, 'derive_fixed_structural_lot_release', derive)
    actual, request, plans, config, client, flat = published(monkeypatch,
        actual_loader=False, exclusive_writer=True, number=101, version=19)
    return actual, request, plans, config, client, flat


def test_actual101_prepared_session_uses_selected_typed_policy(monkeypatch):
    actual, request, plans, config, client, flat = prepared_native_operation(monkeypatch)
    assert reuse.installed_management_reuse_policy(actual.operation.source) == FixedLotManagementReusePolicy()
    assert request.revision == config.strategy_revision == 101
    assert client.backtest_v4_lease.run_id == config.run_id
    assert client.typed_insert_strict is True


def _native_management_graph(monkeypatch, *, disabled=False, recover=False, historical=False):
    """Actual financial/prefix consumers; synthetic transport/source install seam."""
    import asyncio
    from datetime import timedelta
    from decimal import Decimal
    from uuid import uuid4
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
    from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
    from src.trading_runtime.domain import TradingMode, InstrumentContract
    from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioAccountProfile, PortfolioPolicy
    from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime import arte_journal_writer as writer_module
    from src.backend.backtest_market_data import market_day_boundary

    async def exercise(*, disabled):
        if historical:
            from src.trading_runtime.strategy_one_v7_intervals import V7IntervalProjector
            original_observe=V7IntervalProjector.observe
            def observe(projector,**facts):
                if facts['boundary_ms']==41000:
                    original_observe(projector,**{**facts,'boundary_ms':31000})
                return original_observe(projector,**facts)
            monkeypatch.setattr(V7IntervalProjector,'observe',observe)
        actual, request, plans, config, client, flat = prepared_native_operation(monkeypatch)
        source = actual.operation.source
        entry = request.entry.proposal
        at = request.intent.event_time
        if historical:at=market_day_boundary(source.session_date,31000)
        journal = BacktestMemoryJournal(run_id=config.run_id)
        broker = SimulatedBrokerAdapter(config.account_ids, SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST, initial_time=at, fixed_bar_mode=True)
        profile = PortfolioAccountProfile('cash', entry.account_id, 'backtest', 'simulated',
            PortfolioPolicy(allow_outside_rth=True))
        portfolio = PortfolioManagementEngine((profile,), journal=journal, run_id=config.run_id,
            strategy_id=config.strategy_id, strategy_revision=config.strategy_revision, event_clock=lambda: at)
        planner = RuntimeIbkrStrategyOrderPlanner({entry.ticker: InstrumentContract(entry.ticker, 1, entry.ticker, 'STK', 'USD')},
            strategy_id=config.strategy_id, strategy_revision=config.strategy_revision, run_id=config.run_id)
        runtime = TradingRuntime(config, broker, SimpleNamespace(strategy_id=config.strategy_id,
            revision=config.strategy_revision, automatic=True), journal, portfolio=portfolio, intent_planner=planner)
        actual.bind_runtime(runtime)
        monkeypatch.setattr(writer_module, 'storage_preflight', lambda *a, **k: None)
        monkeypatch.setattr(writer_module, 'journal_permission_preflight', lambda *a, **k: None)
        client.fixed_structural_lot_profile = actual.profile
        writer = writer_module.ArteJournalWriter(client, run_id=config.run_id, journal_profile='backtest_v4', coalesce_batches=False)
        publisher = BacktestTypedJournalPublisher(journal, writer, attempt_id=str(uuid4()),
            run_month=source.session_date.replace(day=1), expected_config=flat, fixed_market_parent_plan=plans.market)
        from collections import Counter
        import sys
        counts = Counter()
        reads = Counter()
        for method in ("_load_prefix", "_load_group"):
            original_method = getattr(NativeFixedStructuralLotManagement, method)
            def counted_method(value, *args, _original=original_method, _name=method, **kwargs):
                reads[_name] += 1
                return _original(value, *args, **kwargs)
            monkeypatch.setattr(NativeFixedStructuralLotManagement, method, counted_method)
        original = PreparedFixedStructuralLotSource._request_complete
        def counted(value, proposal):
            counts["/".join(sys._getframe(n).f_code.co_name for n in range(1, 5))] += 1
            return original(value, proposal)
        monkeypatch.setattr(PreparedFixedStructuralLotSource, '_request_complete', counted)
        try:
            await runtime.initialize()
            actual.operation.bind_publisher(publisher)
            publisher.bind_first_price_source(source.price_authority)
            owner = NativeFixedStructuralLotManagement(operation=actual.operation, publisher=publisher, client=client)
            if historical:
                from dataclasses import replace
                from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
                from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
                q=replace(entry,boundary_ms=31000,strategy_number=18,first_price=None,price_source_token=None,
                    momentum=source.price_authority.plan.momentum.lookup(entry.ticker,31000),
                    initial_momentum=source.price_authority.plan.source.parent.selection_witness(entry.ticker,31000))
                q=bind_episode_activity_proposal(source.price_authority,
                    bind_certified_price_break_proposal(source.price_authority.plan,q,strategy_number=36),session_date=source.session_date)
                earlier=source.request(replace(q,strategy_number=entry.strategy_number))
                async def earlier_row(boundary,price):
                    nonlocal at
                    at=market_day_boundary(source.session_date,boundary)
                    us=int((at-timedelta(microseconds=1000)).timestamp()*1000000)
                    local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
                    bucket=(local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
                    await runtime.process_liquidity_boundary([dict(ticker=entry.ticker,resolution_ms=100,bucket_index=bucket,
                        event_count=1,first_event_us=us,last_event_us=us,quote_timestamp_us=us,quote_valid=1,
                        bid_int=int(round(price*10000)),ask_int=int(round((price+.01)*10000)),bid_size=100000.,ask_size=100000.,
                        price_valid=1,close_int=int(round(price*10000)),extremes_valid=1,low_int=int(round(price*10000)),
                        high_int=int(round(price*10000)),execution_volume=100000.,
                        execution_price_levels=({'price_int':int(round(price*10000)),'volume':100000.},))],at=at)
                await earlier_row(31000,10.)
                accepted=await runtime.submit_fixed_structural_lot_request(earlier)
                assert accepted[0]['decision']['status'] in ('approved','resized')
                await earlier_row(31100,10.01)
                earlier_group=next(iter(runtime.order_manager._groups.values()))
                owner.register_entry(earlier,earlier_group.group_id)
                earlier_key=(entry.account_id,entry.assignment_id,entry.ticker)
                await owner.first_held(earlier_key,boundary_ms=31100)
                await earlier_row(31200,entry.initial_stop-.02)
                await earlier_row(31300,entry.initial_stop-.02)
                assert not any(p.position for p in await broker.positions(entry.account_id))
                await owner.retire(earlier_key)
                at=request.intent.event_time
            runtime.order_manager.on_market_snapshot(ExecutionMarketSnapshot(entry.ticker, 10., 10.01, .01, at, 'arte.liquidity_100ms_v1'))
            initial_us = int((at-timedelta(microseconds=1000)).timestamp()*1000000)
            local = at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket = (local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
            initial_row = dict(ticker=entry.ticker,resolution_ms=100,bucket_index=bucket,event_count=1,
                first_event_us=initial_us,last_event_us=initial_us,quote_timestamp_us=initial_us,quote_valid=1,
                bid_int=100000,ask_int=100100,bid_size=100000.,ask_size=100000.,price_valid=1,
                close_int=100100,extremes_valid=1,low_int=100100,high_int=100100,execution_volume=100000.)
            await runtime.process_liquidity_boundary([initial_row], at=at)
            result = await runtime.submit_fixed_structural_lot_request(request)
            assert result[0]['decision']['status'] in ('approved', 'resized')
            now_ms = entry.boundary_ms + 100
            at = market_day_boundary(source.session_date, now_ms)
            quote_us = int((at-timedelta(microseconds=1000)).timestamp()*1000000)
            local = at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket = (local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
            row = dict(ticker=entry.ticker,resolution_ms=100,bucket_index=bucket,event_count=1,
                first_event_us=quote_us,last_event_us=quote_us,quote_timestamp_us=quote_us,quote_valid=1,
                bid_int=100000,ask_int=100100,bid_size=100000.,ask_size=100000.,price_valid=1,
                close_int=100100,extremes_valid=1,low_int=100100,high_int=100100,
                execution_volume=100000.,execution_price_levels=({'price_int':100100,'volume':100000.},))
            await runtime.process_liquidity_boundary([row], at=at)
            group = next(g for g in runtime.order_manager._groups.values() if g.intent.intent_id==request.intent.intent_id)
            owner.register_entry(request, group.group_id)
            key = (entry.account_id, entry.assignment_id, entry.ticker)
            await owner.first_held(key, boundary_ms=now_ms)
            positions = await broker.positions(entry.account_id)
            quantity = sum(float(p.position) for p in positions)
            assert quantity > 0
            assert sum(q for _,q in owner.states[key].roster.remaining) == Decimal(str(quantity))
            assert group.intent.reference_price == request.intent.reference_price
            from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
            from time import perf_counter
            financial = StrategyOneFinancialView(entry.assignment_id, entry.account_id, entry.ticker,
                AssignmentStatus.MANAGING, StrategyPermissions(), quantity, False, False, False, 1)
            if historical:
                active_request=owner.propose(request,financial,now_ms=now_ms+100,
                    bid=10.,ask=10.01,tick=source.tick,low_boundary_ms=None,low_int=None,
                    low_price_valid=False,low_extremes_valid=False,breaks=(),overhead_levels=(),
                    price_bearing_bar=True,allows_completed_30s_trailing=False)
                active_proof=owner._management_reads[active_request]
                active_proof.require()
                snapshot=active_proof.normalized_snapshot
                assert len(snapshot[1])==2
                current_facts=reuse._entry_source_facts(request)
                prior_key=(entry.ticker,31000)
                original_quote=source._quotes[prior_key]
                from dataclasses import replace
                original_bid=original_quote.bid_int
                # Frozen mapping prevents ordinary writes; force nested scalar tamper explicitly.
                object.__setattr__(original_quote,'bid_int',original_bid-1)
                try:
                    assert reuse._entry_source_facts(request)==current_facts
                    changed=reuse._normalized_context_snapshot(owner,snapshot[4])
                    assert not reuse._same_normalized_snapshot(snapshot,changed)
                    with pytest.raises(ValueError,match='ownership, lease, head or context changed'):
                        active_proof.require()
                finally:object.__setattr__(original_quote,'bid_int',original_bid)
                assert reuse._same_normalized_snapshot(snapshot,reuse._normalized_context_snapshot(owner,snapshot[4]))
                return 'historical_context_mutation_rejected'
            before_cash = (await broker.account_summary(entry.account_id)).totalcashvalue
            before_orders = tuple((o.orderId,o.cOID,o.auxPrice,o.price) for o in await broker.live_orders())
            if disabled:
                # Explicit control: same actual101 source, policy reuse disabled only.
                monkeypatch.setattr(reuse, "installed_management_reuse_policy", lambda _: None)
            from src.trading_runtime import strategy_registry as registry
            from dataclasses import replace
            before_policy=owner._management_owner_binding.policy_snapshot
            other=replace(registry._NUMBERED_RELEASES[42],number=9999,approved_digest='')
            other=replace(other,approved_digest=other.digest())
            registry.register_numbered_strategy(other)
            try:
                after_policy=reuse._policy_snapshot(source,before_policy[6])
                assert before_policy==after_policy
                import importlib
                importlib.import_module('fractions')
                assert before_policy==reuse._policy_snapshot(source,before_policy[6])
            finally:registry._NUMBERED_RELEASES.pop(9999)
            print('native_management_factory_dependency_bindings='+str(len(before_policy[6])))
            counts.clear()
            reads.clear()
            import cProfile, os
            profile_directory = os.environ.get("FIXED_LOT_REUSE_PROFILE_DIR")
            profiler = cProfile.Profile() if profile_directory else None
            if profiler is not None:
                profiler.enable()
            started = perf_counter()
            for offset in range(3):
                boundary = now_ms+(offset+1)*100
                at = market_day_boundary(source.session_date, boundary)
                quote_us = int((at-timedelta(microseconds=1000)).timestamp()*1000000)
                local = at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
                bucket = (local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
                decision_row = {**row, 'bucket_index':bucket,'first_event_us':quote_us,
                    'last_event_us':quote_us,'quote_timestamp_us':quote_us,'execution_volume':0.,
                    'execution_price_levels':()}
                await runtime.process_liquidity_boundary([decision_row], at=at)
                publisher.enqueue_pending()
                await publisher.await_fence()
                management = owner.propose(request, financial, now_ms=boundary,
                    bid=10.,ask=10.01,tick=source.tick,low_boundary_ms=None,low_int=None,
                    low_price_valid=False,low_extremes_valid=False,breaks=(),overhead_levels=(),
                    price_bearing_bar=True,allows_completed_30s_trailing=False)
                assert not management.intents
                await runtime.submit_fixed_structural_lot_protection(management)
                assert management not in owner._management_reads
                assert sum(q for _,q in owner.states[key].roster.remaining) == Decimal(str(quantity))
            elapsed = perf_counter()-started
            if profiler is not None:
                from pathlib import Path
                profiler.disable()
                profiler.dump_stats(str(Path(profile_directory)/("decision-"+("cold" if disabled else "reuse")+".prof")))
            assert (await broker.account_summary(entry.account_id)).totalcashvalue == before_cash
            assert tuple((o.orderId,o.cOID,o.auxPrice,o.price) for o in await broker.live_orders()) == before_orders
            print('native_management_disabled='+str(disabled))
            print('native_management_complete_binder_calls='+str(dict(counts)))
            print('native_management_loader_calls='+str(dict(reads)))
            print('native_management_three_decisions_s='+str(elapsed))
            # Actual partial target execution removes one OCA leg; the two
            # remaining independent stops then use the public commandful actor.
            from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
            target_orders=sorted((o for o in await broker.live_orders() if o.orderType=='LMT'
                and o.side=='SELL' and o.remainingQuantity>0 and o.order_status in OPEN_ORDER_STATUSES),key=lambda o:o.price)
            first_target=target_orders[0]
            target_price=float(first_target.price)
            boundary=(now_ms+400+999)//1000*1000
            at=market_day_boundary(source.session_date,boundary)
            quote_us=int((at-timedelta(microseconds=1000)).timestamp()*1000000)
            local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket=(local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
            price_int=int(round(target_price*10000))
            target_row={**row,'bucket_index':bucket,'first_event_us':quote_us,'last_event_us':quote_us,
                'quote_timestamp_us':quote_us,'bid_int':price_int,'ask_int':price_int+100,
                'close_int':price_int,'low_int':price_int,'high_int':price_int,
                'execution_volume':float(first_target.remainingQuantity),
                'execution_price_levels':({'price_int':price_int,'volume':float(first_target.remainingQuantity)},)}
            await runtime.process_liquidity_boundary([target_row],at=at)
            residual=sum(float(p.position) for p in await broker.positions(entry.account_id))
            assert 0 < residual < quantity
            publisher.enqueue_pending(); await publisher.await_fence()
            from dataclasses import replace
            financial=replace(financial,position_quantity=residual)
            from src.trading_runtime.strategy_one_position import ResistanceBreak
            from test_strategy_one_position import level
            command=owner.propose(request,financial,now_ms=boundary,
                bid=target_price,ask=target_price+.01,tick=source.tick,
                low_boundary_ms=None,low_int=None,low_price_valid=False,low_extremes_valid=False,
                breaks=tuple(ResistanceBreak(boundary,level(i,target_price-.06+i*.01)) for i in range(3)),
                overhead_levels=(),price_bearing_bar=True,allows_completed_30s_trailing=False)
            assert command.intents
            if recover:
                modify=broker.modify_order
                modified=[]
                async def partial(*args,**kwargs):
                    modified.append(args[1])
                    if len(modified)==2:
                        raise RuntimeError('controlled second-leg ACK transport failure')
                    return await modify(*args,**kwargs)
                broker.modify_order=partial
                try:
                    with pytest.raises(RuntimeError,match='not approved'):
                        await runtime.submit_fixed_structural_lot_protection(command)
                finally:broker.modify_order=modify
                assert len(modified)==2
                original_record=next(r for r in journal.unfenced_records()
                    if r.category=='strategy' and r.entity_id==command.intents[0].intent_id)
                publisher.enqueue_pending();await publisher.await_fence()
                fresh_boundary=boundary+1000
                at=market_day_boundary(source.session_date,fresh_boundary)
                quote_us=int((at-timedelta(microseconds=1000)).timestamp()*1000000)
                local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
                bucket=(local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
                fresh_row={**target_row,'bucket_index':bucket,'first_event_us':quote_us,'last_event_us':quote_us,
                    'quote_timestamp_us':quote_us,'execution_volume':0.,'execution_price_levels':()}
                await runtime.process_liquidity_boundary([fresh_row],at=at)
                publisher.enqueue_pending();await publisher.await_fence()
                next_request=owner.propose(request,financial,now_ms=fresh_boundary,
                    bid=target_price,ask=target_price+.01,tick=source.tick,
                    low_boundary_ms=None,low_int=None,low_price_valid=False,low_extremes_valid=False,
                    breaks=tuple(ResistanceBreak(fresh_boundary,level(i,target_price-.06+i*.01)) for i in range(3)),
                    overhead_levels=(),price_bearing_bar=True,allows_completed_30s_trailing=False)
                recovery=owner.prepare_recovery(next_request,original_record_id=original_record.record_id)
                assert owner.require_recovery(recovery) is recovery
                recovered=await runtime.submit_fixed_structural_lot_recovery(recovery)
                assert recovered.state==owner.states[key]
                assert client.fixed_lot_recovery_contexts
                from src.trading_runtime.arte_journal_commit_v4 import _batched_detail_rows_v4
                from src.trading_runtime.arte_journal_writer import _literal
                from copy import copy
                issued=owner._recovery_batches[recovery]
                batch_id=next(iter(issued))
                filters=(f"WHERE run_id={_literal(source.run_id)} "
                    f"AND batch_id=toUUID({_literal(batch_id)}) ")
                assert _batched_detail_rows_v4(client,(),filters,fixed_lot_recovery_context=recovery)=={}
                with pytest.raises(ValueError,match='owner/slot'):
                    _batched_detail_rows_v4(client,(),filters,fixed_lot_context=recovery)
                with pytest.raises(ValueError,match='foreign selected operation'):
                    _batched_detail_rows_v4(copy(client),(),filters,fixed_lot_recovery_context=recovery)
                for bad in (filters.replace(source.run_id,str(uuid4())),filters.replace(batch_id,str(uuid4()))):
                    with pytest.raises(ValueError,match='foreign run/batch'):
                        _batched_detail_rows_v4(client,(),bad,fixed_lot_recovery_context=recovery)
                binding=issued.pop(batch_id)
                try:
                    with pytest.raises(ValueError,match='unissued|foreign run/batch'):
                        _batched_detail_rows_v4(client,(),filters,fixed_lot_recovery_context=recovery)
                finally:issued[batch_id]=binding
                for bad in ((binding[0],True,binding[2]),('not-uuid',binding[1],binding[2])):
                    issued[batch_id]=bad
                    try:
                        with pytest.raises(ValueError,match='malformed recovery binding|badly formed hexadecimal UUID'):
                            _batched_detail_rows_v4(client,(),filters,fixed_lot_recovery_context=recovery)
                    finally:issued[batch_id]=binding
                original_owner=recovery.owner
                object.__setattr__(recovery,'owner',object())
                try:
                    with pytest.raises(ValueError,match='owner/slot'):
                        _batched_detail_rows_v4(client,(),filters,fixed_lot_recovery_context=recovery)
                finally:object.__setattr__(recovery,'owner',original_owner)
                before=reuse._normalized_context_snapshot(owner)
                assert reuse._same_normalized_snapshot(before,reuse._normalized_context_snapshot(owner,before[4]))
                stored=owner._recoveries.pop(recovery)
                try:
                    with pytest.raises(ValueError,match='issuance changed'):
                        reuse._normalized_context_snapshot(owner,before[4])
                finally:owner._recoveries[recovery]=stored
                from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
                from src.trading_runtime.fixed_structural_lot_cold_recovery import require_cold_recovery_context
                contexts=tuple(client.fixed_structural_lot_contexts)
                cold=[]
                from copy import copy
                cold_client=copy(client)  # Same persisted transport rows; no exclusive writer lease.
                cold_client.backtest_v4_lease=None
                prefix=load_verified_v4_prefix(cold_client,source.run_id,first_price_source=source.price_authority,
                    fixed_lot_contexts=contexts,_fixed_lot_cold_source=source,_cold_recovery_context_sink=cold)
                assert prefix.last_sequence==journal._fenced_sequence and cold
                client.fixed_lot_recovery_contexts=tuple(cold)
                for batch,context in cold:
                    assert require_cold_recovery_context(context,run_id=source.run_id,batch_id=batch) is context
                before=reuse._normalized_context_snapshot(owner)
                assert reuse._same_normalized_snapshot(before,reuse._normalized_context_snapshot(owner,before[4]))
                command=next_request
            else:
                await runtime.submit_fixed_structural_lot_protection(command)
            assert command not in owner._management_reads
            assert sum(q for _,q in owner.states[key].roster.remaining)==Decimal(str(residual))
            remaining_orders=tuple(o for o in await broker.live_orders() if o.order_status in OPEN_ORDER_STATUSES)
            assert len([o for o in remaining_orders if o.orderType=='STP'])==2
            assert len([o for o in remaining_orders if o.orderType=='LMT'])==2
            assert all(o.auxPrice>entry.initial_stop for o in remaining_orders if o.orderType=='STP')
            print('native_management_commandful_residual='+str(residual))
            return ((await broker.account_summary(entry.account_id)).totalcashvalue, tuple(owner.states[key].roster.remaining),
                tuple((o.auxPrice,o.price,o.totalSize,o.order_status) for o in await broker.live_orders()),
                dict(counts), dict(reads), elapsed)
        finally:
            if runtime.order_manager is not None:
                await runtime.order_manager.close()
            for task in (runtime._broker_stream_task, runtime._risk_refresh_task):
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            writer.close()
            journal.close()
    return asyncio.run(exercise(disabled=disabled))


def test_native_management_runtime_financial_noop_and_complete_replay_counts(monkeypatch):
    with monkeypatch.context() as scoped:
        cold=_native_management_graph(scoped,disabled=True)
    with monkeypatch.context() as scoped:
        warm=_native_management_graph(scoped,disabled=False)
    assert cold[:3]==warm[:3]
    assert sum(warm[3].values())<sum(cold[3].values())


def test_native_historical_context_source_mutation_rejected(monkeypatch):
    assert _native_management_graph(monkeypatch,historical=True)=='historical_context_mutation_rejected'


def test_native_issued_nonempty_live_and_cold_recovery_contexts(monkeypatch):
    _native_management_graph(monkeypatch,recover=True)


@pytest.mark.parametrize("failure", (ValueError, __import__("asyncio").CancelledError))
def test_frontier_independent_authority_restores_scopes_on_failure(monkeypatch, failure):
    from src.trading_runtime import fixed_structural_lot_warm_proof as warm
    source = SimpleNamespace(require_installed_admission=lambda: None, run_id="run", price_authority=object())
    owner = SimpleNamespace(operation=SimpleNamespace(source=source), client=object())
    monkeypatch.setattr(reuse, "_context_frontier", lambda value: (value,))
    active = object()
    previous_context = object()
    def authority(*args):
        assert reuse._ACTIVE.get() is None
        assert reuse._CONTEXT_OWNER.get()[0] is owner
        # Nested entry verification must execute its original complete authority.
        called = []
        request = SimpleNamespace(source=source, _verify_complete=lambda: called.append("independent"))
        reuse.verify_entry(request)
        assert called == ["independent"]
        raise failure()
    monkeypatch.setattr(warm, "_authority", authority)
    a = reuse._ACTIVE.set(active)
    c = reuse._CONTEXT_OWNER.set(previous_context)
    try:
        with pytest.raises(failure):
            reuse._frontier(owner)
        assert reuse._ACTIVE.get() is active
        assert reuse._CONTEXT_OWNER.get() is previous_context
    finally:
        reuse._ACTIVE.reset(a)
        reuse._CONTEXT_OWNER.reset(c)


def test_normalized_nonempty_cold_recovery_membership_and_content(monkeypatch):
    from src.trading_runtime import fixed_structural_lot_cold_recovery as cold
    source = object()
    context = cold.FixedStructuralLotColdRecoveryContext(source, 'batch', 8, '{"graph":1}')
    # Component issuance seam only: production cold graph issuer remains unchanged.
    cold._CONTEXTS[context] = (source, 'batch', 8, '{"graph":1}', True)
    owner = SimpleNamespace(operation=SimpleNamespace(source=source), client=SimpleNamespace(
        fixed_structural_lot_contexts=(), fixed_lot_recovery_contexts=(('batch', context),)))
    monkeypatch.setattr(reuse, '_context_frontier', lambda value: (value,))
    snapshot = reuse._normalized_context_snapshot(owner)
    assert reuse._same_normalized_snapshot(snapshot, reuse._normalized_context_snapshot(owner))
    cold._CONTEXTS.pop(context)
    with pytest.raises(ValueError, match='issuance changed'):
        reuse._normalized_context_snapshot(owner)
    cold._CONTEXTS[context] = (source, 'batch', 8, '{"graph":1}', True)
    object.__setattr__(context, 'before_sequence', 9)
    with pytest.raises(ValueError, match='issuance changed'):
        reuse._normalized_context_snapshot(owner)
    cold._CONTEXTS.pop(context)


def test_normalized_snapshot_rejects_equal_replacement_and_code_mutation(monkeypatch):
    a,b = object(),object()
    def fn(): pass
    initial = ((a,), ((a,a,a,a,a,a,a,a,'content','entry','facts',()),), (), ((fn,fn.__code__),))
    replaced = ((a,), ((b,a,a,a,a,a,a,a,'content','entry','facts',()),), (), ((fn,fn.__code__),))
    assert not reuse._same_normalized_snapshot(initial, replaced)
    def other(): return None
    changed = (initial[0],initial[1],initial[2],((fn,other.__code__),))
    assert not reuse._same_normalized_snapshot(initial, changed)


@pytest.mark.parametrize("failure", (ValueError, __import__('asyncio').CancelledError))
def test_owner_reader_scope_restores_on_original_failure(monkeypatch, failure):
    owner,proof,_ = owner_and_proof(monkeypatch)
    monkeypatch.setattr(reuse,'_context_frontier',lambda value:(value,))
    active=reuse._ACTIVE.set(proof)
    def loader():
        assert reuse._CONTEXT_OWNER.get()[0] is owner
        raise failure()
    try:
        with pytest.raises(failure):reuse._owner_read_context(owner,loader)
        assert reuse._CONTEXT_OWNER.get() is None
    finally:reuse._ACTIVE.reset(active)


def test_owner_reader_scope_rejects_frontier_change(monkeypatch):
    owner,proof,_ = owner_and_proof(monkeypatch)
    current=[1]
    monkeypatch.setattr(reuse,'_context_frontier',lambda value:tuple(current))
    active=reuse._ACTIVE.set(proof)
    try:
        with pytest.raises(ValueError,match='frontier changed'):
            reuse._owner_read_context(owner,lambda:current.append(2))
        assert reuse._CONTEXT_OWNER.get() is None
    finally:reuse._ACTIVE.reset(active)


@pytest.mark.parametrize('name',('_snapshot_tree','_content'))
def test_normalized_snapshot_pins_helper_encoder_code(monkeypatch,name):
    owner=SimpleNamespace(client=SimpleNamespace(fixed_structural_lot_contexts=(),fixed_lot_recovery_contexts=()))
    monkeypatch.setattr(reuse,'_context_frontier',lambda value:(value,))
    before=reuse._normalized_context_snapshot(owner)
    original=getattr(reuse,name)
    monkeypatch.setattr(reuse,name,lambda value:original(value))
    after=reuse._normalized_context_snapshot(owner,before[4])
    assert not reuse._same_normalized_snapshot(before,after)
