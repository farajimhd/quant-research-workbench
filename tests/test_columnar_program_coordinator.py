from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_columnar_strategy_release import bundle
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from src.trading_runtime.columnar_program_coordinator import ProgramBoundary, ManagementState, select_program_proposal
from src.trading_runtime.filled_position_state import FilledPosition, FilledPositionState
from tests.test_filled_position_state import fill


def inputs(opened=False, **signals):
    spec, fields, kwargs = bundle()
    release = restore_release(project_release(spec, fields, **kwargs))
    now = kwargs["published_at"] + timedelta(seconds=200)
    boundary = ProgramBoundary(now, now, "listing", "ABC", 10.5, True,
        {stage: signals.get(stage, False) for stage in spec.programs},
        {stage: True for stage in spec.programs}, 9.)
    position = FilledPosition("account", kwargs["strategy_id"], "assignment", "listing", "ABC")
    if opened:
        position = FilledPositionState().apply_fill(replace(fill(), strategy_id=kwargs["strategy_id"],
            filled_at=kwargs["published_at"]))
    return release, boundary, position, ManagementState(now, False, False, 9., 12., 10.)


def select(data, terminal=False):
    return select_program_proposal(data[0], data[1], position=data[2], management=data[3], terminal=terminal)


def test_entry_requests_cash_without_opening_position_and_pending_blocks_duplicate():
    data = inputs(entry=True)
    p = select(data)[0]
    assert p.action == "enter_long" and p.requested_cash == 1000 and p.quantity == 0
    assert not data[2].position_open
    assert select(data)[0].intent_id == p.intent_id
    assert select((*data[:3], replace(data[3], pending_acquisition=True))) == ()
    assert select(data, terminal=True) == ()


def test_open_branch_and_age_require_actual_fills_and_manage_outside_top10():
    data = inputs(opened=True, add=True)
    assert select(data)[0].requested_cash == 200
    assert select((data[0], replace(data[1], eligible=False), *data[2:]))[0].stage == "add"
    young = replace(data[2], opened_at=data[1].boundary_at, last_management_fill_at=data[1].boundary_at)
    assert select((*data[:2], young, data[3])) == ()
    release = replace(data[0], minimum_age_seconds={**data[0].minimum_age_seconds, "add": 300})
    assert select((release, *data[1:])) == ()


def test_exit_before_reduce_before_add_and_ratchet_before_stop_check():
    assert [p.stage for p in select(inputs(opened=True, exit=True, reduce=True, add=True))] == ["exit"]
    assert [p.stage for p in select(inputs(opened=True, reduce=True, add=True))] == ["reduce"]
    data = inputs(opened=True, trail=True, add=True)
    assert [p.stage for p in select(data)] == ["trail", "add"]
    assert [p.stage for p in select((*data[:3], replace(data[3], high_water_price=20.)))] == ["exit"]


def test_pending_acquisition_is_cancelled_before_exit_without_assumed_cancellation():
    data = inputs(opened=True, exit=True)
    pending = replace(data[3], pending_acquisition=True)
    proposals = select((*data[:3], pending))
    assert [p.action for p in proposals] == ["cancel_entry"]
    assert select((*data[:3], pending)) == proposals
    assert data[2].quantity == 10


def test_missing_mask_and_future_input_are_not_admitted():
    data = inputs(entry=True)
    assert select((data[0], replace(data[1], validity={**data[1].validity, "entry": False}), *data[2:])) == ()
    with pytest.raises(ValueError, match="future"):
        select((data[0], replace(data[1], available_at=data[1].boundary_at + timedelta(seconds=1)), *data[2:]))
    with pytest.raises(ValueError, match="attribution"):
        select((*data[:2], replace(data[2], listing_id="different"), data[3]))
