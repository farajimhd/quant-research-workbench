"""Bounded, deterministic genetic search over a declared strategy space.

The legacy StrategySpace below supplies numeric genes. CategoricalStrategySpace
also supplies typed class IDs and its own discrete variation operators. Global
search is heuristic: this solver never claims a certified global optimum.
"""

from dataclasses import dataclass, replace

import numpy as np

from .strategy_one_program import released_entry_graph, searchable_action_graphs
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
        from .atomic_graph import Threshold
        from .unified_clock import unified_add_graph

        self.add = unified_add_graph()
        # Only purchase bounds become searchable. Alignment, price scale and
        # quote equality tolerances remain structural execution contracts.
        self.add = replace(
            self.add,
            thresholds=tuple(
                Threshold("minimum_add_purchase_ordinal", 2, 3, True, "count")
                if value == 2 and threshold.integer
                else Threshold("maximum_add_purchase_ordinal", 2, 3, True, "count")
                if value == 3 and threshold.integer
                else threshold
                for value, threshold in zip(self.add.values, self.add.thresholds)
            ),
        ).validate()
        self.actions = searchable_action_graphs()
        self.graphs = {"entry": self.entry, "add": self.add, **self.actions}
        self.slots = []
        dimensions = []
        for component, graph in self.graphs.items():
            for index, threshold in enumerate(graph.thresholds):
                if threshold.minimum < threshold.maximum:
                    self.slots.append((component, index))
                    dimensions.append(
                        Dimension(
                            threshold.name,
                            threshold.minimum,
                            threshold.maximum,
                            threshold.integer,
                            graph.values[index],
                        )
                    )
        self.protection_start = len(dimensions)
        dimensions.extend(
            Dimension(name, low, high, True, value)
            for name, value, low, high in PROTECTION_PARAMETERS
        )
        self.dimensions = tuple(dimensions)
        self.indices = {d.name: i for i, d in enumerate(self.dimensions)}
        if len(self.indices) != len(self.dimensions):
            raise ValueError("Search parameter names must be unique")
        self.low = np.array([d.minimum for d in self.dimensions])
        self.high = np.array([d.maximum for d in self.dimensions])
        self.integer = np.array([d.integer for d in self.dimensions])
        self.default = np.array([d.default for d in self.dimensions])

    def repair(self, values):
        """Project declared bounds and ordered categorical/count thresholds."""
        rows = np.asarray(values, dtype=np.float64)
        if (
            rows.ndim != 2
            or rows.shape[1] != len(self.dimensions)
            or not np.isfinite(rows).all()
        ):
            raise ValueError(f"Genome must be finite [B,{len(self.dimensions)}]")
        rows = np.clip(rows, self.low, self.high)
        rows[:, self.integer] = np.rint(rows[:, self.integer])
        for first, second in (
            ("first_target_breaks", "second_target_breaks"),
            ("minimum_add_purchase_ordinal", "maximum_add_purchase_ordinal"),
        ):
            a, b = self.indices[first], self.indices[second]
            rows[:, b] = np.maximum(rows[:, a], rows[:, b])
        return rows

    def decode(self, values):
        """Scatter every eligible coordinate into its complete component tensor.

        Fixed literals are restored from graph defaults, never optimized as
        thresholds. All component tensors are validated before device updates.
        """
        rows = self.repair(values)
        expanded = {
            name: np.tile(graph.values, (len(rows), 1))
            for name, graph in self.graphs.items()
        }
        for coordinate, (component, index) in enumerate(self.slots):
            expanded[component][:, index] = rows[:, coordinate]
        for name, graph in self.graphs.items():
            graph.parameters(expanded[name].tolist(), "cpu")
        protection = rows[:, self.protection_start :].astype(np.int64).tolist()
        AtomicReducer.checked_values(protection, len(rows))
        return {
            "entry": expanded["entry"].tolist(),
            "add": expanded["add"].tolist(),
            "protection": protection,
            "actions": {name: expanded[name].tolist() for name in self.actions},
        }


def initial_population(space, rng, size, seed=None, *, random_only=False):
    if size < 4:
        raise ValueError("Population requires at least four independent candidates")
    population = (
        space.sample(rng, size)
        if hasattr(space, "sample")
        else rng.uniform(space.low, space.high, (size, len(space.dimensions)))
    )
    if random_only:
        if seed is not None:
            raise ValueError("Random initialization cannot also use a seed candidate")
        # Sample actual class IDs, then repair coupled graph constraints. This
        # mode randomizes every class coordinate rather than sparse mutations
        # around the default program. Numeric coordinates come from sample().
        for coordinate, gene in enumerate(
            getattr(space, "genes", ()),
            space.numeric_count if hasattr(space, "genes") else 0,
        ):
            population[:, coordinate] = rng.choice(gene.allowed, size=size)
        return space.repair(population)
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
            result[row] = (
                space.sample(rng, 1)[0]
                if hasattr(space, "sample")
                else rng.uniform(space.low, space.high)
            )
            continue
        parents = []
        for _ in range(2):
            tournament = rng.integers(0, len(result), size=3)
            parents.append(population[tournament[np.argmax(scores[tournament])]])
        if hasattr(space, "offspring"):
            result[row] = space.offspring(rng, parents, diversify)
            continue
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
