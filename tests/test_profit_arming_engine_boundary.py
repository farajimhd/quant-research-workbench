"""Prepared Strategy 31 boundary route; native readers are mocked here."""
import asyncio
from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.strategy_profit_giveback_arm_reference import ProfitArmReference
from test_strategy_profit_giveback_source import fixture


def manager_fixture():
    _, state, financial = fixture()
    runtime = SimpleNamespace(config=SimpleNamespace(strategy_revision=30),
        submit_strategy_one_proposal=AsyncMock(), submit_strategy_one_add=AsyncMock(),
        submit_strategy_one_protection=AsyncMock())
    manager = StrategyOneManagementRunner(runtime=runtime,
        evidence=SimpleNamespace(management_evidence=AsyncMock()), tick_for_ticker=lambda _: .01)
    # Deliberately inject prepared 31 policy; no executable release is registered.
    manager.contract = SimpleNamespace(strategy_number=31)
    for name in ('submitted', 'positions', 'position_highs', 'first_held_boundaries'):
        setattr(manager, '_' + name, dict(getattr(state, name)))
    key = state.submitted[0][0]
    manager._profit_arm_financials[key] = financial
    return manager, financial, state


def reference(candidate):
    return ProfitArmReference(candidate, '00000000-0000-0000-0000-000000000001',
        20, '00000000-0000-0000-0000-000000000002', 'a' * 64)


def test_manager_freezes_one_reference_and_does_not_rearm_at_later_high():
    manager, _, state = manager_fixture()
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    assert len(requests) == 1
    arm = reference(requests[0][0])
    manager.accept_profit_arming_references(requests, (arm,), boundary_ms=state.boundary_ms)
    key = state.submitted[0][0]
    manager._position_highs[key] = 120000
    assert manager.profit_arming_requests(boundary_ms=10000) == ()
    assert manager._profit_arm_references[key].candidate.high_int == 110000


def test_changed_capture_rejects_all_references_before_installation():
    manager, _, state = manager_fixture()
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    manager._position_highs[state.submitted[0][0]] += 1
    with pytest.raises(ValueError, match='differs from completed positions'):
        manager.accept_profit_arming_references(requests, (reference(requests[0][0]),),
                                                boundary_ms=state.boundary_ms)
    assert manager._profit_arm_references == {}


def test_one_invalid_position_reference_rejects_the_entire_capture():
    manager, financial, state = manager_fixture()
    key, source = state.submitted[0]
    other_key = (key[0], key[1], 'ZZZZ')
    manager._submitted[other_key] = replace(source, ticker='ZZZZ')
    manager._positions[other_key] = manager._positions[key]
    manager._position_highs[other_key] = manager._position_highs[key]
    manager._first_held_boundaries[other_key] = manager._first_held_boundaries[key]
    manager._profit_arm_financials[other_key] = replace(financial, ticker='ZZZZ')
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    assert len(requests) == 2
    references = (reference(requests[0][0]),
                  reference(replace(requests[1][0], high_int=120000)))
    with pytest.raises(ValueError, match='differs from completed positions'):
        manager.accept_profit_arming_references(requests, references,
                                                boundary_ms=state.boundary_ms)
    assert manager._profit_arm_references == {}


def test_closed_position_clears_reference_before_next_entry():
    manager, financial, state = manager_fixture()
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    manager.accept_profit_arming_references(requests, (reference(requests[0][0]),),
                                            boundary_ms=state.boundary_ms)
    asyncio.run(manager.on_management(replace(financial, position_quantity=0.,
        pending_entry=False, pending_exit=False), {}, 10000))
    assert manager._profit_arm_references == manager._profit_arm_financials == {}
    assert manager.profit_arming_requests(boundary_ms=10000) == ()


def test_parent_release_does_not_request_arming():
    manager, _, _ = manager_fixture()
    manager.contract = SimpleNamespace(strategy_number=30)
    assert manager.profit_arming_requests(boundary_ms=9900) == ()


def test_unarmed_and_confirmed_positions_do_not_pay_for_deep_capture(monkeypatch):
    manager, _, state = manager_fixture()
    key = state.submitted[0][0]
    manager._position_highs[key] = 109999
    monkeypatch.setattr(manager, 'capture_state', lambda **_: pytest.fail('unnecessary capture'))
    assert manager.profit_arming_requests(boundary_ms=9900) == ()
    manager._position_highs[key] = 120000
    manager._profit_arm_references[key] = object()
    assert manager.profit_arming_requests(boundary_ms=10000) == ()


@pytest.mark.parametrize('fail_confirmation', [False, True])
def test_controller_confirms_after_fence_off_thread_and_closes_reader(monkeypatch, fail_confirmation):
    from src.trading_runtime import arte_journal_writer as readers
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    from src.trading_runtime import strategy_profit_giveback_arm_reference as arms
    manager, _, state = manager_fixture()
    requests = manager.profit_arming_requests(boundary_ms=state.boundary_ms)
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller.run_id = 'run'
    controller._strategy_one_manager = manager
    controller._journal_publisher = SimpleNamespace(
        writer=SimpleNamespace(journal_profile='backtest_v4'), _first_price_source='native-price')
    controller._fixed_keeper_session = object()
    controller._source_cursor = {'boundary_ms': state.boundary_ms}
    controller._record_stage_time = lambda *_: None
    receipt = TypedBacktestReceipt(20, 'batch', 'cursor')
    calls = []
    async def checkpoint(at):
        calls.append('fenced')
        return receipt
    controller._save_restart_checkpoint_responsive = checkpoint
    reader = SimpleNamespace(close=lambda: calls.append('closed'))
    monkeypatch.setattr(readers, 'backtest_v4_operator_client_from_env', lambda: reader)
    monkeypatch.setattr(snapshots, 'ManagedManagerSnapshotHeadReader', lambda keeper: keeper)
    engine_thread = get_ident()
    def confirm(actual_reader, keeper, candidate, financial, actual_receipt, **kwargs):
        assert get_ident() != engine_thread
        assert calls == ['fenced'] and actual_reader is reader
        assert actual_receipt is receipt
        assert kwargs == {'run_id': 'run', 'first_price_source': 'native-price'}
        calls.append('confirmed')
        if fail_confirmation:
            raise ValueError('native checkpoint changed')
        return reference(candidate)
    monkeypatch.setattr(arms, 'confirm_profit_arm_reference', confirm)
    async def exercise():
        if fail_confirmation:
            with pytest.raises(ValueError, match='native checkpoint changed'):
                await controller._confirm_profit_arming_checkpoint(requests, event_time='at')
            assert manager._profit_arm_references == {}
        else:
            result = await controller._confirm_profit_arming_checkpoint(requests, event_time='at')
            assert result == (reference(requests[0][0]),)
            assert manager.profit_arming_requests(boundary_ms=9900) == ()
        assert calls == ['fenced', 'confirmed', 'closed']
    asyncio.run(exercise())
