from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.paths import MLOpsPathConfig

from .sol_teacher_batch import (
    DEFAULT_MAX_BATCH_ATTEMPTS,
    DEFAULT_MAX_ENQUEUED_INPUT_TOKENS,
    HARD_MAX_COST_USD,
    TeacherBatchConfig,
    run_teacher_batch,
)


def default_corpus_root() -> Path:
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "sol_teacher_10000_v1"
    )


def default_runtime_root() -> Path:
    return default_corpus_root() / "sol_batch"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan, submit, resume, and collect bounded GPT-5.6 Sol Batch labels "
            "for the immutable 10,000-article teacher corpus."
        )
    )
    parser.add_argument("--corpus-root", type=Path, default=default_corpus_root())
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    parser.add_argument("--chunk-rows", type=int, default=250)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument(
        "--max-enqueued-input-tokens",
        type=int,
        default=DEFAULT_MAX_ENQUEUED_INPUT_TOKENS,
        help=(
            "Local admission ceiling for simultaneously enqueued Sol input "
            "tokens; the default stays below the current 1.35M organization limit."
        ),
    )
    parser.add_argument(
        "--max-batch-attempts", type=int, default=DEFAULT_MAX_BATCH_ATTEMPTS
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--authorize-cost-usd", default="0")
    args = parser.parse_args(argv)
    try:
        authorization = Decimal(str(args.authorize_cost_usd))
    except InvalidOperation:
        parser.error(f"invalid --authorize-cost-usd: {args.authorize_cost_usd}")
    if authorization < 0:
        parser.error("--authorize-cost-usd cannot be negative")
    if authorization > HARD_MAX_COST_USD:
        parser.error(
            f"--authorize-cost-usd cannot exceed the ${HARD_MAX_COST_USD:.2f} hard limit"
        )
    if args.max_enqueued_input_tokens < 1:
        parser.error("--max-enqueued-input-tokens must be positive")
    if args.max_batch_attempts < 1:
        parser.error("--max-batch-attempts must be positive")
    repo = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo), verbose=True)
    config = TeacherBatchConfig(
        corpus_root=args.corpus_root,
        runtime_root=args.runtime_root,
        chunk_rows=args.chunk_rows,
        poll_seconds=args.poll_seconds,
        max_enqueued_input_tokens=args.max_enqueued_input_tokens,
        max_batch_attempts=args.max_batch_attempts,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        project_id=os.environ.get("OPENAI_PROJECT_ID", ""),
    )
    visible = [
        sys.executable,
        "-m",
        "research.text_intelligence.semantic_calibration_v1.run_sol_teacher_labels",
        "--chunk-rows",
        str(config.chunk_rows),
        "--max-enqueued-input-tokens",
        str(config.max_enqueued_input_tokens),
    ]
    if args.execute:
        visible.extend(("--execute", "--authorize-cost-usd", str(authorization)))
    if args.no_wait:
        visible.append("--no-wait")
    print("COMMAND " + " ".join(_quote(value) for value in visible), flush=True)
    return run_teacher_batch(
        config,
        execute=args.execute,
        authorized_cost_usd=authorization,
        no_wait=args.no_wait,
    )


def _quote(value: str) -> str:
    return f'"{value}"' if any(character.isspace() for character in value) else value


if __name__ == "__main__":
    raise SystemExit(main())
