"""Immutable Structural management cadence alternative, preserving Strategy106 entry, sizing, costs and exit rules."""
from .strategy_one_hundred_six_release import release_contract as prior_release
from .strategy_registry import NumberedStrategyRelease
from .fixed_lot_management_cadence_policy import RULE
from .fixed_lot_management_cadence_policy import FixedLotManagementCadencePolicy

def management_cadence_policy():
    return FixedLotManagementCadencePolicy(5000)
BEHAVIOR=('Preserve Strategy106 sizing, costs, entry and broker 100ms clocks, fixed targets and inherited exit rules. '
    'Structural per-lot ratchets run at completed 5000ms boundaries, with immediate first-held, '
    'cold-restored and changed execution-state processing. Causal resistance witnesses accumulate '
    'between decisions. This explicitly delays structural protection; Backtest only.')

def release_contract():
    prior=prior_release()
    values=dict(number=107,executor_strategy_id=prior.executor_strategy_id,executor_revision=107,
        evaluation_interval=prior.evaluation_interval,input_contracts=prior.input_contracts,
        rule_set_contracts=(*prior.rule_set_contracts,RULE),behavior_specification=BEHAVIOR)
    draft=NumberedStrategyRelease(**values,approved_digest='')
    result=NumberedStrategyRelease(**values,approved_digest=draft.digest());result.verify();return result

def derive_strategy_one_hundred_seven_configuration(parent,**approval):
    from .fixed_structural_lot_release_v26 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_six_release import derive_strategy_one_hundred_six_configuration
    return derive_fixed_structural_lot_release(parent,inherited_derive=derive_strategy_one_hundred_six_configuration,
        inherited_release=prior_release(),release=release_contract(),management_cadence_policy=management_cadence_policy(),**approval)

def verify_prepared_strategy_one_hundred_seven_configuration(parent,payload):
    from .fixed_structural_lot_release_v26 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_six_release import derive_strategy_one_hundred_six_configuration
    return verify_prepared_fixed_structural_lot_release(parent,payload,inherited_derive=derive_strategy_one_hundred_six_configuration,
        inherited_release=prior_release(),release=release_contract(),management_cadence_policy=management_cadence_policy())
