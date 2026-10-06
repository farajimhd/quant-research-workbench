import numpy as np
import pytest
from research.vectorized_backtest.v4.torch_backtest.feature_bank import CATALOG
from research.vectorized_backtest.v4.torch_backtest.search_operands import searchable_features, REGIME_FLAGS
from research.vectorized_backtest.v4.torch_backtest.evolution import sample, mutate
from research.vectorized_backtest.v4.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v4.torch_backtest.program import Op

TRAINING = [{'start': '2026-08-21T01:00:00-07:00', 'end': '2026-08-21T06:30:00-07:00'}]


def test_population_and_mutations_exclude_only_regime_operands():
    indices = searchable_features(TRAINING, 'premarket')
    assert len(indices) == len(CATALOG) - 3
    assert {f.name for i, f in enumerate(CATALOG) if i not in indices} == REGIME_FLAGS
    rng = np.random.default_rng(20261005)
    space = StrategySpace()
    population = sample(rng, space, 128, indices)
    population += [mutate(rng, p, space, indices) for p in population]
    for candidate in population:
        for program in candidate.programs().values():
            assert all(n.feature in indices for n in program.nodes if n.op == Op.FEATURE)


def test_boundary_rejects_regular_hours_and_naive_times():
    for end in ('2026-08-21T09:31:00-04:00', '2026-08-21T09:30:00'):
        with pytest.raises(ValueError):
            searchable_features([dict(TRAINING[0], end=end)], 'premarket')
    assert searchable_features(TRAINING) == tuple(range(len(CATALOG)))
