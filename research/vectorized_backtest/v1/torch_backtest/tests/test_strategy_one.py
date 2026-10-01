"""Compare tensor policy blocks with the application's pure reducers.

These are policy tests, not a claim of full Portfolio/OMS/broker equivalence.
Both CPU and actual CUDA tensors run; source facts use stable identities.
"""

from dataclasses import replace

import pytest
import torch

from research.vectorized_backtest.v1.torch_backtest.strategy_one import (
    STRATEGY_ONE_ARRAY,
    ProtectionRegisters,
    ResistanceRegisters,
    add_admission,
    advance_protection,
    confirm_protection,
    entry_admission,
    observe_resistance,
    validate_strategy_one_array,
)
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_position import (
    ProtectionState,
    ResistanceBreak,
)
from src.trading_runtime.strategy_one_position import (
    advance_protection as app_advance,
)
from src.trading_runtime.strategy_one_position import (
    confirm_protection_transition as app_confirm,
)
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryInput,
    StrategyOneFinancialView,
    StrategyOneReentryWitness,
    propose_strategy_one_entry,
)

DEVICES = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])


def tensor(value, device):
    dtype = (
        torch.bool
        if type(value) is bool
        else torch.int64
        if type(value) is int
        else torch.float64
    )
    return torch.tensor([[value]], dtype=dtype, device=device)


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize(
    "variant",
    [
        "first",
        "rapid_cross",
        "rapid_no_cross",
        "late_other",
        "late_same",
        "pending",
        "expired",
        "stale_quote",
    ],
)
def test_entry_against_app(device, variant):
    at = 40_000
    financial = StrategyOneFinancialView(
        "assignment",
        "account",
        "TEST",
        AssignmentStatus.WATCHING,
        StrategyPermissions(enter=True, reenter=True),
        0.0,
        False,
        False,
        False,
        0,
    )
    evidence = StrategyOneEntryInput(
        "TEST",
        at,
        100,
        0.5,
        30_000,
        "level-b",
        True,
        9.0,
        12.0,
        "target",
        3,
        100000,
        100100,
        0,
    )
    prior_id = 1
    if variant in {"rapid_cross", "rapid_no_cross", "late_other", "late_same"}:
        financial = replace(financial, completed_entries=1)
        same = variant in {"rapid_no_cross", "late_same"}
        witness = StrategyOneReentryWitness(
            35_000 if variant.startswith("rapid") else 20_000,
            "level-b" if same else "level-a",
            100000,
            99900,
            100100 if variant == "rapid_cross" else 100000,
        )
        evidence = replace(evidence, reentry=witness)
        prior_id = 2 if same else 1
    elif variant == "pending":
        financial = replace(financial, pending_entry=True)
    elif variant == "expired":
        evidence = replace(evidence, boundary_ms=400_000)
    elif variant == "stale_quote":
        evidence = replace(evidence, quote_age_us=1_000_001)
    witness = evidence.reentry
    values = {
        "candidate_valid": True,
        "boundary_ms": evidence.boundary_ms,
        "episode_start_ms": evidence.episode_start_ms,
        "bid_int": evidence.bid_int,
        "ask_int": evidence.ask_int,
        "quote_age_us": evidence.quote_age_us,
        "bos_break_boundary_ms": evidence.bos_break_boundary_ms,
        "completed_entries": financial.completed_entries,
        "closed_boundary_ms": witness.closed_boundary_ms if witness else 0,
        "prior_entry_resistance_id": prior_id if witness else 0,
        "bos_support_level_id": 2,
        "prior_position_high_int": witness.prior_position_high_int if witness else 0,
        "previous_bar_close_int": witness.previous_bar_close_int if witness else 0,
        "current_bar_close_int": witness.current_bar_close_int if witness else 0,
        "quantity": financial.position_quantity,
        "pending_entry": financial.pending_entry,
        "pending_exit": financial.pending_exit,
        "pending_capital_request": False,
        "entry_permission": True,
        "reentry_not_before_ms": 0,
        "activation_gap": evidence.activation_gap,
        "protection_valid": True,
        "target_level_id": 3,
        "target_ordinal": 3,
        "stop": evidence.stop_price,
        "target": evidence.target_price,
    }
    x = {key: tensor(value, device) for key, value in values.items()}
    actual = entry_admission(x).item()
    assert actual == (
        propose_strategy_one_entry(evidence, financial).proposal is not None
    )


