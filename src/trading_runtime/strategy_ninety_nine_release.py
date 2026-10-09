"""Prepared successor; no registration, source approval or financial result."""
from .strategy_ninety_eight_release import release_contract as prior_release
from .selected_exit_publication_policy import INPUT, RULE, SelectedExitPublicationPolicy
from .strategy_registry import NumberedStrategyRelease

SELECTED_EXIT_POLICY = SelectedExitPublicationPolicy(1)
BEHAVIOR = (
    'Preserve Strategy98 activation, entries, additions, reentries, sizing, aggregate exposure, '
    'cash, trading costs, fixed-lot protection, decision clocks and complete market transport. '
    'Publish selected-entry exits through the original typed writer with the exact installed '
    'source, verified committed predecessor and identical price authority, including inherited '
    'followthrough without an original-risk diagnostic. Retain complete entry ancestry, issued '
    'ownership, source certification, child validation and cold recovery. Missing or foreign '
    'context fails closed. Prepared only; no registration, source approval, native integration, '
    'financial result or full-session speed acceptance.'
)


def release_contract():
    prior = prior_release()
    values = dict(number=99, executor_strategy_id=prior.executor_strategy_id,
                  executor_revision=99, evaluation_interval=prior.evaluation_interval,
                  input_contracts=(*prior.input_contracts, INPUT),
                  rule_set_contracts=(*prior.rule_set_contracts, RULE),
                  behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result
