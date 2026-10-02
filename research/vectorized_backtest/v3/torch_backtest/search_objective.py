"""Resident tapes, bounded shape groups, independent causal portfolio resets."""

import gc
from collections import OrderedDict
from time import perf_counter

import numpy as np
import torch

from .search_runner import SearchRunner


class SessionObjective:
    def __init__(
        self, tape, space, batch, *, backend="eager", maximum_groups=2, progress=None
    ):
        if (
            type(batch) is not int
            or batch < 1
            or type(maximum_groups) is not int
            or maximum_groups < 1
        ):
            raise ValueError("Positive bounded batch and shape cache required")
        self.tape = tape.validate()
        self.space, self.batch = space, batch
        self.backend, self.maximum_groups = backend, maximum_groups
        self.runners = OrderedDict()
        self.progress = progress

    def __call__(self, values):
        rows = self.space.validate(values)
        groups = OrderedDict()
        for i, row in enumerate(rows):
            groups.setdefault(self.space.group_key(row), []).append(i)
        metrics = (
            "net_pnl",
            "drawdown",
            "entered",
            "positions_opened",
            "open_positions",
            "fill_count",
            "open_quantity",
            "exposure_seconds",
            "terminal_valid",
        )
        result = {name: [None] * len(rows) for name in metrics}
        compiled, replayed = 0.0, 0.0
        for group, (key, indices) in enumerate(groups.items()):
            if len(indices) > self.batch:
                raise ValueError("Group exceeds fixed GPU batch")
            padded = indices + [indices[0]] * (self.batch - len(indices))
            if self.progress:
                self.progress(
                    dict(
                        stage="group_setup",
                        active_group=group + 1,
                        total_groups=len(groups),
                    )
                )
            if key not in self.runners:
                if len(self.runners) >= self.maximum_groups:
                    _, old = self.runners.popitem(last=False)
                    del old
                    gc.collect()
                    if self.tape.device.type == "cuda":
                        torch.cuda.empty_cache()
                started = perf_counter()
                runner = SearchRunner(
                    self.tape, self.space, rows[padded], backend=self.backend
                ).compile()
                compiled += perf_counter() - started
                self.runners[key] = runner
            self.runners.move_to_end(key)
            runner = self.runners[key]
            runner.set_genomes(rows[padded])
            observed = runner.run(progress=self.progress)
            replayed += observed["replay_seconds"]
            for name in metrics:
                host = observed[name].cpu().tolist()
                for lane, index in enumerate(indices):
                    result[name][index] = host[lane]
        return dict(
            **result,
            compile_seconds=compiled,
            replay_seconds=replayed,
            shape_groups=len(groups),
        )


def score(
    results,
    *,
    initial_cash=10000,
    drawdown_weight=0.5,
    dispersion_weight=0.25,
    position_weight=0.0,
    exposure_weight=0.0,
    minimum_training_entries=0,
):
    """Independent-session return/risk objective. Invalid residuals score null.

    Required entry count is an explicit research constraint, off by default.
    Every rejection carries a reason; never reward fictitious final liquidation.
    """
    weights = (drawdown_weight, dispersion_weight, position_weight, exposure_weight)
    if (
        not results
        or not np.isfinite(initial_cash)
        or initial_cash <= 0
        or not all(np.isfinite(w) and w >= 0 for w in weights)
    ):
        raise ValueError("Invalid objective contract")
    if type(minimum_training_entries) is not int or minimum_training_entries < 0:
        raise ValueError("Minimum activity must be a nonnegative integer")
    pnl = np.array([r["net_pnl"] for r in results], dtype=float) / initial_cash
    dd = np.array([r["drawdown"] for r in results], dtype=float) / initial_cash
    entries = np.array([r["positions_opened"] for r in results], dtype=float)
    exposure = np.array([r["exposure_seconds"] for r in results], dtype=float) / 3600
    if not all(np.isfinite(v).all() for v in (pnl, dd, entries, exposure)):
        raise ValueError(
            "Nonfinite financial result; reject evaluation rather than skip it"
        )
    values = (
        pnl.mean(0)
        - drawdown_weight * dd.mean(0)
        - dispersion_weight * pnl.std(0)
        - position_weight * entries.mean(0) / 100
        - exposure_weight * exposure.mean(0)
    )
    valid = np.array([r["terminal_valid"] for r in results], dtype=bool).all(0)
    active = (entries >= minimum_training_entries).all(0)
    reasons = [
        None
        if v and a
        else "residual_exposure"
        if not v
        else "minimum_training_activity"
        for v, a in zip(valid, active)
    ]
    return [
        float(v) if reason is None else None for v, reason in zip(values, reasons)
    ], reasons
