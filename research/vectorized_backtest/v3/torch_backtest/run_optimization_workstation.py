"""Standalone v3 workstation search: dates, plan, profile, run or monitor.

All available certified premarket dates are discovered, with the last six
reserved for frozen evaluation. The random training search has ONE phase.
No v2 package, runtime or launcher is imported or required.
"""

import os
import sys
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[4]
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO))

import argparse
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import torch

from research.vectorized_backtest.v3.torch_backtest.availability import (
    configure_reader,
    discover_sources,
    select_dates,
    session_bounds,
)
from research.vectorized_backtest.v3.torch_backtest.optimization_ui import (
    SearchPanel,
    monitor,
)
from research.vectorized_backtest.v3.torch_backtest.runtime import (
    DEFAULT,
    code_hash,
    configure_caches,
    require_runtime,
    source_revision,
    write_json,
)


def session_spec(source):
    start, end = session_bounds(source["day"], "premarket")
    zone = ZoneInfo("America/New_York")
    return dict(
        source,
        start=datetime.fromisoformat(source["day"] + "T" + start)
        .replace(tzinfo=zone)
        .isoformat(),
        end=datetime.fromisoformat(source["day"] + "T" + end)
        .replace(tzinfo=zone)
        .isoformat(),
    )


def qualified_followup_arguments(args, job):
    """Forward sealed run settings after qualification; exclude profile flags."""
    command = ['run', '--resume', str(args.run_after_profile),
               '--qualification', str(job / 'qualification.json')]
    # Forward the sealed solver/resources, not profile-only flags.
    omit = {'command', 'resume', 'qualification', 'run_after_profile',
            'profile_date', 'profile_pipeline', 'profile_all_modes',
            'short_study_origin'}
    aliases = {'start_date': 'from', 'end_date': 'to'}
    for name, value in vars(args).items():
        if name in omit or value is None or value is False:
            continue
        flag = '--' + aliases.get(name, name.replace('_', '-'))
        command += [flag] if value is True else [flag, str(value)]
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("dates", "plan", "profile", "run", "monitor", "study"),
        default="plan",
    )
    parser.add_argument("--from", dest="start_date")
    parser.add_argument("--to", dest="end_date")
    parser.add_argument("--validation-sessions", type=int, default=6)
    parser.add_argument("--profile-date", default="2026-09-03")
    parser.add_argument("--profile-pipeline", action="store_true",
                        help="Qualify full cached-input pipeline on two training sessions")
    parser.add_argument("--run-after-profile",
                        help="New job to launch only after this same-source qualification passes")
    parser.add_argument(
        "--profile-all-modes",
        action="store_true",
        help="Also remeasure diagnostic unique-only and rule-precompute paths",
    )
    parser.add_argument("--population", type=int, default=64)
    parser.add_argument("--generations", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--preparation-workers", type=int, default=2)
    parser.add_argument("--preparation-lookahead", type=int, default=2)
    parser.add_argument("--reuse-prepared", help="Previous experiment with compatible sealed inputs")
    parser.add_argument("--short-study-origin",
                        help="Stopped full population study; reuse measured timing and its frozen B64 checkpoint")
    parser.add_argument("--continue-training", help="Stopped training experiment to continue with new mutation identity")
    parser.add_argument('--warm-start-study', help='Completed training-only study used to initialize full search')
    parser.add_argument("--runtime", type=Path, default=DEFAULT)
    parser.add_argument("--resume", type=Path)
    parser.add_argument(
        "--qualification",
        type=Path,
        help="Successful same-code real-session profile receipt",
    )
    parser.add_argument("--resident-gib", type=float, default=48)
    parser.add_argument("--maximum-host-gib", type=float, default=320)
    parser.add_argument("--maximum-tape-gib", type=float, default=12)
    parser.add_argument("--maximum-state-gib", type=float, default=8)
    parser.add_argument("--maximum-fills", type=int, default=65536)
    parser.add_argument("--graph-steps", type=int, default=32)
    parser.add_argument("--structural-workers", type=int, default=0)
    parser.add_argument("--minimum-training-entries", type=int, default=1)
    parser.add_argument("--maximum-training-batches", type=int, default=20)
    parser.add_argument("--long-hold-seconds", type=int, default=300)
    parser.add_argument("--long-hold-weight", type=float, default=0.0)
    parser.add_argument("--excess-activity-weight", type=float, default=0.0)
    parser.add_argument("--stop-risk-weight", type=float, default=0.10)
    parser.add_argument("--capital-time-weight", type=float, default=0.002)
    parser.add_argument("--maximum-stop-risk-fraction", type=float, default=0.02)
    parser.add_argument("--maximum-position-hold-seconds", type=int, default=3600)
    parser.add_argument("--plain", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "monitor":
        if not args.resume:
            parser.error("Monitor requires --resume RUN_DIRECTORY")
        monitor(args.resume)
        return 0
    runtime = require_runtime(args.runtime)
    job = require_runtime(args.resume or runtime / "optimization_jobs" / uuid4().hex)
    configure_reader(REPO)
    torch.set_num_threads(1)
    with SearchPanel(job, plain=args.plain) as panel:
        panel.emit(
            dict(stage="Discover certified session catalogue", status="discovering")
        )
        catalog = discover_sources()
        write_json(job / "available-dates.json", catalog)
        selected, closed = select_dates(
            catalog["sources"], start=args.start_date, end=args.end_date
        )
        if args.command == "dates":
            for value in selected:
                panel.console.print(
                    value["day"] + f"  {value['tickers']:,} certified tickers",
                    markup=False,
                )
            panel.emit(
                dict(
                    status="completed",
                    stage="Catalogue saved",
                    message=f"{len(selected)} available trading dates; full tape preflight still required",
                )
            )
            return 0
        if not 1 <= args.validation_sessions < len(selected):
            raise ValueError(
                "Reserve at least one later validation session and one training session"
            )
        spec = dict(
            training=[session_spec(v) for v in selected[: -args.validation_sessions]],
            validation=[session_spec(v) for v in selected[-args.validation_sessions :]],
        )
        request = dict(
            commit=source_revision(REPO),
            code_hash=code_hash(),
            sessions=spec,
            config={
                k: v
                for k, v in vars(args).items()
                if k not in ("command", "runtime", "resume", "qualification", "plain")
            },
            closed_calendar_dates=closed,
            initialization="random_no_default_injection",
            phase_count=1,
            account_contract="reset_at_each_session_start; cash evolves within_session; no_cross_session_carry",
        )
        old = job / "job.json"
        if (
            old.exists()
            and json.loads(old.read_text(encoding="utf-8"))["request"] != request
        ):
            raise ValueError("Resume differs from frozen workstation request")
        write_json(old, dict(request=request, status="planned"))
        spec_path = job / "sessions.json"
        write_json(spec_path, spec)
        panel.emit(
            dict(
                status="planned",
                stage="Pinned session split",
                focus=f"{selected[0]['day']}–{selected[-1]['day']} · premarket 04:00–09:30 New York",
                message=f"{len(spec['training'])} training + {len(spec['validation'])} validation; no trading data writes",
            )
        )
        if args.command == "plan":
            panel.console.print(str(job), markup=False)
            return 0
        if (
            not torch.cuda.is_available()
            or torch.cuda.get_device_properties(0).total_memory < 80 * 1024**3
        ):
            raise RuntimeError(
                "Requires workstation 96GB CUDA GPU; no laptop or CPU fallback"
            )
        configure_caches(runtime)
        if args.command == 'study':
            from research.vectorized_backtest.v3.torch_backtest.population_study import run
            return run(spec, args, job, panel)
        if args.command == "profile":
            from research.vectorized_backtest.v3.torch_backtest.profile_session import (
                profile,
            )

            source = next((v for v in selected if v["day"] == args.profile_date), None)
            if source is None or source in selected[-args.validation_sessions :]:
                raise ValueError(
                    "Profile must use an available TRAINING date, never evaluation"
                )
            if args.profile_pipeline:
                from research.vectorized_backtest.v3.torch_backtest.profile_pipeline import profile as pipeline_profile
                result = pipeline_profile(spec['training'], args, job, panel)
            else:
                result = profile(session_spec(source), args, job, panel)
            write_json(job / "qualification.json", result)
            panel.emit(
                dict(
                    status="completed",
                    stage="Real-session profile verified",
                    message=f"Prepared replay {result['optimized_replay_seconds']:.2f}s; qualification.json saved",
                )
            )
            if args.run_after_profile:
                if not result.get('passed') or not result.get('full_ledger_parity'):
                    raise ValueError('Automatic continuation requires passed full-ledger qualification')
                command = qualified_followup_arguments(args, job)
                # Recursive main runs before this with-context exits. Release
                # its refresh thread now so it cannot redraw stale profile data.
                panel.close_live()
                return main(command)
            return 0
    # The optimization owns the panel below. Profile and plan never launch GA.
    if not args.qualification:
        raise ValueError(
            "Full search requires --qualification from same-code real-session profiling"
        )
    qualification = json.loads(args.qualification.read_text(encoding="utf-8"))
    if qualification.get("code_hash") != code_hash() or not qualification.get("passed"):
        raise ValueError(
            "Profile qualification is missing, failed or stale after source changes"
        )
    if (
        qualification["population"] != args.population
        or qualification["graph_steps"] != args.graph_steps
    ):
        raise ValueError("Run population/graph shape must match measured qualification")
    from research.vectorized_backtest.v3.torch_backtest.optimize import main as optimize

    command = [
        "--execute",
        "--sessions",
        str(spec_path),
        "--runtime",
        str(runtime),
        "--population",
        str(args.population),
        "--generations",
        str(args.generations),
        "--seed",
        str(args.seed),
    ]
    for name in (
        "resident_gib",
        "maximum_host_gib",
        "maximum_tape_gib",
        "maximum_state_gib",
        "maximum_fills",
        "graph_steps",
        "structural_workers",
        "preparation_workers",
        "preparation_lookahead",
        "minimum_training_entries",
        "maximum_training_batches",
        "long_hold_seconds",
        "long_hold_weight",
        "excess_activity_weight",
        "stop_risk_weight",
        "capital_time_weight",
        "maximum_stop_risk_fraction",
        "maximum_position_hold_seconds",
    ):
        command += ["--" + name.replace("_", "-"), str(getattr(args, name))]
    if qualification["precompute_rules"]:
        command.append("--precompute-rules")
    command += ["--ledger-mode", qualification["ledger_mode"]]
    if args.plain:
        command.append("--plain")
    if args.reuse_prepared:
        command += ["--reuse-prepared", args.reuse_prepared]
    if args.continue_training:
        command += ["--continue-training", args.continue_training]
    if args.warm_start_study:
        command += ['--warm-start-study', args.warm_start_study]
    # One stable directory owns launcher plan and optimization status/checkpoint.
    experiment = job / "experiment"
    if (experiment / "identity.json").exists():
        command += ["--resume", str(experiment)]
    else:
        command += ["--output", str(experiment)]
    write_json(job / "command.json", command)
    return optimize(command)


if __name__ == "__main__":
    raise SystemExit(main())
