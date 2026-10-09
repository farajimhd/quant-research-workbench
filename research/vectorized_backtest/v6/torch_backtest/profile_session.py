"""Real-session replay qualification and before/after timing, never GA fitness.

Use a training date. Compare identical fixed candidate tensors and certified
inputs; validate full ledgers, then measure prepared replay separately from
source preparation, graph compilation and atomic-policy preparation.
"""

import gc
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
import torch

from .encoding.config import Session
from .genome import StrategySpace
from .grid import Settings
from .prepare import prepare_tape
from .runtime import code_hash, write_json
from .search_runner import SearchRunner


def profile(item, args, output, panel):
    space = StrategySpace(replace(Settings(), long_hold_seconds=args.long_hold_seconds))
    session = Session(
        Path(item["manifest"]),
        Path(item["ledger"]),
        Path(args.runtime) / "source_cache",
        datetime.fromisoformat(item["start"]),
        datetime.fromisoformat(item["end"]),
        max_prepared_gib=args.maximum_tape_gib,
    )
    started = perf_counter()
    progress_stage = "Prepare real tape"

    def progress(event):
        panel.emit(
            dict(
                status="profiling",
                stage=event.get("stage", progress_stage),
                focus=item["day"],
                progress=event,
                message=event.get("message", ""),
            )
        )

    host = prepare_tape(
        session,
        space.settings,
        maximum_gib=args.maximum_tape_gib,
        structural_workers=args.structural_workers,
        progress=progress,
    )
    preparation_seconds = perf_counter() - started
    torch.save(dict(tape=host, code_hash=code_hash()), output / "profile-tape.pt")
    tape = host.to("cuda", args.maximum_tape_gib)
    rows = space.sample(np.random.default_rng(args.seed), args.population)
    # Diagnostic baseline only; the optimizer's separate population stays random.
    rows[0] = space.default
    options = dict(
        backend="compiled_graph",
        maximum_state_gib=args.maximum_state_gib,
        maximum_fills=args.maximum_fills,
        graph_steps=args.graph_steps,
    )
    timings, reference, reference_ledger = [], None, None
    # Routine qualification needs the unchanged reference and corrected path.
    # Keep slower experimental alternatives available for explicit diagnosis;
    # do not repeatedly spend minutes requalifying already-rejected modes.
    modes = [(False, "atomic"), (False, "inplace")]
    if args.profile_all_modes:
        modes[1:1] = [(False, "unique"), (True, "unique")]
    for precompute, ledger_mode in modes:
        panel.emit(
            dict(
                status="profiling",
                stage="Compile "
                + ("precomputed" if precompute else "inline")
                + " policy replay",
                message="Setup timing is separate from prepared replay",
            )
        )
        runner = SearchRunner(
            tape,
            space,
            rows,
            precompute_rules=precompute,
            ledger_mode=ledger_mode,
            **options,
        ).compile()
        rule_times = []
        if precompute:
            progress_stage = "Prepare atomic policy gates"
            rule_times.append(runner.rule_compiler.prepare(progress))
        progress_stage = "Measure prepared replay"
        first = runner.run(progress=progress)
        ledger = runner.ledger.detach().cpu().clone()
        metrics = {
            k: first[k].cpu()
            for k in (
                "cash",
                "net_pnl",
                "fees",
                "drawdown",
                "filled_batches",
                "positions_opened",
                "fill_count",
                "open_positions",
                "long_hold_dollar_seconds",
                "sold_share_seconds",
            )
        }
        if reference is None:
            reference, reference_ledger = metrics, ledger
        else:
            for name in metrics:
                if not torch.allclose(
                    metrics[name], reference[name], rtol=0, atol=1e-7
                ):
                    raise RuntimeError(
                        "Full-session rule-precompute parity failed: " + name
                    )
            if not torch.allclose(ledger, reference_ledger, rtol=0, atol=1e-7):
                raise RuntimeError("Full-session rule-precompute ledger parity failed")
        replay_times = [first["replay_seconds"]]
        for repeat in range(2):
            if precompute:
                progress_stage = "Prepare atomic policy gates"
                rule_times.append(runner.rule_compiler.prepare(progress))
            progress_stage = "Measure prepared replay"
            panel.emit(
                dict(
                    status="profiling",
                    stage="Measure prepared replay",
                    focus=f"{item['day']} · repetition {repeat + 2}/3",
                )
            )
            replay_times.append(runner.run(progress=progress)["replay_seconds"])
        measurement = dict(
            precompute_rules=precompute,
            ledger_mode=ledger_mode,
            compile_seconds=runner.setup_seconds,
            replay_seconds=replay_times,
            median_replay_seconds=median(replay_times),
            rule_prepare_seconds=rule_times,
            warm_rule_prepare_seconds=median(rule_times[1:]) if precompute else 0,
            steady_objective_seconds=median(replay_times)
            + (median(rule_times[1:]) if precompute else 0),
        )
        timings.append(measurement)
        write_json(output / "profile_measurements.json", timings)
        # Trace one captured block, bounded by graph_steps (32 by default).
        if ledger_mode == "inplace":
            runner.reset()
            with torch.profiler.profile(
                activities=[
                    torch.profiler.ProfilerActivity.CPU,
                    torch.profiler.ProfilerActivity.CUDA,
                ],
                record_shapes=False,
            ) as trace:
                runner.graph.replay()
                torch.cuda.synchronize()
            trace.export_chrome_trace(str(output / "gpu_trace.json"))
            (output / "gpu_profile.txt").write_text(
                trace.key_averages().table(
                    sort_by="self_cuda_time_total", row_limit=25
                ),
                encoding="utf-8",
            )
        del runner, ledger, first
        gc.collect()
        torch.cuda.empty_cache()
    best = min(timings, key=lambda v: v["steady_objective_seconds"])
    return dict(
        passed=True,
        code_hash=code_hash(),
        session=item,
        source=tape.provenance,
        slots=len(tape.clocks),
        tickers=len(tape.tickers),
        population=args.population,
        graph_steps=args.graph_steps,
        precompute_rules=best["precompute_rules"],
        ledger_mode=best["ledger_mode"],
        preparation_seconds=preparation_seconds,
        measurements=timings,
        optimized_replay_seconds=best["median_replay_seconds"],
        optimized_objective_seconds=best["steady_objective_seconds"],
        baseline_replay_seconds=timings[0]["median_replay_seconds"],
        full_ledger_parity=True,
        baseline_pnl=float(reference["net_pnl"][0]),
        baseline_open_positions=int(reference["open_positions"][0]),
        previous_v2_september3_64_lane_seconds=69.62,
        comparison_note="Past v2 grid has different policies; same-date inline v3 is the controlled performance baseline; fastest measured steady objective selected",
    )
