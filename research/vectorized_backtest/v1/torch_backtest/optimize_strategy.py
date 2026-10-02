"""Resident-GPU genetic optimization: one session -> two sessions -> validation.

Run through ``python -B -m ...run_optimization``. Default budget is 8 candidates
and 8 generations per phase. All accounts reset for every session/evaluation.
Validation is read only AFTER both winners have been frozen; it is never a
fitness input. A checkpoint preserves population and RNG after each generation.
"""

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
import torch

from .genetic_search import (
    StrategySpace,
    initial_population,
    next_population,
    objective,
)
from .search_sessions import RUNTIME, certified_cache, validate_split
from .strategy_one_replay import StrategyOneReplay
from .strategy_one_tape import prepare


def save(path, value):
    """Atomic, runtime-owned checkpoint publication; retain generation receipts."""
    path = Path(path).resolve()
    if not path.is_relative_to(RUNTIME.resolve()):
        raise ValueError("Search artifacts require the configured runtime root")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class SessionObjective:
    """One resident tape/compiled runner reused for every training population.

    [B,10] genomes decode into policy tensors. Market [T,N,F] is shared across
    B independent accounts. Session tapes are transferred one at a time when
    their objective is constructed; a memory budget is enforced before upload.
    """

    def __init__(self, session, space, size, clock_ms):
        self.session, self.space = session, space
        print(
            f"Preparing/compiling {session['session_date']} with B={size}, clock={clock_ms}ms",
            flush=True,
        )
        started = perf_counter()
        tape = prepare(session["cache"], clock_ms=clock_ms)
        free, _ = torch.cuda.mem_get_info()
        # Allow headroom for capture, compilation and expanded account state.
        estimate = tape.manifest["resident_estimate_bytes"] + size * 8193 * 6 * 8
        if estimate > free * 0.7:
            raise RuntimeError(
                "Insufficient GPU headroom for resident session; reduce session/ticker envelope explicitly"
            )
        tape = tape.to("cuda")
        decoded = space.decode(np.tile(space.default, (size, 1)))
        self.replay = StrategyOneReplay(
            tape,
            candidates=decoded["entry"],
            protection_values=decoded["protection"],
            action_values=decoded["actions"],
        )
        self.replay.compile()
        self.setup_seconds = perf_counter() - started
        print(
            f"Resident runner ready {session['session_date']}: setup={self.setup_seconds:.2f}s",
            flush=True,
        )

    def __call__(self, population):
        self.replay.update_candidates(**self.space.decode(population))
        result = self.replay.run()
        # Copy only small objective summaries. Full ledger remains on GPU and
        # can be exported for frozen winners without inflating every receipt.
        keys = (
            "net_pnl",
            "fees",
            "fill_count",
            "entered_episodes",
            "max_drawdown",
            "position_seconds",
            "open_quantities",
            "replay_seconds",
        )
        return {
            "session_date": self.session["session_date"],
            **{k: result[k] for k in keys},
        }


