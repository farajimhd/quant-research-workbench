from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
PHASES = ("authority", "sidecar", "benchmark", "v16", "v17")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the integrity-gated news reaction rebuild. This command never "
            "profiles or trains a model."
        )
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-at", choices=PHASES, default="authority")
    parser.add_argument("--stop-after", choices=PHASES, default="v17")
    parser.add_argument("--restart-v16", action="store_true")
    parser.add_argument("--restart-v17", action="store_true")
    parser.add_argument("--benchmark-session", default="2026-07-10")
    parser.add_argument("--reaction-workers", type=int, default=4)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="24G")
    return parser.parse_args(argv)


def commands(args: argparse.Namespace) -> dict[str, list[str]]:
    authority = [
        sys.executable,
        "-m",
        "pipelines.news.benzinga.run_news_reaction_extract",
        "--stages",
        "calendar,reactions",
        "--reaction-workers",
        str(args.reaction_workers),
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        str(args.max_memory_usage),
    ]
    sidecar = [
        sys.executable,
        "-m",
        "research.news_reaction_model.certified_targets_v1.run_build",
    ]
    benchmark = [
        sys.executable,
        "-m",
        "research.news_reaction_model.v16.run_benchmark_reader",
        "--session-date",
        args.benchmark_session,
        "--no-write",
    ]
    v16 = [
        sys.executable,
        "-m",
        "research.news_reaction_model.v16.run_prepare_data",
    ]
    v17 = [
        sys.executable,
        "-m",
        "research.news_reaction_model.v17.run_prepare_targets",
    ]
    if args.execute:
        authority.append("--execute")
        sidecar.append("--execute")
        v16.append("--execute")
        v17.append("--execute")
    if args.restart_v16:
        v16.append("--restart")
    if args.restart_v17:
        v17.append("--restart")
    return {
        "authority": authority,
        "sidecar": sidecar,
        "benchmark": benchmark,
        "v16": v16,
        "v17": v17,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    start_index = PHASES.index(args.start_at)
    stop_index = PHASES.index(args.stop_after)
    if start_index > stop_index:
        raise SystemExit("--start-at must not follow --stop-after")
    selected = PHASES[start_index : stop_index + 1]
    phase_commands = commands(args)
    print(
        "CERTIFIED BUILD "
        f"mode={'execute' if args.execute else 'plan'} "
        f"phases={','.join(selected)}",
        flush=True,
    )
    if args.restart_v16:
        print("V16 restart explicitly authorized; existing v2 prepared files will be discarded.", flush=True)
    if args.restart_v17:
        print("V17 restart explicitly authorized; existing v3 target files will be discarded.", flush=True)
    for phase in selected:
        command = phase_commands[phase]
        print(f"\n=== {phase.upper()} ===", flush=True)
        print("COMMAND", subprocess.list2cmdline(command), flush=True)
        completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
        if completed.returncode:
            print(
                f"STOPPED phase={phase} exit_code={completed.returncode}; "
                "no later phase was started.",
                flush=True,
            )
            return int(completed.returncode)
    print(
        "CERTIFIED BUILD COMPLETED. Profiling and training remain a separate, "
        "explicit operator action.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
