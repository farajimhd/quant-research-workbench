"""Actual flat actors and fenced controlled transport; installation is a seam."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest


def test_certified_empty_horizon_flat_terminal_and_fresh_cold_actors(monkeypatch):
    async def run():
        from tests import test_fixed_structural_lot_empty as fixture
        from tests.test_fixed_structural_lot_native import declarations,cert
        from src.trading_runtime.strategy_registry import numbered_strategy
        from src.trading_runtime.fixed_structural_lot_release import derive_fixed_structural_lot_release
        from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
        def registered_declarations():
            parent,_,_,parent_release=declarations();release=numbered_strategy(77)
            own=derive_fixed_structural_lot_release(parent,parent_release=parent_release,release=release,
                policy=FixedStructuralLotPolicy().payload(),approved_code_commit='a'*40,
                approved_code_fingerprint='b'*64,approval_reference='controlled noninstalled empty lifecycle')['payload']
            return parent,cert(own),release,parent_release
        monkeypatch.setattr(fixture,'declarations',registered_declarations)
        source,_,_=fixture.source_fixture(monkeypatch)
        from src.backend.backtest_fixed_structural_lot_execution import (
            prepare_fixed_structural_lot_session,bind_fixed_structural_lot_manager)
        def source_client():
            reader=fixture.EmptyReader();reader.close=lambda:None
            return reader
        prepared=prepare_fixed_structural_lot_session(number=77,run_id=source.run_id,
            session_date=source.session_date,market=source.market,candidates=source.candidates,
            entry=None,seeds=None,through_boundary_ms=source.through_boundary_ms,client_factory=source_client)
        source=prepared.operation.source
        assert prepared.entry_authorities[1:]==(None,None,None,None,(),None)
        account='DU-EMPTY'
        at=(datetime.combine(source.session_date,datetime.min.time(),ZoneInfo('America/New_York'))
            +timedelta(hours=4,milliseconds=source.through_boundary_ms)).astimezone(timezone.utc)
        from src.backend.backtest_journal_memory import BacktestMemoryJournal
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
        from src.trading_runtime.domain import TradingMode
        from src.trading_runtime.risk import RiskAuthority
        from src.trading_runtime.order_management import OrderManagementEngine
        from src.trading_runtime.portfolio import PortfolioManagementEngine,PortfolioAccountProfile,PortfolioPolicy
        from src.trading_runtime.runtime import TradingRuntime
        from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
        from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
        from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
        from tests.fixed_structural_lot_transport_fixture import ExactDecisionTransport
        from tests.test_arte_journal_commit_v4 import attached_v4_client
        from tests.test_arte_journal_writer import run_row,run_context
        from src.trading_runtime import arte_journal_writer as writer_module
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from src.trading_runtime import fixed_structural_lot_profile as profile_module
        from src.trading_runtime.arte_journal_writer import publish_typed_run,publish_typed_run_context
        journal=BacktestMemoryJournal(run_id=source.run_id)
        broker=SimulatedBrokerAdapter([account],SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
        await broker.initialize()
        risk=RiskAuthority();await risk.prime(broker,[account])
        profile=PortfolioAccountProfile('cash',account,'backtest','simulated',PortfolioPolicy(allow_outside_rth=True))
        config=dict(mode='backtest',strategy_id=source._strategy_id,strategy_revision=source._revision,
            parent_configuration_hash=source.parent_payload_hash,selected_configuration_hash=source.selected_configuration_hash)
        client=ExactDecisionTransport()
        publish_typed_run(client,{**run_row(),'run_id':source.run_id,
            'run_month':source.session_date.replace(day=1).isoformat(),'session_date':source.session_date.isoformat(),
            'configuration_hash':source.selected_configuration_hash,'started_at':at.isoformat()})
        publish_typed_run_context(client,run_id=source.run_id,
            config={**run_context(),'strategy_id':source._strategy_id,'strategy_revision':source._revision,
                'anchor_date':source.session_date.isoformat()},account_ids=(account,))
        client=attached_v4_client(client)
        client.fixed_structural_lot_profile=prepared.profile
        # Only uninstalled infrastructure is controlled. No actor, financial
        # capture, full V4 prefix, selected projection or cold decoder is stubbed.
        monkeypatch.setattr(writer_module,'storage_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'journal_permission_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'_verify_run_identity',lambda *a:dict(mode='backtest',account_ids=(account,)))
        writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,journal_profile='backtest_v4',coalesce_batches=False)
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),
            run_month=source.session_date.replace(day=1),expected_config=config)
        publisher.bind_fixed_structural_lot_source(source)
        portfolio=PortfolioManagementEngine((profile,),journal=journal,run_id=source.run_id,
            strategy_id=source._strategy_id,strategy_revision=source._revision,event_clock=lambda:at)
        await portfolio.synchronize(broker)
        def no_plan(*args,**kwargs):raise AssertionError('Empty horizon must not create an order')
        from src.trading_runtime.runtime import RunConfig,RunMode
        runtime=TradingRuntime(RunConfig(RunMode.BACKTEST,source._strategy_id,source._revision,
            (account,),source.session_date,run_id=source.run_id,safety_supervisor_enabled=False,
            write_progress_checkpoints=False),broker,
            SimpleNamespace(strategy_id=source._strategy_id,revision=source._revision,
                automatic=True,assignments=lambda:()),journal,risk=risk,
            portfolio=portfolio,intent_planner=SimpleNamespace(plan=no_plan))
        oms=runtime.order_manager
        runtime.last_event_time=at
        prepared.bind_runtime(runtime)
        class NoEvidence:
            async def management_evidence(self,*a,**k):raise AssertionError('Empty horizon needs no price evidence')
        manager=StrategyOneManagementRunner(runtime=runtime,evidence=NoEvidence(),tick_for_ticker=no_plan)
        owner=bind_fixed_structural_lot_manager(prepared,manager=manager,publisher=publisher,session=source.session_date)
        with pytest.raises(ValueError):prepared.operation.request(object())
        with pytest.raises(ValueError):prepared.bind_runtime(runtime)
        client.fixed_structural_lot_contexts=()
        try:
            from tests.test_arte_typed_insert_dispatch import Keeper
            from src.trading_runtime.keeper_session import ManagedKeeperSession
            from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch,_context_receipt_path
            keeper=Keeper();keeper.add_listener=lambda listener:None;keeper.connected=True;keeper.client_id=(101,b'fixture')
            keeper.exists=lambda path:keeper.rows.get(path)
            session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
            client.manager_keeper_session=session
            dispatch=TypedInsertDispatch(keeper);dispatch.initialize_new_run(source.run_id)
            keeper.create(_context_receipt_path(source.run_id),b'1\n'+b'a'*64)
            client.typed_insert_dispatch=dispatch
            # Native fixed-bar broker starts without fabricated market rows.
            initial=journal.append(run_id=source.run_id,category='checkpoint',entity_type='market_boundary',
                entity_id=f'{source.session_date.isoformat()}:100',event_time=at-timedelta(milliseconds=source.through_boundary_ms-100),
                payload=dict(session_date=source.session_date.isoformat(),boundary_ms=100,market_sequence=0,
                    frame_as_of=None,frame_ticker=None,frame_timeframe=None,frame_sequence=None))
            await publisher._drain(target_sequence=initial.sequence)
            captured=owner.capture(manager,boundary_ms=source.through_boundary_ms)
            cursor=f'{source.session_date.isoformat()}:{source.through_boundary_ms}'
            record=journal.append(run_id=source.run_id,category='checkpoint',entity_type='market_boundary',
                entity_id=cursor,event_time=at,payload=dict(session_date=source.session_date.isoformat(),
                    boundary_ms=source.through_boundary_ms,market_sequence=0,frame_as_of=None,frame_ticker=None,
                    frame_timeframe=None,frame_sequence=None))
            captures=tuple(replace(v,state_revision=record.sequence) for v in owner.require_checkpoint(captured)[6])
            receipt=await publisher.fence_checkpoint(boundary_id=cursor,manager_state=captured,fixed_lot_owner=owner,
                broker_state=(source.through_boundary_ms,broker.broker_match_snapshot_state()),
                oms_observations=oms.capture_observed_broker_states(),portfolio_captures=captures)
            assert receipt.last_sequence==record.sequence
            assert captured.selected_positions==captured.financials==()
            assert not await broker.live_orders() and not await broker.positions(account)
            ledger=await broker.account_ledger(account)
            assert ledger.cashbalance==ledger.netliquidationvalue==10000.
            assert ledger.realizedpnl==ledger.unrealizedpnl==0.
            assert not portfolio.reservations and not oms._groups
            from src.trading_runtime.fixed_structural_lot_manager_snapshot import load_cold_manager_image,_PUBLICATIONS
            _PUBLICATIONS.clear();owner._checkpoints.clear()
            # The genuine factory must certify the same run identity afresh.
            # Never copy/reseal the issued empty object.
            from src.backend import backtest_fixed_structural_lot_empty as empty
            reader=fixture.EmptyReader()
            fresh=empty.prepare_empty_fixed_structural_lot_source(reader,number=77,run_id=source.run_id,
                session_date=source.session_date,market=source.market,candidates=source.candidates,
                through_boundary_ms=source.through_boundary_ms)
            image=load_cold_manager_image(client,session,source=fresh,fixed_lot_contexts=())
            assert image.selected_positions==() and image.inherited.positions==image.inherited.submitted==()
            assert image.sequence==record.sequence and image.source is fresh
            from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
            from src.trading_runtime.strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot
            from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
            from src.backend.backtest_fixed_journal_bootstrap import restore_fixed_structural_lot_native_manager
            prefix=load_verified_v4_prefix(client,source.run_id)
            recovered=recover_portfolio_engine_state(client,run_id=source.run_id,profiles=(profile,),
                state_revisions={account:image.sequence},cutoff_at=at)
            cold_broker=SimulatedBrokerAdapter([account],SimulationConfig(initial_cash=10000.),
                mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
            await cold_broker.initialize()
            rows=load_unattested_broker_match_snapshot(client,run_id=source.run_id,checkpoint_sequence=image.sequence)
            cold_broker.restore_checkpoint_state(reconstruct_broker_match_state(rows,requests_by_broker_id={},quotes={}))
            cold_journal=BacktestMemoryJournal(run_id=source.run_id,initial_sequence=prefix.last_sequence)
            cold_writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,journal_profile='backtest_v4',coalesce_batches=False)
            cold_publisher=BacktestTypedJournalPublisher(cold_journal,cold_writer,attempt_id=str(uuid4()),
                run_month=source.session_date.replace(day=1),expected_config=config,initial_sequence=prefix.last_sequence,
                prior_batch_id=prefix.last_batch_id,source_cursor=prefix.source_cursor)
            cold_publisher.restore_fixed_structural_lot_source(fresh,prefix=prefix,contexts=())
            cold_portfolio=PortfolioManagementEngine((profile,),journal=cold_journal,run_id=source.run_id,
                strategy_id=source._strategy_id,strategy_revision=source._revision,typed_recovery=recovered,event_clock=lambda:at)
            cold_risk=RiskAuthority();await cold_risk.prime(cold_broker,[account])
            cold_oms=OrderManagementEngine(broker=cold_broker,planner=no_plan,risk=cold_risk,journal=cold_journal,
                run_id=source.run_id,strategy_id=source._strategy_id,strategy_revision=source._revision,causal_execution_clock=True)
            # Bootstrap receives entirely new actors and source issuance; no
            # previous manager, journal cache or broker checkpoint is delegated.
            cold_runtime=object.__new__(TradingRuntime)
            cold_runtime.config=runtime.config;cold_runtime.run_id=source.run_id;cold_runtime.journal=cold_journal
            cold_runtime.portfolio=cold_portfolio;cold_runtime.broker=cold_broker;cold_runtime.order_manager=cold_oms
            cold_runtime.strategy=SimpleNamespace(assignments=lambda:())
            cold_owner=NativeFixedStructuralLotManagement(operation=NativeFixedStructuralLotOperation(fresh),
                publisher=cold_publisher,client=client)
            cold_manager=StrategyOneManagementRunner(runtime=cold_runtime,evidence=NoEvidence(),tick_for_ticker=no_plan)
            cold_manager.bind_fixed_structural_lot_management(cold_owner)
            try:
                cold_broker._cash[account]=9999.
                with pytest.raises(ValueError,match='complete actual broker checkpoint differs'):
                    await restore_fixed_structural_lot_native_manager(cold_owner,cold_manager,session)
                assert cold_manager._positions==cold_manager._submitted=={}
                cold_broker._cash[account]=10000.
                restored=await restore_fixed_structural_lot_native_manager(cold_owner,cold_manager,session)
                assert restored.inherited==image.inherited
                assert cold_manager._positions==cold_manager._submitted=={}
                assert not cold_owner.states and not cold_portfolio.reservations and not cold_oms._groups
                assert (await cold_broker.account_ledger(account)).cashbalance==10000.
                assert not await cold_broker.live_orders() and not await cold_broker.positions(account)
            finally:
                cold_writer.close();await cold_oms.close();await asyncio.sleep(0);cold_journal.close()
            # Actual runtime terminal producer, then original lifecycle-last
            # V4 terminal writer. No synthetic sequence or terminal row graft.
            await runtime.finish('completed')
            terminal_sequence=journal.latest_sequence(source.run_id)
            terminal_capture=(portfolio.capture_recovery_snapshot(account,state_revision=terminal_sequence,snapshot_at=at),)
            terminal=await publisher.enqueue_terminal(terminal_capture)
            assert terminal.last_sequence==terminal_sequence
            terminal_prefix=load_verified_v4_prefix(client,source.run_id)
            assert terminal_prefix.last_sequence==terminal_sequence
            assert terminal_prefix.status=='completed'
            accounts=client.tables['trading_backtest_account_snapshot_v2']
            assert len(accounts)==1 and accounts[0]['account_id']==account
            assert not journal.unfenced_records()
            assert (await broker.account_ledger(account)).cashbalance==10000.
            assert not portfolio.reservations and not oms._groups
        finally:
            writer.close();await oms.close();await asyncio.sleep(0);journal.close()
    asyncio.run(run())