def run_phase(evaluators, space, args, output, phase, checkpoint, seed):
    rng = np.random.default_rng(args.seed + phase)
    if checkpoint is None:
        population = initial_population(space, rng, args.population, seed)
        best, best_score, start, stagnant = None, None, 0, 0
        history = []
    else:
        rng.bit_generator.state = checkpoint["rng"]
        population = np.array(checkpoint["population"])
        best, best_score = checkpoint["best"], checkpoint["best_score"]
        start, stagnant, history = (
            checkpoint["next_generation"],
            checkpoint["stagnant"],
            checkpoint["history"],
        )
    for generation in range(start, args.generations):
        results = [evaluate(population) for evaluate in evaluators]
        scores = objective(results, **args.weights)
        index = int(np.argmax(scores))
        improved = best_score is None or scores[index] > best_score + args.tolerance
        if improved:
            best, best_score, stagnant = (
                population[index].tolist(),
                float(scores[index]),
                0,
            )
        else:
            stagnant += 1
        receipt = {
            "phase": phase,
            "generation": generation,
            "population": population.tolist(),
            "scores": scores.tolist(),
            "session_results": results,
            "best_score": best_score,
            "stagnant_generations": stagnant,
            "diversify_next": stagnant >= 3,
        }
        save(output / f"phase_{phase}_generation_{generation:03d}.json", receipt)
        history.append(
            {
                "generation": generation,
                "best_score": best_score,
                "median_score": float(np.median(scores)),
            }
        )
        population = next_population(
            space, rng, population, scores, diversify=stagnant >= 3
        )
        state = {
            "phase": phase,
            "next_generation": generation + 1,
            "population": population.tolist(),
            "best": best,
            "best_score": best_score,
            "stagnant": stagnant,
            "history": history,
            "rng": rng.bit_generator.state,
        }
        save(output / "checkpoint.json", state)
        save(
            output / "status.json",
            {
                "status": "training",
                "phase": phase,
                "completed_generations": generation + 1,
                "generation_cap": args.generations,
                "best_training_score": best_score,
                "stagnant_generations": stagnant,
            },
        )
        print(
            json.dumps(
                {
                    "phase": phase,
                    "generation": generation + 1,
                    "best_training_score": best_score,
                    "stagnant": stagnant,
                }
            ),
            flush=True,
        )
    winner = {
        "phase": phase,
        "genome": best,
        "training_score": best_score,
        "history": history,
        "decoded": space.decode([best]),
    }
    save(output / f"winner_{phase}.json", winner)
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-run-id",
        action="append",
        help="Exactly two certified saved source runs",
    )
    parser.add_argument(
        "--validation-run-id", action="append", help="Independent later source runs"
    )
    parser.add_argument(
        "--cache", action="append", default=[], help="run_id=runtime/cache/path"
    )
    parser.add_argument("--clock-ms", type=int, choices=(500, 1000), default=1000)
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--drawdown-weight", type=float, default=0.5)
    parser.add_argument("--dispersion-weight", type=float, default=0.25)
    parser.add_argument("--position-weight", type=float, default=0.0)
    parser.add_argument("--exposure-weight", type=float, default=0.0)
    parser.add_argument(
        "--resume",
        type=Path,
        help="Existing runtime run with identical source/configuration",
    )
    args = parser.parse_args(argv)
    train_ids = args.train_run_id or [
        "51dcacfb-bc9e-44a0-a9c8-8e2d2f103156",
        "1cd937c3-2dd1-4369-a477-bc74e532c94d",
    ]
    validation_ids = args.validation_run_id or ["640966f8-2b4c-470e-a291-9e002339c172"]
    if len(train_ids) != 2 or len(set(train_ids + validation_ids)) != len(
        train_ids + validation_ids
    ):
        parser.error("Use two distinct training runs and disjoint validation runs")
    if (
        args.population < 4
        or args.population > 64
        or not 1 <= args.generations <= 100
        or not np.isfinite(args.tolerance)
        or args.tolerance < 0
    ):
        parser.error(
            "Population 4..64, generations 1..100 and finite nonnegative tolerance required"
        )
    args.weights = {
        name: getattr(args, name)
        for name in (
            "drawdown_weight",
            "dispersion_weight",
            "position_weight",
            "exposure_weight",
        )
    }
    if not all(np.isfinite(v) and v >= 0 for v in args.weights.values()):
        parser.error("Cost weights must be finite and nonnegative")
    if not RUNTIME.is_dir() or not torch.cuda.is_available():
        raise RuntimeError("Required runtime root and CUDA must be available")
    cache = {}
    for item in args.cache:
        key, value = item.split("=", 1)
        if key in cache:
            parser.error("Duplicate cache mapping")
        cache[key] = value
    output = (
        args.resume or RUNTIME / "vectorized_backtest" / "strategy_search" / uuid4().hex
    ).resolve()
    if not output.is_relative_to(RUNTIME.resolve()):
        raise ValueError("Search output must belong to runtime root")
    output.mkdir(parents=True, exist_ok=args.resume is not None)
    print("Search output: " + str(output), flush=True)
    space = StrategySpace()
    code = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Path(__file__).parent.glob("*.py")
    }
    identity = {
        "train_run_ids": train_ids,
        "validation_run_ids": validation_ids,
        "clock_ms": args.clock_ms,
        "population": args.population,
        "generations": args.generations,
        "seed": args.seed,
        "tolerance": args.tolerance,
        "weights": args.weights,
        "source_hashes": code,
    }
    if args.resume:
        if json.loads((output / "identity.json").read_text()) != identity:
            raise RuntimeError(
                "Resume requires identical source bytes, split and solver/objective configuration"
            )
        if (output / "report.json").exists():
            raise RuntimeError(
                "Completed validation is immutable; this run needs no resume"
            )
    else:
        save(output / "identity.json", identity)
    from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials

    _load_private_credentials()
    try:
        save(output / "status.json", {"status": "certifying_sources"})
        sessions = [
            certified_cache(key, output / key / "tape", existing=cache.get(key))
            for key in train_ids + validation_ids
        ]
        train, validation = sessions[:2], sessions[2:]
        validate_split(train, validation)
        save(
            output / "manifest.json",
            {
                "identity": identity,
                "sessions": sessions,
                "parameter_dimensions": [asdict(d) for d in space.dimensions],
                "policy_contract": "unified-strategy-one-causal-v2",
                "funnel": "frozen-release-population",
                "commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip(),
            },
        )
        # Only training runners are resident during search. Validation is not
        # even compiled until both winners are sealed on disk.
        save(output / "status.json", {"status": "compiling_training_runners"})
        evaluators = [
            SessionObjective(s, space, args.population, args.clock_ms) for s in train
        ]
        save(
            output / "setup.json",
            [
                {
                    "session_date": e.session["session_date"],
                    "setup_seconds": e.setup_seconds,
                }
                for e in evaluators
            ],
        )
        checkpoint = (
            json.loads((output / "checkpoint.json").read_text())
            if args.resume and (output / "checkpoint.json").exists()
            else None
        )
        if (output / "winner_1.json").exists():
            first = json.loads((output / "winner_1.json").read_text())["genome"]
        else:
            first = run_phase(
                evaluators[:1],
                space,
                args,
                output,
                1,
                checkpoint if checkpoint and checkpoint["phase"] == 1 else None,
                None,
            )
        if (output / "winner_2.json").exists():
            second = json.loads((output / "winner_2.json").read_text())["genome"]
        else:
            second = run_phase(
                evaluators,
                space,
                args,
                output,
                2,
                checkpoint if checkpoint and checkpoint["phase"] == 2 else None,
                first,
            )
        finalists = space.repair(
            [space.default, first, second] + [second] * (args.population - 3)
        )
        training_results = [evaluate(finalists) for evaluate in evaluators]
        del evaluators
        import gc

        gc.collect()
        torch.cuda.empty_cache()
        save(output / "status.json", {"status": "validating_frozen_winners"})
        validation_results = []
        for session in validation:
            evaluate = SessionObjective(session, space, args.population, args.clock_ms)
            validation_results.append(evaluate(finalists))
            del evaluate
            gc.collect()
            torch.cuda.empty_cache()
        # Final reports expose the three frozen finalists, not the duplicate
        # padding lanes needed to reuse a fixed-shape captured runner.
        training_results = [
            {k: v[:3] if isinstance(v, list) else v for k, v in r.items()}
            for r in training_results
        ]
        validation_results = [
            {k: v[:3] if isinstance(v, list) else v for k, v in r.items()}
            for r in validation_results
        ]
        report = {
            "status": "completed",
            "candidate_order": ["default", "one_session_winner", "two_session_winner"],
            "finalists": finalists[:3].tolist(),
            "training_results": training_results,
            "validation_results": validation_results,
            "training_scores": objective(training_results, **args.weights)[:3].tolist(),
            "validation_scores": objective(validation_results, **args.weights)[
                :3
            ].tolist(),
            "validation_used_for_selection": False,
            "global_optimum_certified": False,
        }
        save(output / "report.json", report)
        save(output / "status.json", {"status": "completed"})
        print(json.dumps(report), flush=True)
    except Exception as error:
        save(
            output / "status.json",
            {
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        raise
    return 0
