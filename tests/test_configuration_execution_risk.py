from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from src.trading_runtime.configuration_execution_risk import (
    RISK, project_execution_risk, restore_execution_risk, evaluate_acquisition_risk,
)


def contract(**changes):
    values = dict(configuration_id="00000000-0000-0000-0000-000000000001",
        configuration_hash="a" * 64, maximum_stop_distance=.2,
        maximum_position_risk=300., fee_per_share=.005, minimum_order_fee=1.,
        cost_bps=0., published_at=datetime.now(UTC))
    return project_execution_risk(**(values | changes))


def test_actual_filled_risk_classifies_adverse_fill_and_paid_episode_loss_without_changing_stop():
    from src.trading_runtime.configuration_execution_risk import evaluate_filled_execution_risk
    values = dict(quantity=D(100), average_price=D(20), stop_price=D('17.02'), episode_net_pnl=D(-1))
    exact = evaluate_filled_execution_risk(contract(), **values)
    assert exact.allowed and exact.modeled_stop_loss == D(300)
    adverse = evaluate_filled_execution_risk(contract(), **(values | {'average_price': D('20.0001')}))
    assert adverse.reasons == ('post_fill_maximum_position_risk_exceeded',)
    lost = evaluate_filled_execution_risk(contract(), **(values | {'episode_net_pnl': D(-2)}))
    assert lost.modeled_stop_loss == D(301) and not lost.allowed
    distance = evaluate_filled_execution_risk(contract(), quantity=D(1), average_price=D(10),
        stop_price=D(7), episode_net_pnl=D(-1))
    assert distance.reasons == ('post_fill_maximum_stop_distance_exceeded',)
    assert values['stop_price'] == D('17.02')


def entry(**changes):
    return dict(action="enter_long", quantity=D(0), average_price=D(0),
        stop_price=D(8), episode_paid_fees=D(0), episode_realized_gross_pnl=D(0),
        requested_cash=D(1000), execution_price=D(10)) | changes


def test_normalized_immutable_roundtrip_and_explicit_storage():
    selected = contract()
    assert restore_execution_risk(selected.rows).header == selected.header
    with pytest.raises(TypeError):
        selected.header["cost_bps"] = 1.
    assert "storage_policy = 'live_market_ssd'" in RISK.ddl()
    assert "JSON" not in RISK.ddl()


def test_binding_requires_its_exact_configuration_and_plan_never_executes():
    from src.trading_runtime.configuration_execution_risk import bind_execution_risk, execution_risk_schema_plan
    selected = contract()
    revision = dict(revision_id='strategy-configuration:' + selected.header['configuration_id'],
                    selection_hash=selected.header['configuration_hash'])
    bound = bind_execution_risk(revision, selected)
    assert 'execution_risk_hash' not in revision
    assert bound['execution_risk_hash'] == selected.header['content_hash']
    with pytest.raises(ValueError):
        bind_execution_risk(bound | {'selection_hash': 'b' * 64}, selected)
    plan = execution_risk_schema_plan(reader_principals=('reader',), publisher_principal='publisher')
    assert len(plan) == 3 and 'SELECT, INSERT' in plan[-1] and 'ALTER' not in plan[-1]
    with pytest.raises(ValueError):
        execution_risk_schema_plan(reader_principals=('reader;DROP',), publisher_principal='publisher')


@pytest.mark.parametrize("change", [{"maximum_stop_distance": .21},
    {"maximum_position_risk": 301.}, {"fee_per_share": float("nan")},
    {"minimum_order_fee": True}, {"fee_per_share": -.005},
    {"minimum_order_fee": -1.}, {"cost_bps": -1.},
    {"published_at": datetime(2026, 1, 1)}])
def test_invalid_contract_is_rejected(change):
    with pytest.raises(ValueError):
        contract(**change)


def test_modified_rows_are_not_a_contract():
    selected = contract()
    row = dict(selected.header, fee_per_share=.01)
    with pytest.raises(ValueError, match="integrity"):
        restore_execution_risk({RISK.name: [row]})


def test_entry_boundary_includes_both_order_minima():
    result = evaluate_acquisition_risk(contract(maximum_position_risk=202.), **entry())
    assert result.allowed and result.modeled_stop_loss == D(202)
    result = evaluate_acquisition_risk(contract(maximum_position_risk=201.99), **entry())
    assert result.reasons == ("maximum_position_risk_exceeded",)


