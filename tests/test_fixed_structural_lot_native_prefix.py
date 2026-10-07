"""Real original journal prefix, with explicit missing installed-hook seam only."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
import pytest
from test_fixed_structural_lot_source import prepared
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.domain import InstrumentContract,TradingMode
from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.portfolio import PortfolioManagementEngine,PortfolioAccountProfile,PortfolioPolicy
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from tests.test_trading_runtime import quote


@pytest.mark.parametrize('queued,ack_failure',[(False,None),(True,None),(True,'partial'),(True,'lost'),(True,'lost_without_context')])
def test_complete_selected_actor_original_records_form_contiguous_v4_prefix(monkeypatch,queued,ack_failure):
    async def run():
        writer=None
        source,proposal,calls=prepared(monkeypatch)
        request=source.request(proposal)
        account=proposal.account_id
        journal=BacktestMemoryJournal(run_id=source.run_id)
        broker=SimulatedBrokerAdapter([account],SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST,initial_time=request.intent.event_time)
        await broker.initialize()
        risk=RiskAuthority(); await risk.prime(broker,[account])
        at=request.intent.event_time
        profile=PortfolioAccountProfile('cash',account,'backtest','simulated',PortfolioPolicy(allow_outside_rth=True))
        portfolio=PortfolioManagementEngine((profile,),journal=journal,run_id=source.run_id,
            strategy_id=request.strategy_id,strategy_revision=request.revision,event_clock=lambda:at)
        await portfolio.synchronize(broker)
        journal.append_fixed_structural_lot_entry(request=request)
        decision,approved=await portfolio.approve(request.intent,account_id=account,assignment_id=proposal.assignment_id)
        assert approved is not None,decision.reasons
        async def updated(snapshot): portfolio.on_order_group_update(snapshot)
        planner=RuntimeIbkrStrategyOrderPlanner({proposal.ticker:InstrumentContract(proposal.ticker,1,proposal.ticker,'STK','USD')},
            strategy_id=request.strategy_id,strategy_revision=request.revision,run_id=source.run_id)
        oms=OrderManagementEngine(broker=broker,planner=lambda intent,account_id,event:planner.plan(
            account_id=account_id,intent=intent,event=event),risk=risk,journal=journal,
            run_id=source.run_id,strategy_id=request.strategy_id,strategy_revision=request.revision,
            state_callback=updated,causal_execution_clock=True)
        from src.trading_runtime.runtime import TradingRuntime
        fill_recorder=object.__new__(TradingRuntime)
        fill_recorder.run_id=source.run_id;fill_recorder.journal=journal
        async def step(bid,ask):
            nonlocal at
            at+=timedelta(milliseconds=100)
            event=replace(quote(bid=bid,ask=ask,ask_size=10000,bid_size=10000),ticker=proposal.ticker,
                raw={'conid':1},ts=at,ingest_ts=at)
            oms.on_market_snapshot(ExecutionMarketSnapshot(proposal.ticker,bid,ask,source.tick,at,'qmd-history'))
            executions=await broker.on_market_event(event)
            fill_recorder._record_executions(executions)
            await oms.reconcile();await portfolio.synchronize(broker)
        try:
            oms.on_market_snapshot(ExecutionMarketSnapshot(proposal.ticker,10.,10.01,source.tick,at,'qmd-history'))
            snap=await oms.submit_intent(approved,account_id=account,event=None)
            group=oms._groups[snap.group_id]
            await step(10.,10.01);await step(12.01,12.02);await step(12.2,12.21)
            from src.trading_runtime.signals import StrategyIntent
            command=StrategyIntent(intent_id=str(uuid4()),ticker=proposal.ticker,event_time=at,
                action='replace_protective_stop',quantity=sum(float(p.position) for p in await broker.positions(account)),
                reference_price=12.2,invalidation_price=12.1,reason='three_resistance_step_stop')
            # Controlled earned-stop input uses the real typed protection
            # producer; this does not claim an installed management owner.
            journal.append_strategy_one_protection_intent(intent=command,account_id=account,
                strategy_id=request.strategy_id,strategy_revision=request.revision)
            decision,approved_stop=await portfolio.approve(command,account_id=account,assignment_id=proposal.assignment_id)
            assert approved_stop is not None,decision.reasons
            await oms.submit_intent(approved_stop,account_id=account,event=None)
            for order in await broker.live_orders():
                if group.broker_order_slices[str(order.orderId)]=='lot-2' and group.broker_order_roles[str(order.orderId)]!='entry':
                    await broker.cancel_order(account,str(order.orderId))
            await oms.reconcile()
            assert next(o for o in group.orders if 'repair-' in o.cOID and o.orderType=='STP').auxPrice==12.1
            # Pending repair lineage is only an earlier acknowledged price;
            # removing its binding never invents effective protection.
            from src.trading_runtime.arte_oms_projection import freeze_oms_group,canonical_oms_order_metadata
            frozen=freeze_oms_group(group)
            pending=replace(frozen,broker_order_request_indexes={k:v for k,v in frozen.broker_order_request_indexes.items() if v<9})
            repair_stop=next(o for o in pending.orders if 'repair-' in o.cOID and o.orderType=='STP')
            earned=tuple(r for r in journal.protection_records(source.run_id)
                if r.payload.get('action')=='replace_protective_stop' and r.payload.get('phase')=='effective')
            assert earned
            fence=max(r.sequence for r in journal.records(source.run_id))+1
            proof_map={str(r.sequence):r for r in earned}
            metadata=canonical_oms_order_metadata(pending,repair_stop,proof_map,
                source_sequence=fence,source_boundary=at,source_run_id=source.run_id)
            assert metadata['confirmed_support_stop']==12.1
            assert not {'stop_confirmed','broker_order_id','protection_coverage_quantity'} & set(metadata)
            for mutation in ('missing','future_sequence','future_clock','foreign','price','bound_without_creation'):
                candidate=pending; proofs=proof_map; order=repair_stop
                if mutation=='missing':proofs={}
                elif mutation=='future_sequence':proofs={k:replace(v,sequence=fence) for k,v in proof_map.items()}
                elif mutation=='future_clock':proofs={k:replace(v,event_time=at+timedelta(milliseconds=1)) for k,v in proof_map.items()}
                elif mutation=='foreign':proofs={k:replace(v,run_id=str(uuid4())) for k,v in proof_map.items()}
                elif mutation=='price':
                    order=replace(repair_stop,auxPrice=12.11)
                    candidate=replace(pending,orders=tuple(order if o.cOID==order.cOID else o for o in pending.orders))
                elif mutation=='bound_without_creation':candidate=frozen
                with pytest.raises(ValueError):
                    canonical_oms_order_metadata(candidate,order,proofs,
                        source_sequence=fence,source_boundary=at,source_run_id=source.run_id)
            from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
            config={'mode':'backtest','strategy_id':request.strategy_id,'strategy_revision':request.revision,
                'parent_configuration_hash':source.parent_payload_hash,'selected_configuration_hash':source.selected_configuration_hash}
            units=project_pending_backtest_v4_prefix(journal,attempt_id=str(uuid4()),run_month=source.session_date.replace(day=1),
                prior_sequence=0,expected_config=config,through_sequence=journal.latest_sequence(source.run_id))
            bases=[u if hasattr(u,'first_sequence') else u.base for u in units]
            assert bases[0].first_sequence==1
            assert bases[-1].last_sequence==journal.latest_sequence(source.run_id)
            assert all(a.last_sequence+1==b.first_sequence and b.prior_batch_id==a.batch_id for a,b in zip(bases,bases[1:]))
            from src.trading_runtime.fixed_structural_lot_entry_v4 import (
                FixedStructuralLotPublicationContext,publish_fixed_structural_lot_entry_v4)
            from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
            from tests.test_arte_journal_commit_v4 import attached_v4_client
            from src.trading_runtime import arte_journal_commit_v4 as commits
            from tests.test_arte_journal_writer import MemoryClient
            from tests.fixed_structural_lot_transport_fixture import ExactDecisionTransport
            client=ExactDecisionTransport()
            # Run context is published before the first original actor prefix
            # and before Keeper seals it; never retrofit a frozen context.
            from tests.test_arte_journal_writer import run_row,run_context
            from src.trading_runtime.arte_journal_writer import publish_typed_run,publish_typed_run_context
            parent={**run_row(),'run_id':source.run_id,'run_month':source.session_date.replace(day=1).isoformat(),
                'session_date':source.session_date.isoformat(),'configuration_hash':source.selected_configuration_hash,
                'market_plan_token':source.price_authority.plan.source.market.token,
                'started_at':request.intent.event_time.isoformat()}
            publish_typed_run(client,parent)
            publish_typed_run_context(client,run_id=source.run_id,
                config={**run_context(),'strategy_id':request.strategy_id,'strategy_revision':request.revision,
                    'anchor_date':source.session_date.isoformat()},account_ids=(account,))
            client=attached_v4_client(client)
            first_record=journal.records(source.run_id)[0]
            context=FixedStructuralLotPublicationContext(units[0],first_record,source)
            # Missing installed-own authority seam only. Real actor/source,
            # family projection, complete original sequences and all seals run.
            monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_installed_admission',lambda self:None)
            # Uninstalled controlled transport fixture; no real selected profile exists.
            from src.trading_runtime import fixed_structural_lot_profile as profile_module
            monkeypatch.setattr(profile_module,'require_fixed_structural_lot_client_context',lambda *args:None)
            client.fixed_structural_lot_contexts=(context,)
            if queued:
                from src.trading_runtime import arte_journal_writer as writer_module
                from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
                monkeypatch.setattr(writer_module,'storage_preflight',lambda *a,**k:None)
                monkeypatch.setattr(writer_module,'journal_permission_preflight',lambda *a,**k:None)
                monkeypatch.setattr(writer_module,'_verify_run_identity',lambda *a:dict(mode='backtest',account_ids=(account,)))
                client.fixed_structural_lot_contexts=()
                writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,journal_profile='backtest_v4',coalesce_batches=False)
                publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),
                    run_month=source.session_date.replace(day=1),expected_config=config)
                publisher.bind_fixed_structural_lot_source(source)
                try:
                    receipt=await publisher._drain(target_sequence=journal.latest_sequence(source.run_id))
                    assert receipt.last_sequence==bases[-1].last_sequence
                    context,=client.fixed_structural_lot_contexts
                    units=(context.unit,*units[1:])
                    expected_batch_ids=tuple(r['batch_id'] for r in sorted(client.tables['trading_commit_v4'],key=lambda r:int(r['last_sequence'])))
                except BaseException:
                    writer.close()
                    raise
            else:
                for unit in units:
                    kind=type(unit).__name__
                    if kind=='V4FixedStructuralLotEntryBatch':publish_fixed_structural_lot_entry_v4(client,context)
                    elif kind=='TypedJournalBatch':commits.publish_base_typed_batch_v4(client,unit)
                    elif kind=='V4OmsTacticBatch':commits.publish_oms_tactic_batch_v4(client,unit.base,
                        tactic_state=unit.tactic_state,tactic_steps=unit.tactic_steps)
                    elif kind=='V4ProtectionChangeBatch':commits.publish_protection_change_batch_v4(client,unit.base,
                        change=unit.change,entry_orders=unit.entry_orders)
                    elif kind=='V4BrokerAcknowledgementBatch':commits.publish_broker_acknowledgement_batch_v4(client,unit.base,
                        acknowledgement=unit.acknowledgement)
                    elif kind=='V4PortfolioAllocationBatch':commits.publish_portfolio_allocation_batch_v4(client,unit.base,
                        allocation=unit.allocation)
                    else:raise AssertionError(kind)
                expected_batch_ids=tuple(b.batch_id for b in bases)
            # A new preparation operation independently reloads the same
            # controlled parent/product/source fixtures; never clone issuance.
            from src.backend import backtest_fixed_structural_lot_source as owner
            market=source.price_authority.entry_activity_source.plan.market
            broker_unit=replace(market.units[0],stage='broker_100ms',
                attempt_id=source.quotes[0].broker_attempt_id)
            market=replace(market,units=(*market.units,broker_unit))
            class FreshQuoteTransport:
                def execute(self,sql):
                    import json,re
                    keys=set(re.findall(r"\('([^']+)',(\d+),toUUID\('([^']+)'\)\)",sql))
                    from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
                    rows=[]
                    for q in source.quotes:
                        bucket=(q.boundary_ms+SESSION_OPEN_OFFSET_MS)//100-1
                        if (q.ticker,str(bucket),q.broker_attempt_id) in keys:
                            rows.append(dict(ticker=q.ticker,bucket_index=bucket,liquidity_attempt_id=q.broker_attempt_id,
                                bid_int=q.bid_int,ask_int=q.ask_int,quote_timestamp_us=q.quote_timestamp_us,quote_valid=q.quote_valid))
                    return '\n'.join(json.dumps(r) for r in rows)
            fresh_source=owner.prepare_fixed_structural_lot_source(FreshQuoteTransport(),run_id=source.run_id,parent_number=42,
                session_date=source.session_date,policy=source.policy,market=market,seeds=object(),price_authority=source.price_authority)
            assert fresh_source is not source and calls=={'configuration':2,'product':2,'full_children':2}
            cold_context=FixedStructuralLotPublicationContext(units[0],first_record,fresh_source)
            prefix=commits.load_verified_v4_prefix(client,source.run_id,fixed_lot_contexts=(cold_context,))

            assert prefix.last_sequence==journal.latest_sequence(source.run_id)
            assert prefix.batch_ids==expected_batch_ids
            from src.trading_runtime.arte_oms_projection import (
                load_latest_committed_oms_groups,load_committed_oms_admission_page,
                load_committed_oms_decision_page,reconstruct_strategy_one_oms_lineage,
                _approved_strategy_one_oms_intent)
            from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
            from src.trading_runtime.arte_intent_projection import RecoveredIntent
            states=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset({account}),
                strategy_identity=(request.strategy_id,request.revision),require_tactic=True,fixed_lot_contexts=(cold_context,))
            assert len(states)==1
            state=states[0]
            history=load_complete_typed_protection_history(client,prefix,fixed_lot_contexts=(cold_context,))
            reservations=load_committed_oms_admission_page(client,prefix,(state,))
            decisions=load_committed_oms_decision_page(client,prefix,(state,),reservations)
            recovered_source=cold_context.verify_source()
            from decimal import Decimal
            from src.trading_runtime.fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
            native_roster=load_fixed_structural_lot_stop_ceiling(client,prefix,
                entry=recovered_source.entry,intervals=None,intent=recovered_source.intent,
                group_id=state.group['group_id'],strategy_identity=(request.strategy_id,request.revision),
                entry_request=recovered_source,fixed_lot_contexts=(cold_context,))
            assert native_roster.ceiling==Decimal('13.0')
            assert native_roster.group_sequence==state.sequence
            assert calls=={'configuration':2,'product':2,'full_children':2}
            with pytest.raises(ValueError,match='foreign entry/source'):
                load_fixed_structural_lot_stop_ceiling(client,prefix,
                    entry=recovered_source.entry,intervals=None,intent=recovered_source.intent,
                    group_id=state.group['group_id'],strategy_identity=(request.strategy_id,request.revision),
                    entry_request=recovered_source,fixed_lot_contexts=(context,))
            with pytest.raises(ValueError,match='foreign entry/source'):
                load_fixed_structural_lot_stop_ceiling(client,prefix,
                    entry=recovered_source.entry,intervals=None,intent=recovered_source.intent,
                    group_id=state.group['group_id'],strategy_identity=(request.strategy_id,request.revision),
                    entry_request=recovered_source,fixed_lot_contexts=())
            original=RecoveredIntent(1,account,first_record.record_id,units[0].base.batch_id,
                recovered_source.intent,units[0].base)
            reservation=reservations[state.sequence]
            decision=decisions[state.sequence]
            restored_orders=reconstruct_strategy_one_oms_lineage(state,original,history,
                admission_reservation=reservation,admission_decision=decision)
            restored_intent,_=_approved_strategy_one_oms_intent(state,original,history,reservation,decision)
            assert [s.profit_target_price for s in restored_intent.protection_profile.slices]==[12.,13.,14.]
            assert [s.stop.price for s in restored_intent.protection_profile.slices]==[proposal.initial_stop,12.1,12.1]
            assert next(o for o in restored_orders if 'repair-' in o.cOID and o.orderType=='STP').auxPrice==12.1
            if queued:
                from types import SimpleNamespace
                from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
                from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
                from src.trading_runtime.runtime import TradingRuntime,RunMode
                from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
                from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
                from src.trading_runtime.strategy_one_position import ResistanceBreak
                from tests.test_strategy_one_position import level
                owner=NativeFixedStructuralLotManagement(operation=NativeFixedStructuralLotOperation(source),
                    publisher=publisher,client=client)
                owner.open(request,group_id=group.group_id)
                # Controlled completed resistance inputs exercise the actual
                # public actor. They are not a certified financial session.
                for _ in range(7):await step(12.3,12.31)
                publisher.enqueue_pending();await publisher.await_fence()
                boundary=proposal.boundary_ms+1000
                financial=StrategyOneFinancialView(proposal.assignment_id,account,proposal.ticker,
                    AssignmentStatus.MANAGING,StrategyPermissions(enter=True),
                    sum(float(p.position) for p in await broker.positions(account)),False,False,False,1)
                management=owner.propose(request,financial,now_ms=boundary,bid=12.3,ask=12.31,tick=source.tick,
                    low_boundary_ms=None,low_int=None,low_price_valid=False,low_extremes_valid=False,
                    breaks=tuple(ResistanceBreak(boundary,level(i,center))
                        for i,center in enumerate((12.16,12.17,12.18))),overhead_levels=(),
                    price_bearing_bar=True,allows_completed_30s_trailing=False)
                assert management.proposal.transition.stop_amendment is not None
                runtime=object.__new__(TradingRuntime)
                runtime.config=SimpleNamespace(mode=RunMode.BACKTEST,strategy_id=request.strategy_id,
                    strategy_revision=request.revision,anchor_date=source.session_date,account_ids=(account,))
                runtime.run_id=source.run_id;runtime.journal=journal;runtime.last_event_time=at
                runtime.portfolio=portfolio;runtime.order_manager=oms;runtime.intent_planner=planner
                runtime.strategy=None;runtime.broker=broker;runtime._canonical_session=None;runtime._review_only=False
                if ack_failure is not None:
                    from src.trading_runtime.order_management import OrderManagementState
                    prior=owner.states[(account,proposal.assignment_id,proposal.ticker)]
                    prior_leg_stops={s.slice_id:s.stop.price for s in group.intent.protection_profile.slices}
                    modify=broker.modify_order;modified=[]
                    async def interrupted(*args,**kwargs):
                        modified.append(args[1])
                        if len(modified)==2:
                            if ack_failure.startswith('lost'):await modify(*args,**kwargs)
                            raise RuntimeError('controlled management ACK transport loss')
                        return await modify(*args,**kwargs)
                    broker.modify_order=interrupted
                    try:
                        with pytest.raises(RuntimeError,match='not approved'):
                            await runtime.submit_fixed_structural_lot_protection(management)
                        assert len(modified)==2
                        live_group=oms._groups[group.group_id]
                        first_lot=live_group.broker_order_slices[modified[0]]
                        unresolved_lot=live_group.broker_order_slices[modified[1]]
                        profile_prices={s.slice_id:s.stop.price for s in live_group.intent.protection_profile.slices}
                        broker_prices={str(o.orderId):o.auxPrice for o in await broker.live_orders()}
                        desired=management.proposal.transition.stop_amendment['price']
                        assert profile_prices[first_lot]==desired and broker_prices[modified[0]]==desired,(first_lot,profile_prices,broker_prices)
                        assert profile_prices[unresolved_lot]==prior_leg_stops[unresolved_lot]
                        assert broker_prices[modified[1]]==(desired if ack_failure.startswith('lost') else prior_leg_stops[unresolved_lot])
                        # The common runtime may reconcile its group back to
                        # WORKING; that status does not confirm every amendment.
                        assert owner.states[(account,proposal.assignment_id,proposal.ticker)] is prior
                        # Authenticate the actual failed command unit, then
                        # prove matching intent fields cannot rekey its batch
                        # or replace its normalized source content.
                        from src.trading_runtime.arte_journal_projection import project_journal_record
                        from uuid import uuid5,NAMESPACE_URL
                        pending=journal.unfenced_records()
                        original_command_record=next(r for r in pending if r.category=='strategy' and r.entity_id==management.intents[0].intent_id)
                        binding=owner._requests[management]
                        command_batch=project_journal_record(original_command_record,run_month=publisher.run_month,
                            attempt_id=publisher.attempt_id,batch_id=str(uuid5(NAMESPACE_URL,
                                f'arte-backtest-v1:{original_command_record.run_id}:{publisher.attempt_id}:{original_command_record.sequence}:{original_command_record.record_id}')),
                            prior_batch_id=binding[3],source_cursor=binding[4],expected_config=config,expected_mode='backtest')
                        owner.verify_deferral_source(management,command_batch,management.intents[0])
                        for altered in (replace(command_batch,batch_id=str(uuid4())),
                                replace(command_batch,intents=({**command_batch.intents[0],
                                    'reference_price':str(Decimal(command_batch.intents[0]['reference_price'])+1)},))):
                            with pytest.raises(ValueError,match='batch/content'):
                                owner.verify_deferral_source(management,altered,management.intents[0])
                        with pytest.raises(ValueError,match='not issued'):
                            owner.verify_deferral_source(replace(management),command_batch,management.intents[0])
                        publisher.enqueue_pending();await publisher.await_fence()
                        failed_prefix=commits.load_verified_v4_prefix(client,source.run_id,fixed_lot_contexts=(cold_context,))
                        failed_history=load_complete_typed_protection_history(client,failed_prefix,fixed_lot_contexts=(cold_context,))
                        effective=tuple(r for r in failed_history.records if r.category=='protection'
                            and r.payload.get('intent_id')==management.intents[0].intent_id
                            and r.payload.get('phase')=='effective')
                        assert len(effective)==1 and effective[0].payload['price']==management.proposal.transition.stop_amendment['price']
                        with pytest.raises((RuntimeError,ValueError)):
                            await owner.confirm(management)
                        assert owner.states[(account,proposal.assignment_id,proposal.ticker)] is prior
                        # A copied request never gains owner-issued authority, even
                        # with an otherwise identical command and source graph.
                        with pytest.raises(ValueError,match='not issued'):
                            await owner.confirm(replace(management))
                        fresh_boundary=boundary+1000
                        next_request=owner.propose(request,financial,now_ms=fresh_boundary,
                            bid=12.3,ask=12.31,tick=source.tick,low_boundary_ms=None,
                            low_int=None,low_price_valid=False,low_extremes_valid=False,
                            breaks=tuple(ResistanceBreak(fresh_boundary,level(i,center))
                                for i,center in enumerate((12.16,12.17,12.18))),
                            overhead_levels=(),price_bearing_bar=True,
                            allows_completed_30s_trailing=False)
                        recovery=owner.prepare_recovery(next_request,original_record_id=original_command_record.record_id)
                        assert recovery.original_command.intent_id==management.intents[0].intent_id
                        assert recovery.original_command.event_time<next_request.intents[0].event_time
                        assert len(recovery.requested_records)==2
                        assert owner.require_recovery(recovery) is recovery
                        with pytest.raises(ValueError,match='not issued'):
                            owner.require_recovery(replace(recovery))
                        # This actor fixture deliberately has no installed own
                        # release/profile. Keep that seam explicit; all command,
                        # broker outcome, V4 source and cold graph guards run.
                        import src.backend.backtest_fixed_structural_lot_management as selected_owner
                        monkeypatch.setattr(selected_owner,'require_recovery_client',
                            lambda candidate,proof: proof.owner.require_recovery(proof,revalidate=False)
                                if candidate is client else (_ for _ in ()).throw(ValueError('foreign client')))
                        broker.modify_order=modify
                        continued=[]
                        async def continuing(*args,**kwargs):
                            continued.append(args[1])
                            return await modify(*args,**kwargs)
                        broker.modify_order=continuing
                        if ack_failure=='lost_without_context':
                            with pytest.raises(RuntimeError,match='not approved'):
                                await runtime.submit_fixed_structural_lot_protection(next_request)
                            assert continued==[]
                            assert not any(r.payload.get('intent_id')==next_request.intents[0].intent_id
                                and r.payload.get('phase')=='effective'
                                for r in journal.protection_records(source.run_id))
                            assert any('requires explicit issued recovery' in str(r.payload.get('reason',''))
                                for r in journal.unfenced_records() if r.entity_type=='protection_replacement_deferred')
                            return
                        try:
                            recovered=await runtime.submit_fixed_structural_lot_recovery(recovery)
                        except RuntimeError as error:
                            raise AssertionError(tuple(r.payload for r in journal.unfenced_records()
                                if r.entity_type=='protection_replacement_deferred')) from error
                        assert len(continued)==(1 if ack_failure=='partial' else 0)
                        assert all(v.price==desired for v in recovered.legs)
                        assert sum(v.outcome_kind=='broker_readback' for v in recovered.legs)==(
                            1 if ack_failure=='partial' else 2)
                        assert recovered.state.protection.stop==desired
                        assert all(v.effective_at==next_request.intents[0].event_time for v in recovered.legs)
                        assert owner.states[(account,proposal.assignment_id,proposal.ticker)]==recovered.state
                        executed=owner._recovery_executions[recovery]
                        assert executed.intent_id==next_request.intents[0].intent_id
                        assert executed.metadata['portfolio_decision_id']
                        for bad in (replace(executed,intent_id=str(uuid4())),
                                replace(executed,reference_price=executed.reference_price+.01),
                                replace(executed,quantity=executed.quantity-1),
                                replace(executed,metadata={**executed.metadata,'assignment_id':'foreign'}),
                                replace(executed,metadata={**executed.metadata,'portfolio_decision_id':str(uuid4())})):
                            with pytest.raises(ValueError,match='normalized Portfolio command'):
                                owner.verify_recovery_execution(recovery,bad)
                        with pytest.raises(ValueError):
                            owner.verify_recovery_execution(replace(recovery),executed)
                    finally:broker.modify_order=modify
                    receipt=recovered;management=next_request;boundary=fresh_boundary
                    # Public recovery completed at its fresh command clock;
                    # subsequent controlled source bars start after that clock.
                    at=next_request.intents[0].event_time
                else:
                    receipt=await runtime.submit_fixed_structural_lot_protection(management)
                earned_stop=management.proposal.transition.stop_amendment['price']
                assert receipt.state.protection.stop==earned_stop and 12.1<earned_stop<13.
                assert len(receipt.legs)==2 and {v.lot_id for v in receipt.legs}=={'lot-2','lot-3'}
                assert all(v.effective_sequence<receipt.group_sequence for v in receipt.legs)
                assert [s.profit_target_price for s in group.intent.protection_profile.slices]==[12.,13.,14.]
                assert [s.stop.price for s in group.intent.protection_profile.slices][1:]==[earned_stop,earned_stop]
                from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
                from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
                key=(account,proposal.assignment_id,proposal.ticker)
                class CompletedEvidence:
                    async def management_evidence(self,financial,resolutions,boundary_ms):
                        return StrategyOneManagementEvidence(proposal.ticker,boundary_ms,12.3,12.31,
                            True,None,None,tuple(ResistanceBreak(boundary_ms,level(i,center))
                                for i,center in enumerate((12.19,12.20,12.21),3)),())
                manager=StrategyOneManagementRunner(runtime=runtime,evidence=CompletedEvidence(),
                    tick_for_ticker=lambda ticker:source.tick)
                manager.bind_fixed_structural_lot_management(owner)
                manager._submitted[key]=proposal
                manager._positions[key]=receipt.state.protection
                manager._position_highs[key]=123000
                manager._first_held_boundaries[key]=proposal.boundary_ms+100
                # Existing aggregate exit stages keep priority. This fixture
                # has no liquidity window, rather than disabling that stage.
                manager._liquidity_lookup=SimpleNamespace(window_at=lambda *a:None)
                for _ in range(10):await step(12.3,12.31)
                runtime.last_event_time=at
                await manager.on_management(financial,{},boundary+1000)
                assert manager._positions[key]==owner.states[key].protection
                assert manager._positions[key].stop>earned_stop
                earned_stop=manager._positions[key].stop
                captured=owner.capture(manager,boundary_ms=boundary+1000)
                owner.require_checkpoint(captured)
                queued_capture=owner.freeze_checkpoint(captured)
                assert queued_capture is not captured
                assert queued_capture.selected_positions==captured.selected_positions
                owner.require_checkpoint(queued_capture)
                with pytest.raises(ValueError):owner.require_checkpoint(replace(captured))
                with pytest.raises(ValueError):owner.require_checkpoint(replace(captured,financials=()))
                checkpoint_prefix,_=owner._prefix()
                rebound,checked_prefix,_=owner.rebind_checkpoint(captured,
                    checkpoint_sequence=checkpoint_prefix.last_sequence,journal_batch_id=checkpoint_prefix.last_batch_id)
                assert checked_prefix==checkpoint_prefix
                assert rebound.inherited is captured.inherited
                assert rebound.selected_positions==captured.selected_positions
                assert rebound is not captured
                from src.trading_runtime.fixed_structural_lot_manager_snapshot import (
                    issue_manager_publication,require_manager_publication,verify_manager_insert)
                from src.trading_runtime.fixed_structural_lot_manager_schema import PARENT as SELECTED_MANAGER
                from types import SimpleNamespace
                # Controlled uninstalled profile seam, as above. The actual
                # owner capture/source/prefix projection still runs in full.
                monkeypatch.setattr(profile_module,'require_fixed_structural_lot_profile',
                    lambda profile:profile if getattr(profile,'operation',None) is not None else SimpleNamespace(operation=owner.operation))
                publication=issue_manager_publication(owner,captured,client=client,
                    sequence=checkpoint_prefix.last_sequence,batch_id=checkpoint_prefix.last_batch_id)
                frozen=require_manager_publication(publication,client=client)
                parent=frozen[SELECTED_MANAGER.name][0]
                assert parent['selected_position_count']==1
                assert parent['selected_configuration_hash']==source.selected_configuration_hash
                for table,rows in frozen.items():
                    verify_manager_insert(publication,client=client,table=table,rows=rows,
                        run_id=source.run_id,sequence=publication.sequence,batch_id=publication.batch_id,
                        snapshot_hash=parent['content_hash'])
                frozen[SELECTED_MANAGER.name][0]['selected_position_count']=0
                assert require_manager_publication(publication)[SELECTED_MANAGER.name][0]['selected_position_count']==1
                for copied in (replace(publication),replace(publication,sequence=publication.sequence+1),
                        replace(publication,rows_json='{}')):
                    with pytest.raises(ValueError):require_manager_publication(copied)
                with pytest.raises(ValueError):verify_manager_insert(publication,client=client,
                    table=SELECTED_MANAGER.name,rows=frozen[SELECTED_MANAGER.name],run_id=source.run_id,
                    sequence=publication.sequence,batch_id=publication.batch_id,snapshot_hash=parent['content_hash'])
                owner.require_checkpoint(rebound)
                with pytest.raises(ValueError):owner.rebind_checkpoint(captured,
                    checkpoint_sequence=checkpoint_prefix.last_sequence+1,journal_batch_id=checkpoint_prefix.last_batch_id)
                assert captured.inherited.position_highs==((key,123000),)
                assert captured.inherited.first_held_boundaries==((key,proposal.boundary_ms+100),)
                # New source operation and manager, persisted selected scalar
                # rows, no original owner state or mutable arm references.
                client.fixed_structural_lot_contexts=(cold_context,)
                live_recoveries=tuple(getattr(client,'fixed_lot_recovery_contexts',()))
                cold_recoveries=[]
                import src.trading_runtime.fixed_structural_lot_cold_recovery as cold_module
                approval_calls=[]
                approved_command=cold_module._approved_command
                def tracked_approval(*args):
                    result=approved_command(*args)
                    approval_calls.append(args)
                    return result
                monkeypatch.setattr(cold_module,'_approved_command',tracked_approval)
                current=commits.load_verified_v4_prefix(client,source.run_id,first_price_source=fresh_source.price_authority,
                    fixed_lot_contexts=(cold_context,),_fixed_lot_cold_source=fresh_source,_cold_recovery_context_sink=cold_recoveries)
                if ack_failure in {'partial','lost'}:
                    from src.trading_runtime.fixed_structural_lot_cold_recovery import (
                        require_cold_recovery_context,FixedStructuralLotColdRecoveryContext,_issue_graph,_start_walk,_advance_walk,
                        _accept_verified_commit,_VerifiedFrontierSeal)
                    assert len(cold_recoveries)==(1 if ack_failure=='partial' else 2)
                    for batch,proof in cold_recoveries:
                        require_cold_recovery_context(proof,run_id=source.run_id,batch_id=batch)
                        for bad in (replace(proof),replace(proof,batch_id=str(uuid4())),replace(proof,graph_json='[]')):
                            with pytest.raises(ValueError):require_cold_recovery_context(bad,run_id=source.run_id,batch_id=batch)
                        with pytest.raises(ValueError):owner.require_recovery(proof)
                        with pytest.raises(ValueError):proof.verify_recovery_record(None,None)
                    walk=_start_walk(client,fresh_source,(cold_context,))
                    with pytest.raises(ValueError,match='substituted committed frontier'):
                        _advance_walk(walk,replace(current))
                    with pytest.raises(ValueError,match='full-verifier-issued seal'):
                        _accept_verified_commit(walk,_VerifiedFrontierSeal())
                    with pytest.raises(ValueError,match='genuine independently verified'):
                        _issue_graph(walk,replace(current),batch_id=str(uuid4()),parents=(),actions=(),replies=(),events=())
                    # Reseal actual persisted approval rows: content hashes
                    # alone must not grant foreign Portfolio command authority.
                    import src.trading_runtime.arte_oms_projection as cold_rows
                    from src.trading_runtime.arte_journal_writer import _canonical_typed_content
                    from src.trading_runtime.journal_contract import canonical_json
                    from hashlib import sha256
                    real_rows=cold_rows._rows
                    assert approval_calls
                    args=approval_calls[0]
                    for table,field,value in (
                            ('trading_portfolio_reservation_event_v1','assignment_id','foreign'),
                            ('trading_portfolio_reservation_event_v1','strategy_id','foreign'),
                            ('trading_portfolio_reservation_event_v1','quantity','1'),
                            ('trading_portfolio_decision_v1','requested_quantity','1'),
                            ('trading_portfolio_decision_v1','request_id','foreign'),
                            ('trading_portfolio_decision_v1','policy_id','foreign'),
                            ('trading_event_v1','correlation_id','foreign'),
                            ('trading_event_v1','causation_id','foreign')):
                        def changed_rows(client_arg,sql,*,_table=table,_field=field,_value=value):
                            rows=real_rows(client_arg,sql)
                            if f'FROM arte.{_table} ' in sql:
                                rows=[dict(v) for v in rows]
                                for row in rows:
                                    row[_field]=_value
                                    row['content_hash']=sha256(canonical_json(_canonical_typed_content(
                                        _table,{k:v for k,v in row.items() if k!='content_hash'},stored_utc=True)).encode()).hexdigest()
                            return rows
                        with monkeypatch.context() as guard:
                            guard.setattr(cold_rows,'_rows',changed_rows)
                            with pytest.raises(ValueError,match='Portfolio'):
                                approved_command(*args)
                client.fixed_lot_recovery_contexts=tuple(cold_recoveries)
                cold_journal=BacktestMemoryJournal(run_id=source.run_id,initial_sequence=current.last_sequence)
                cold_writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,
                    journal_profile='backtest_v4',coalesce_batches=False)
                try:
                    cold_publisher=BacktestTypedJournalPublisher(cold_journal,cold_writer,attempt_id=str(uuid4()),
                        run_month=source.session_date.replace(day=1),expected_config=config,
                        initial_sequence=current.last_sequence,prior_batch_id=current.last_batch_id,
                        source_cursor=current.source_cursor)
                    cold_publisher.restore_fixed_structural_lot_source(fresh_source,prefix=current,contexts=(cold_context,))
                    cold_owner=NativeFixedStructuralLotManagement(operation=NativeFixedStructuralLotOperation(fresh_source),
                        publisher=cold_publisher,client=client)
                    cold_runtime=object.__new__(TradingRuntime)
                    cold_runtime.config=runtime.config;cold_runtime.run_id=source.run_id;cold_runtime.journal=cold_journal
                    cold_manager=StrategyOneManagementRunner(runtime=cold_runtime,evidence=CompletedEvidence(),
                        tick_for_ticker=lambda ticker:source.tick)
                    cold_manager.bind_fixed_structural_lot_management(cold_owner)
                    cold_owner.restore(cold_manager,captured)
                    assert cold_manager.capture_state(boundary_ms=boundary+1000)==captured.inherited
                    assert cold_owner.states[key]==owner.states[key]
                    assert cold_owner.states[key] is not owner.states[key]
                    assert cold_owner.entries[key].source is fresh_source
                    assert cold_manager._profit_arm_references=={}
                    assert cold_manager._profit_arm_financials[key]==financial
                finally:cold_writer.close();cold_journal.close()
                # Real selected queue/projector/INSERT/readback/CAS dispatch,
                # using the existing controlled ClickHouse transport and an
                # in-memory Keeper. Installed profile remains the explicit
                # seam above, not a fabricated numbered source certificate.
                from tests.test_arte_typed_insert_dispatch import Keeper,Stat
                from src.trading_runtime.keeper_session import ManagedKeeperSession
                from src.trading_runtime.arte_typed_insert_dispatch import (
                    TypedInsertDispatch,_Gate,_gate_path,_context_receipt_path)
                keeper=Keeper();keeper.add_listener=lambda listener:None
                keeper.connected=True;keeper.client_id=(101,b'fixture')
                keeper.exists=lambda path:keeper.rows.get(path)
                session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
                client.manager_keeper_session=session
                client.fixed_structural_lot_contexts=(context,)
                client.fixed_lot_recovery_contexts=live_recoveries
                client.fixed_structural_lot_profile=SimpleNamespace(operation=owner.operation)
                # This original actor fixture is event-mode. Enter the native
                # completed-bar transport through its public validated API;
                # never rewrite a checkpoint flag. The preceding stable quote
                # lies at the completed bucket's left boundary. No trade/fill
                # is invented by this controlled quote-only source fixture.
                local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
                ms=((local.hour*3600+local.minute*60+local.second)*1000+local.microsecond//1000)
                observed_us=int(at.timestamp()*1_000_000)-100000
                await broker.on_liquidity_bar(dict(ticker=proposal.ticker,resolution_ms=100,
                    bucket_index=ms//100-1,event_count=1,first_event_us=observed_us,last_event_us=observed_us,
                    quote_timestamp_us=observed_us,quote_valid=1,bid_int=123000,ask_int=123100,
                    bid_size=10000,ask_size=10000,price_valid=0,extremes_valid=0,
                    execution_volume=0,execution_price_levels=()),at=at)
                cursor_id=f'{source.session_date.isoformat()}:{boundary+1000}'
                checkpoint_record=journal.append(run_id=source.run_id,category='checkpoint',
                    entity_type='market_boundary',entity_id=cursor_id,event_time=at,
                    payload=dict(session_date=source.session_date.isoformat(),boundary_ms=boundary+1000,
                        market_sequence=20,frame_as_of=None,frame_ticker=None,frame_timeframe=None,frame_sequence=None))
                journal.attach_backtest_progress(checkpoint_record,dict(
                    identity=dict(run_id=source.run_id,mode='backtest'),
                    controller=dict(source_cursor=dict(session_date=source.session_date.isoformat(),
                        boundary_ms=boundary+1000,sequence=20),frame_cursor={},current_time=at.isoformat(),
                        processed_events=20,warmup_events=0,processed_frames=0),
                    runtime=dict(processed_events=20,last_event_time=at.isoformat())))
                # Bootstrap the controlled Keeper at the already verified
                # original prefix. No journal sequence is changed or grafted.
                dispatch=TypedInsertDispatch(keeper);dispatch.initialize_new_run(source.run_id)
                keeper.create(_context_receipt_path(source.run_id),b'1\n'+b'a'*64)
                gate,version=dispatch._read_gate(source.run_id)
                keeper.rows[_gate_path(source.run_id)]=(_Gate('open',0,gate.epoch,0,
                    current.last_sequence,current.last_batch_id,'a'*64,
                    '00000000-0000-0000-0000-000000000000').wire(),Stat(version+1))
                client.typed_insert_dispatch=dispatch
                captures=tuple(replace(v,state_revision=checkpoint_record.sequence)
                    for v in owner.require_checkpoint(captured)[6])
                checkpoint_receipt=await publisher.enqueue_checkpoint(boundary_id=cursor_id,
                    manager_state=captured,fixed_lot_owner=owner,
                    broker_state=(boundary+1000,broker.broker_match_snapshot_state()),
                    oms_observations=oms.capture_observed_broker_states(),portfolio_captures=captures,
                    evidence_state=__import__('src.backend.backtest_strategy_one_evidence',fromlist=['StrategyOneEvidenceState']).StrategyOneEvidenceState(boundary+1000,(),(),()),
                    campaign_ownership=journal.campaign_ownership_snapshot())
                assert checkpoint_receipt.last_sequence==checkpoint_record.sequence
                from src.trading_runtime.fixed_structural_lot_manager_snapshot import selected_manager_head_path
                assert keeper.exists(selected_manager_head_path(source.run_id)) is not None
                assert keeper.exists('/trading/strategy-one-manager-snapshot/v2/'+
                    __import__('hashlib').sha256(source.run_id.encode()).hexdigest()+'/head') is None
                from src.trading_runtime.fixed_structural_lot_manager_snapshot import load_cold_manager_image,require_cold_manager_image
                # Drop all live publication/checkpoint issuance caches. Cold
                # issuance reads complete persisted rows and fresh source only.
                from src.trading_runtime.fixed_structural_lot_manager_snapshot import _PUBLICATIONS
                _PUBLICATIONS.clear();owner._checkpoints.clear();cold_owner._checkpoints.clear()
                owner._recoveries.clear();owner._recovery_records.clear();owner._recovery_batches.clear()
                from src.backend.backtest_fixed_structural_lot_resume import load_persisted_fixed_lot_contexts
                persisted_contexts=load_persisted_fixed_lot_contexts(client,source=fresh_source)
                assert len(persisted_contexts)==1
                assert persisted_contexts[0].record==cold_context.record
                assert persisted_contexts[0].unit==cold_context.unit
                assert persisted_contexts[0] is not cold_context
                cold_context=persisted_contexts[0]
                # Public selected-resume preparation reconstructs contexts
                # exclusively from normalized rows; source/config loaders are
                # the same explicit component seams used by this actor test.
                from src.backend import backtest_fixed_structural_lot_execution as resume_execution
                from src.backend import backtest_strategy_one_execution as inherited_execution
                from src.backend import backtest_fixed_structural_lot_native as native_module
                from src.backend import backtest_strategy_one_candidate_store as resume_candidates
                from src.backend.backtest_fixed_structural_lot_resume import (
                    prepare_fixed_structural_lot_resume,require_fixed_structural_lot_resume)
                selected_market=fresh_source.price_authority.plan.source.market
                selected_candidates=fresh_source.price_authority.plan.source.parent.candidates
                selected_entry=object()
                with monkeypatch.context() as preparation:
                    preparation.setattr(profile_module,'issue_fixed_structural_lot_profile',lambda op:SimpleNamespace(operation=op))
                    preparation.setattr(resume_candidates,'project_candidate_plan',lambda *a,**kw:selected_candidates)
                    preparation.setattr(inherited_execution,'prepare_strategy_one_entry_authorities',
                        lambda **kw:(selected_candidates,None,None,None,None,(),fresh_source.price_authority))
                    preparation.setattr(native_module,'prepare_native_fixed_structural_lot_operation',
                        lambda *a,**kw:NativeFixedStructuralLotOperation(fresh_source))
                    prepared_session=resume_execution.prepare_fixed_structural_lot_session(
                        number=fresh_source._revision,run_id=fresh_source.run_id,session_date=fresh_source.session_date,
                        market=selected_market,candidates=selected_candidates,entry=selected_entry,seeds=object(),
                        through_boundary_ms=boundary+1000,client_factory=lambda:SimpleNamespace(close=lambda:None))
                resume_binding=prepare_fixed_structural_lot_resume(client,session,prepared=prepared_session)
                assert resume_binding.contexts[0].unit==cold_context.unit
                assert resume_binding.prefix(client,fresh_source.run_id).last_sequence==checkpoint_record.sequence
                if ack_failure is None:
                    from src.backend.backtest_v4_running_recovery import load_v4_running_recovery_evidence
                    from src.trading_runtime.strategy_one_broker_match_snapshot import ManagedBrokerMatchHeadReader
                    from src.trading_runtime.strategy_one_evidence_snapshot import ManagedEvidenceSnapshotHeadReader
                    from src.trading_runtime.strategy_one_campaign_snapshot import ManagedCampaignSnapshotHeadReader
                    from src.trading_runtime.strategy_one_oms_observation_snapshot import ManagedOmsObservationHeadReader
                    # Explicit controlled canonical-market transport: the
                    # exact completed bucket consumed above is returned for
                    # the genuine bounded quote recovery query.
                    import json
                    class CompletedQuoteTransport:
                        def execute(self,sql):
                            from src.backend.backtest_market_data import assert_select_only
                            assert_select_only(sql)
                            assert 'FROM arte.liquidity_100ms_v1 ' in sql
                            return json.dumps(dict(session_date=source.session_date.isoformat(),ticker=proposal.ticker,
                                bucket_index=ms//100-1,event_count=1,last_event_us=observed_us,
                                quote_timestamp_us=observed_us,quote_valid=1,bid_int=123000,ask_int=123100,
                                bid_size=10000,ask_size=10000))
                    broker_unit=replace(selected_market.units[0],stage='broker_100ms',
                        attempt_id=fresh_source.quotes[0].broker_attempt_id)
                    recovery_market=replace(selected_market,units=tuple(v for v in selected_market.units
                        if v.stage!='broker_100ms')+(broker_unit,))
                    recovery=load_v4_running_recovery_evidence(client,run_id=source.run_id,account_ids=(account,),
                        manager_keeper=None,broker_keeper=ManagedBrokerMatchHeadReader(session),
                        evidence_keeper=ManagedEvidenceSnapshotHeadReader(session),
                        campaign_keeper=ManagedCampaignSnapshotHeadReader(session),
                        oms_observation_keeper=ManagedOmsObservationHeadReader(session),
                        market_client=CompletedQuoteTransport(),market_plan=recovery_market,
                        strategy_number=request.revision,fixed_lot_resume=resume_binding)
                    assert recovery.prefix==resume_binding.prefix(client,source.run_id)
                    assert recovery.manager==resume_binding.image.inherited
                    # Actual ReplayRunService caller orchestration. The
                    # installed configuration/credentials/lease/bootstrap
                    # boundary remains an explicit component fixture seam;
                    # persisted selected issuance, all recovery families and
                    # full actor image construction run without substitutes.
                    from src.backend import replay_run_service as service_module
                    from src.backend import backtest_fixed_journal_bootstrap as bootstrap_module
                    from src.backend import backtest_fixed_running_anchor as anchor_module
                    from src.backend import backtest_fixed_market_authority as market_authority_module
                    from src.backend import backtest_v4_run_context as run_context_module
                    from src.backend import backtest_journal_clickhouse as source_code_module
                    from src.backend import backtest_v3_clients as market_clients_module
                    from src.backend import backtest_v4_keeper_lease as lease_module
                    from src.backend import backtest_fixed_structural_lot_configuration as selected_configuration_module
                    from src.trading_runtime import keeper_session as keeper_module
                    from src.trading_runtime.runtime import RunMode
                    from unittest.mock import AsyncMock
                    from pathlib import Path
                    configuration=dict(strategy=dict(strategy_id=request.strategy_id,strategy_number=request.revision))
                    definition=SimpleNamespace(mode=RunMode.BACKTEST,session_date=source.session_date,
                        session_start=request.intent.event_time,configuration_revision=dict(payload=configuration,content_hash=source.selected_configuration_hash))
                    plans=SimpleNamespace(market=recovery_market,candidates=selected_candidates,
                        entry=selected_entry,seeds=object(),execution_market=recovery_market)
                    anchor=anchor_module.load_fixed_running_prefix_anchor(client,run_id=source.run_id,
                        plan=recovery_market,configuration_hash=source.selected_configuration_hash,account_ids=(account,),
                        journal_profile='backtest_v4',fixed_lot_resume=resume_binding)
                    assembled=[]
                    class ClientBoundary:
                        def __init__(self,raw):self.raw=raw;self.closed=False
                        def __getattr__(self,name):return getattr(self.raw,name)
                        def close(self):self.closed=True
                    def controller_init(self,definition,*,run_id,**options):
                        self.definition=definition;self.run_id=run_id
                        self._fixed_market_plan=recovery_market;self._fixed_price_plan=None
                        self._data_authority={};self._stage_timings={}
                        self.actor_image=options.get('fixed_v4_runtime_image')
                    with monkeypatch.context() as service_seams:
                        service_seams.setattr(service_module,'_backtest_launch_blocker',lambda d:None)
                        service_seams.setattr(service_module.ReplayRunController,'__init__',controller_init)
                        service_seams.setattr(service_module.ReplayRunController,'_fixed_strategy_one_plans',AsyncMock(return_value=plans))
                        service_seams.setattr(service_module.ReplayRunController,'_fixed_through_boundary_ms',lambda self:boundary+1000)
                        service_seams.setattr(service_module.ReplayRunController,'_attach_resumed_fixed_v4_assembly',
                            lambda self,assembly,**kw:assembled.append((self,assembly,kw)))
                        service_seams.setattr(selected_configuration_module,'declared_fixed_structural_lot_contract',lambda n:object())
                        service_seams.setattr(resume_execution,'prepare_fixed_structural_lot_session',lambda **kw:prepared_session)
                        service_seams.setattr(run_context_module,'historical_strategy_one_portfolio_profiles',lambda cfg:((profile,),()))
                        service_seams.setattr(source_code_module,'backtest_code_hash',lambda *a:'b'*64)
                        keeper.remove_listener=lambda listener:None
                        keeper.stop=lambda:None;keeper.close=lambda:None
                        def fresh_keeper_session():
                            value=ManagedKeeperSession(keeper);value._on_state('CONNECTED');return value
                        service_seams.setattr(keeper_module,'open_workstation_keeper_session',fresh_keeper_session)
                        service_seams.setattr(lease_module.BacktestV4KeeperLease,'acquire',lambda *a,**kw:SimpleNamespace(release=lambda:None))
                        service_seams.setattr(writer_module,'backtest_v4_operator_client_from_env',lambda **kw:ClientBoundary(client))
                        service_seams.setattr(writer_module,'backtest_v4_journal_client_from_env',lambda **kw:ClientBoundary(client))
                        service_seams.setattr(market_clients_module,'v3_client',lambda *a:ClientBoundary(CompletedQuoteTransport()))
                        service_seams.setattr(anchor_module,'cold_verify_v4_resume_anchor',lambda *a,**kw:anchor)
                        service_seams.setattr(market_authority_module,'load_committed_fixed_market_authority',lambda *a,**kw:plans.market)
                        service_seams.setattr(bootstrap_module,'prepare_fixed_v4_journal_token',lambda *a,**kw:object())
                        service_seams.setattr(bootstrap_module,'assemble_resumed_fixed_v4_journal',
                            lambda *a,**kw:(SimpleNamespace(writer=SimpleNamespace(close=lambda:None),journal=SimpleNamespace(close=lambda:None)),anchor))
                        service=object.__new__(service_module.ReplayRunService)
                        service.runtime_root=Path('D:/TradingML/runtimes/strategy-optimization-20261005')
                        resumed=await service._prepare_typed_v4_resume(source.run_id,definition)
                        assert resumed._fixed_structural_lot_session is prepared_session
                        assert resumed.actor_image.manager==resume_binding.image.inherited
                        assert resumed.actor_image.anchor==anchor and assembled[0][0] is resumed
                        from src.trading_runtime.fixed_structural_lot_entry_schema import ENTRY,LOT
                        for invalid in ('missing_lot','duplicate_source','stored_event','foreign_head','financial_cash'):
                            if invalid=='foreign_head':
                                head_path=selected_manager_head_path(source.run_id)
                                saved_head=keeper.rows[head_path]
                                fields=saved_head[0].decode().split('\n');fields[2]=str(checkpoint_record.sequence+1)
                                keeper.rows[head_path]=('\n'.join(fields).encode(),saved_head[1])
                                changed_table=None
                            else:
                                changed_table={'missing_lot':LOT.name,'duplicate_source':ENTRY.name,
                                    'stored_event':'trading_event_v1','financial_cash':'trading_strategy_one_broker_match_account_v5'}[invalid]
                                prior_table=client.tables[changed_table]
                                mutated=[dict(v) for v in prior_table]
                                if invalid=='missing_lot':mutated=mutated[:-1]
                                elif invalid=='duplicate_source':mutated.append(dict(mutated[0]))
                                elif invalid=='stored_event':
                                    next(v for v in mutated if v['record_id']==cold_context.record.record_id)['content_hash']='f'*64
                                else:
                                    # Persisted same-cursor financial content,
                                    # not a caller-provided manager quantity.
                                    from src.trading_runtime.strategy_one_broker_match_snapshot import float64_bits
                                    mutated[0]['cash_f64_bits']=float64_bits(9999.,'cash')
                                client.tables[changed_table]=mutated
                            try:
                                with pytest.raises((ValueError,RuntimeError)):
                                    await service._prepare_typed_v4_resume(source.run_id,definition)
                                assert len(assembled)==1
                            finally:
                                if changed_table is None:keeper.rows[head_path]=saved_head
                                else:client.tables[changed_table]=prior_table
                    from src.trading_runtime.strategy_one_evidence_snapshot import load_attested_evidence_snapshot
                    from src.trading_runtime.strategy_one_broker_match_snapshot import load_attested_broker_match_snapshot
                    from src.trading_runtime.strategy_one_campaign_snapshot import load_attested_campaign_snapshot
                    from src.trading_runtime.strategy_one_oms_observation_snapshot import load_attested_oms_observation_snapshot
                    for loader,reader in ((load_attested_evidence_snapshot,ManagedEvidenceSnapshotHeadReader(session)),
                            (load_attested_broker_match_snapshot,ManagedBrokerMatchHeadReader(session)),
                            (load_attested_campaign_snapshot,ManagedCampaignSnapshotHeadReader(session)),
                            (load_attested_oms_observation_snapshot,ManagedOmsObservationHeadReader(session))):
                        for invalid in ('conflicting_price','copied_binding','foreign_run'):
                            options=dict(run_id=source.run_id,checkpoint_sequence=checkpoint_record.sequence,
                                fixed_lot_resume=resume_binding)
                            if invalid=='conflicting_price':options['first_price_source']=object()
                            elif invalid=='copied_binding':options['fixed_lot_resume']=replace(resume_binding)
                            else:options['run_id']=str(uuid4())
                            with pytest.raises(ValueError):loader(client,reader,**options)
                with pytest.raises(ValueError):require_fixed_structural_lot_resume(replace(resume_binding),fresh_source.run_id)
                with pytest.raises(ValueError):require_fixed_structural_lot_resume(resume_binding,str(uuid4()))
                if ack_failure is None:
                    from src.trading_runtime.fixed_structural_lot_entry_schema import ENTRY,LOT,NODE
                    from src.trading_runtime.arte_journal_writer import _canonical_typed_content
                    from hashlib import sha256
                    from src.trading_runtime.journal_contract import canonical_json
                    for kind in ('missing_lot','duplicate_root','source_drift','event_hash'):
                        table={'missing_lot':LOT.name,'duplicate_root':ENTRY.name,
                            'source_drift':ENTRY.name,'event_hash':'trading_event_v1'}[kind]
                        previous=client.tables[table]
                        altered=[dict(v) for v in previous]
                        if kind=='missing_lot':altered=altered[:-1]
                        elif kind=='duplicate_root':altered.append(dict(altered[0]))
                        elif kind=='source_drift':
                            altered[0]['source_checkpoint_hash']='f'*64
                            altered[0]['content_hash']=sha256(canonical_json(_canonical_typed_content(table,
                                {k:v for k,v in altered[0].items() if k!='content_hash'},stored_utc=True)).encode()).hexdigest()
                        else:
                            row=next(v for v in altered if v['record_id']==cold_context.record.record_id)
                            row['content_hash']='f'*64
                        client.tables[table]=altered
                        try:
                            with pytest.raises((ValueError,RuntimeError)):
                                prepare_fixed_structural_lot_resume(client,session,prepared=prepared_session)
                        finally:client.tables[table]=previous
                cold_image=load_cold_manager_image(client,session,source=fresh_source,fixed_lot_contexts=(cold_context,))
                require_cold_manager_image(cold_image,source=fresh_source)
                from src.trading_runtime.strategy_one_management_snapshot import _project_manager_snapshot_scalar
                from src.trading_runtime.strategy_one_protection_snapshot import _serialize_protection_snapshot
                def normalized(state):
                    protection=_serialize_protection_snapshot(run_id=source.run_id,session_date=source.session_date,
                        checkpoint_sequence=cold_image.sequence,boundary_ms=state.boundary_ms,
                        positions={(k[0],k[2],k[1]):v for k,v in state.positions})
                    return _project_manager_snapshot_scalar(run_id=source.run_id,session_date=source.session_date,
                        checkpoint_sequence=cold_image.sequence,state=state,_protection_rows=protection)
                assert normalized(cold_image.inherited)==normalized(captured.inherited)
                assert cold_image.inherited.submitted==captured.inherited.submitted
                assert cold_image.source is fresh_source
                assert float(cold_image.selected_positions[0][1].root['stop'])==earned_stop
                for bad in (replace(cold_image),replace(cold_image,sequence=cold_image.sequence+1)):
                    with pytest.raises(ValueError):require_cold_manager_image(bad,source=fresh_source)
                with pytest.raises(ValueError):require_manager_publication(cold_image)
                with pytest.raises(ValueError):load_cold_manager_image(client,session,
                    source=fresh_source,fixed_lot_contexts=(context,))
                with pytest.raises(ValueError):load_cold_manager_image(client,session,
                    source=fresh_source,fixed_lot_contexts=())
                # Required financial commit omission remains fail-closed,
                # preserving the existing root and every original journal row.
                saved=client.tables['trading_portfolio_snapshot_commit_v1']
                client.tables['trading_portfolio_snapshot_commit_v1']=[]
                try:
                    with pytest.raises((ValueError,RuntimeError)):
                        load_cold_manager_image(client,session,source=fresh_source,fixed_lot_contexts=(cold_context,))
                finally:client.tables['trading_portfolio_snapshot_commit_v1']=saved
                final_cold_recoveries=[]
                prefix=commits.load_verified_v4_prefix(client,source.run_id,first_price_source=fresh_source.price_authority,
                    fixed_lot_contexts=(cold_context,),_fixed_lot_cold_source=fresh_source,_cold_recovery_context_sink=final_cold_recoveries)
                state,=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset({account}),
                    strategy_identity=(request.strategy_id,request.revision),require_tactic=True,fixed_lot_contexts=(cold_context,))
                history=load_complete_typed_protection_history(client,prefix,fixed_lot_contexts=(cold_context,))
                reservations=load_committed_oms_admission_page(client,prefix,(state,))
                decisions=load_committed_oms_decision_page(client,prefix,(state,),reservations)
                reservation=reservations[state.sequence];decision=decisions[state.sequence]
                restored_orders=reconstruct_strategy_one_oms_lineage(state,original,history,
                    admission_reservation=reservation,admission_decision=decision)
                restored_intent,_=_approved_strategy_one_oms_intent(state,original,history,reservation,decision)
            else:earned_stop=12.1
            # Fresh broker and OMS; cold reads below use normalized recovered
            # fields only. The journal proxy is a controlled adapter fixture,
            # not an installed durable runtime recovery implementation.
            checkpoint=broker.checkpoint_state()
            old_ledger=await broker.account_ledger(account)
            old_orders=await broker.live_orders()
            await oms.close()
            fresh_broker=SimulatedBrokerAdapter([account],SimulationConfig(initial_cash=10000.),
                mode=TradingMode.BACKTEST,initial_time=at)
            await fresh_broker.initialize();fresh_broker.restore_checkpoint_state(checkpoint)
            payload={**state.group,'intent':restored_intent.payload(),
                'orders':[o.to_cpapi() for o in restored_orders],
                'batch_lengths':[state.order_batch_ordinals.count(i) for i in sorted(set(state.order_batch_ordinals))],
                'order_slice_ids':state.order_slice_ids,
                'broker_order_ids':[b['broker_order_id'] for b in state.broker_bindings],
                'broker_order_roles':{b['broker_order_id']:b['role'] for b in state.broker_bindings},
                'broker_order_slices':{b['broker_order_id']:b['slice_id'] for b in state.broker_bindings},
                'broker_order_request_indexes':{b['broker_order_id']:b['request_index'] for b in state.broker_bindings},
                'filled_by_broker_order':{b['broker_order_id']:float(b['filled_quantity']) for b in state.broker_bindings},
                'terminal_broker_order_ids':[b['broker_order_id'] for b in state.broker_bindings if b['terminal']]}
            from src.trading_runtime.arte_journal_reader import _journal_instant
            for clock_name in ('created_at','updated_at','submitted_at'):
                if payload.get(clock_name) is not None:
                    payload[clock_name]=_journal_instant(payload[clock_name])
            cold_sink=BacktestMemoryJournal(run_id=source.run_id)
            class NormalizedRecoveryJournal:
                def protection_records(self,run_id):
                    assert run_id==prefix.run_id
                    return history.records
                def order_management_states(self):
                    return [dict(run_id=prefix.run_id,group_id=state.group['group_id'],account_id=account,state=payload)]
                def append(self,*args,**kwargs):return cold_sink.append(*args,**kwargs)
                def save_order_management_state(self,*args,**kwargs):
                    return cold_sink.save_order_management_state(*args,**kwargs)
                def portfolio_reservation(self,account_id,reservation_id):
                    assert account_id==account and reservation_id==reservation['reservation_id']
                    return dict(reservation)
            fresh_risk=RiskAuthority();await fresh_risk.prime(fresh_broker,[account])
            recovered_oms=OrderManagementEngine(broker=fresh_broker,
                planner=lambda intent,account_id,event:planner.plan(account_id=account_id,intent=intent,event=event),
                risk=fresh_risk,journal=NormalizedRecoveryJournal(),run_id=source.run_id,
                strategy_id=request.strategy_id,strategy_revision=request.revision,causal_execution_clock=True)
            try:
                recovered_oms.on_market_snapshot(ExecutionMarketSnapshot(proposal.ticker,12.2,12.21,source.tick,at,'qmd-history'))
                recovered=await recovered_oms.recover()
                assert len(recovered)==1
                cold_group=recovered_oms._groups[recovered[0].group_id]
                assert cold_group is not group
                assert [s.profit_target_price for s in cold_group.intent.protection_profile.slices]==[12.,13.,14.]
                assert [s.stop.price for s in cold_group.intent.protection_profile.slices]==[proposal.initial_stop,earned_stop,earned_stop]
                assert await fresh_broker.account_ledger(account)==old_ledger
                assert len(await fresh_broker.live_orders())==len(old_orders)
                assert (await recovered_oms.reconcile_protection(cold_group))['actions']==[]
                if queued:
                    # Actual fresh financial owners + normalized cold source,
                    # then the selected native bootstrap (no prior manager
                    # capture, requested ACK bool or live ownership cache).
                    from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
                    from src.trading_runtime.strategy_engine import StrategyAssignment
                    from src.backend.backtest_fixed_journal_bootstrap import restore_fixed_structural_lot_native_manager
                    recovery=recover_portfolio_engine_state(client,run_id=source.run_id,profiles=(profile,),
                        state_revisions={account:cold_image.sequence},cutoff_at=at)
                    bootstrap_journal=BacktestMemoryJournal(run_id=source.run_id,initial_sequence=prefix.last_sequence)
                    bootstrap_writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,
                        journal_profile='backtest_v4',coalesce_batches=False)
                    try:
                        bootstrap_publisher=BacktestTypedJournalPublisher(bootstrap_journal,bootstrap_writer,
                            attempt_id=str(uuid4()),run_month=source.session_date.replace(day=1),expected_config=config,
                            initial_sequence=prefix.last_sequence,prior_batch_id=prefix.last_batch_id,source_cursor=prefix.source_cursor)
                        bootstrap_publisher.restore_fixed_structural_lot_source(fresh_source,prefix=prefix,contexts=(cold_context,))
                        fresh_portfolio=PortfolioManagementEngine((profile,),journal=bootstrap_journal,run_id=source.run_id,
                            strategy_id=request.strategy_id,strategy_revision=request.revision,typed_recovery=recovery,event_clock=lambda:at)
                        assignment=StrategyAssignment(proposal.assignment_id,request.strategy_id,request.revision,
                            account,proposal.ticker,1,AssignmentStatus.MANAGING,StrategyPermissions(enter=True),{})
                        bootstrap_runtime=object.__new__(TradingRuntime)
                        bootstrap_runtime.config=runtime.config;bootstrap_runtime.run_id=source.run_id
                        bootstrap_runtime.journal=bootstrap_journal;bootstrap_runtime.portfolio=fresh_portfolio
                        bootstrap_oms=OrderManagementEngine(broker=fresh_broker,
                            planner=lambda intent,account_id,event:planner.plan(account_id=account_id,intent=intent,event=event),
                            risk=fresh_risk,journal=bootstrap_journal,run_id=source.run_id,
                            strategy_id=request.strategy_id,strategy_revision=request.revision,causal_execution_clock=True)
                        bootstrap_runtime.broker=fresh_broker;bootstrap_runtime.order_manager=bootstrap_oms
                        bootstrap_runtime.strategy=SimpleNamespace(assignments=lambda:(assignment,))
                        bootstrap_owner=NativeFixedStructuralLotManagement(operation=NativeFixedStructuralLotOperation(fresh_source),
                            publisher=bootstrap_publisher,client=client)
                        bootstrap_manager=StrategyOneManagementRunner(runtime=bootstrap_runtime,evidence=CompletedEvidence(),
                            tick_for_ticker=lambda ticker:source.tick)
                        bootstrap_manager.bind_fixed_structural_lot_management(bootstrap_owner)
                        image=await restore_fixed_structural_lot_native_manager(bootstrap_owner,bootstrap_manager,session)
                        assert normalized(bootstrap_manager.capture_state(boundary_ms=image.inherited.boundary_ms))==normalized(image.inherited)
                        assert bootstrap_owner.states[key].protection.stop==earned_stop
                        assert bootstrap_manager._profit_arm_references=={}
                        assert bootstrap_owner.entries[key].source is fresh_source
                        with pytest.raises(ValueError,match='fresh actual manager'):
                            await restore_fixed_structural_lot_native_manager(bootstrap_owner,bootstrap_manager,session)
                    finally:
                        if 'bootstrap_oms' in locals():await bootstrap_oms.close();await asyncio.sleep(0)
                        bootstrap_writer.close();bootstrap_journal.close()
            finally:
                await recovered_oms.close();await asyncio.sleep(0);cold_sink.close()



        finally:
            if writer is not None:writer.close()
            await oms.close();await asyncio.sleep(0);journal.close()
    asyncio.run(run())
