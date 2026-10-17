"""Sparse dispatch skips need the same exact prepared admission proof on recovery."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import polars as pl
import pytest
from test_columnar_history_publication import prepared_source,Store,publish,consume
from tests.test_backtest_program_inputs import release_for
from tests.test_qmd_columnar_history_transport import response_for
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_signal_source_runtime_bootstrap import source
from tests.test_program_signal_source import executable_contextual_publisher,source_context_for
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_program_inputs import prepare_program_scanner_projection,publish_prepared_program_source_clock
from src.backend.program_policy_recovery import rebuild_program_policy_events,verify_recovered_program_positions,program_policy_fingerprint
from src.backend.qmd_columnar_population import CertifiedPopulationWindow
from src.backend.replay_run_service import ReplayRunController
from src.market_engine import columnar_history_product as history
from src.market_engine.volume_rank_rules import select_volume_universe
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
from src.trading_runtime.execution_policies import ExecutionPolicy,ExecutionEnvelope,ProtectionProfile,ProtectionSlice,StopRule


def test_real_sparse_clock_cold_admission_requires_prepared_rejection(prepared_source,tmp_path):
    store=Store();publish(store,prepared_source);certificate=consume(store,prepared_source);pin=certificate.pin()
    release=release_for(pin);definition,original=definition_and_actor();configuration,scope,_=source()
    scope['session_key']=pin.scope.session_date.isoformat()
    revision={**definition.configuration_revision,'revision_id':scope['configuration_revision'],'payload':{**definition.configuration_revision['payload'],**configuration}}
    definition=replace(definition,session_date=pin.scope.session_date,configuration_revision=revision)
    actor=AssignedProgramStrategy(release,original.assignments(),{'assignment':'listing'})
    controller=ReplayRunController(definition,runtime_root=tmp_path,program_strategy=actor,program_liquidity_resolution_ms=100)
    controller._journal=BacktestMemoryJournal(run_id=controller.run_id)
    publisher=executable_contextual_publisher(source_context_for(controller,scope['source_revision']),scope['catalogs'])
    def post(path,request,timeout):return response_for(certificate,request['first_ordinal'],request['stop_ordinal'],request['field_ids'])
    async def check():
        await controller._initialize_runtime(record_configuration=False,record_lifecycle=False)
        try:
            block=await controller.prepare_program_inputs({'listing':pin},first_ordinal=0,stop_ordinal=200,evaluation_block='origin',concurrency=1,as_of=history.completed_clock(pin.scope,pin.scope.stop_bucket-1),history_post=post)
            rows=block.market.with_columns(pl.lit('ABC').alias('ticker'),(pl.col('boundary_at')-pl.duration(days=1)).alias('prior_close_available_at'),pl.lit(10.).alias('adjusted_prior_close'),pl.lit(30).alias('volume_window_seconds'))
            population=CertifiedPopulationWindow(scope['source_revision'],rows,select_volume_universe(rows,release.universe),1,1,0,0,0)
            # The real fixture has early invalid/rejected entry masks. Select an
            # actually ranked rejected clock, retaining native mask validity.
            eligible=[]
            for ordinal in range(block.first_ordinal,block.stop_ordinal):
                at=block.row_for('listing',ordinal)[0]['boundary_at']
                _,candidate=block.boundary_at(release,population,listing_id='listing',ticker='ABC',at=at)
                if candidate.eligible and not(candidate.rules['entry'] and candidate.validity['entry']):eligible.append((at,candidate))
            assert eligible
            at,candidate=eligible[0]
            from src.backend.backtest_program_clock import is_fresh_rejected_program_assignment
            admitted=replace(candidate,eligible=True,rules={**candidate.rules,'entry':True},validity={**candidate.validity,'entry':True})
            assert not is_fresh_rejected_program_assignment(actor,actor.assignments()[0],admitted,lambda:pytest.fail('Entry-eligible candidate read OMS unnecessarily'))
            await controller.bootstrap_program_signals(publisher,**scope)
            from tests.test_broker_liquidity_resolution import second_bar
            bar=second_bar(at);bar.update(ticker='ABC',resolution_ms=100,bucket_index=(bar['bucket_index']+1)*10-1)
            await controller._runtime.process_liquidity_boundary((bar,),at=at)
            await controller._runtime.advance_program_session_clock(at)
            projection=prepare_program_scanner_projection(block,population,release)
            await publish_prepared_program_source_clock(controller,projection,publisher,at=at,catalogs=scope['catalogs'])
            # No journal publisher is needed for a deliberately unadmitted clock.
            before=controller._journal.latest_sequence(controller.run_id)
            result=await controller.dispatch_prepared_program_clock({'assignment':block},population,at=at,assignment_order=('assignment',),execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=3000)),protection_profiles={'assignment':ProtectionProfile('unused',1,(ProtectionSlice('one',1.,StopRule(price=1.)),))},outside_rth=False)
            assert result[0][1]['status']=='not_admitted'
            assert controller._journal.latest_sequence(controller.run_id)==before and not actor._selection_clocks
            await controller._runtime._refresh_portfolio_from_broker()
            hashes=controller._program_signal_runtime.typed_source_commit_hashes()
            from src.backend.program_policy_recovery import project_pending_program_policy_events
            records=controller._journal.records(controller.run_id)
            events=project_pending_program_policy_events(records,executions=(),run_month=at.date().replace(day=1),attempt_id='00000000-0000-0000-0000-000000000001',batch_id='00000000-0000-0000-0000-000000000002',prior_batch_id='',source_cursor='test')
            packet=rebuild_program_policy_events(actor,events,run_id=controller.run_id,source_hashes=hashes,blocks=(block,),population=population,executions=(),order_manager=controller._runtime.order_manager,maximum_events=1000,last_sequence=records[-1].sequence)
            runtime=controller._runtime;accounts=runtime.config.account_ids
            image=PortfolioRecovery(controller.run_id,{account:packet.journal_sequence for account in accounts},deepcopy(runtime.portfolio.states),deepcopy(runtime.portfolio.reservations),deepcopy(runtime.portfolio.allocations),deepcopy(runtime.portfolio.differences),deepcopy(runtime.portfolio._last_filled_by_reservation))
            args=dict(run_id=controller.run_id,source_hashes=hashes,at=at,broker_positions={account:await runtime.broker.positions(account) for account in accounts},broker_ledgers={account:await runtime.broker.account_ledger(account) for account in accounts},broker_executions=(),broker_orders=await runtime.broker.canonical_orders(),order_manager=runtime.order_manager,portfolio=runtime.portfolio,portfolio_recovery=image,observations={'assignment':controller.program_order_observation(assignment_id='assignment',at=at)},population=population)
            with pytest.raises(ValueError,match='prepared admission boundary'):verify_recovered_program_positions(packet,actor,**args)
            verify_recovered_program_positions(packet,actor,**args,policy_blocks=(block,))
            assert program_policy_fingerprint(packet.actor)==packet.state_hash
            invalid_entry_block=replace(block,masks=block.masks.with_columns(pl.lit(True).alias('entry'),pl.lit(False).alias('entry__valid')))
            verify_recovered_program_positions(packet,actor,**args,policy_blocks=(invalid_entry_block,))
            shifted=replace(block,market=block.market.with_columns((pl.col('boundary_at')+pl.duration(days=1)).alias('boundary_at')),masks=block.masks.with_columns((pl.col('boundary_at')+pl.duration(days=1)).alias('boundary_at')))
            with pytest.raises(ValueError,match='prepared admission boundary'):verify_recovered_program_positions(packet,actor,**args,policy_blocks=(shifted,))
            for wrong in ((block,block),(replace(block,release_hash='f'*64),)):
                with pytest.raises(ValueError):verify_recovered_program_positions(packet,actor,**args,policy_blocks=wrong)
            with pytest.raises(ValueError):verify_recovered_program_positions(packet,actor,**dict(args,at=at.replace(microsecond=1)),policy_blocks=(block,))
            # A now-entry-eligible mask cannot excuse a missing completion.
            entry_block=replace(block,masks=block.masks.with_columns(pl.lit(True).alias('entry'),pl.lit(True).alias('entry__valid')))
            with pytest.raises(ValueError,match='incomplete current source decisions'):verify_recovered_program_positions(packet,actor,**args,policy_blocks=(entry_block,))
            # Previously visited or fill-state-owning flat assignments remain
            # required even when no longer ranked.
            unranked=replace(population,ranked=population.ranked.head(0))
            packet.actor._selection_clocks['assignment']=(at,(candidate,False))
            visited=replace(packet,state_hash=program_policy_fingerprint(packet.actor))
            with pytest.raises(ValueError,match='incomplete current source decisions'):verify_recovered_program_positions(visited,actor,**dict(args,population=unranked),policy_blocks=(block,))
            packet.actor._selection_clocks.clear()
            position=packet.actor.position_for('assignment');key=(position.account_id,position.strategy_id,position.assignment_id,position.listing_id)
            packet.actor.execution_state._positions[key]=position
            stateful=replace(packet,state_hash=program_policy_fingerprint(packet.actor))
            with pytest.raises(ValueError,match='incomplete current source decisions'):verify_recovered_program_positions(stateful,actor,**dict(args,population=unranked),policy_blocks=(block,))
            packet.actor.execution_state._positions.clear()
            observation=args['observations']['assignment']
            from src.trading_runtime.oms_assignment_observation import ObservedWorkingOrder
            working=replace(observation,working_orders=(ObservedWorkingOrder('pending','entry',1.,1.,0.,'Submitted'),))
            with pytest.raises(ValueError,match='incomplete current source decisions'):verify_recovered_program_positions(packet,actor,**dict(args,observations={'assignment':working}),policy_blocks=(block,))
            missing=replace(observation,unobserved_order_ids=('unconfirmed',))
            with pytest.raises(ValueError,match='completed OMS observations'):verify_recovered_program_positions(packet,actor,**dict(args,observations={'assignment':missing}),policy_blocks=(block,))
            for pending in ('pending_acquisition','pending_reduction'):
                changed=replace(observation,**{pending:True})
                with pytest.raises(ValueError,match='incomplete current source decisions'):verify_recovered_program_positions(packet,actor,**dict(args,observations={'assignment':changed}),policy_blocks=(block,))
            assert program_policy_fingerprint(packet.actor)==packet.state_hash
        finally:
            await controller._runtime.finish()
            publisher.close()
    asyncio.run(check())
