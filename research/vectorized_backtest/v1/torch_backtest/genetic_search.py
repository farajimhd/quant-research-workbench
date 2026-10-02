"""Bounded, deterministic genetic search over the numeric atomic policy ABI.

Genome [B,10] contains ONLY adjustable values. Fixed literals and categorical
instruction arrays stay in the compiled graphs. Global search is heuristic:
this solver never claims a certified global optimum.
"""

from dataclasses import dataclass

import numpy as np

from .strategy_one_program import released_action_graphs, released_entry_graph
from .strategy_one_tensor_policy import PROTECTION_PARAMETERS, AtomicReducer


@dataclass(frozen=True)
class Dimension:
    name: str
    minimum: float
    maximum: float
    integer: bool
    default: float


class StrategySpace:
    """The same constraints validate genomes and expanded engine parameters."""

    def __init__(self):
        self.entry = released_entry_graph()
        self.actions = released_action_graphs()
        self.entry_indices = [
            i for i, t in enumerate(self.entry.thresholds) if t.minimum < t.maximum
        ]
        if len(self.entry_indices) != 3:
            raise ValueError(
                "Strategy search ABI requires exactly three entry dimensions"
            )
        capital = self.actions["capital_fraction"].thresholds[0]
        self.dimensions = (
            tuple(
                Dimension(t.name, t.minimum, t.maximum, t.integer, self.entry.values[i])
                for i, t in enumerate(self.entry.thresholds)
                if i in self.entry_indices
            )
            + (
                Dimension(
                    capital.name,
                    capital.minimum,
                    capital.maximum,
                    capital.integer,
                    self.actions["capital_fraction"].values[0],
                ),
            )
            + tuple(
                Dimension(name, low, high, True, value)
                for name, value, low, high in PROTECTION_PARAMETERS
            )
        )
        self.low = np.array([d.minimum for d in self.dimensions])
        self.high = np.array([d.maximum for d in self.dimensions])
        self.integer = np.array([d.integer for d in self.dimensions])
        self.default = np.array([d.default for d in self.dimensions])

    def repair(self, values):
        """Project bounds, integer coordinates and ordered target breakpoints."""
        rows = np.asarray(values, dtype=np.float64)
        if (
            rows.ndim != 2
            or rows.shape[1] != len(self.dimensions)
            or not np.isfinite(rows).all()
        ):
            raise ValueError("Genome must be finite [B,10]")
        rows = np.clip(rows, self.low, self.high)
        rows[:, self.integer] = np.rint(rows[:, self.integer])
        rows[:, 6] = np.maximum(rows[:, 5], rows[:, 6])
        return rows

    def decode(self, values):
        rows = self.repair(values)
        entry = np.tile(self.entry.values, (len(rows), 1))
        entry[:, self.entry_indices] = rows[:, :3]
        protection = rows[:, 4:].astype(np.int64).tolist()
        self.entry.parameters(entry, "cpu")
        AtomicReducer.checked_values(protection, len(rows))
        actions = {"capital_fraction": rows[:, 3:4].tolist()}
        self.actions["capital_fraction"].parameters(actions["capital_fraction"], "cpu")
        return {"entry": entry.tolist(), "protection": protection, "actions": actions}


def initial_population(space, rng, size, seed=None):
    if size < 4:
        raise ValueError("Population requires at least four independent candidates")
    population = rng.uniform(space.low, space.high, (size, len(space.dimensions)))
    population[0] = space.default if seed is None else seed
    if seed is not None:
        population[1] = space.default
    return space.repair(population)


def next_population(space, rng, population, scores, *, diversify=False):
    """Elitism + tournament crossover + mutations + global random immigrants.

    A plateau increases mutation and immigration within the fixed run budget;
    it never consults validation scores or extends the generation cap.
    """
    population = space.repair(population)
    scores = np.asarray(scores, dtype=float)
    if scores.shape != (len(population),) or not np.isfinite(scores).all():
        raise ValueError("Every candidate needs one finite training score")
    order = np.argsort(-scores, kind="stable")
    result = population.copy()
    result[:2] = population[order[:2]]
    for row in range(2, len(result)):
        if rng.random() < (0.5 if diversify else 0.2):
            result[row] = rng.uniform(space.low, space.high)
            continue
        parents = []
        for _ in range(2):
            tournament = rng.integers(0, len(result), size=3)
            parents.append(population[tournament[np.argmax(scores[tournament])]])
        child = np.where(rng.random(len(space.dimensions)) < 0.5, *parents)
        mutation = rng.random(len(space.dimensions)) < 0.3
        child = child + mutation * rng.normal(
            0, 0.25 if diversify else 0.1, len(space.dimensions)
        ) * (space.high - space.low)
        result[row] = child
    return space.repair(result)


def objective(
    results,
    *,
    initial_cash=10000,
    drawdown_weight=0.5,
    dispersion_weight=0.25,
    position_weight=0.0,
    exposure_weight=0.0,
):
    """Higher is better; mean net return penalized for risk/activity.

    Each session starts with fresh $10k accounts. Return/drawdown use initial
    equity units; positions use entries/100; exposure uses position-hours.
    Commissions are already included in net P&L. Open positions are marked,
    never silently liquidated. Session dispersion discourages one-day luck.
    """
    if not results or not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError(
            "Objective requires nonempty sessions and positive initial cash"
        )
    weights = (drawdown_weight, dispersion_weight, position_weight, exposure_weight)
    if not all(np.isfinite(w) and w >= 0 for w in weights):
        raise ValueError("Objective weights must be finite and nonnegative")
    returns = np.array([r["net_pnl"] for r in results]) / initial_cash
    drawdown = np.array([r["max_drawdown"] for r in results]) / initial_cash
    entries = np.array([r["entered_episodes"] for r in results]) / 100
    exposure = np.array([r["position_seconds"] for r in results]) / 3600
    score = (
        returns.mean(0)
        - drawdown_weight * drawdown.mean(0)
        - dispersion_weight * returns.std(0)
        - position_weight * entries.mean(0)
        - exposure_weight * exposure.mean(0)
    )
    if not np.isfinite(score).all():
        raise ValueError("Nonfinite financial objective")
    return score
