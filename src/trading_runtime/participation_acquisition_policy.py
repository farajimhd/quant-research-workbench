"""Explicit opportunity ordering; mandatory admission and cash stay external."""
from dataclasses import dataclass

from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy
from .journal_contract import canonical_json

RULE = 'completed-participation-ranked-acquisition@1'
SOURCE_CONTRACT = 'certified-native-participation-ranking@1'
POLICY_KEY = 'participation_acquisition_policy'


@dataclass(frozen=True, slots=True)
class ParticipationAcquisitionPolicy:
    scoring: ParticipationRankingPolicy
    missing_score: str

    def __post_init__(self):
        if type(self.scoring) is not ParticipationRankingPolicy:
            raise ValueError('Acquisition needs exact declared scoring policy')
        self.scoring.__post_init__()
        if type(self.missing_score) is not str or self.missing_score != 'eligible_unranked_last':
            raise ValueError('Unknown scores must preserve eligible candidates unranked last')

    def payload(self):
        self.__post_init__()
        score = self.scoring
        return dict(rule=RULE, source=SOURCE_CONTRACT,
            resolution_weights=[list(pair) for pair in score.resolution_weights],
            volume_weight=score.volume_weight, trade_weight=score.trade_weight,
            wick_penalty=score.wick_penalty,
            minimum_known_resolutions=score.minimum_known_resolutions,
            max_rows=score.max_rows, missing_score=self.missing_score,
            ordering='boundary-score-descending-ticker-ascending',
            dispatch='after-broker-and-management-before-boundary-finish',
            admission='unchanged-mandatory-gates', cash='portfolio-current-view-per-acquisition')


def parse_participation_acquisition_policy(release, policies):
    rule_count = release.rule_set_contracts.count(RULE)
    source_count = release.input_contracts.count(SOURCE_CONTRACT)
    supplied = POLICY_KEY in policies
    if rule_count == 0 and source_count == 0 and not supplied:
        return None
    if rule_count != 1 or source_count != 1 or not supplied:
        raise ValueError('Acquisition ranking requires one rule, source and policy')
    value = policies[POLICY_KEY]
    required = {'rule', 'source', 'resolution_weights', 'volume_weight', 'trade_weight',
        'wick_penalty', 'minimum_known_resolutions', 'max_rows', 'missing_score',
        'ordering', 'dispatch', 'admission', 'cash'}
    if type(value) is not dict or set(value) != required:
        raise ValueError('Complete acquisition ranking declaration required')
    pairs = value['resolution_weights']
    if type(pairs) is not list or any(type(pair) is not list or len(pair) != 2 for pair in pairs):
        raise ValueError('Explicit resolution weights required')
    policy = ParticipationAcquisitionPolicy(ParticipationRankingPolicy(
        tuple(tuple(pair) for pair in pairs), value['volume_weight'], value['trade_weight'],
        value['wick_penalty'], value['minimum_known_resolutions'], value['max_rows']),
        value['missing_score'])
    if canonical_json(value) != canonical_json(policy.payload()):
        raise ValueError('Acquisition ranking semantics differ from declaration')
    return policy
