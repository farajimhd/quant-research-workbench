"""Bounded random genetic search: one session, two sessions, frozen evaluation.

Default invocation writes a plan. --synthetic executes only dummy tapes.
Historical execution needs --execute and an explicit session specification.
All accounts reset independently; this does not carry cash between dates.
"""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

import argparse
import gc
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import torch

from .encoding.config import Session
from .fixtures import synthetic_tape
from .genome import VERSION, StrategySpace
from .grid import Settings
from .prepare import prepare_tape
from .progress import safe_diagnostic
from .runtime import DEFAULT, code_hash, configure_caches, require_runtime, write_json
from .search_objective import SessionObjective, score


def evolve(space, rng, population, scores, diversify=False):
    numeric = np.array([-float("inf") if value is None else value for value in scores])
    order = np.argsort(-numeric, kind="stable")
    rows = population.copy()
    rows[:2] = population[order[:2]]
    for i in range(2, len(rows)):
        if rng.random() < (0.5 if diversify else 0.2):
            rows[i] = space.sample(rng, 1)[0]
        else:
            parents = []
            for _ in range(2):
                choices = rng.integers(len(rows), size=3)
                parents.append(population[choices[np.argmax(numeric[choices])]])
            rows[i] = space.offspring(rng, *parents)
    return space.validate(rows)


def phase(evaluators, space, args, output, number, seed=None, checkpoint=None):
    rng = np.random.default_rng(args.seed + number)
    best, best_score, stagnant, start = None, None, 0, 0
    last = None
    if checkpoint:
        rng.bit_generator.state = checkpoint["rng"]
        rows = space.validate(checkpoint["population"])
        best, best_score = checkpoint["best"], checkpoint["best_score"]
        stagnant, start = checkpoint["stagnant"], checkpoint["next_generation"]
        last = checkpoint["last_completed"]
        space.repair_counts = dict(checkpoint["repair_counts"])
    else:
        rows = space.sample(rng, args.population)
        if seed is not None:
            rows[0] = seed
        write_json(
            output / f"phase_{number}_initial.json",
            dict(
                population=rows.tolist(),
                decoded=[asdict(v) for v in space.decode(rows)],
                initialization="random" if seed is None else "previous_winner",
                repair_counts=dict(space.repair_counts),
            ),
        )
    for generation in range(start, args.generations):
        started = perf_counter()
        status = dict(
            status="training",
            phase=number,
            active_generation=generation + 1,
            completed_generations=generation,
            generation_cap=args.generations,
            last_completed=last,
        )
        write_json(output / "status.json", status)
        results = []
        for index, evaluator in enumerate(evaluators):

            def progress(event):
                # Publish last COMPLETE metrics even during the next group.
                write_json(
                    output / "status.json",
                    dict(status, active_session=index, progress=event),
                )

            evaluator.progress = progress
            results.append(evaluator(rows))
        scores, reasons = score(
            results,
            **args.weights,
            minimum_training_entries=args.minimum_training_entries,
        )
        numeric = np.array([-float("inf") if s is None else s for s in scores])
        winner = int(numeric.argmax())
        improved = scores[winner] is not None and (
            best_score is None or scores[winner] > best_score + 1e-12
        )
        if improved:
            best, best_score, stagnant = rows[winner].tolist(), scores[winner], 0
        else:
            stagnant += 1
        receipt = dict(
            phase=number,
            generation=generation + 1,
            population=rows.tolist(),
            repair_counts=dict(space.repair_counts),
            scores=scores,
            rejection_reasons=reasons,
            session_results=results,
            best_score=best_score,
            stagnant_generations=stagnant,
            end_to_end_seconds=perf_counter() - started,
        )
        write_json(output / f"phase_{number}_generation_{generation:03d}.json", receipt)
        last = dict(
            generation=generation + 1,
            best_score=best_score,
            generation_best_candidate=winner if scores[winner] is not None else None,
            sessions=[
                {
                    key: value[winner]
                    for key, value in r.items()
                    if isinstance(value, list)
                }
                for r in results
            ],
            inactive_candidates=sum(
                all(r["positions_opened"][i] == 0 for r in results)
                for i in range(len(rows))
            ),
            invalid_candidates=sum(reason is not None for reason in reasons),
            compile_seconds=sum(r["compile_seconds"] for r in results),
            replay_seconds=sum(r["replay_seconds"] for r in results),
            end_to_end_seconds=receipt["end_to_end_seconds"],
        )
        rows = evolve(space, rng, rows, scores, stagnant >= 3)
        state = dict(
            phase=number,
            next_generation=generation + 1,
            population=rows.tolist(),
            best=best,
            best_score=best_score,
            stagnant=stagnant,
            rng=rng.bit_generator.state,
            last_completed=last,
            repair_counts=dict(space.repair_counts),
        )
        write_json(output / "checkpoint.json", state)
        write_json(
            output / "status.json",
            dict(status, completed_generations=generation + 1, last_completed=last),
        )
        print(
            json.dumps(
                dict(
                    phase=number,
                    completed=generation + 1,
                    best_score=best_score,
                    inactive=last["inactive_candidates"],
                )
            ),
            flush=True,
        )
    if best is None:
        raise RuntimeError(
            "No valid training candidate; preserve receipts and revise a new experiment explicitly"
        )
    write_json(
        output / f"winner_{number}.json",
        dict(
            genome=best,
            genome_sha256=space.identity(best),
            fitness=best_score,
            decoded=asdict(space.decode([best])[0]),
            selected_using="training_only",
        ),
    )
    return best


