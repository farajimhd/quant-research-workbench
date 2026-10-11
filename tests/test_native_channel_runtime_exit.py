"""Real cold source reproof and shared runtime route with controlled OMS."""
import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_confirmed_exit_unit import confirmed_unit_case
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_native_channel_execution_source import NativeChannelExitExecutionSource
from src.backend.backtest_native_channel_exit_unit import (
    native_channel_exit_intent, prepare_confirmed_native_channel_exit_request,
)
from src.backend.backtest_native_channel_publisher_source import NativeChannelExitPublisherSource
from src.trading_runtime.runtime import TradingRuntime, RunMode
from src.trading_runtime.strategy_engine import StrategyEvaluation


@dataclass(frozen=True)
class ControlledUnfilledOrderGroup:
    filled_quantity: float = 0.0


def runtime_case(installed, monkeypatch):
    from src.trading_runtime.native_channel_exit_journal_context import NativeChannelExitJournalContext
    from tests.test_backtest_strategy_first_price_source import authority, Bars
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import (
        CertifiedPriceReadbackAuthority, compile_certified_price_break_plan,
    )
    # A controlled successor declaration is used only in this fixture. The
    # production Strategy1 guard and installed releases are left intact.
    live = confirmed_unit_case(installed, monkeypatch, strategy_number=2)
    market, parent = authority()
    live['first_price_source'] = CertifiedPriceReadbackAuthority(live['run_id'],
        compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars())))
    unit_, request = prepare_confirmed_native_channel_exit_request(**live)
    context = NativeChannelExitJournalContext(unit_, live['management_policy'], live['contexts'],
        live['first_price_source'], **{key: live[key] for key in (
            'recorded_at', 'attempt_id', 'batch_id', 'parent_record_id', 'decision_record_id')})
    publisher_source = NativeChannelExitPublisherSource(context.base.run_id, live['client'],
        live['manager_keeper'], live['broker_keeper'], context.policy, context.first_price_source)
    source = NativeChannelExitExecutionSource(publisher_source, live['receipt'], context, live['pending_record_id'])
    intent = native_channel_exit_intent(request, strategy_number=2, management_policy=context.policy)
    runtime = TradingRuntime.__new__(TradingRuntime)
    runtime.run_id = context.base.run_id
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST, strategy_id='early-squeeze-strategy',
        strategy_revision=2, account_ids=(request.financial.account_id,),
        anchor_date=request.acquisition.session_date)
    runtime._strategy_one_price_source = context.first_price_source
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id, initial_sequence=live['receipt'].last_sequence)
    runtime.intent_planner = object()
    runtime.last_event_time = intent.event_time
    approved = replace(intent, metadata={'assignment_id': request.financial.assignment_id})
    runtime.portfolio = SimpleNamespace(approve=AsyncMock(return_value=(
        SimpleNamespace(payload=lambda: {'status': 'approved'}), approved)),
        _typed_recovery=False, has_pending_entry_requests=lambda _account: False)
    runtime.order_manager = SimpleNamespace(submit_intent=AsyncMock(
        return_value=ControlledUnfilledOrderGroup()))
    runtime.strategy = SimpleNamespace(assignments=lambda: ())
    return runtime, request, source, intent


def test_native_exit_cold_reproves_then_uses_shared_portfolio_and_oms(installed, monkeypatch):
    runtime, request, source, intent = runtime_case(installed, monkeypatch)
    try:
        asyncio.run(runtime.submit_native_channel_exit(request, source))
        runtime.portfolio.approve.assert_awaited_once_with(intent,
            account_id=request.financial.account_id, assignment_id=request.financial.assignment_id)
        runtime.order_manager.submit_intent.assert_awaited_once()
        record, = runtime.journal.unfenced_records()
        pending = runtime.journal.native_channel_exit_for_record(record.record_id)
        assert pending[1] is source.context
        assert pending[0].financial == request.financial
    finally:
        runtime.journal.close()


