"""Immutable native bounded-market-read successor."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_twenty_seven_release as parent
from .bounded_market_read_policy import BoundedMarketReadPolicy, RULE, SOURCE_CONTRACT, POLICY_KEY
CONTROL_REVISION = 'strategy-one-127:97f9e3f4-1a4f-49da-ab40-54e08c7cab86'
CONTROL_PAYLOAD_HASH = '960c5219e4f2b83c2a15c5a56d6fc45a2dde7d8e922bdc2e605f4682e960d7c1'
BEHAVIOR = 'Retain every Strategy127 trading policy, sizing, exposure and cost. Select complete certified market windows before sequential financial consumption. Declare a 300000ms maximum window, 250000 rows and 128000000 serialized UTF8 JSON bytes; subdivide unexposed oversized windows at completed 100ms boundaries. Preserve every source pin, row and execution order. Transport failures remain terminal without retry. PM/AH only; AH retains prior-day V7 and full regular-session warmup. Backtest-only; financial improvement and validation remain unproven.'

def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=128, executor_revision=128,
        behavior_specification=BEHAVIOR, approved_digest='',
        rule_set_contracts=(*prior.rule_set_contracts, RULE),
        input_contracts=(*prior.input_contracts, SOURCE_CONTRACT))
    release = replace(draft, approved_digest=draft.digest())
    release.verify()
    return release

def declared_policies():
    policies = deepcopy(parent.declared_policies())
    policies[POLICY_KEY] = BoundedMarketReadPolicy(300000,250000,128000000,True).payload()
    return policies
