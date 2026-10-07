"""Prepared immutable fixed-lot comparison; no installed execution approval."""
from src.trading_runtime.strategy_registry import IMMUTABLE_NUMBERED_IDENTITY_RULE as IDENTITY_RULE, BATCHED_DETAIL_SELECT_RULE as BATCHED_RULE
from . import strategy_forty_two_release as parent_release
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from .fixed_structural_lot_release_v10 import derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release
from .strategy_registry import NumberedStrategyRelease
from .numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from .fixed_structural_lot_interval_validator_v2 import VALIDATOR_RULE
from src.backend.backtest_fixed_structural_lot_projection_runtime_authority import PROJECTION_RULE
from .fixed_structural_lot_native_source_rule import NATIVE_SOURCE_RULE
from .fixed_structural_lot_causal_clock import CLOCK_RULE
from .independent_lot_initial_stop_lineage import RULE as INITIAL_STOP_RULE
from .fixed_structural_lot_warm_proof import RULE as WARM_RULE
BEHAVIOR = 'Inherit Strategy42 entry, reentry, sizing, exposure, fees and exit precedence. Three equal fixed structural target lots; tick rounding and upward-only stops, no target escalation. Portfolio owns cash; OMS owns fills/recovery. Certified V7 PM/AH Backtest only. Source@2/validator@2 preserve causal price/candidate scope; projection@3 binds distinct runtime/backend hashes. Real native source certification; exact OMS/owned fill clocks at reached100ms boundaries; exact initial ACK lineage. Exclusive-writer warm proof reuses verified immutable transport at unchanged source/context/lease/frontier; checkpoints retain exact financial owner. Installed sealed registry selects exact certified revision/run-plan identity. Declared batched detail reads use bounded outer SELECTs with the strict warm guard unchanged. Economics unchanged.'

def release_contract() -> NumberedStrategyRelease:
    parent = parent_release.release_contract()
    values = dict(number=88, executor_strategy_id=parent.executor_strategy_id, executor_revision=88, evaluation_interval=parent.evaluation_interval, input_contracts=(*parent.input_contracts, DECLARED_FIXED_ADAPTER, 'fixed-structural-lot-source@2'), rule_set_contracts=(*parent.rule_set_contracts, 'fixed-structural-lot-entry@1', VALIDATOR_RULE, PROJECTION_RULE, NATIVE_SOURCE_RULE, CLOCK_RULE, INITIAL_STOP_RULE, WARM_RULE, IDENTITY_RULE, BATCHED_RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release

def derive_strategy_eighty_eight_configuration(source, *, approved_code_commit, approved_code_fingerprint, approval_reference):
    """Prepare the declared comparison from an authentic installed parent."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(source.payload['strategy'])
    return derive_fixed_structural_lot_release(source, parent_release=parent_release.release_contract(), release=release_contract(), policy=FixedStructuralLotPolicy().payload(), approved_code_commit=approved_code_commit, approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)

def verify_prepared_strategy_eighty_eight_configuration(parent, payload):
    """Reject policy changes under this identity and reconstruct the whole tree."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(parent) is not CertifiedStrategyOneConfiguration or parent.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(parent.payload['strategy'])
    if type(payload) is not dict or parse_fixed_structural_lot_policy(payload['strategy']['parameters'].get('fixed_structural_lot_policy')) != FixedStructuralLotPolicy():
        raise ValueError('Prepared comparison differs from its declared three-equal-lot policy')
    return verify_prepared_fixed_structural_lot_release(parent, payload, parent_release=parent_release.release_contract(), release=release_contract())