def test_preparation_rebuilds_full_capture_then_routes_confirmed_exit(installed, monkeypatch):
    from tests.test_backtest_native_channel_management_owner import owner_case
    from src.backend.backtest_native_channel_execution_source import prepare_native_channel_execution_source
    owner, original, evidence, quote, args = owner_case(installed,strategy_number=2)
    pending = owner.stage(evidence['prepared'],quote,**args)
    capture = owner.freeze(boundary_ms=args['boundary_ms'])
    saved, contexts = capture.capture(original.inherited,journal_batch_id=evidence['batch_id'])
    monkeypatch.setattr('tests.test_backtest_native_channel_financial_checkpoint.manager_case',
        lambda *_args,**_kwargs:(saved,owner.policy,{key:dict(value) for key,value in contexts.items()}))
    runtime, _, execution, _ = runtime_case(installed,monkeypatch)
    monkeypatch.setattr('src.trading_runtime.native_channel_manager_reader.load_unattested_native_channel_manager_rows',
        lambda *_args,**_kwargs:saved)
    try:
        context=execution.context
        confirmed, prepared=prepare_native_channel_execution_source(execution.publisher_source,
            execution.receipt,capture,pending,recorded_at=context.recorded_at,
            attempt_id=context.attempt_id,batch_id=context.batch_id,
            parent_record_id=context.parent_record_id,decision_record_id=context.decision_record_id)
        assert confirmed.financial==pending.financial
        assert confirmed.acquisition==pending.acquisition
        asyncio.run(runtime.submit_native_channel_exit(confirmed,prepared))
        runtime.portfolio.approve.assert_awaited_once()
        runtime.order_manager.submit_intent.assert_awaited_once()
    finally:
        runtime.journal.close()


def test_preparation_rejects_frozen_inventory_that_differs_from_saved_checkpoint(installed, monkeypatch):
    from tests.test_backtest_native_channel_management_owner import owner_case
    from src.backend.backtest_native_channel_execution_source import prepare_native_channel_execution_source
    owner, saved, evidence, quote, args = owner_case(installed)
    pending = owner.stage(evidence['prepared'], quote, **args)
    capture = owner.freeze(boundary_ms=args['boundary_ms'])
    runtime, _, execution, _ = runtime_case(installed, monkeypatch)
    # Controlled saved-row transport isolates the inventory comparison; the
    # other runtime tests exercise independent cold checkpoint confirmation.
    monkeypatch.setattr('src.trading_runtime.native_channel_manager_reader.load_unattested_native_channel_manager_rows',
        lambda *_args, **_kwargs: saved)
    try:
        context = execution.context
        # The existing saved transport contains a different pending identity.
        # Rebuilding must reject it before reaching order execution.
        with pytest.raises(ValueError, match='frozen inventory differs'):
            prepare_native_channel_execution_source(execution.publisher_source,
                execution.receipt, capture, pending, recorded_at=context.recorded_at,
                attempt_id=context.attempt_id, batch_id=context.batch_id,
                parent_record_id=context.parent_record_id, decision_record_id=context.decision_record_id)
        runtime.portfolio.approve.assert_not_awaited()
        runtime.order_manager.submit_intent.assert_not_awaited()
    finally:
        runtime.journal.close()


@pytest.mark.parametrize('defect', ['naked', 'quantity', 'clock', 'source', 'mixed', 'checkpoint'])
def test_native_exit_invalid_authority_never_reaches_orders(installed, monkeypatch, defect):
    runtime, request, source, intent = runtime_case(installed, monkeypatch)
    try:
        if defect == 'naked':
            operation = runtime._execute_intents(StrategyEvaluation(intents=(intent,)),
                request.financial.account_id, None)
        elif defect == 'mixed':
            operation = runtime._execute_intents(StrategyEvaluation(intents=(intent,)),
                request.financial.account_id, None, native_channel_source=(request, source),
                numbered_exit_assignment_id=request.financial.assignment_id)
        else:
            if defect == 'quantity': request = replace(request,
                financial=replace(request.financial, position_quantity=request.financial.position_quantity + 1))
            elif defect == 'clock': runtime.last_event_time += timedelta(milliseconds=100)
            elif defect == 'source': runtime._strategy_one_price_source = object()
            elif defect == 'checkpoint':
                # Changed current head is independently read during confirm(),
                # rather than treating the previously built unit as permission.
                source.publisher_source.manager_keeper.read_head = lambda **_scope: None
            operation = runtime.submit_native_channel_exit(request, source)
        with pytest.raises((ValueError, RuntimeError)): asyncio.run(operation)
        runtime.portfolio.approve.assert_not_awaited()
        runtime.order_manager.submit_intent.assert_not_awaited()
        assert not runtime.journal.unfenced_records()
    finally:
        runtime.journal.close()
