"""Mutation diversity and checkpointed randomness, without historical trading."""

import copy
import json
from argparse import Namespace

import numpy as np
import pytest

from research.vectorized_backtest.v3.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v3.torch_backtest.optimize import evolve, import_training_continuation
from research.vectorized_backtest.v3.torch_backtest.runtime import file_hash


def test_mutation_rng_resume_is_exact_and_every_coordinate_can_change():
    space = StrategySpace()
    rng = np.random.default_rng(123)
    parent = space.sample(rng, 1)[0]
    state = copy.deepcopy(rng.bit_generator.state)
    children = np.array([space.offspring(rng, parent, parent) for _ in range(800)])
    restored = np.random.default_rng()
    restored.bit_generator.state = state
    replay = np.array([space.offspring(restored, parent, parent) for _ in range(800)])
    np.testing.assert_array_equal(children, replay)
    space.validate(children)
    assert np.all(np.any(children != parent, axis=0))
    # Most children refine a few genes rather than disrupting the whole genome.
    assert np.median(np.count_nonzero(children != parent, axis=1)) < 8


def test_evolution_preserves_elites_and_reproduces_from_saved_rng():
    space = StrategySpace()
    rng = np.random.default_rng(9)
    population = space.sample(rng, 128)
    ranks = np.arange(128)
    state = copy.deepcopy(rng.bit_generator.state)
    next_rows = evolve(space, rng, population, ranks)
    restored = np.random.default_rng()
    restored.bit_generator.state = state
    np.testing.assert_array_equal(next_rows, evolve(space, restored, population, ranks))
    np.testing.assert_array_equal(next_rows[:2], population[[127, 126]])
    assert len(set(map(tuple, next_rows))) > 120


def test_continuation_rejects_changed_objective_and_observed_validation(tmp_path):
    origin = tmp_path / 'old'
    origin.mkdir()
    previous = dict(grammar={}, sessions={}, population=128, generations=32,
                    seed=9, objective={'risk': .1}, minimum_training_entries=1)
    (origin / 'identity.json').write_text(json.dumps(previous))
    (origin / 'checkpoint.json').write_text('{}')
    identity = copy.deepcopy(previous)
    identity['continuation'] = dict(checkpoint_sha256=file_hash(origin / 'checkpoint.json'),
                                   identity_sha256=file_hash(origin / 'identity.json'))
    identity['objective']['risk'] = .2
    with pytest.raises(ValueError, match='protected contract: objective'):
        import_training_continuation(StrategySpace(), origin, tmp_path / 'new', identity)
    identity['objective'] = previous['objective']
    (origin / 'winner.json').write_text('{}')
    with pytest.raises(ValueError, match='precede frozen winner'):
        import_training_continuation(StrategySpace(), origin, tmp_path / 'new', identity)


def test_qualified_followup_preserves_training_settings(tmp_path):
    from research.vectorized_backtest.v3.torch_backtest.run_optimization_workstation import qualified_followup_arguments

    args = Namespace(command='profile', resume='old-profile', profile_pipeline=True,
                     run_after_profile='new-search', population=128, generations=32,
                     seed=20261005, continue_training='old/experiment',
                     reuse_prepared='old/experiment', start_date='2026-07-30',
                     plain=False, qualification=None)
    command = qualified_followup_arguments(args, tmp_path)
    assert command[:3] == ['run', '--resume', 'new-search']
    assert '--profile-pipeline' not in command and '--run-after-profile' not in command
    assert command[command.index('--continue-training') + 1] == 'old/experiment'
    assert command[command.index('--generations') + 1] == '32'
    assert command[command.index('--from') + 1] == '2026-07-30'