def level(identity, lower, upper):
    return {
        "unified_level_id": identity,
        "lower": lower,
        "upper": upper,
        "role": "resistance",
        "side": "resistance",
    }


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize(
    "stop_ack,target_ack", [(True, True), (False, True), (False, False)]
)
def test_protection_multiple_breaks_refusal_and_retry_against_app(
    device, stop_ack, target_ack
):
    app = ProtectionState(1000, 8.0, 14.0)
    state = ProtectionRegisters.empty(
        tensor(1000, device),
        tensor(8.0, device),
        tensor(14.0, device),
        identities=10,
    )
    # Two triples complete in the same clock. Only the last earned triple
    # owns the resistance stop; duplicates cannot earn another group.
    levels = [level(str(i), float(8 + i), float(8 + i)) for i in range(1, 7)]
    overhead = [level(str(i), float(20 + i), float(20 + i)) for i in range(1, 5)]
    for at, breaks in ((2000, levels), (3000, levels[:1])):
        x = {
            "boundary_ms": tensor(at, device),
            "bid": tensor(18.0, device),
            "ask": tensor(18.1, device),
            "tick": tensor(0.01, device),
            "price_bearing_bar": tensor(True, device),
            "low_boundary_ms": tensor(0, device),
            "low_int": tensor(0, device),
            "low_valid": tensor(False, device),
            "break_id": torch.tensor(
                [[[int(r["unified_level_id"]) for r in breaks]]], device=device
            ),
            "break_lower": torch.tensor(
                [[[r["lower"] for r in breaks]]], dtype=torch.float64, device=device
            ),
            "overhead_id": torch.tensor(
                [[[int(r["unified_level_id"]) for r in overhead]]], device=device
            ),
            "overhead_midpoint": torch.tensor(
                [[[r["lower"] for r in overhead]]], dtype=torch.float64, device=device
            ),
        }
        expected = app_advance(
            app,
            now_ms=at,
            bid=18.0,
            ask=18.1,
            tick=0.01,
            low_boundary_ms=None,
            low_int=None,
            low_price_valid=False,
            low_extremes_valid=False,
            breaks=tuple(ResistanceBreak(at, r) for r in breaks),
            overhead_levels=overhead,
            price_bearing_bar=True,
        )
        proposed, stop_changed, target_changed, _ = advance_protection(state, x)
        assert proposed.stop.item() == expected.state.stop
        assert proposed.target.item() == expected.state.target
        assert proposed.earned_groups.item() == expected.state.earned_groups
        assert proposed.accepted.sum().item() == len(expected.state.accepted_ids)
        stop_confirmed = stop_ack and expected.stop_amendment is not None
        target_confirmed = target_ack and expected.target_amendment is not None
        assert stop_changed.item() == (expected.stop_amendment is not None)
        assert target_changed.item() == (expected.target_amendment is not None)
        state = confirm_protection(
            state,
            proposed,
            tensor(stop_confirmed, device),
            tensor(target_confirmed, device),
        )
        app = app_confirm(
            app,
            expected,
            stop_confirmed=stop_confirmed,
            target_confirmed=target_confirmed,
        )
        assert state.stop.item() == app.stop
        assert state.target.item() == app.target
        assert state.applied_groups.item() == app.applied_groups


def test_sealed_array_rejects_dropped_lifecycle_block():
    validate_strategy_one_array(STRATEGY_ONE_ARRAY)
    with pytest.raises(ValueError, match="complete declared"):
        validate_strategy_one_array(STRATEGY_ONE_ARRAY[:-1])


