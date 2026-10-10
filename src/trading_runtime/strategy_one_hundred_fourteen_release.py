"""Immutable two-candle successor declaration; native source approval pending."""
from copy import deepcopy
from dataclasses import replace
import json

from . import strategy_one_hundred_thirteen_release as parent
from .consecutive_price_confirmed_risk import INPUT, RULE, POLICY_KEY, ConsecutivePriceRiskPolicy
from .price_confirmed_original_risk import INPUT as SINGLE_INPUT, RULE as SINGLE_RULE, POLICY_KEY as SINGLE_KEY
from .journal_contract import canonical_json

CONTROL_REVISION = 'strategy-one-113:d1d9d803-1788-45e7-b61e-8766b596ea53'
CONTROL_PAYLOAD_HASH = '4df93b7527d7a6895febcfad3734d6fd13bda45df7b492a803427441b3356c0a'

PRICE_POLICY = ConsecutivePriceRiskPolicy(parent.PRICE_POLICY)
BEHAVIOR = (
    'Retain Strategy113 entries, reentry, sizing, exposure, costs, fixed protection '
    'and inherited exits. Replace only its single-candle price-risk extension: '
    'require two consecutive certified completed 5s closes and a fresh bid at or '
    'below original proposal ask minus half its fixed-stop risk. Both candles '
    'must be wholly after first held and share certified session, build, bars, '
    'indicators and liquidity identities. Eligibility is 120000ms; quote age '
    'at most 1000000us. Require finite MACD evidence without a direction veto. '
    'Inherited exits retain priority, including early AH weak-positive MACD '
    'failure. Preserve both witnesses and firing rule through checkpoint, '
    'journal and recovery. Portfolio and OMS retain cash, sizing, reservations, '
    'fills and OCA authority. PM/AH only; 100ms decisions; Backtest-only. '
    'Source approval, financial evaluation and app deployment pending.')



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
