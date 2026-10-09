"""Immutable Portfolio admission alternative, retaining Strategy105 economics."""
from .strategy_one_hundred_five_release import release_contract as prior_release
from .strategy_registry import NumberedStrategyRelease
from .portfolio_acquisition_policy import RULE
from .portfolio_acquisition_contract import SessionAcquisitionQuotaPolicy

def acquisition_policy():
    return SessionAcquisitionQuotaPolicy(maximum=1,scope='independent_native_session')
BEHAVIOR=('Preserve every Strategy105 input, target, stop, exit, sizing, cost, fill and OCA rule. '
    'Portfolio permits at most one positive accepted entry reservation per ticker/account in the original '
    'independent native session. Released and filled reservations remain acquisitions; rejected requests do not. '
    'Multiple protected lots are one acquisition. Same-intent retries remain idempotent. '
    'Original committed run-window authority is mandatory across cold recovery. Backtest only.')
def release_contract():
    prior=prior_release()
    values=dict(number=106,executor_strategy_id=prior.executor_strategy_id,executor_revision=106,
        evaluation_interval=prior.evaluation_interval,input_contracts=prior.input_contracts,
        rule_set_contracts=(*prior.rule_set_contracts,RULE),behavior_specification=BEHAVIOR)
    draft=NumberedStrategyRelease(**values,approved_digest='')
    result=NumberedStrategyRelease(**values,approved_digest=draft.digest());result.verify();return result

def derive_strategy_one_hundred_six_configuration(parent,**approval):
    from .fixed_structural_lot_release_v25 import derive_fixed_structural_lot_release
    from .strategy_one_hundred_five_release import derive_strategy_one_hundred_five_configuration
    return derive_fixed_structural_lot_release(parent,inherited_derive=derive_strategy_one_hundred_five_configuration,
        inherited_release=prior_release(),release=release_contract(),acquisition_policy=acquisition_policy(),**approval)

def verify_prepared_strategy_one_hundred_six_configuration(parent,payload):
    from .fixed_structural_lot_release_v25 import verify_prepared_fixed_structural_lot_release
    from .strategy_one_hundred_five_release import derive_strategy_one_hundred_five_configuration
    return verify_prepared_fixed_structural_lot_release(parent,payload,inherited_derive=derive_strategy_one_hundred_five_configuration,
        inherited_release=prior_release(),release=release_contract(),acquisition_policy=acquisition_policy())
