"""Real inherited factories; no transport, financial or memory acceptance claim."""
from dataclasses import fields

from src.trading_runtime.strategy_one_hundred_eleven_contract import strategy_one_hundred_eleven_contract
from src.trading_runtime.strategy_one_hundred_twelve_contract import strategy_one_hundred_twelve_contract
from src.trading_runtime.strategy_one_hundred_twelve_release import initial_held_recovery_reuse_policy


def test_context_candidate_preserves_every_other_contract_field():
    prior = strategy_one_hundred_eleven_contract()
    candidate = strategy_one_hundred_twelve_contract()
    changed = {field.name for field in fields(prior)
        if getattr(prior, field.name) != getattr(candidate, field.name)}
    assert changed == {'strategy_number', 'release', 'initial_held_recovery_reuse_policy'}
    before = prior.initial_held_recovery_reuse_policy.payload()
    after = candidate.initial_held_recovery_reuse_policy.payload()
    assert before['max_contexts'] == 32
    assert after == {**before, 'max_contexts': 128}
    assert candidate.initial_held_recovery_reuse_policy == initial_held_recovery_reuse_policy(prior.initial_held_recovery_reuse_policy)
    assert candidate.proposal_decision_inventory_reuse_policy == prior.proposal_decision_inventory_reuse_policy
    assert candidate.release.executor_strategy_id == prior.release.executor_strategy_id
    assert candidate.release.evaluation_interval == prior.release.evaluation_interval
    assert candidate.release.input_contracts[:-1] == prior.release.input_contracts
    assert candidate.release.rule_set_contracts[:-1] == prior.release.rule_set_contracts
    candidate.__post_init__()
