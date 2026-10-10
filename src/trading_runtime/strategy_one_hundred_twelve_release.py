"""Unpublished context-capacity candidate; native speed and profit unproved."""
from dataclasses import replace

from .strategy_one_hundred_eleven_release import release_contract as prior_release
from .strategy_registry import NumberedStrategyRelease

INPUT = 'declared-initial-held-context-capacity@1'
RULE = 'initial-held-bounded-context-capacity@1'
BEHAVIOR = (
    'Preserve every Strategy111 trading, sizing, cash, exposure, cost and exit rule. '
    'Declare capacity for 128 initial-held entry/recovery contexts instead of 32. '
    'Preserve the inherited inventory byte, row and entry limits, complete first '
    'verification, mutable source and content guards, and complete cold overflow '
    'fallback. Rejected proposals remain authoritative. No change to proposal '
    'decision reuse bounds. Backtest only; memory, full-session speed and profit '
    'acceptance remain unproved.')


def initial_held_recovery_reuse_policy(inherited_policy):
    return replace(inherited_policy, max_contexts=128)


def release_contract():
    prior = prior_release()
    values = dict(number=112, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=112, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE),
        behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result
