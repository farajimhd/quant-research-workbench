from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research.mlops.env import discover_env_files, load_env_files

from .config import (
    DEFAULT_PROFILES,
    HARD_MAX_COST_USD,
    MODEL_REGISTRY,
    BatchConfig,
    default_runtime_root,
    default_sample_path,
)
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan, submit, reconcile, persist, and compare the approved OpenAI "
            "news-label Batch experiment."
        )
    )
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    parser.add_argument("--sample-jsonl", type=Path, default=default_sample_path())
    parser.add_argument(
        "--profiles",
        default=",".join(DEFAULT_PROFILES),
        help="Comma-separated approved profiles. Defaults to the exact seven-model matrix.",
    )
    parser.add_argument("--max-output-tokens", type=int, default=1_536)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--disagreement-limit", type=int, default=48)
    parser.add_argument(
        "--answer-key-jsonl",
        type=Path,
        help="Optional reviewed labels used to calculate semantic accuracy.",
    )
    parser.add_argument(
        "--authorize-cost-usd",
        default="0",
        help=(
            "Explicit dollar authorization. Execution refuses to start unless this "
            "covers the protected plan and is no greater than the hard $20 ceiling."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Allow file upload and Batch submission. Omit for a no-cost planning pass.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Submit/reconcile and exit; rerun later without this flag to collect results.",
    )
    args = parser.parse_args(argv)

    try:
        authorized = Decimal(str(args.authorize_cost_usd))
    except InvalidOperation as exc:
        parser.error(f"invalid --authorize-cost-usd: {args.authorize_cost_usd!r}")
        raise AssertionError from exc
    profiles = tuple(value.strip() for value in args.profiles.split(",") if value.strip())
    unknown = sorted(set(profiles) - set(MODEL_REGISTRY))
    if unknown:
        parser.error(
            "unsupported or unpriced profiles: "
            + ", ".join(unknown)
            + "; allowed: "
            + ", ".join(MODEL_REGISTRY)
        )
    if not profiles:
        parser.error("--profiles must contain at least one approved model")
    if len(profiles) != len(set(profiles)):
        parser.error("--profiles contains duplicate model aliases")
    if args.max_output_tokens < 256:
        parser.error("--max-output-tokens must be at least 256")
    if args.poll_seconds < 5:
        parser.error("--poll-seconds must be at least 5")

    repo_root = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo_root), verbose=True)
    config = BatchConfig(
        runtime_root=args.runtime_root,
        sample_path=args.sample_jsonl,
        profiles=profiles,
        max_output_tokens=args.max_output_tokens,
        poll_seconds=args.poll_seconds,
        hard_max_cost_usd=HARD_MAX_COST_USD,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        project_id=os.environ.get("OPENAI_PROJECT_ID", ""),
        disagreement_limit=args.disagreement_limit,
        answer_key_path=args.answer_key_jsonl,
    )
    visible = [
        sys.executable,
        "-m",
        "research.news_labeling.openai_batch_v1.run_experiment",
        "--runtime-root",
        str(config.runtime_root),
        "--sample-jsonl",
        str(config.sample_path),
        "--profiles",
        ",".join(config.profiles),
        "--max-output-tokens",
        str(config.max_output_tokens),
        "--poll-seconds",
        str(config.poll_seconds),
    ]
    if args.execute:
        visible.extend(("--execute", "--authorize-cost-usd", str(authorized)))
    if args.answer_key_jsonl:
        visible.extend(("--answer-key-jsonl", str(args.answer_key_jsonl)))
    if args.no_wait:
        visible.append("--no-wait")
    print("COMMAND " + " ".join(_quote(value) for value in visible), flush=True)
    return run(
        config,
        execute=args.execute,
        authorized_cost_usd=authorized,
        no_wait=args.no_wait,
    )


def _quote(value: str) -> str:
    return f'"{value}"' if any(character.isspace() for character in value) else value


if __name__ == "__main__":
    raise SystemExit(main())
