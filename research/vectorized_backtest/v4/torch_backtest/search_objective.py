"""Resident tapes, bounded shape groups, independent causal portfolio resets."""

import gc
from collections import OrderedDict
from time import perf_counter

import numpy as np
import torch

from .search_runner import SearchRunner


class SessionObjective:
    def __init__(
        self,
        tape,
        space,
        batch,
        *,
        backend="eager",
        maximum_groups=2,
        progress=None,
        **runner_options,
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
        self.runner_options = runner_options
        self.before_replay = None

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
            "filled_batches",
            "long_hold_dollar_seconds",
            "stop_risk_dollar_seconds",
            "capital_dollar_seconds",
            "peak_reserved_stop_risk",
            "sold_share_seconds",
            "sold_shares",
            "entry_retry_count",
            "requested_entry_shares",
            "filled_entry_shares",
            "pending_entry_shares",
            "expired_entry_shares",
            "policy_cancelled_entry_shares",
            "exit_cancelled_entry_shares",
            "terminal_cancelled_entry_shares",
        )
        result = {name: [None] * len(rows) for name in metrics}
        compiled, replayed, rule_seconds = 0.0, 0.0, 0.0
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
                    self.tape,
                    self.space,
                    rows[padded],
                    backend=self.backend,
                    **self.runner_options,
                )
                runner.compile()
                compiled += perf_counter() - started
                self.runners[key] = runner
            self.runners.move_to_end(key)
            runner = self.runners[key]
            runner.set_genomes(rows[padded])
            if runner.precompute_rules:
                rule_seconds += runner.rule_compiler.prepare(self.progress)
            # Transfers may start only AFTER warmup/graph capture. Concurrent
            # CUDA allocation while another graph captures is not permitted.
            if self.before_replay:
                self.before_replay()
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
            rule_prepare_seconds=rule_seconds,
        )


def score(
    results,
    *,
    initial_cash=10000,
    drawdown_weight=0.25,
    dispersion_weight=0.25,
    position_weight=0.0,
    exposure_weight=0.0,
    minimum_training_entries=0,
    maximum_training_batches=20,
    excess_activity_weight=0.0,
    long_hold_weight=0.0,
    stop_risk_weight=0.10,
    capital_time_weight=0.002,
    with_components=False,
):
    """Independent-session return/risk objective. Invalid residuals score null.

    Required entry count is an explicit research constraint, off by default.
    Every rejection carries a reason; never reward fictitious final liquidation.
    """
    weights = (
        drawdown_weight,
        dispersion_weight,
        position_weight,
        exposure_weight,
        excess_activity_weight,
        long_hold_weight,
        stop_risk_weight,
        capital_time_weight,
    )
    if (
        not results
        or not np.isfinite(initial_cash)
        or initial_cash <= 0
        or not all(np.isfinite(w) and w >= 0 for w in weights)
    ):
        raise ValueError("Invalid objective contract")
    if type(minimum_training_entries) is not int or minimum_training_entries < 0:
        raise ValueError("Minimum activity must be a nonnegative integer")
    if type(maximum_training_batches) is not int or maximum_training_batches < max(
        1, minimum_training_entries
    ):
        raise ValueError("Maximum activity target must cover minimum activity")
    pnl = np.array([r["net_pnl"] for r in results], dtype=float) / initial_cash
    dd = np.array([r["drawdown"] for r in results], dtype=float) / initial_cash
    entries = np.array([r["positions_opened"] for r in results], dtype=float)
    exposure = np.array([r["exposure_seconds"] for r in results], dtype=float) / 3600
    batches = np.array(
        [r.get("filled_batches", r["positions_opened"]) for r in results], dtype=float
    )
    overdue = np.array(
        [r.get("long_hold_dollar_seconds", [0] * len(r["net_pnl"])) for r in results],
        dtype=float,
    ) / (initial_cash * 3600)
    # Missing metrics are an error when their weights are enabled. Never
    # silently give an older replay a free risk/holding penalty.
    def hours(name, weight):
        if weight and any(name not in r for r in results):
            raise ValueError(f"Objective requires replay metric {name}")
        return np.array([r.get(name, [0] * len(r['net_pnl'])) for r in results], dtype=float) / (initial_cash * 3600)
    risk_hours = hours('stop_risk_dollar_seconds', stop_risk_weight)
    capital_hours = hours('capital_dollar_seconds', capital_time_weight)
    if not all(
        np.isfinite(v).all() for v in (pnl, dd, entries, exposure, batches, overdue, risk_hours, capital_hours)
    ):
        raise ValueError(
            "Nonfinite financial result; reject evaluation rather than skip it"
        )
    components = dict(
        mean_return=pnl.mean(0), drawdown_penalty=drawdown_weight * dd.mean(0),
        downside_penalty=dispersion_weight * np.sqrt(np.square(np.minimum(pnl, 0)).mean(0)),
        position_penalty=position_weight * entries.mean(0) / 100,
        exposure_penalty=exposure_weight * exposure.mean(0),
        excess_activity_penalty=excess_activity_weight * (np.maximum(batches - maximum_training_batches, 0) / maximum_training_batches).mean(0),
        legacy_long_hold_penalty=long_hold_weight * overdue.mean(0),
        stop_risk_penalty=stop_risk_weight * risk_hours.mean(0),
        capital_time_penalty=capital_time_weight * capital_hours.mean(0),
    )
    values = components['mean_return'] - sum(v for k, v in components.items() if k != 'mean_return')
    valid = np.array([r["terminal_valid"] for r in results], dtype=bool).all(0)
    active = (batches >= minimum_training_entries).all(0)
    reasons = [
        None
        if v and a
        else "residual_exposure"
        if not v
        else "minimum_training_activity"
        for v, a in zip(valid, active)
    ]
    scores = [
        float(v) if reason is None else None for v, reason in zip(values, reasons)
    ]
    if with_components:
        return scores, reasons, {k: v.tolist() for k, v in components.items()}
    return scores, reasons
