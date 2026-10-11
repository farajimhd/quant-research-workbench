"""Immutable completed-participation acquisition successor."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_twenty_nine_release as parent
CONTROL_REVISION = 'strategy-one-129:4cd25c57-e542-44c3-94e9-8d74afa393e3'
CONTROL_PAYLOAD_HASH = '3f0f0e9ec5f5481bd2695e4a41ca8cdd102b58ea611f872d2a75cfe648c5d219'
BEHAVIOR = 'Retain Strategy129 mandatory entry gates, sizing, aggregate exposure, costs, native exits and bounded reader. After all completed broker events and position management, rank new eligible acquisitions using completed 30s and 1m participation and wick rejection. Equal timeframe weights; volume and trade log1p weights 0.5 each; upper-wick/range penalty 1.0; both resolutions required for a known score. Unknown scores preserve eligible candidates unranked last, with ticker tie-breaking. Portfolio reads current cash and reservations per acquisition; no assumed exit proceeds. PM/AH only; causal source and AH prior-day V7/full RTH warmup remain mandatory. Backtest-only; profit and validation unproven.'

def release_contract():
    prior = parent.release_contract()
    from .participation_acquisition_policy import RULE, SOURCE_CONTRACT
    draft = replace(prior, rule_set_contracts=(*prior.rule_set_contracts, RULE),
        input_contracts=(*prior.input_contracts, SOURCE_CONTRACT), number=130, executor_revision=130,
        behavior_specification=BEHAVIOR, approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    release.verify()
    return release

def declared_policies():
    from .participation_acquisition_policy import POLICY_KEY, ParticipationAcquisitionPolicy
    from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy
    policies = deepcopy(parent.declared_policies())
    policies[POLICY_KEY] = ParticipationAcquisitionPolicy(
        ParticipationRankingPolicy(((30000, 1.), (60000, 1.)), .5, .5, 1., 2, 500000),
        'eligible_unranked_last').payload()
    return policies
