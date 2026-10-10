"""Immutable repaired native-reader financial-equivalence candidate."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_fifteen_release as parent
from .original_risk_verification_reuse import RULE, POLICY_KEY, OriginalRiskVerificationReusePolicy

CONTROL_REVISION = 'strategy-one-115:2746aebb-a2b1-4326-97d5-69a496eb04fe'
CONTROL_PAYLOAD_HASH = '2bb17d6746912ba75bc9d904713ca2bc0465dc8d5f5f056e654dcb71667027f5'
REUSE_POLICY = OriginalRiskVerificationReusePolicy(96, 1048576)
BEHAVIOR = (
    'Retain Strategy115 entries, reentry, sizing, exposure, costs, fixed protection '
    'and all exit semantics. Select bounded request-local verification reuse only '
    'with native parenthesized journal reads and full cold failure cleanup, under the same certified source and original exclusive writer frontier. '
    'Reuse completed successful exact requests; discard the operation at exit. '
    'Cold readers and unsupported contexts retain full verification. Budget '
    'overflow executes the complete verifier. Preserve cash, fills, per-lot '
    'protection, OCA ordering and recovery. PM/AH only; 100ms decisions. '
    'Backtest-only; financial equivalence, speed acceptance and deployment pending.')


def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=117, executor_revision=117,
        rule_set_contracts=prior.rule_set_contracts + (RULE,),
        behavior_specification=BEHAVIOR, approved_digest='')
    result = replace(draft, approved_digest=draft.digest())
    result.verify()
    return result


def declared_policies():
    result = deepcopy(parent.declared_policies())
    result[POLICY_KEY] = REUSE_POLICY.payload()
    return result
