"""Prepared immutable fixed-lot comparison; no installed execution approval."""
from . import strategy_forty_two_release as parent_release
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from .fixed_structural_lot_release import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from .strategy_registry import NumberedStrategyRelease
from .numbered_fixed_strategy import DECLARED_FIXED_ADAPTER


BEHAVIOR = (
    'Inherit Strategy42 entry, reentry, aggregate sizing, exposure limits, '
    'trading cost formula and aggregate exit precedence. Divide an accepted '
    'acquisition into three equal independently protected lots, keeping the '
    'original structural target and the next two higher certified targets. '
    'Use inherited midpoint and nearest-tick target prices. Keep each target '
    'fixed for that lot lifetime and apply inherited upward-only stop '
    'management to remaining lots only after exact per-leg effective '
    'outcomes. Portfolio owns cash and reservations; OMS owns fills, '
    'protection and recovery. Additional orders retain the same fee formula '
    'and may increase total fees. PM/AH Backtest only; AH requires the '
    'certified prior-day V7 checkpoint and full causal regular-session warmup.'
)


def release_contract() -> NumberedStrategyRelease:
    parent = parent_release.release_contract()
    values = dict(
        number=77, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=77, evaluation_interval=parent.evaluation_interval,
        input_contracts=(*parent.input_contracts, DECLARED_FIXED_ADAPTER, 'fixed-structural-lot-source@1'),
        rule_set_contracts=(*parent.rule_set_contracts, 'fixed-structural-lot-entry@1'),
        behavior_specification=BEHAVIOR,
    )
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release


def derive_strategy_seventy_seven_configuration(
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


def verify_prepared_strategy_seventy_seven_configuration(parent, payload):
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