@pytest.mark.parametrize("device", DEVICES)
def test_resistance_identity_changes_gap_and_reacceptance_against_app(device):
    from src.trading_runtime.strategy_one_resistance import (
        ResistanceObservation,
        observe_completed_resistance_second,
    )

    app = ResistanceObservation()
    boolean = torch.zeros((1, 1, 2), dtype=torch.bool, device=device)
    geometry = torch.zeros((1, 1, 2), dtype=torch.float64, device=device)
    state = ResistanceRegisters(
        tensor(0, device),
        tensor(0, device),
        boolean.clone(),
        geometry.clone(),
        geometry.clone(),
        boolean.clone(),
    )
    cases = [
        (1000, 90000, 90000, 10.0),
        (2000, 90000, 110000, 10.0),
        (3000, 110000, 90000, 10.0),
        (4000, 90000, 110000, 10.0),
        (6000, 90000, 110000, 10.0),
        (7000, 90000, 110000, 10.1),
    ]
    for at, opened, close, price in cases:
        row = {
            "boundary_ms": at,
            "open_int": opened,
            "close_int": close,
            "resolution_ms": 1000,
            "price_valid": 1,
        }
        app, breaks = observe_completed_resistance_second(
            app, row, admitted_levels=[level("one", price, price)]
        )
        x = {
            "boundary_ms": tensor(at, device),
            "open_int": tensor(opened, device),
            "close_int": tensor(close, device),
            "present": torch.tensor([[[False, True]]], device=device),
            "eligible_resistance": torch.tensor([[[False, True]]], device=device),
            "lower": torch.tensor([[[0.0, price]]], dtype=torch.float64, device=device),
            "upper": torch.tensor([[[0.0, price]]], dtype=torch.float64, device=device),
        }
        state, actual = observe_resistance(state, x)
        assert actual.sum().item() == len(breaks)
        assert state.accepted.sum().item() == len(app.accepted_ids)


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize(
    "variant", ["valid", "pending", "stale_bar", "bearish", "fourth", "duplicate"]
)
def test_add_against_app(device, variant):
    from src.trading_runtime.strategy_one_add import propose_strategy_one_add

    financial = StrategyOneFinancialView(
        "assignment",
        "account",
        "TEST",
        AssignmentStatus.WATCHING,
        StrategyPermissions(),
        10.0,
        False,
        False,
        False,
        1,
    )
    protection = ProtectionState(2000, 9.0, 12.0, frozenset({"one"}))
    event = ResistanceBreak(2000, level("one", 9.9, 10.1))
    rows = {
        resolution: {
            "ticker": "TEST",
            "resolution_ms": resolution,
            "boundary_ms": 2000,
            "price_valid": 1,
            "indicator_resolution_ms": resolution,
            "macd_line": 0.2,
            "macd_signal": 0.1,
            "open_int": 99900,
            "close_int": 100100,
            "quote_valid": 1,
            "bid_int": 100100,
            "ask_int": 100200,
        }
        for resolution in (100, 1000)
    }
    if variant == "pending":
        financial = replace(financial, pending_entry=True)
    elif variant == "stale_bar":
        rows[100]["boundary_ms"] = 1900
    elif variant == "bearish":
        rows[1000]["macd_line"] = 0.0
    elif variant == "fourth":
        financial = replace(financial, current_purchase_groups=3)
    expected = None
    # A duplicate is an invalid witness at the application's boundary and
    # cannot authorize a tensor proposal either.
    if variant != "duplicate":
        expected = propose_strategy_one_add(
            financial,
            protection,
            event,
            rows,
            boundary_ms=2000,
            purchase_ordinal=2,
            fresh_bid=10.01,
            fresh_ask=10.02,
            prior_accepted_ids=frozenset(),
        )
    values = {
        "boundary_ms": 2000,
        "break_boundary_ms": 2000,
        "break_is_resistance": True,
        "break_new": variant != "duplicate",
        "break_accepted": True,
        "quantity": financial.position_quantity,
        "pending_exit": False,
        "pending_entry": financial.pending_entry,
        "pending_capital_request": False,
        "purchase_ordinal": 2,
        "current_purchase_groups": financial.current_purchase_groups,
        "bars_valid": True,
        "bar_100_boundary_ms": rows[100]["boundary_ms"],
        "bar_1s_boundary_ms": 2000,
        "macd_100_line": rows[100]["macd_line"],
        "macd_100_signal": 0.1,
        "macd_1s_line": rows[1000]["macd_line"],
        "macd_1s_signal": 0.1,
        "open_1s_int": 99900,
        "close_1s_int": 100100,
        "close_100_int": 100100,
        "break_midpoint": 10.0,
        "quote_valid": True,
        "bid": 10.01,
        "ask": 10.02,
        "fresh_bid": 10.01,
        "fresh_ask": 10.02,
        "stop": 9.0,
        "target": 12.0,
    }
    actual = add_admission(
        {key: tensor(value, device) for key, value in values.items()}
    )
    assert actual.item() == (expected is not None)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA compilation")
def test_compiled_protection_prefix_scan_matches_eager():
    # Two listings, two candidate accounts, repeated witnesses, and an existing
    # unfinished triple. The compiled path must retain exact event semantics.
    device = "cuda"
    shape = (2, 2)
    scalar = lambda value: torch.full(shape, value, dtype=torch.float64, device=device)
    integer = lambda value: torch.full(shape, value, dtype=torch.int64, device=device)
    state = ProtectionRegisters.empty(
        integer(1000), scalar(8.0), scalar(20.0), identities=8
    )
    state.pending_count.fill_(1)
    state.pending_min.fill_(8.5)
    state.accepted[..., 7] = True
    x = {
        "boundary_ms": integer(2000),
        "bid": scalar(18.0),
        "ask": scalar(18.1),
        "tick": scalar(0.01),
        "price_bearing_bar": integer(1).bool(),
        "low_boundary_ms": integer(0),
        "low_int": integer(0),
        "low_valid": integer(0).bool(),
        "break_id": torch.tensor([1, 1, 2, 3, 4, 5, 6], device=device).expand(
            *shape, 7
        ),
        "break_lower": torch.tensor(
            [9, 9, 10, 11, 12, 13, 14], dtype=torch.float64, device=device
        ).expand(*shape, 7),
        "overhead_id": torch.tensor([1, 2, 3], device=device).expand(*shape, 3),
        "overhead_midpoint": torch.tensor(
            [21, 22, 23], dtype=torch.float64, device=device
        ).expand(*shape, 3),
    }
    expected = advance_protection(state, x)
    compiled = torch.compile(advance_protection, fullgraph=True)
    actual = compiled(state, x)
    for name in state.__dataclass_fields__:
        torch.testing.assert_close(
            getattr(actual[0], name), getattr(expected[0], name), rtol=0, atol=1e-7
        )
    for observed, wanted in zip(actual[1:], expected[1:]):
        assert torch.equal(observed, wanted)
