"""Prepared immutable fixed-lot comparison; no installed execution approval."""
from . import strategy_forty_two_release as parent_release
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from .fixed_structural_lot_release_v5 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from .strategy_registry import NumberedStrategyRelease
from .numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from .fixed_structural_lot_interval_validator_v2 import VALIDATOR_RULE
from src.backend.backtest_fixed_structural_lot_projection_runtime_authority import PROJECTION_RULE


BEHAVIOR = 'Inherit Strategy42 entry, reentry, aggregate sizing, exposure, fees and exit precedence. Split each acquisition into three equal independently protected lots using the original structural target and next two higher certified targets. Fixed targets, inherited midpoint/nearest tick and upward-only stops; exact per-leg outcomes. Portfolio owns cash/reservations; OMS owns fills/protection/recovery. PM/AH Backtest only; AH requires certified prior-day V7 and causal regular-session warmup. Source@2 retains full market price authority and exact complete candidate V7 scope. Validator@2 hashes children in producer (valid_from_ms,level_id) order, retaining all count, clock, type, causal and hash checks. Projection authority@3 validates the published runtime Backtest hash and installed backend fingerprint/source commit separately. Run/source/configuration and canonical seven-field Start remain bound. Economics and broker ordering remain inherited.'


def release_contract() -> NumberedStrategyRelease:
    parent = parent_release.release_contract()
    values = dict(
        number=83, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=83, evaluation_interval=parent.evaluation_interval,
        input_contracts=(*parent.input_contracts, DECLARED_FIXED_ADAPTER, 'fixed-structural-lot-source@2'),
        rule_set_contracts=(*parent.rule_set_contracts, 'fixed-structural-lot-entry@1', VALIDATOR_RULE, PROJECTION_RULE),
        behavior_specification=BEHAVIOR,
    )
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def derive_strategy_eighty_three_configuration(
    source, *, approved_code_commit, approved_code_fingerprint, approval_reference,
):
    """Prepare the declared comparison from an authentic installed parent."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(source.payload['strategy'])
    return derive_fixed_structural_lot_release(
        source, parent_release=parent_release.release_contract(),
        release=release_contract(), policy=FixedStructuralLotPolicy().payload(),
        approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference,
    )


def verify_prepared_strategy_eighty_three_configuration(parent, payload):
    """Reject policy changes under this identity and reconstruct the whole tree."""
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(parent) is not CertifiedStrategyOneConfiguration or parent.strategy_number != 42:
        raise ValueError('Fixed-lot comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(parent.payload['strategy'])
    if (type(payload) is not dict
            or parse_fixed_structural_lot_policy(
                payload['strategy']['parameters'].get('fixed_structural_lot_policy'))
            != FixedStructuralLotPolicy()):
        raise ValueError('Prepared comparison differs from its declared three-equal-lot policy')
    return verify_prepared_fixed_structural_lot_release(
        parent, payload, parent_release=parent_release.release_contract(),
        release=release_contract(),
    )
