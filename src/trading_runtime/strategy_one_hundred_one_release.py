"""Immutable successor to exact99; catalog installation grants no publication."""
from .strategy_ninety_nine_release import release_contract as prior_release
from .fixed_lot_management_reuse_policy import INPUT, RULE, FixedLotManagementReusePolicy
from .strategy_registry import NumberedStrategyRelease

MANAGEMENT_REUSE_POLICY = FixedLotManagementReusePolicy(1, 32)
BEHAVIOR = (
    'Preserve every Strategy99 trading rule, price authority, economic configuration, '
    'entry, addition, reentry, stop, target, exposure, sizing, cash, cost and decision clock. '
    'Reuse only operation-issued complete entry decisions and exact decision-local reads '
    'under the same native owner, lease, committed head and content. Invalidate before '
    'commands and independently confirm after commands. Cold recovery independently '
    'verifies complete native evidence. Missing, foreign or changed authority fails closed. '
    'Backtest only; registration is not source approval or financial acceptance.'
)


def release_contract():
    prior = prior_release()
    values = dict(number=101, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=101, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def derive_strategy_one_hundred_one_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    from .fixed_structural_lot_release_v20 import derive_fixed_structural_lot_release
    from .strategy_ninety_nine_release import _compiler_arguments, _verify_parent
    from .fixed_structural_lot_policy import FixedStructuralLotPolicy
    _verify_parent(source)
    prepared = derive_fixed_structural_lot_release(source, inherited_arguments=_compiler_arguments(),
        release=release_contract(), management_reuse_policy=MANAGEMENT_REUSE_POLICY,
        policy=FixedStructuralLotPolicy().payload(), approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)
    # Registered derivation is the publisher/reader transfer contract. The
    # internal compiler retains nodes for typed verification and reconstruction.
    return {key: prepared[key] for key in ('source_candidate_id', 'source_candidate_hash',
        'payload_hash', 'node_hash', 'node_count', 'payload')}


def verify_prepared_strategy_one_hundred_one_configuration(parent, payload):
    from .fixed_structural_lot_release_v20 import verify_prepared_fixed_structural_lot_release
    from .strategy_ninety_nine_release import _compiler_arguments, _verify_parent
    _verify_parent(parent)
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        inherited_arguments=_compiler_arguments(), release=release_contract(),
        management_reuse_policy=MANAGEMENT_REUSE_POLICY)
