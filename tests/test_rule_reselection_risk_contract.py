"""The isolated verifier must make the same real risk-gated selection."""
from types import MappingProxyType
from dataclasses import replace

import pytest

from src.backend import rule_decision_reselection as module
from src.backend.program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF, program_policy_fingerprint
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation
from tests.test_columnar_program_actor import actor_inputs
from tests.test_configuration_execution_risk import contract


@pytest.mark.parametrize('risk,invalid_stop', ((None, False), (100., False), (300., False), (300., True)))
def test_recovered_reselection_preserves_actual_selected_risk_contract(monkeypatch, risk, invalid_stop):
    original, boundary, *_ = actor_inputs()
    if invalid_stop:
        boundary = replace(boundary, initial_stop=7.)
    selected_risk = None if risk is None else contract(maximum_position_risk=risk)
    original = AssignedProgramStrategy(original.program_release, original.assignments(), original._listings,
        execution_risk=selected_risk)
    observation = AssignmentOrderObservation('account', 'assignment', 'ABC', boundary.boundary_at,
        False, False, (), (), 0.)
    live = AssignedProgramStrategy(original.program_release, original.assignments(), original._listings,
        execution_risk=selected_risk)
    expected = live.select_from_orders(boundary, assignment_id='assignment', observation=observation)
    packet = RecoveredProgramPolicy(original, 3, 0, 0, MappingProxyType({}), 'run', ('c'*64,),
        program_policy_fingerprint(original), _RECOVERY_PROOF)
    parent = dict(run_id='run', basis_sequence=3, source_batch_sequence=1, source_commit_hash='c'*64)
    # Scope persistence and prepared-data verification have their native funnel
    # tests. This seam exercises the isolated actor's real selector and risk gate.
    def compare_selection(*args, actor, **kwargs):
        actual = actor.select_from_orders(boundary, assignment_id='assignment', observation=observation)
        assert actual.status == expected.status
        assert actual.proposals == expected.proposals
        assert actor.execution_risk == selected_risk
        return actual
    monkeypatch.setattr(module, 'reselect_rule_decision', compare_selection)
    actual = module.reselect_recovered_rule_decision(parent, (), (), packet=packet)
    assert actual == expected
    assert program_policy_fingerprint(original) == packet.state_hash
    assert original._selection_clocks == {} and original.risk_rejections == {}
    if risk == 100.:
        assert actual.status == 'risk_rejected:maximum_position_risk_exceeded'
    if invalid_stop:
        assert actual.status.startswith('risk_rejected:')
        assert 'maximum_stop_distance_exceeded' in actual.status
