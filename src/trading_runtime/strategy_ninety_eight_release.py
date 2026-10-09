"""Prepared transport successor; registration and native approval stay closed."""
from .strategy_ninety_six_release import (
    release_contract as prior_release, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY,
    OWNED_SNAPSHOT_POLICY, EMPTY_CONFIRMATION_POLICY,
)
from .complete_market_window_policy import INPUT, RULE, CompleteMarketWindowPolicy, parse_complete_market_window_policy
from .strategy_registry import NumberedStrategyRelease
from .fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from . import strategy_forty_two_release as parent_release

COMPLETE_MARKET_POLICY = CompleteMarketWindowPolicy(1, 300000, 4 * 1024 * 1024,
    10000, 64 * 1024 * 1024, 10000, 256)
BEHAVIOR = ('Preserve Strategy96 trading economics, cash, sizing, exposure, costs, entries, reentries, '
    'fixed lots, exits and recovery. Select complete bounded market responses through exact declared '
    'source and factory only. Finish every HTTP response before releasing its window; retain certified '
    'source and price plans, completed clock ordering, sequential liquidity, cash, fills and OCA. '
    'Abort partial responses or exhausted budgets without retry, truncation, timeout changes or spool. '
    'Retain commandless confirmation only when no commands or recovery require acknowledgement history. '
    'Prepared only; no registration, native approval, publication, financial or full-session speed result.')


def release_contract():
    prior = prior_release()
    values = dict(number=98, executor_strategy_id=prior.executor_strategy_id,
        executor_revision=98, evaluation_interval=prior.evaluation_interval,
        input_contracts=(*prior.input_contracts, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE), behavior_specification=BEHAVIOR)
    draft = NumberedStrategyRelease(**values, approved_digest='')
    result = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    result.verify()
    return result


def derive_strategy_ninety_eight_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .fixed_structural_lot_release_v18 import derive_fixed_structural_lot_release
    if type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 42:
        raise ValueError('Complete market comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(source.payload['strategy'])
    return derive_fixed_structural_lot_release(source, parent_release=parent_release.release_contract(),
        release=release_contract(), policy=FixedStructuralLotPolicy().payload(),
        reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY,
        owned_snapshot_policy=OWNED_SNAPSHOT_POLICY, empty_confirmation_policy=EMPTY_CONFIRMATION_POLICY,
        complete_market_policy=COMPLETE_MARKET_POLICY, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference)


def verify_prepared_strategy_ninety_eight_configuration(parent, payload):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    from .fixed_structural_lot_release_v18 import verify_prepared_fixed_structural_lot_release
    if type(parent) is not CertifiedStrategyOneConfiguration or parent.strategy_number != 42:
        raise ValueError('Complete market comparison requires certified Strategy42 parent')
    parent_release.verify_strategy_forty_two_manifest(parent.payload['strategy'])
    if parse_fixed_structural_lot_policy(payload['strategy']['parameters'].get(
            'fixed_structural_lot_policy')) != FixedStructuralLotPolicy():
        raise ValueError('Prepared comparison differs from declared fixed lot policy')
    if parse_complete_market_window_policy(payload['strategy']['parameters'].get(
            'complete_market_window_policy')) != COMPLETE_MARKET_POLICY:
        raise ValueError('Prepared comparison differs from declared complete market policy')
    # Full v18 reconstruction validates every inherited field against the same
    # complete certified parent; no fields are stripped to make comparison pass.
    return verify_prepared_fixed_structural_lot_release(parent, payload,
        parent_release=parent_release.release_contract(), release=release_contract(),
        reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY,
        owned_snapshot_policy=OWNED_SNAPSHOT_POLICY, empty_confirmation_policy=EMPTY_CONFIRMATION_POLICY,
        complete_market_policy=COMPLETE_MARKET_POLICY)
