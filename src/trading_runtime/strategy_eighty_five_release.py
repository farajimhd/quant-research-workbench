"""Prepared immutable fixed-lot comparison; no installed execution approval."""
from . import strategy_forty_two_release as parent_release
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from .fixed_structural_lot_release_v7 import derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release
from .strategy_registry import NumberedStrategyRelease
from .numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from .fixed_structural_lot_interval_validator_v2 import VALIDATOR_RULE
from src.backend.backtest_fixed_structural_lot_projection_runtime_authority import PROJECTION_RULE
from .fixed_structural_lot_native_source_rule import NATIVE_SOURCE_RULE
from .fixed_structural_lot_causal_clock import CLOCK_RULE
from .independent_lot_initial_stop_lineage import RULE as INITIAL_STOP_RULE
BEHAVIOR = 'Inherit Strategy42 entry, reentry, aggregate sizing, exposure, fees and exit precedence. Three equal independent fixed structural target lots, inherited tick rounding and upward-only stops; no target escalation. Portfolio owns cash and reservations; OMS owns fills, protection and recovery. PM/AH Backtest only; AH requires certified prior-day V7 and causal regular-session warmup. Source@2 verifies full market price authority and complete candidate V7 scope. Validator@2 retains producer child ordering and all causal/count/type/hash checks. Projection@3 separately binds published Backtest runtime hash and installed backend fingerprint/source commit. Native source-certifier@1 uses the real shared certifier. Causal roster clock@1 binds exact OMS and owned committed-fill times to entry and reached100ms boundaries; preserves aligned protection and cold recovery. Pending initial-stop lineage binds original slice ACKs without granting repair coverage. Economics unchanged.'

def release_contract() -> NumberedStrategyRelease:
    parent = parent_release.release_contract()
    values = dict(number=85, executor_strategy_id=parent.executor_strategy_id, executor_revision=85, evaluation_interval=parent.evaluation_interval, input_contracts=(*parent.input_contracts, DECLARED_FIXED_ADAPTER, 'fixed-structural-lot-source@2'), rule_set_contracts=(*parent.rule_set_contracts, 'fixed-structural-lot-entry@1', VALIDATOR_RULE, PROJECTION_RULE, NATIVE_SOURCE_RULE, CLOCK_RULE, INITIAL_STOP_RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release

def derive_strategy_eighty_five_configuration(source, *, approved_code_commit, approved_code_fingerprint, approval_reference):
    """Prepare the declared comparison from an authentic installed parent."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(source.payload['strategy'])
    return derive_fixed_structural_lot_release(source, parent_release=parent_release.release_contract(), release=release_contract(), policy=FixedStructuralLotPolicy().payload(), approved_code_commit=approved_code_commit, approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)

def verify_prepared_strategy_eighty_five_configuration(parent, payload):
    """Reject policy changes under this identity and reconstruct the whole tree."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(parent) is not CertifiedStrategyOneConfiguration or parent.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(parent.payload['strategy'])
    if type(payload) is not dict or parse_fixed_structural_lot_policy(payload['strategy']['parameters'].get('fixed_structural_lot_policy')) != FixedStructuralLotPolicy():
        raise ValueError('Prepared comparison differs from its declared three-equal-lot policy')
    return verify_prepared_fixed_structural_lot_release(parent, payload, parent_release=parent_release.release_contract(), release=release_contract())