def split(spec):
    training, validation = spec.get("training", []), spec.get("validation", [])
    if len(training) != 2 or not validation:
        raise ValueError("Exactly two training dates and later validation required")
    starts = [datetime.fromisoformat(s["start"]) for s in training + validation]
    ends = [datetime.fromisoformat(s["end"]) for s in training + validation]
    if any(
        t.tzinfo is None or e.tzinfo is None or e <= t for t, e in zip(starts, ends)
    ):
        raise ValueError("Explicit timezone-aware start/end required")
    market_zone = ZoneInfo("America/New_York")
    dates = [s.astimezone(market_zone).date() for s in starts]
    if any(e.astimezone(market_zone).date() != d for e, d in zip(ends, dates)):
        raise ValueError("A session must remain within its New York market date")
    if (
        len(set(dates)) != len(dates)
        or dates[1] <= dates[0]
        or min(dates[2:]) <= dates[1]
    ):
        raise ValueError("Training must precede unique disjoint validation dates")
    return training, validation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument(
        "--sessions",
        type=Path,
        help="JSON training[2]/validation with manifest,ledger,start,end",
    )
    parser.add_argument("--runtime", type=Path, default=DEFAULT)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument(
        "--backend",
        choices=("eager", "compile", "cudagraph", "compiled_graph"),
        default="compiled_graph",
    )
    parser.add_argument("--minimum-training-entries", type=int, default=0)
    parser.add_argument("--validation-preobserved", action="store_true")
    parser.add_argument("--drawdown-weight", type=float, default=0.5)
    parser.add_argument("--dispersion-weight", type=float, default=0.25)
    parser.add_argument("--position-weight", type=float, default=0.0)
    parser.add_argument("--exposure-weight", type=float, default=0.0)
    args = parser.parse_args(argv)
    if (
        not 4 <= args.population <= 64
        or not 1 <= args.generations <= 100
        or args.minimum_training_entries < 0
    ):
        parser.error(
            "Population4..64/generations1..100/nonnegative activity constraint required"
        )
    args.weights = dict(
        initial_cash=Settings().initial_cash,
        drawdown_weight=args.drawdown_weight,
        dispersion_weight=args.dispersion_weight,
        position_weight=args.position_weight,
        exposure_weight=args.exposure_weight,
    )
    if not all(np.isfinite(v) and v >= 0 for v in args.weights.values()):
        parser.error("Invalid objective weights")
    torch.set_num_threads(1)
    root = require_runtime(args.runtime)
    space = StrategySpace()
    spec = json.loads(args.sessions.read_text()) if args.sessions else None
    if args.execute and not args.synthetic:
        if spec is None:
            parser.error("Historical execution requires explicit --sessions")
        split(spec)
    identity = dict(
        version=VERSION,
        code_hash=code_hash(),
        grammar=space.manifest(),
        sessions=spec,
        synthetic=args.synthetic,
        seed=args.seed,
        population=args.population,
        generations=args.generations,
        minimum_training_entries=args.minimum_training_entries,
        objective=args.weights,
        device=args.device,
        backend=args.backend,
        validation_preobserved=args.validation_preobserved,
    )
    # JSON canonicalizes integer class-map keys, so resume compares the same
    # persisted representation rather than rejecting an otherwise identical run.
    identity = json.loads(json.dumps(identity, sort_keys=True, allow_nan=False))
    if not args.execute and not args.synthetic:
        destination = require_runtime(root / "plans" / uuid4().hex)
        write_json(destination / "plan.json", identity)
        print(str(destination))
        return 0
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Requested CUDA unavailable; no fallback")
    if args.backend in ("compile", "compiled_graph"):
        configure_caches(root)
    output = require_runtime(args.resume or root / "experiments" / uuid4().hex)
    if args.resume:
        if json.loads((output / "identity.json").read_text()) != identity:
            raise RuntimeError("Resume requires identical code/grammar/split/budget")
        if (output / "report.json").exists():
            raise RuntimeError("Completed frozen evaluation is immutable")
    else:
        write_json(output / "identity.json", identity)
    print("Optimization output: " + str(output), flush=True)

    source_timings = {"training": [], "validation": []}

    def tape(role, index):
        started = perf_counter()

        def preparation(event):
            write_json(
                output / "status.json",
                dict(
                    status="preparing_source",
                    role=role,
                    session_index=index,
                    progress=event
                    if isinstance(event, dict)
                    else safe_diagnostic(event),
                ),
            )

        preparation(
            "Prepare certified resident tape"
            if not args.synthetic
            else "Prepare dummy resident tape"
        )
        if args.synthetic:
            value = synthetic_tape(seconds=75, listings=2).to(args.device)
            source_timings[role].append(perf_counter() - started)
            return value
        from research.mlops.env import discover_env_files, load_env_files

        repo = Path(__file__).resolve().parents[4]
        load_env_files(discover_env_files(repo), verbose=False)
        item = spec[role][index]
        session = Session(
            Path(item["manifest"]),
            Path(item["ledger"]),
            root / "source_cache",
            datetime.fromisoformat(item["start"]),
            datetime.fromisoformat(item["end"]),
        )
        value = prepare_tape(session, space.settings, progress=preparation).to(
            args.device
        )
        source_timings[role].append(perf_counter() - started)
        return value

    try:
        training = [
            SessionObjective(
                tape("training", i), space, args.population, backend=args.backend
            )
            for i in range(2)
        ]
        sources = [e.tape.provenance for e in training]
        fingerprints = [s.get("fingerprint", s) for s in sources]
        source_file = output / "training_sources.json"
        if (
            source_file.exists()
            and json.loads(source_file.read_text())["fingerprints"] != fingerprints
        ):
            raise RuntimeError(
                "Resume source provenance differs from the sealed training tapes"
            )
        if not source_file.exists():
            write_json(
                source_file,
                dict(
                    fingerprints=fingerprints,
                    provenance=sources,
                    preparation_seconds=source_timings["training"],
                ),
            )
        checkpoint = (
            json.loads((output / "checkpoint.json").read_text())
            if args.resume and (output / "checkpoint.json").exists()
            else None
        )
        winners = []
        for number in (1, 2):
            file = output / f"winner_{number}.json"
            if file.exists():
                frozen = json.loads(file.read_text())
                winner = frozen["genome"]
                if frozen["genome_sha256"] != space.identity(winner):
                    raise RuntimeError("Frozen winner genome hash mismatch")
                decoded = json.loads(json.dumps(asdict(space.decode([winner])[0])))
                if frozen["decoded"] != decoded:
                    raise RuntimeError("Frozen winner decode disagrees with its genome")
            else:
                winner = phase(
                    training[:number],
                    space,
                    args,
                    output,
                    number,
                    seed=winners[0] if winners else None,
                    checkpoint=checkpoint
                    if checkpoint and checkpoint["phase"] == number
                    else None,
                )
            winners.append(winner)
        finalists = space.validate([space.default, *winners])
        # Both genomes are frozen on disk before validation construction/replay.
        train_results = [e(finalists) for e in training]
        del training
        gc.collect()
        if args.device == "cuda":
            torch.cuda.empty_cache()
        write_json(output / "status.json", dict(status="evaluating_frozen_winners"))
        count = 1 if args.synthetic else len(spec["validation"])
        validation_results, validation_sources = [], []
        for i in range(count):
            evaluator = SessionObjective(
                tape("validation", i), space, args.population, backend=args.backend
            )
            validation_sources.append(evaluator.tape.provenance)
            validation_results.append(evaluator(finalists))
            del evaluator
            gc.collect()
            if args.device == "cuda":
                torch.cuda.empty_cache()
        report = dict(
            status="completed",
            synthetic=args.synthetic,
            candidate_order=["default", "one_session", "two_session"],
            finalists=finalists.tolist(),
            training_results=train_results,
            validation_results=validation_results,
            validation_sources=validation_sources,
            source_preparation_seconds=source_timings,
            validation_scores=score(validation_results, **args.weights)[0],
            validation_used_for_selection=False,
            validation_preobserved=args.validation_preobserved,
        )
        write_json(output / "report.json", report)
        write_json(
            output / "status.json",
            dict(
                status="completed",
                candidate_order=report["candidate_order"],
                training_results=train_results,
                validation_results=validation_results,
            ),
        )
        return 0
    except KeyboardInterrupt:
        last = (
            json.loads((output / "checkpoint.json").read_text())
            if (output / "checkpoint.json").exists()
            else {}
        )
        write_json(
            output / "status.json",
            dict(
                status="interrupted",
                last_completed=last.get("last_completed"),
                recovery="Resume identical arguments from the last completed generation",
            ),
        )
        raise
    except Exception as error:
        write_json(
            output / "status.json",
            dict(
                status="failed",
                error_type=type(error).__name__,
                message=safe_diagnostic(error),
            ),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
