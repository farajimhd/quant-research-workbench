"""One-time vectorized necessary-condition mask over prepared causal scores."""
from typing import NamedTuple

import numpy as np

from .backtest_native_participation_ranking import PreparedParticipationScores
from src.trading_runtime.participation_admission_policy import ParticipationAdmissionPolicy

MISSING_SCORE = 1
BELOW_MINIMUM = 2
MANDATORY_REJECTION = 4


class PreparedParticipationAdmission(NamedTuple):
    eligible: np.ndarray
    rejection_mask: np.ndarray


def prepare_participation_admission(scores, *, policy):
    """Add a restriction; source certification and Portfolio admission stay external."""
    if type(scores) is not PreparedParticipationScores or type(policy) is not ParticipationAdmissionPolicy:
        raise ValueError('Exact prepared scores and declared admission policy required')
    policy.__post_init__()
    if type(scores.scores) is not np.ndarray:
        raise ValueError('Complete immutable participation arrays required')
    n = scores.scores.size
    for values, dtype in ((scores.scores, np.float64), (scores.available, bool),
                          (scores.mandatory_eligible, bool)):
        if (type(values) is not np.ndarray or values.dtype != np.dtype(dtype)
                or values.shape != (n,) or values.flags.writeable):
            raise ValueError('Complete immutable participation arrays required')
    if (np.any(scores.available & ~scores.mandatory_eligible)
            or np.any(scores.available & ~np.isfinite(scores.scores))
            or np.any(~scores.available & ~np.isnan(scores.scores))):
        raise ValueError('Participation availability differs from mandatory source')
    rejection = np.where(~scores.mandatory_eligible, MANDATORY_REJECTION,
        np.where(~scores.available, MISSING_SCORE,
            np.where(scores.scores < policy.minimum_score, BELOW_MINIMUM, 0))).astype(np.uint8)
    return PreparedParticipationAdmission(
        np.frombuffer((rejection == 0).tobytes(), dtype=bool),
        np.frombuffer(rejection.tobytes(), dtype=np.uint8))
