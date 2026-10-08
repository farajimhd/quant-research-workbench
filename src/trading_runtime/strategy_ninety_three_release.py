"""Prepared immutable fixed-lot comparison; no installed execution approval."""
from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE as IDENTITY_RULE, BATCHED_DETAIL_SELECT_RULE as BATCHED_RULE, SELECTED_CHECKPOINT_PRODUCT_RULE as CHECKPOINT_RULE, OPERATION_CHECKPOINT_READER_RULE as READER_RULE
from . import strategy_forty_two_release as parent_release
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from .fixed_structural_lot_release_v14 import derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release
from .strategy_registry import NumberedStrategyRelease
from .numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from .fixed_structural_lot_interval_validator_v2 import VALIDATOR_RULE
from src.backend.backtest_fixed_structural_lot_projection_runtime_authority import PROJECTION_RULE
from .fixed_structural_lot_native_source_rule import NATIVE_SOURCE_RULE
from .fixed_structural_lot_causal_clock import CLOCK_RULE
from .independent_lot_initial_stop_lineage import RULE as INITIAL_STOP_RULE
from .fixed_structural_lot_warm_proof import RULE as WARM_RULE
from .decimal_snapshot_readback import RULE as DECIMAL_RULE
from .packet_validation_reuse_policy import INPUT as REUSE_INPUT, RULE as REUSE_RULE, PacketValidationReusePolicy
VALIDATION_REUSE_POLICY = PacketValidationReusePolicy(2, 30033, 64 * 1024 * 1024)
BEHAVIOR = 'Inherit unchanged Strategy42 entries, reentries, sizing, aggregate exposure, fees and exit precedence. Retain three equal independent structural lots, earned targets, canonical100ms fills, ACK lineage, warm proof, source equivalence and current/historical checkpoint recovery. Retain exact Decimal readback and operation-bound readers. Reuse successful pure packet content validation only, with two retained entries, 30033 scalar rows and 64 MiB conservatively counted keys. Guard every ordered row, exact scalar type and Float64 bit pattern before and after validation. Never reuse failed or mutated content. Source, ownership, admission, cash, liquidity, fills, protection and recovery checks execute independently. Backtest-only; no profitability or speed acceptance claim.'

def release_contract() -> NumberedStrategyRelease:
    parent = parent_release.release_contract()
    values = dict(number=93, executor_strategy_id=parent.executor_strategy_id, executor_revision=93, evaluation_interval=parent.evaluation_interval, input_contracts=(*parent.input_contracts, DECLARED_FIXED_ADAPTER, 'fixed-structural-lot-source@2', REUSE_INPUT), rule_set_contracts=(*parent.rule_set_contracts, 'fixed-structural-lot-entry@1', VALIDATOR_RULE, PROJECTION_RULE, NATIVE_SOURCE_RULE, CLOCK_RULE, INITIAL_STOP_RULE, WARM_RULE, IDENTITY_RULE, BATCHED_RULE, CHECKPOINT_RULE, READER_RULE, DECIMAL_RULE, REUSE_RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release

def derive_strategy_ninety_three_configuration(source, *, approved_code_commit, approved_code_fingerprint, approval_reference):
    """Prepare the declared comparison from an authentic installed parent."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(source.payload['strategy'])
    return derive_fixed_structural_lot_release(source, parent_release=parent_release.release_contract(), release=release_contract(), policy=FixedStructuralLotPolicy().payload(), reuse_policy=VALIDATION_REUSE_POLICY, approved_code_commit=approved_code_commit, approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)

def verify_prepared_strategy_ninety_three_configuration(parent, payload):
    """Reject policy changes under this identity and reconstruct the whole tree."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(parent) is not CertifiedStrategyOneConfiguration or parent.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(parent.payload['strategy'])
    if type(payload) is not dict or parse_fixed_structural_lot_policy(payload['strategy']['parameters'].get('fixed_structural_lot_policy')) != FixedStructuralLotPolicy():
        raise ValueError('Prepared comparison differs from its declared three-equal-lot policy')
    from .packet_validation_reuse_policy import parse_packet_validation_reuse_policy
    if parse_packet_validation_reuse_policy(payload['strategy']['parameters'].get('packet_validation_reuse_policy')) != VALIDATION_REUSE_POLICY:
        raise ValueError('Prepared reuse policy differs from declared bounds')
    return verify_prepared_fixed_structural_lot_release(parent, payload, parent_release=parent_release.release_contract(), release=release_contract(), reuse_policy=VALIDATION_REUSE_POLICY)

