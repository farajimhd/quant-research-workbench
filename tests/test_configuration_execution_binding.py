from dataclasses import asdict, replace
from datetime import datetime, UTC

import pytest

from src.trading_runtime.configuration_execution_binding import (
    POLICY, SEAL, project_execution_binding, restore_execution_binding, bind_execution_configuration)
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionPolicyName


def binding():
    entry = ExecutionPolicy(policy_id='entry', name=ExecutionPolicyName.ADAPTIVE_URGENT)
    exit = replace(entry, policy_id='exit', name=ExecutionPolicyName.ADAPTIVE_VERY_URGENT)
    return project_execution_binding(configuration_id='00000000-0000-0000-0000-000000000001',
        configuration_hash='a' * 64, policies=(exit, entry), entry_policy_id='entry',
        exit_policy_id='exit', tick_size=.01, limit_offset_bps=5., published_at=datetime.now(UTC))


def test_execution_binding_roundtrips_native_objects_and_action_selections():
    actual = binding()
    restored = restore_execution_binding(actual.rows)
    assert restored.header == actual.header
    assert asdict(restored.policy_for('enter_long')) == asdict(actual.policies['entry'])
    assert restored.policy_for('exit') is restored.policies['exit']
    with pytest.raises(ValueError, match='recognize'):
        restored.policy_for('invented')
    assert all('JSON' not in kind for table in (POLICY, SEAL) for _, kind in table.columns)


def test_binding_joins_only_its_exact_selection_without_mutating_it():
    actual = binding()
    revision = dict(revision_id='strategy-configuration:' + actual.header['configuration_id'],
        selection_hash=actual.header['configuration_hash'], payload={'oms': {'limit_offset_bps': 5.}})
    result = bind_execution_configuration(revision, actual)
    assert result['payload']['oms']['entry_execution_policy_id'] == 'entry'
    assert result['execution_binding_hash'] == actual.header['content_hash']
    assert revision['payload']['oms'] == {'limit_offset_bps': 5.}
    assert bind_execution_configuration(result, actual) == result
    with pytest.raises(ValueError, match='selected configuration'):
        bind_execution_configuration({**revision, 'selection_hash': 'b' * 64}, actual)


def test_program_proposals_resolve_entry_and_exit_through_native_intents():
    from src.trading_runtime.columnar_program_coordinator import ProgramProposal
    from src.trading_runtime.execution_policies import ProtectionProfile, ProtectionSlice, StopRule
    actual = binding()
    at = datetime.now(UTC)
    proposal = ProgramProposal('00000000-0000-0000-0000-000000000002', 'entry', 'enter_long',
        at, 'ABC', 0., 10., 1000., 5., 'frozen-rule')
    profile = ProtectionProfile('frozen-stop', 1, (ProtectionSlice('one', 1., StopRule(price=5.)),))
    entry = proposal.to_intent(execution_policy=actual, protection_profile=profile, outside_rth=True)
    assert entry.execution_policy == actual.policies['entry']
    exit = replace(proposal, stage='exit', action='exit', quantity=100., requested_cash=None)
    intent = exit.to_intent(execution_policy=actual, protection_profile=None, outside_rth=True)
    assert intent.execution_policy == actual.policies['exit']
    assert entry.capital_request.mode == 'fixed_notional' and entry.capital_request.value == 1000.


@pytest.mark.parametrize('case', ['policy_drift', 'selection_drift', 'missing', 'duplicate'])
def test_execution_binding_rejects_unsealed_changes_and_partial_rows(case):
    actual = binding()
    rows = {name: [dict(row) for row in values] for name, values in actual.rows.items()}
    if case == 'policy_drift':
        rows[POLICY.name][0]['deadline_ms'] += 1
    elif case == 'selection_drift':
        rows[SEAL.name][0]['entry_policy_id'] = 'other'
    elif case == 'missing':
        rows[POLICY.name].pop()
    else:
        rows[POLICY.name].append(rows[POLICY.name][0])
    with pytest.raises(ValueError):
        restore_execution_binding(rows)
