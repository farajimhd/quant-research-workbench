"""One bounded random genetic search over all training sessions, then frozen evaluation.

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
from dataclasses import asdict, replace
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
from .optimization_ui import SearchPanel
from .prepare import prepare_tape
from .progress import safe_diagnostic
from .runtime import DEFAULT, code_hash, configure_caches, require_runtime, write_json
from .search_objective import score
from .session_pool import SessionPool
from .session_pipeline import PreparedSessions


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


def summarize(results, lane, initial_cash=10000):
    """Completed metrics only: distinct batches, child orders and fill events."""
    sold = sum(r.get("sold_shares", [0] * len(r["net_pnl"]))[lane] for r in results)
    age = sum(
        r.get("sold_share_seconds", [0] * len(r["net_pnl"]))[lane] for r in results
    )
    return dict(
        total_pnl=sum(r["net_pnl"][lane] for r in results),
        worst_pnl=min(r["net_pnl"][lane] for r in results),
        worst_drawdown=max(r["drawdown"][lane] for r in results),
        batches=sum(
            r.get("filled_batches", r["positions_opened"])[lane] for r in results
        ),
        positions=sum(r["positions_opened"][lane] for r in results),
        fills=sum(r.get("fill_count", [0] * len(r["net_pnl"]))[lane] for r in results),
        open=sum(
            r.get("open_positions", [0] * len(r["net_pnl"]))[lane] for r in results
        ),
        mean_hold_seconds=age / max(1, sold),
        long_hold_capital_hours=sum(
            r.get("long_hold_dollar_seconds", [0] * len(r["net_pnl"]))[lane]
            for r in results
        )
        / (initial_cash * 3600),
        stop_risk_hours=sum(r.get('stop_risk_dollar_seconds', [0] * len(r['net_pnl']))[lane] for r in results) / (initial_cash * 3600),
        capital_hours=sum(r.get('capital_dollar_seconds', [0] * len(r['net_pnl']))[lane] for r in results) / (initial_cash * 3600),
    )


def constraint_ranks(results, scores, minimum):
    """Feasibility dominates fitness; infeasible lanes search toward feasibility.

    This is NOT their financial objective. A missing session cannot be bought
    off by profit on another. Once feasible, selection uses actual objective.
    """
    batches = np.array(
        [r.get("filled_batches", r["positions_opened"]) for r in results], dtype=float
    )
    flat = np.array([r["terminal_valid"] for r in results], dtype=bool)
    deficit = np.maximum(minimum - batches, 0).mean(0) / max(1, minimum)
    violation = deficit + (~flat).mean(0)
    feasible = np.array([v is not None for v in scores])
    fitness = np.array([v if v is not None else 0 for v in scores])
    order = np.lexsort((fitness, -violation, feasible))
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(len(scores))
    return ranks, violation


def phase(
    evaluators, space, args, output, number=1, seed=None, checkpoint=None, panel=None,
    before_selection=None,
    pipeline_state=None,
    initial_rows=None,
):
    """ONE phase on the complete training set; compatibility name is internal."""
    if seed is not None or number != 1:
        raise ValueError(
            "Single-phase search must start randomly, without injected winners"
        )
    rng = np.random.default_rng(args.seed)
    best, best_score, best_metrics, stagnant, start = None, None, None, 0, 0
    last, seen = None, set()
    if checkpoint:
        rng.bit_generator.state = checkpoint["rng"]
        rows = space.validate(checkpoint["population"])
        best, best_score = checkpoint["best"], checkpoint["best_score"]
        best_metrics = checkpoint.get("best_metrics")
        stagnant, start = checkpoint["stagnant"], checkpoint["next_generation"]
        last = checkpoint["last_completed"]
        seen = set(checkpoint.get("seen", []))
        space.repair_counts = dict(checkpoint["repair_counts"])
    else:
        rows = space.sample(rng, args.population) if initial_rows is None else space.validate(initial_rows)
        if len(rows) != args.population:
            raise ValueError('Initial random population has wrong batch size')
        write_json(
            output / "population_initial.json",
            dict(
                population=rows.tolist(),
                decoded=[asdict(v) for v in space.decode(rows)],
                initialization="random",
                repair_counts=dict(space.repair_counts),
            ),
        )

    def emit(event):
        if panel:
            panel.emit(event)
        else:
            write_json(output / "status.json", event)

    # Persist the exact random population/RNG before any long session replay.
    def save(next_generation):
        write_json(
            output / "checkpoint.json",
            dict(
                next_generation=next_generation,
                population=rows.tolist(),
                best=best,
                best_score=best_score,
                best_metrics=best_metrics,
                stagnant=stagnant,
                rng=rng.bit_generator.state,
                last_completed=last,
                seen=sorted(seen),
                repair_counts=dict(space.repair_counts),
            ),
        )

    save(start)
    for generation in range(start, args.generations):
        started = perf_counter()
        wait_start = pipeline_state()["data_wait_seconds"] if pipeline_state else 0.0
        seen.update(space.identity(row) for row in rows)
        status = dict(
            status="training",
            stage="Replay training population",
            active_generation=generation + 1,
            completed_generations=generation,
            generation_cap=args.generations,
            last_completed=last,
            best_score=best_score,
            best_metrics=best_metrics,
            unique_candidates=len(seen),
            objective_evaluations=generation * len(rows),
            candidate_session_replays=generation * len(rows) * len(evaluators),
            checkpoint=str(output / "checkpoint.json"),
        )
        emit(status)
        results = []
        for index, evaluator in enumerate(evaluators):
            if hasattr(evaluator, "prepare"):
                evaluator.prepare()
            session_file = (
                output / f"generation_{generation:03d}" / f"session_{index:03d}.json"
            )
            fingerprints = [space.identity(row) for row in rows]

            def progress(event):
                emit(
                    dict(
                        status,
                        active_session=index,
                        focus=f"Generation {generation + 1} · training session {index + 1}/{len(evaluators)}",
                        candidate_session_replays=(generation * len(evaluators) + index)
                        * len(rows),
                        progress=event,
                        **{k: event[k] for k in (
                            "resident_sessions", "gpu_resident_gib", "prefetched_sessions",
                            "transfer_wait_seconds", "transfer_prefetch_blocked", "capacity_growths"
                        ) if k in event},
                    )
                )

            evaluator.progress = progress
            if session_file.exists():
                receipt = json.loads(session_file.read_text(encoding="utf-8"))
                if receipt["population_fingerprints"] != fingerprints:
                    raise RuntimeError(
                        "Partial generation differs from restart population"
                    )
                result = receipt["result"]
                if (
                    hasattr(evaluator, "pool")
                    and result.get("source_fingerprint")
                    != evaluator.pool.tapes[index].provenance["fingerprint"]
                ):
                    raise RuntimeError("Partial generation source fingerprint mismatch")
            else:
                result = evaluator(rows)
                write_json(
                    session_file,
                    dict(population_fingerprints=fingerprints, result=result),
                )
            results.append(result)
            # Fixed lane's completed-session metrics are UI observations,
            # never a partial cross-session fitness or a selection decision.
            emit(
                dict(
                    status,
                    active_session=index,
                    focus=f"Completed session {index + 1}/{len(evaluators)}: {result.get('session', index)}",
                    candidate_session_replays=(generation * len(evaluators) + index + 1)
                    * len(rows),
                    last_session_metrics=summarize([result], 0),
                    progress={},
                )
            )
        # Certification/sealing is a prerequisite for aggregate selection, not
        # a prerequisite for replaying an already certified earlier session.
        if before_selection:
            before_selection()
        scores, reasons, components = score(
            results,
            **args.weights,
            minimum_training_entries=args.minimum_training_entries,
            with_components=True,
        )
        ranks, violations = constraint_ranks(
            results, scores, args.minimum_training_entries
        )
        numeric = np.array([-float("inf") if v is None else v for v in scores])
        winner = int(numeric.argmax())
        improved = scores[winner] is not None and (
            best_score is None or scores[winner] > best_score + 1e-12
        )
        if improved:
            best, best_score, stagnant = rows[winner].tolist(), scores[winner], 0
            best_metrics = summarize(results, winner)
            best_metrics['objective_components'] = {k: v[winner] for k, v in components.items()}
        else:
            stagnant += 1
        receipt = dict(
            generation=generation + 1,
            population=rows.tolist(),
            repair_counts=dict(space.repair_counts),
            scores=scores,
            objective_components=components,
            rejection_reasons=reasons,
            constraint_violations=violations.tolist(),
            selection_ranks=ranks.tolist(),
            session_results=results,
            best_score=best_score,
            best_metrics=best_metrics,
            stagnant_generations=stagnant,
            end_to_end_seconds=perf_counter() - started,
            pipeline=pipeline_state() if pipeline_state else None,
        )
        write_json(output / f"generation_{generation:03d}.json", receipt)
        # Bounded training-only archive preserves alternative profit/risk
        # policies even when the scalar winner changes. No evaluation inputs.
        archive_path = output / 'training_archive.json'
        archive = json.loads(archive_path.read_text()) if archive_path.exists() else []
        for lane, value in enumerate(scores):
            if value is not None:
                metrics = summarize(results, lane)
                archive.append(dict(genome=rows[lane].tolist(), sha256=space.identity(rows[lane]), objective=value, metrics=metrics))
        archive = list({item['sha256']: item for item in archive}.values())
        # Preserve both objective leaders and profit leaders, bounded at64.
        keep = sorted(archive, key=lambda x: x['objective'], reverse=True)[:32]
        keep += sorted(archive, key=lambda x: x['metrics']['total_pnl'], reverse=True)[:32]
        write_json(archive_path, list({item['sha256']: item for item in keep}.values()))
        last = dict(
            generation=generation + 1,
            best_score=best_score,
            compile_seconds=sum(r["compile_seconds"] for r in results),
            replay_seconds=sum(r["replay_seconds"] for r in results),
            bind_seconds=sum(r.get("bind_seconds", 0) for r in results),
            end_to_end_seconds=receipt["end_to_end_seconds"],
            data_wait_seconds=(pipeline_state()["data_wait_seconds"] - wait_start) if pipeline_state else 0.0,
            rule_prepare_seconds=sum(r.get("rule_prepare_seconds", 0) for r in results),
        )
        rows = evolve(space, rng, rows, ranks.tolist(), stagnant >= 3)
        save(generation + 1)
        emit(
            dict(
                status,
                completed_generations=generation + 1,
                best_score=best_score,
                best_metrics=best_metrics,
                last_completed=last,
                objective_evaluations=(generation + 1) * len(rows),
                candidate_session_replays=(generation + 1)
                * len(rows)
                * len(evaluators),
                feasible_candidates=sum(v is not None for v in scores),
                invalid_candidates=sum(v is not None for v in reasons),
                closest_violation=float(violations.min()),
                closest_metrics=summarize(results, int(ranks.argmax())),
                stagnant_generations=stagnant,
                immigrant_percent=50 if stagnant >= 3 else 20,
                eta=f"{(args.generations - generation - 1) * last['replay_seconds'] / 3600:.1f}h replay estimate",
            )
        )
    if best is None:
        raise RuntimeError(
            "Budget exhausted without an all-session feasible policy; no inactivity or residual exposure accepted"
        )
    write_json(
        output / "winner.json",
        dict(
            genome=best,
            genome_sha256=space.identity(best),
            fitness=best_score,
            metrics=best_metrics,
            decoded=asdict(space.decode([best])[0]),
            selected_using="all_training_sessions_only",
        ),
    )
    return best


def split(spec):
    training, validation = spec.get("training", []), spec.get("validation", [])
    if not training or not validation:
        raise ValueError("Nonempty training dates and later validation required")
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
        or dates[: len(training)] != sorted(dates[: len(training)])
        or min(dates[len(training) :]) <= max(dates[: len(training)])
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
        help="JSON training[N]/validation with manifest,ledger,start,end",
    )
    parser.add_argument("--runtime", type=Path, default=DEFAULT)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--output", type=Path, help="New explicit experiment directory")
    parser.add_argument("--population", type=int, default=32)
    parser.add_argument("--generations", type=int, default=50)
    parser.add_argument("--preparation-workers", type=int, default=2)
    parser.add_argument("--preparation-lookahead", type=int, default=2)
    parser.add_argument("--reuse-prepared", type=Path,
                        help="Explicit import of compatible sealed inputs from a previous experiment")
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument(
        "--backend",
        choices=("eager", "compile", "cudagraph", "compiled_graph"),
        default="compiled_graph",
    )
    parser.add_argument("--minimum-training-entries", type=int, default=1)
    parser.add_argument("--maximum-training-batches", type=int, default=20)
    parser.add_argument("--excess-activity-weight", type=float, default=0.0)
    parser.add_argument("--long-hold-weight", type=float, default=0.0)
    parser.add_argument("--stop-risk-weight", type=float, default=0.10)
    parser.add_argument("--capital-time-weight", type=float, default=0.002)
    parser.add_argument("--maximum-stop-risk-fraction", type=float, default=0.02)
    parser.add_argument("--maximum-position-hold-seconds", type=int, default=3600)
    parser.add_argument("--long-hold-seconds", type=int, default=300)
    parser.add_argument("--resident-gib", type=float, default=48)
    parser.add_argument("--maximum-host-gib", type=float, default=320)
    parser.add_argument("--maximum-tape-gib", type=float, default=12)
    parser.add_argument("--maximum-state-gib", type=float, default=8)
    parser.add_argument("--maximum-fills", type=int, default=65536)
    parser.add_argument("--graph-steps", type=int, default=32)
    parser.add_argument(
        "--ledger-mode", choices=("unique", "atomic", "inplace"), default="inplace"
    )
    parser.add_argument("--structural-workers", type=int, default=0)
    parser.add_argument("--plain", action="store_true")
    parser.add_argument(
        "--precompute-rules",
        action="store_true",
        help="Compile immutable atomic gates before account replay",
    )
    parser.add_argument("--validation-preobserved", action="store_true")
    parser.add_argument("--drawdown-weight", type=float, default=0.25)
    parser.add_argument("--dispersion-weight", type=float, default=0.25)
    parser.add_argument("--position-weight", type=float, default=0.0)
    parser.add_argument("--exposure-weight", type=float, default=0.0)
    args = parser.parse_args(argv)
    if (
        not 1 <= args.graph_steps <= 64
        or not 1 <= args.maximum_fills <= 1_000_000
        or args.maximum_training_batches < max(1, args.minimum_training_entries)
        or any(
            not np.isfinite(v) or v <= 0
            for v in (
                args.maximum_tape_gib,
                args.maximum_host_gib,
                args.maximum_state_gib,
            )
        )
        or not np.isfinite(args.resident_gib)
        or args.resident_gib < 0
    ):
        parser.error("Invalid graph/ledger/memory/activity bounds")
    if (
        not 4 <= args.population <= 1024
        or not 1 <= args.generations <= 100
        or args.minimum_training_entries < 0
    ):
        parser.error(
            "Population4..1024/generations1..100/nonnegative activity constraint required"
        )
    args.weights = dict(
        initial_cash=Settings().initial_cash,
        drawdown_weight=args.drawdown_weight,
        dispersion_weight=args.dispersion_weight,
        position_weight=args.position_weight,
        exposure_weight=args.exposure_weight,
        maximum_training_batches=args.maximum_training_batches,
        excess_activity_weight=args.excess_activity_weight,
        long_hold_weight=args.long_hold_weight,
        stop_risk_weight=args.stop_risk_weight,
        capital_time_weight=args.capital_time_weight,
    )
    if not all(np.isfinite(v) and v >= 0 for v in args.weights.values()):
        parser.error("Invalid objective weights")
    torch.set_num_threads(1)
    root = require_runtime(args.runtime)
    space = StrategySpace(replace(Settings(), long_hold_seconds=args.long_hold_seconds,
                                 maximum_stop_risk_fraction=args.maximum_stop_risk_fraction,
                                 maximum_position_hold_seconds=args.maximum_position_hold_seconds))
    spec = (
        json.loads(args.sessions.read_text(encoding="utf-8")) if args.sessions else None
    )
    if args.execute and not args.synthetic:
        if spec is None:
            parser.error("Historical execution requires explicit --sessions")
        split(spec)
        if args.minimum_training_entries < 1:
            parser.error(
                "Historical training requires at least one filled batch per session"
            )
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
        precompute_rules=args.precompute_rules,
        resources=dict(
            resident_gib=args.resident_gib,
            maximum_host_gib=args.maximum_host_gib,
            maximum_tape_gib=args.maximum_tape_gib,
            maximum_state_gib=args.maximum_state_gib,
            maximum_fills=args.maximum_fills,
            graph_steps=args.graph_steps,
            ledger_mode=args.ledger_mode,
            structural_workers=args.structural_workers,
            preparation_workers=args.preparation_workers,
            preparation_lookahead=args.preparation_lookahead,
            reuse_prepared=str(args.reuse_prepared) if args.reuse_prepared else None,
        ),
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
    if args.output and args.resume:
        parser.error("Use --output OR --resume")
    output = require_runtime(
        args.resume or args.output or root / "experiments" / uuid4().hex
    )
    if not args.resume and (output / "identity.json").exists():
        raise RuntimeError(
            "New experiment directory already owns an identity; use exact resume"
        )
    if args.resume:
        if json.loads((output / "identity.json").read_text()) != identity:
            raise RuntimeError("Resume requires identical code/grammar/split/budget")
        if (output / "report.json").exists():
            raise RuntimeError("Completed frozen evaluation is immutable")
    else:
        write_json(output / "identity.json", identity)
    print("Optimization output: " + str(output), flush=True)

    training_count = 2 if args.synthetic else len(spec["training"])
    validation_count = 1 if args.synthetic else len(spec["validation"])
    source_timings = {"training": [None] * training_count,
                      "validation": [None] * validation_count}
    if (not 1 <= args.preparation_workers <= 4 or not 0 <= args.preparation_lookahead <= 4
            or args.preparation_workers + args.preparation_lookahead > 6):
        raise ValueError("Bound preparation to 1..4 workers and <=6 upcoming sessions")
    if not args.synthetic:
        from .availability import configure_reader
        from .structural import worker_budget
        configure_reader(Path(__file__).resolve().parents[4])
        # --structural-workers remains a GLOBAL budget, not a per-session fanout.
        structural_budget = worker_budget(args.structural_workers)
        if structural_budget < args.preparation_workers:
            raise ValueError("Structural worker budget must cover concurrent preparation workers")
        structural_width = structural_budget // args.preparation_workers
    else:
        structural_width = 1
    # Every concurrent structural call now observes the same spawn environment;
    # its existing save/restore scope cannot remove another worker's settings.
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    config = dict(
        population=args.population,
        generations=args.generations,
        seed=args.seed,
        preparation_workers=args.preparation_workers,
        preparation_lookahead=args.preparation_lookahead,
        structural_workers_per_session=structural_width,
        training_sessions=training_count,
        validation_sessions=validation_count,
        minimum_training_entries=args.minimum_training_entries,
        maximum_training_batches=args.maximum_training_batches,
        long_hold_seconds=args.long_hold_seconds,
        long_hold_weight=args.long_hold_weight,
        maximum_position_hold_seconds=args.maximum_position_hold_seconds,
        maximum_stop_risk_fraction=args.maximum_stop_risk_fraction,
    )
    options = dict(
        maximum_state_gib=args.maximum_state_gib,
        maximum_fills=args.maximum_fills,
        graph_steps=args.graph_steps,
        precompute_rules=args.precompute_rules,
        ledger_mode=args.ledger_mode,
    )
    with SearchPanel(output, plain=args.plain) as panel:
        panel.emit(
            dict(
                config=config,
                status="preparing",
                stage="Prepare certified training tapes",
            )
        )

        def tape(role, index):
            started = perf_counter()

            def preparation(event):
                panel.emit(
                    dict(
                        preparation_focus=f"{role} {index + 1}: "
                        + ("dummy" if args.synthetic else spec[role][index]["start"]),
                        preparation_progress=event if isinstance(event, dict) else {},
                        preparation_message=event.get("message", "")
                        if isinstance(event, dict)
                        else safe_diagnostic(event),
                    )
                )

            if args.synthetic:
                value = synthetic_tape(seconds=75, listings=2)
            else:
                item = spec[role][index]
                session = Session(
                    Path(item["manifest"]),
                    Path(item["ledger"]),
                    root / "source_cache",
                    datetime.fromisoformat(item["start"]),
                    datetime.fromisoformat(item["end"]),
                    max_prepared_gib=args.maximum_tape_gib,
                )
                from .encoding.clickhouse import certify_source
                from .prepared_cache import import_prepared, load_prepared, save_prepared
                from .runtime import file_hash

                # A snapshot reuses exactly certified market evidence, never a
                # fallback query. Recheck the producer certificate on resume.
                certificate = certify_source(session)
                snapshot_identity = dict(
                    code_hash=code_hash(),
                    session=item,
                    manifest_sha256=file_hash(session.manifest),
                    build_id=certificate.source["build_id"],
                )
                snapshot = output / "inputs" / f"{role}_{index:03d}"
                value = load_prepared(snapshot, snapshot_identity)
                if value is None and args.reuse_prepared:
                    origin = args.reuse_prepared / "inputs" / f"{role}_{index:03d}"
                    if origin.exists():
                        value = import_prepared(origin, snapshot, snapshot_identity, space.manifest())
                        preparation(dict(stage="Import verified prepared dataset",
                                         message="Original bytes/provenance retained; new consumer receipt"))
                if value is None:
                    value = prepare_tape(
                        session,
                        space.settings,
                        maximum_gib=args.maximum_tape_gib,
                        structural_workers=structural_width,
                        progress=preparation,
                    )
                    save_prepared(snapshot, value, snapshot_identity)
                elif value.provenance["source_build"] != certificate.source["build_id"]:
                    raise ValueError(
                        "Cached market evidence belongs to another producer build"
                    )
            source_timings[role][index] = perf_counter() - started
            return value

        def pipeline_event(event):
            panel.emit(event)
            write_json(output / "preparation_progress.json", event["pipeline"])

        with PreparedSessions(
            training_count, lambda i: tape("training", i),
            workers=args.preparation_workers, lookahead=args.preparation_lookahead,
            maximum_host_gib=args.maximum_host_gib,
            maximum_tape_gib=args.maximum_tape_gib, emit=pipeline_event,
        ) as pipeline:
            pool = SessionPool(
                [], space, args.population, supplier=pipeline,
                cycle_prefetch=True,
                device=args.device, backend=args.backend,
                maximum_host_gib=args.maximum_host_gib,
                resident_gib=args.resident_gib, **options,
            )

            def seal_sources():
                if any(t is None for t in pool.tapes):
                    raise RuntimeError("Cannot select before EVERY training tape is certified")
                sources = [t.provenance for t in pool.tapes]
                fingerprints = [s["fingerprint"] for s in sources]
                source_file = output / "training_sources.json"
                if source_file.exists():
                    sealed = json.loads(source_file.read_text(encoding="utf-8"))
                    if sealed["fingerprints"] != fingerprints:
                        raise RuntimeError("Resume differs from sealed training tapes")
                else:
                    write_json(source_file, dict(
                        fingerprints=fingerprints, provenance=sources,
                        preparation_seconds=source_timings["training"],
                    ))

            checkpoint = (
                json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
                if (output / "checkpoint.json").exists() else None
            )
            try:
                if (output / "winner.json").exists():
                    frozen = json.loads((output / "winner.json").read_text(encoding="utf-8"))
                    winner = frozen["genome"]
                    if frozen["genome_sha256"] != space.identity(winner) or frozen[
                        "decoded"
                    ] != json.loads(json.dumps(asdict(space.decode([winner])[0]))):
                        raise RuntimeError("Frozen winner hash/decode differs from its tensor")
                else:
                    winner = phase(
                        pool.objectives(), space, args, output,
                        checkpoint=checkpoint, panel=panel,
                        before_selection=seal_sources,
                        pipeline_state=pipeline.state,
                    )
                finalists = space.validate([space.default, winner])
                panel.emit(dict(status="reporting", stage="Compare frozen finalists on training"))
                train_results = [e(finalists) for e in pool.objectives()]
                seal_sources()
                write_json(output / "pipeline_summary.json", {
                    **pipeline.state(), **pool.residency,
                    "preparation_seconds": pipeline.seconds,
                })
            except BaseException as error:
                panel.emit(dict(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                                stage="Drain preparation workers",
                                message=safe_diagnostic(error)))
                raise
            finally:
                pool.close()
            del pool
        gc.collect()
        if args.device == "cuda":
            torch.cuda.empty_cache()
        validation_results, validation_sources = [], []
        evaluation_tapes = [tape("validation", i) for i in range(validation_count)]
        validation_sources = [t.provenance for t in evaluation_tapes]
        pool = SessionPool(
            evaluation_tapes,
            space,
            args.population,
            device=args.device,
            backend=args.backend,
            maximum_host_gib=args.maximum_host_gib,
            resident_gib=args.resident_gib,
            **options,
        )
        for i in range(validation_count):
            panel.emit(
                dict(
                    status="evaluating",
                    stage="Evaluate frozen finalists",
                    focus=f"Validation {i + 1}/{validation_count}",
                )
            )
            validation_results.append(pool.evaluate(i, finalists))
        pool.close()
        del pool, evaluation_tapes
        gc.collect()
        if args.device == "cuda":
            torch.cuda.empty_cache()
        report = dict(
            status="completed",
            synthetic=args.synthetic,
            candidate_order=["default", "optimized"],
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
        panel.emit(
            dict(
                status="completed",
                stage="Frozen comparison saved",
                candidate_order=report["candidate_order"],
                message="Training and independent evaluation retained in report.json; worker finished",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
