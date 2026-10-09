"""Deterministic staged budgets, balanced training panels and migration."""
from dataclasses import dataclass, asdict
from copy import deepcopy
import numpy as np
from .evolution import sample, mutate, Individual, STAGES


@dataclass(frozen=True)
class Stage:
    end_generation: int
    population: int
    sessions: int
    archive_top: int
    archive_random: int


def validate_schedule(stages, training_days=30):
    previous = 0
    for stage in stages:
        if (stage.end_generation <= previous or stage.population < 10 or
                not 2 <= stage.sessions <= training_days or stage.archive_top < 1 or
                stage.archive_random < 0 or stage.archive_top + stage.archive_random > stage.population):
            raise ValueError('Invalid staged search budget')
        previous = stage.end_generation
    if not stages:
        raise ValueError('Explicit measured schedule required')
    return stages


def balanced_panels(rng, generations, count, days):
    """No duplicate date within a panel; each cycle covers every training day."""
    pool = []
    panels = []
    for _ in range(generations):
        selected = []
        deferred = []
        while len(selected) < count:
            if not pool:
                pool = rng.permutation(days).tolist()
            day = pool.pop(0)
            if day in selected:
                deferred.append(day)
            else:
                selected.append(day)
        pool = deferred + pool
        panels.append(selected)
    return panels


def counts(size):
    # Largest-remainder rounding, with deterministic tie order.
    weights = np.asarray([.1, .1, .1, .5, .2])
    exact = weights * size
    result = np.floor(exact).astype(int)
    for index in sorted(range(5), key=lambda i: (-(exact[i] - result[i]), i))[:size-int(result.sum())]:
        result[index] += 1
    return result.tolist()


def crossover(rng, left, right, space):
    """Whole typed stage programs preserve operand indices and window bounds."""
    policy = space.offspring(rng, np.asarray(left.policy), np.asarray(right.policy)).tolist()
    policy[:4] = space.default[:4].tolist()
    policy[50:] = space.default[50:].tolist()
    clauses, connectors = {}, {}
    for stage in STAGES:
        parent = left if rng.random() < .5 else right
        clauses[stage] = deepcopy(parent.clauses[stage])
        connectors[stage] = deepcopy(parent.connectors[stage])
    from .evolution import conditional_policy
    policy=conditional_policy(policy,space,previous=left.policy,rng=rng)
    management={name:(left if rng.random()<.5 else right).management[name] for name in left.management}
    child = Individual(policy, clauses, connectors,management)
    child.programs()
    return child


def migrate(rng, population, rank, size, space, features):
    """10% elite, 10% other unchanged, 10% fresh, 50% elite / 20% random offspring."""
    if not rank:
        raise ValueError('No valid parents; stop rather than evolve invalid strategies')
    elite_n, unchanged_n, fresh_n, top_n, random_n = counts(size)
    elite_pool = rank[:max(1, int(np.ceil(len(rank)*.1)))]
    survivors = rank[:min(elite_n, len(rank))]
    remainder = [i for i in rank if i not in survivors]
    # Never duplicate an unchanged survivor to disguise missing valid coverage.
    unchanged = rng.choice(remainder, min(unchanged_n, len(remainder)), replace=False).tolist()
    children = [deepcopy(population[i]) for i in survivors + unchanged]
    children.extend(sample(rng, space, fresh_n + elite_n-len(survivors) + unchanged_n-len(unchanged), features))
    for number, pool in ((top_n, elite_pool), (random_n, rank)):
        for _ in range(number):
            left, right = [population[int(rng.choice(pool))] for _ in range(2)]
            parent = crossover(rng, left, right, space) if rng.random() < .5 else left
            children.append(mutate(rng, parent, space, features))
    if len(children) != size:
        raise AssertionError('Migration budget mismatch')
    return children


def objective_matrix(results, population, objective):
    import torch
    from .stability import score
    from .feature_bank import CATALOG
    def matrix(key):
        return torch.tensor([r[key] for r in results], dtype=torch.float64)
    complexity = torch.tensor([sum(p.validate(CATALOG)['active_nodes'] for p in individual.programs().values())
                               for individual in population], dtype=torch.float64)
    return score(matrix('net_pnl'), matrix('drawdown'), matrix('stop_risk_dollar_seconds'),
                 matrix('capital_dollar_seconds'), matrix('filled_batches'),
                 matrix('terminal_valid'), complexity, config=objective,
                 inactivity=matrix('inactivity_seconds').sum(0)/matrix('eligible_seconds').sum(0) if hasattr(objective,'inactivity_weight') else None)


def selection_rank(rng,rank,scored,results,objective):
    """Seeded half-removal of inactive candidates; financial validity unchanged."""
    if not hasattr(objective,'inactive_removal_fraction'):return rank
    inactive=[i for i in rank if sum(r['filled_batches'][i] for r in results)==0]
    remove=int(len(inactive)*objective.inactive_removal_fraction)
    excluded=set(rng.choice(inactive,remove,replace=False).tolist()) if remove else set()
    return [i for i in rank if i not in excluded]
