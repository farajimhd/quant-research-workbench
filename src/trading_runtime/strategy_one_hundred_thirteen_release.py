"""Unpublished immutable Strategy57-control price-risk candidate.

This declaration neither registers an executor nor grants native source approval.
"""
from copy import deepcopy
from dataclasses import replace

from . import strategy_fifty_seven_release as parent
from .declared_early_original_risk_policy import INPUT_CONTRACT
from .journal_contract import canonical_json
from .numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from .price_confirmed_original_risk import (
    INPUT, RULE, POLICY_KEY, PriceConfirmedOriginalRiskPolicy,
)

CONTROL_REVISION = 'strategy-one-57:b611dd4f-4c47-45da-88b6-f5f5f04f1f8e'
CONTROL_PAYLOAD_HASH = 'db993542c9408f5a1c76e7576aeedb0076ae8521cb9e6d20ea44004d5a7829ca'
PRICE_POLICY = PriceConfirmedOriginalRiskPolicy((1, 2), (1, 2), 120000, 1000000)
BEHAVIOR = (
    'Retain Strategy57 entries, sizing, aggregate exposure, costs, fixed protection, '
    'reentry and inherited exits, including its entry spread cap and AH early failure. '
    'After inherited exits, during the first 120000 milliseconds from the native '
    'first completed held bucket, exit when a certified completed 5-second candle '
    'wholly after that bucket and a fresh bid both confirm half of original proposal '
    'ask-to-fixed-stop risk lost. Quote age is at most 1000000 microseconds. '
    'Original proposal ask is the reference, not first fill or final average fill. '
    'Finite certified momentum remains required evidence without a directional veto. '
    'No synthetic observations, pending-exit duplication or new cash/order authority. '
    'PM/AH only, Backtest-only; source approval, financial evaluation and publication pending.')


def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=113, executor_revision=113,
        input_contracts=(*prior.input_contracts, DECLARED_FIXED_ADAPTER, INPUT_CONTRACT, INPUT),
        rule_set_contracts=(*prior.rule_set_contracts, RULE),
        behavior_specification=BEHAVIOR, approved_digest='')
    result = replace(draft, approved_digest=draft.digest())
    result.verify()
    return result


def declared_policies():
    import json
    return deepcopy({**parent.INHERITED_POLICIES,
        'half_risk_liquidity_policy': parent.HALF_RISK_LIQUIDITY_POLICY,
        'entry_spread_risk_policy': parent.ENTRY_SPREAD_RISK_POLICY_PAYLOAD,
        'early_original_risk_failure_policy': parent.EARLY_FAILURE_POLICY_PAYLOAD,
        POLICY_KEY: json.loads(canonical_json(PRICE_POLICY.payload()))})
