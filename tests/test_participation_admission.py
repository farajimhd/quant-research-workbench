from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from src.backend.backtest_native_participation_ranking import PreparedParticipationScores
from src.backend.backtest_participation_admission import prepare_participation_admission
from src.trading_runtime.participation_admission_policy import (
    RULE, POLICY_KEY, ParticipationAdmissionPolicy, parse_participation_admission_policy)
from tests.test_participation_acquisition_policy import declaration


def frozen(values, dtype):
    return np.frombuffer(np.asarray(values, dtype=dtype).tobytes(), dtype=dtype)


def test_additional_mask_preserves_mandatory_and_rejects_unknown():
    scores = PreparedParticipationScores(frozen([.9, 1., 1.1, np.nan, np.nan], np.float64),
        frozen([True, True, True, False, False], bool), frozen([True]*4+[False], bool))
    result = prepare_participation_admission(scores, policy=ParticipationAdmissionPolicy(1.))
    assert result.eligible.tolist() == [False, True, True, False, False]
    assert result.rejection_mask.tolist() == [2, 0, 0, 1, 4]
    assert not result.eligible.flags.writeable and not result.rejection_mask.flags.writeable


@pytest.mark.parametrize('minimum', [True, 1, float('nan'), float('inf')])
def test_invalid_declared_threshold(minimum):
    with pytest.raises(ValueError):
        ParticipationAdmissionPolicy(minimum)


def test_explicit_policy_pair_and_unchanged_default():
    release, policies, _ = declaration()
    assert parse_participation_admission_policy(release, policies) is None
    release.rule_set_contracts += (RULE,)
    policy = ParticipationAdmissionPolicy(1.)
    policies[POLICY_KEY] = policy.payload()
    assert parse_participation_admission_policy(release, policies) == policy
    for key, value in [('missing_score', 'accept'), ('minimum_score', 1),
                       ('cash', 'released-on-proposed-exit')]:
        changed = deepcopy(policies)
        changed[POLICY_KEY][key] = value
        with pytest.raises(ValueError):
            parse_participation_admission_policy(release, changed)
    with pytest.raises(ValueError):
        parse_participation_admission_policy(SimpleNamespace(rule_set_contracts=(RULE,),
            input_contracts=()), {POLICY_KEY: policy.payload()})


def test_mutable_or_fabricated_known_scores_rejected():
    policy = ParticipationAdmissionPolicy(1.)
    for scores in [PreparedParticipationScores(np.array([1.]), frozen([True], bool), frozen([True], bool)),
                   PreparedParticipationScores(frozen([np.nan], np.float64), frozen([True], bool), frozen([True], bool)),
                   PreparedParticipationScores(frozen([2.], np.float64), frozen([True], bool), frozen([False], bool))]:
        with pytest.raises(ValueError):
            prepare_participation_admission(scores, policy=policy)
