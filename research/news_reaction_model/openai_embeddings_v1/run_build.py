from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.openai_embeddings_v1.config import HARD_MAX_COST_USD, PipelineConfig
from research.news_reaction_model.openai_embeddings_v1.pipeline import audit_pipeline, run_pipeline


REPO_ROOT = Path(__file__).resolve().parents[3]


def positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal dollar amount") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    if parsed > HARD_MAX_COST_USD:
        raise argparse.ArgumentTypeError(f"cannot exceed the compiled ${HARD_MAX_COST_USD:.2f} safety ceiling")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build durable OpenAI embeddings for the exact V7 news-reaction article population."
    )
    parser.add_argument("--execute", action="store_true", help="Plan exactly, then submit bounded OpenAI Batch jobs.")
    parser.add_argument("--audit", action="store_true", help="Print durable database coverage and integrity counters.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed items with fewer than three attempts.")
    parser.add_argument("--no-wait", action="store_true", help="Submit at most one batch and return; rerun to reconcile.")
    parser.add_argument("--max-cost-usd", type=positive_decimal, default=HARD_MAX_COST_USD)
    parser.add_argument("--planner-workers", type=int, default=PipelineConfig().planner_workers)
    parser.add_argument("--poll-seconds", type=int, default=PipelineConfig().poll_seconds)
    parser.add_argument("--env-file", default="", help="Optional explicit .env path; secret values are never printed.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.planner_workers < 1 or args.poll_seconds < 1:
        raise SystemExit("--planner-workers and --poll-seconds must be positive")
    load_env_files(discover_env_files(REPO_ROOT, args.env_file or None))
    config = replace(
        PipelineConfig(),
        hard_max_cost_usd=args.max_cost_usd,
        planner_workers=args.planner_workers,
        poll_seconds=args.poll_seconds,
    )
    effective = [
        "--max-cost-usd", str(config.hard_max_cost_usd),
        "--planner-workers", str(config.planner_workers),
        "--poll-seconds", str(config.poll_seconds),
    ]
    for flag, enabled in (
        ("--execute", args.execute),
        ("--audit", args.audit),
        ("--retry-failed", args.retry_failed),
        ("--no-wait", args.no_wait),
    ):
        if enabled:
            effective.append(flag)
    print(
        "COMMAND python -m research.news_reaction_model.openai_embeddings_v1.run_build "
        + " ".join(shlex.quote(value) for value in effective),
        flush=True,
    )
    if args.audit:
        result = audit_pipeline(config)
        return 0 if not result["duplicates"] and not result["invalid_dimensions"] else 5
    try:
        return run_pipeline(config, execute=args.execute, retry_failed=args.retry_failed, no_wait=args.no_wait)
    except KeyboardInterrupt:
        print(
            "INTERRUPTED | Local work stopped. Submitted Batch jobs remain tracked remotely and in ClickHouse; "
            "rerun the same command to reconcile without duplicating completed items.",
            flush=True,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
