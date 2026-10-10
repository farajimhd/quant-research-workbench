"""Immutable two-candle successor declaration; native source approval pending."""
from copy import deepcopy
from dataclasses import replace
import json

from . import strategy_one_hundred_thirteen_release as parent
from .consecutive_price_confirmed_risk import INPUT, RULE, POLICY_KEY, ConsecutivePriceRiskPolicy
from .price_confirmed_original_risk import INPUT as SINGLE_INPUT, RULE as SINGLE_RULE, POLICY_KEY as SINGLE_KEY
from .journal_contract import canonical_json

PRICE_POLICY = ConsecutivePriceRiskPolicy(parent.PRICE_POLICY)
BEHAVIOR = (
    'Retain exact Strategy57 entries, reentry, sizing, aggregate exposure, costs, '
    'fixed protection and inherited exits selected by Strategy113. Replace only '
    'its single-candle price-risk extension with two consecutive certified completed '
    '5-second closes at or below half the original proposal-ask-to-fixed-stop risk, '
    'plus a fresh current bid at or below that threshold. Both candles must be '
    'wholly after the native first completed held bucket and use identical certified '
    'session/build/bars/indicators/liquidity source identities. Eligibility remains '
    '120000 milliseconds and quote freshness at most 1000000 microseconds. '
    'Finite completed MACD values remain evidence without a direction veto. '
    'Inherited exits retain priority, including the exact early AH weak-positive '
    'MACD-level failure rule. Preserve both producer witnesses and the selected '
    'firing rule through native checkpoint, journal publication and recovery. '
    'Portfolio and OMS retain all cash, sizing, reservations, fills and OCA authority. '
    'PM/AH only, 100ms decisions, Backtest-only; financial evaluation, source '
    'approval, native publication and app deployment remain pending.')


def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=114, executor_revision=114,
        input_contracts=tuple(item for item in prior.input_contracts if item != SINGLE_INPUT) + (INPUT,),
        rule_set_contracts=tuple(item for item in prior.rule_set_contracts if item != SINGLE_RULE) + (RULE,),
        behavior_specification=BEHAVIOR, approved_digest='')
    result = replace(draft, approved_digest=draft.digest())
    result.verify()
    return result


def declared_policies():
    policies = deepcopy(parent.declared_policies())
    del policies[SINGLE_KEY]
    policies[POLICY_KEY] = json.loads(canonical_json(PRICE_POLICY.payload()))
    return policies
