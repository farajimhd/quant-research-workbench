"""Immutable initial and proposal verification reuse, preserving Strategy107 behavior."""
from .strategy_one_hundred_seven_release import release_contract as prior_release
from .strategy_registry import NumberedStrategyRelease
from .initial_held_recovery_reuse_policy import RULE,INPUT
from .initial_held_recovery_reuse_policy import InitialHeldRecoveryReusePolicy

from .proposal_decision_inventory_reuse_policy import INPUT as PROPOSAL_INPUT,RULE as PROPOSAL_RULE,ProposalDecisionInventoryReusePolicy

def proposal_decision_inventory_reuse_policy():
    return ProposalDecisionInventoryReusePolicy(32,8,100000,67108864)

def initial_held_recovery_reuse_policy():
    return InitialHeldRecoveryReusePolicy(32,8,100000,67108864)
BEHAVIOR=('Preserve the complete Strategy107 entry, quota, sizing, costs, fixed targets,5000ms structural cadence and inherited exit clocks. '
    'Initial-held operations may reuse independently verified normalized context and OMS inventories only within one exact owned fence, '
    'with full mutable source/content/code/lease checks and bounded private copies. Separately declared proposal-decision reuse applies only after the existing full issued decision proof. '
    'Retention-budget overflow executes the complete original path without truncation. Cold and changed-frontier reads remain complete. Backtest only.')

def release_contract():
    prior=prior_release()
    values=dict(number=108,executor_strategy_id=prior.executor_strategy_id,executor_revision=108,
        evaluation_interval=prior.evaluation_interval,input_contracts=(*prior.input_contracts,INPUT,PROPOSAL_INPUT),
        rule_set_contracts=(*prior.rule_set_contracts,RULE,PROPOSAL_RULE),behavior_specification=BEHAVIOR)
    draft=NumberedStrategyRelease(**values,approved_digest='')
    result=NumberedStrategyRelease(**values,approved_digest=draft.digest());result.verify();return result

def derive_strategy_one_hundred_eight_configuration(parent,**approval):
    from .fixed_structural_lot_release_v27 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_seven_release import derive_strategy_one_hundred_seven_configuration
    return derive_fixed_structural_lot_release(parent,inherited_derive=derive_strategy_one_hundred_seven_configuration,
        inherited_release=prior_release(),release=release_contract(),initial_held_recovery_reuse_policy=initial_held_recovery_reuse_policy(),proposal_decision_inventory_reuse_policy=proposal_decision_inventory_reuse_policy(),**approval)

def verify_prepared_strategy_one_hundred_eight_configuration(parent,payload):
    from .fixed_structural_lot_release_v27 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_seven_release import derive_strategy_one_hundred_seven_configuration
    return verify_prepared_fixed_structural_lot_release(parent,payload,inherited_derive=derive_strategy_one_hundred_seven_configuration,
        inherited_release=prior_release(),release=release_contract(),initial_held_recovery_reuse_policy=initial_held_recovery_reuse_policy(),proposal_decision_inventory_reuse_policy=proposal_decision_inventory_reuse_policy())
