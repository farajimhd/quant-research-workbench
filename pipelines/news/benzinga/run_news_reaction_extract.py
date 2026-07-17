from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pipelines").exists())
SCRIPT = REPO_ROOT / "pipelines" / "news" / "benzinga" / "news_reaction_extract.py"

DEFAULTS = {
    "start_date": "2019-01-01",
    "end_date": "2027-01-01",
    "stats_start_date": "2019-01-01",
    "stats_end_date": "2026-01-01",
    "stages": "calendar,dictionary,features,reactions,stats",
    "max_threads": 24,
    "max_memory_usage": "0",
    "progress_layout": "auto",
    "progress_refresh_per_second": 2.0,
    "progress_log_lines": 8,
    "output_root": r"D:\market-data\prepared\news_reaction_labels",
}


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Safe launcher for the deterministic news phrase and causal reaction table build."
    )
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"], help="Exclusive; includes the 2026 holdout by default.")
    parser.add_argument("--stats-start-date", default=DEFAULTS["stats_start_date"])
    parser.add_argument("--stats-end-date", default=DEFAULTS["stats_end_date"], help="Exclusive; keeps 2026 out of phrase probabilities.")
    parser.add_argument("--stages", default=DEFAULTS["stages"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--progress-refresh-per-second", type=float, default=DEFAULTS["progress_refresh_per_second"])
    parser.add_argument("--progress-log-lines", type=int, default=DEFAULTS["progress_log_lines"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--allow-partial-bar-coverage", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, passthrough = parse_args(argv)
    command = [
        sys.executable,
        str(SCRIPT),
        "--start-date", args.start_date,
        "--end-date", args.end_date,
        "--stats-start-date", args.stats_start_date,
        "--stats-end-date", args.stats_end_date,
        "--stages", args.stages,
        "--max-threads", str(args.max_threads),
        "--max-memory-usage", str(args.max_memory_usage),
        "--progress-layout", args.progress_layout,
        "--progress-refresh-per-second", str(args.progress_refresh_per_second),
        "--progress-log-lines", str(args.progress_log_lines),
        "--output-root", args.output_root,
    ]
    if args.execute:
        command.append("--execute")
    if args.replace_existing:
        command.append("--replace-existing")
    if args.allow_partial_bar_coverage:
        command.append("--allow-partial-bar-coverage")
    command.extend(passthrough)
    print("COMMAND", subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
