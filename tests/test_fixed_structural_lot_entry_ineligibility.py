"""Valid target shortage is domain rejection; source and admission stay strict."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import pytest
from test_fixed_structural_lot_source import prepared
from test_fixed_structural_lots import fixture
from src.trading_runtime.fixed_structural_lot_entry import (
    FixedStructuralLotTargetCountIneligible, prepare_fixed_structural_lot_entry,
)
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.backend import backtest_fixed_structural_lot_native as native


def test_valid_causal_source_final_count_shortage_has_exact_counts():
    day, proposal, intervals = fixture(count=4)
    with pytest.raises(FixedStructuralLotTargetCountIneligible) as caught:
        prepare_fixed_structural_lot_entry(proposal, session_date=day,
            policy=FixedStructuralLotPolicy(), intervals=intervals, tick=.01)
    assert (caught.value.required, caught.value.available) == (3, 2)


@pytest.mark.parametrize('change', ['original', 'hash', 'tick'])
def test_invalid_source_or_original_remains_fatal_not_domain_rejection(change):
    day, proposal, intervals = fixture(count=4)
    tick=.01
    if change=='original':proposal=replace(proposal,initial_target=10.31)
    if change=='hash':intervals=replace(intervals, coverage=(replace(intervals.coverage[0],interval_hash='9'*64),))
    if change=='tick':tick=float('nan')
    with pytest.raises(ValueError) as caught:
        prepare_fixed_structural_lot_entry(proposal,session_date=day,
            policy=FixedStructuralLotPolicy(),intervals=intervals,tick=tick)
    assert type(caught.value) is not FixedStructuralLotTargetCountIneligible


def test_native_original_checked_then_issued_rejection_before_any_portfolio_call(monkeypatch):
    source,proposal,calls=prepared(monkeypatch,target_centers=((1,10.1),(2,10.2),(3,12.)))
    # Explicit component installed-proof seam. Genuine complete source and the
    # actual original native price/activity factory still execute above it.
    monkeypatch.setattr(native,'require_installed_source',lambda value: value.require_prepared_source())
    with pytest.raises(FixedStructuralLotTargetCountIneligible) as caught:
        source.request(proposal)
    reason=source.verified_entry_rejection(caught.value,proposal)
    assert reason.endswith('required=3:available=1')
    with pytest.raises(ValueError,match='Unissued'):
        source.verified_entry_rejection(FixedStructuralLotTargetCountIneligible(3,1),proposal)
    with pytest.raises(ValueError,match='Unissued'):
        source.verified_entry_rejection(caught.value,replace(proposal,assignment_id='foreign'))
    manager=object.__new__(StrategyOneManagementRunner)
    manager.contract=SimpleNamespace(strategy_number=proposal.strategy_number,entry_allowed=lambda boundary:True)
    manager._submitted={}
    async def forbidden(*args):raise AssertionError('No Portfolio/reservation/runtime submission permitted')
    manager.runtime=SimpleNamespace(submit_fixed_structural_lot_request=forbidden)
    manager._fixed_lot_owner=SimpleNamespace(operation=SimpleNamespace(source=source,request=source.request),entry_rejections={})
    asyncio.run(manager.on_entry_proposal(proposal))
    assert manager._submitted=={}
    assert manager._fixed_lot_owner.entry_rejections=={reason:1}
    assert calls=={'configuration':1,'product':1,'full_children':1}
    with pytest.raises(ValueError) as bad:
        asyncio.run(manager.on_entry_proposal(replace(proposal,reference_ask=10.02)))
    assert type(bad.value) is not FixedStructuralLotTargetCountIneligible
    assert manager._fixed_lot_owner.entry_rejections=={reason:1}


def test_ordinary_manager_does_not_catch_an_unissued_shortage():
    _,proposal,_=fixture(count=4)
    manager=object.__new__(StrategyOneManagementRunner)
    manager.contract=SimpleNamespace(strategy_number=proposal.strategy_number,entry_allowed=lambda boundary:True)
    manager._submitted={};manager._fixed_lot_owner=None
    async def fail(*args):raise FixedStructuralLotTargetCountIneligible(3,2)
    manager.runtime=SimpleNamespace(submit_strategy_one_proposal=fail)
    with pytest.raises(FixedStructuralLotTargetCountIneligible):
        asyncio.run(manager.on_entry_proposal(proposal))