def test_adverse_execution_price_rechecks_distance_without_changing_stop():
    result = evaluate_acquisition_risk(contract(), **entry(execution_price=D(11)))
    assert not result.allowed and "maximum_stop_distance_exceeded" in result.reasons


def test_addition_reserves_existing_episode_losses_and_future_exit_fee():
    state = entry(action="add_long", quantity=D(100), average_price=D(10),
                  requested_cash=D(200), episode_paid_fees=D(1))
    result = evaluate_acquisition_risk(contract(), **state)
    assert result.allowed and result.modeled_stop_loss == D(243)
    result = evaluate_acquisition_risk(contract(), **(state | {"episode_realized_gross_pnl": D(-100)}))
    assert result.modeled_stop_loss == D(343) and not result.allowed
    result = evaluate_acquisition_risk(contract(), **(state | {"episode_realized_gross_pnl": D(500)}))
    assert result.modeled_stop_loss == D(242)


def test_risk_consumes_repeating_filled_basis_and_episode_loss_without_rounding():
    from src.trading_runtime.filled_position_state import FilledPositionState, FeeFact
    from tests.test_filled_position_state import fill
    from src.trading_runtime.configuration_execution_risk import evaluate_filled_execution_risk
    state = FilledPositionState()
    state.apply_fill(fill(q='.1', price='10.000000000000002'))
    state.apply_fee(FeeFact('e1', D(1), 'USD'))
    state.apply_fill(fill(execution='e2', q='.2', price='10.01', second=1))
    state.apply_fee(FeeFact('e2', D(0), 'USD'))
    state.apply_fill(fill(execution='e3', intent='reduce', action='reduce_long',
        q='.1', price='9.5', second=2))
    position = state.apply_fee(FeeFact('e3', D(1), 'USD'))
    assert position.average_price.as_tuple().exponent < -28
    assert position.episode_net_pnl.as_tuple().exponent < -28
    values = entry(action='add_long', quantity=position.quantity,
        average_price=position.average_price, requested_cash=D(200), stop_price=D('8.1'),
        episode_realized_gross_pnl=position.episode_net_pnl)
    with pytest.raises(ValueError, match='bounded finite'):
        evaluate_acquisition_risk(contract(), **values)
    acquired = evaluate_acquisition_risk(contract(), **values, filled_position=position)
    assert acquired.allowed
    filled = evaluate_filled_execution_risk(contract(), quantity=position.quantity,
        average_price=position.average_price, stop_price=D('8.1'),
        episode_net_pnl=position.episode_net_pnl, filled_position=position)
    assert filled.allowed
    assert position == state.positions[next(iter(state.positions))]
    # The same actual loss still rejects risk excess; precision is not a waiver.
    rejected = evaluate_acquisition_risk(contract(maximum_position_risk=1.),
        **values, filled_position=position)
    assert not rejected.allowed and 'maximum_position_risk_exceeded' in rejected.reasons
    with pytest.raises(ValueError, match='projection'):
        evaluate_acquisition_risk(contract(), **(values | {'average_price': D(10)}),
            filled_position=position)
    with pytest.raises(ValueError, match='bounded finite'):
        evaluate_acquisition_risk(contract(), **(values | {'requested_cash': D('1e-29')}),
            filled_position=position)


@pytest.mark.parametrize('bad', [D('NaN'), D('Infinity'), D('1e28'), D('1e-109'),
    D('1.' + '1' * 80)])
def test_derived_projection_is_still_finite_magnitude_and_precision_bounded(bad):
    from src.trading_runtime.filled_position_state import FilledPosition
    position = FilledPosition('account', 'program', 'assignment', 'listing', 'ABC',
        quantity=D(1), average_price=bad)
    with pytest.raises(ValueError, match='bounded finite'):
        evaluate_acquisition_risk(contract(), **entry(action='add_long', quantity=D(1),
            average_price=bad), filled_position=position)


def test_entry_includes_both_sides_of_bps_cost():
    result = evaluate_acquisition_risk(contract(cost_bps=10.), **entry())
    assert result.modeled_stop_loss == D("203.8")


@pytest.mark.parametrize("change", [{"quantity": D(1)}, {"execution_price": D(0)},
    {"episode_paid_fees": D(-1)}, {"requested_cash": 1000.},
    {"quantity": D("NaN")}, {"action": "exit"}])
def test_gate_fails_closed_on_invalid_or_unfilled_state(change):
    with pytest.raises(ValueError):
        evaluate_acquisition_risk(contract(), **entry(**change))
