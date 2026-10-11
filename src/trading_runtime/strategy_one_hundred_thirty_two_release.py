"""Immutable native participation session-binding successor."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_thirty_one_release as parent
CONTROL_REVISION = 'strategy-one-131:77ca574b-c68d-4a1f-9567-6e21f8871bce'
CONTROL_PAYLOAD_HASH = 'a122bab5915d35b45cdb160e04573e652ad5784109c79a4b3fdae004c2644e87'
BEHAVIOR = 'Retain Strategy131 mandatory gates, relative multiresolution scoring, exits, sizing, exposure and costs. Require a known completed participation score at least 1.0 as an additional entry condition. This declared cutoff is experimental, not a proven optimum. Portfolio cash and OMS protection remain authoritative. PM/AH only; AH prior-day V7 and full regular-session warmup remain mandatory. Backtest-only; profit and validation unproven.'

def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=132, executor_revision=132,
        behavior_specification=BEHAVIOR,
        rule_set_contracts=prior.rule_set_contracts + ('completed-participation-quality-admission@1',), approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    release.verify()
    return release

def declared_policies():
    from .participation_admission_policy import ParticipationAdmissionPolicy, POLICY_KEY
    policies = deepcopy(parent.declared_policies())
    policies[POLICY_KEY] = ParticipationAdmissionPolicy(1.0).payload()
    return policies
