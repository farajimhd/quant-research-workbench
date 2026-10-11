"""Declared necessary participation quality; never financial admission."""
from dataclasses import dataclass
from math import isfinite

from .journal_contract import canonical_json
from .participation_acquisition_policy import parse_participation_acquisition_policy

RULE = 'completed-participation-quality-admission@1'
POLICY_KEY = 'participation_admission_policy'


@dataclass(frozen=True, slots=True)
class ParticipationAdmissionPolicy:
    minimum_score: float

    def __post_init__(self):
        if type(self.minimum_score) is not float or not isfinite(self.minimum_score):
            raise ValueError('Explicit finite participation minimum required')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, minimum_score=self.minimum_score,
                    comparison='greater-than-or-equal', missing_score='reject',
                    admission='additional-necessary-condition',
                    cash='unchanged-portfolio-authority')


def parse_participation_admission_policy(release, policies):
    count = release.rule_set_contracts.count(RULE)
    supplied = POLICY_KEY in policies
    if count == 0 and not supplied:
        return None
    if count != 1 or not supplied:
        raise ValueError('Participation admission requires one rule and policy')
    if parse_participation_acquisition_policy(release, policies) is None:
        raise ValueError('Participation admission requires declared causal scoring')
    value = policies[POLICY_KEY]
    if type(value) is not dict or set(value) != set(ParticipationAdmissionPolicy(0.).payload()):
        raise ValueError('Complete participation admission declaration required')
    policy = ParticipationAdmissionPolicy(value['minimum_score'])
    if canonical_json(value) != canonical_json(policy.payload()):
        raise ValueError('Participation admission semantics differ from declaration')
    return policy
