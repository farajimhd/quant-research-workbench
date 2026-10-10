"""Immutable successor selecting both required journal capabilities."""
from copy import deepcopy
from dataclasses import replace
import json
from . import strategy_one_hundred_fourteen_release as parent
from .consecutive_price_confirmed_risk import ENTRY_COST_CAPABILITY_RULE, POLICY_KEY
from .journal_contract import canonical_json

CONTROL_REVISION = 'strategy-one-114:b26c3c82-f58b-4f2a-b17f-d9f22e009e26'
CONTROL_PAYLOAD_HASH = 'b20d26e637b4e49c3415c0528d81811342f32bda3f2cc6dcd37e4694bdeed7ea'
PRICE_POLICY = replace(parent.PRICE_POLICY, entry_cost_capability=True)
BEHAVIOR = (
    'Retain Strategy114 entries, reentry, sizing, exposure, costs, fixed protection '
    'and all exit semantics, including two wholly post-held completed 5s closes '
    'and a fresh bid below the declared half-original-risk threshold. Add only '
    'explicit combined original-risk diagnostic and entry-cost journal capability. '
    'Require its dedicated principal, exact grants and SSD schema/parts before '
    'execution. Preserve both diagnostic witnesses and all entry-cost evidence '
    'through publication, checkpoint and recovery. No economic thresholds change. '
    'PM/AH only; 100ms decisions; Backtest-only. Financial evaluation and app '
    'deployment pending.')


def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=115, executor_revision=115,
        rule_set_contracts=prior.rule_set_contracts + (ENTRY_COST_CAPABILITY_RULE,),
        behavior_specification=BEHAVIOR, approved_digest='')
    result = replace(draft, approved_digest=draft.digest())
    result.verify()
    return result


def declared_policies():
    policies = deepcopy(parent.declared_policies())
    policies[POLICY_KEY] = json.loads(canonical_json(PRICE_POLICY.payload()))
    return policies
