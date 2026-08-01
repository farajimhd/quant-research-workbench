from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research.mlops.env import discover_env_files, load_env_files

from .openai_gold_benchmark import (
    HARD_MAX_COST_USD,
    MODEL_PROFILES,
    BenchmarkConfig,
    run_benchmark,
)


DEFAULT_COLLECTION = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\news_1000"
)
DEFAULT_RUNTIME = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\openai_gold_100_v5"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark approved OpenAI models against 100 human-reviewed News articles."
    )
    parser.add_argument("--collection-root", type=Path, default=DEFAULT_COLLECTION)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--profiles", default=",".join(MODEL_PROFILES))
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--max-output-tokens", type=int, default=2_048)
    parser.add_argument("--max-dynamic-output-tokens", type=int, default=16_384)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--authorize-cost-usd", default="0")
    args = parser.parse_args(argv)
    try:
        authorization = Decimal(str(args.authorize_cost_usd))
    except InvalidOperation as exc:
        parser.error(f"Invalid --authorize-cost-usd: {args.authorize_cost_usd}")
        raise AssertionError from exc
    profiles = tuple(value.strip() for value in args.profiles.split(",") if value.strip())
    unknown = sorted(set(profiles) - set(MODEL_PROFILES))
    if unknown:
        parser.error("Unsupported profiles: " + ", ".join(unknown))
    if len(profiles) != len(set(profiles)) or not profiles:
        parser.error("--profiles must be unique and non-empty")
    if args.sample_size < 10 or args.sample_size > 1_000:
        parser.error("--sample-size must be between 10 and 1000")
    if args.max_output_tokens < 512:
        parser.error("--max-output-tokens must be at least 512")
    if args.max_dynamic_output_tokens < args.max_output_tokens:
        parser.error("--max-dynamic-output-tokens must be >= --max-output-tokens")
    repo_root = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo_root), verbose=True)
    config = BenchmarkConfig(
        collection_root=args.collection_root,
        runtime_root=args.runtime_root,
        profiles=profiles,
        sample_size=args.sample_size,
        max_output_tokens=args.max_output_tokens,
        max_dynamic_output_tokens=args.max_dynamic_output_tokens,
        poll_seconds=args.poll_seconds,
        hard_max_cost_usd=HARD_MAX_COST_USD,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        project_id=os.environ.get("OPENAI_PROJECT_ID", ""),
    )
    visible = [
        sys.executable,
        "-m",
        "research.text_intelligence.semantic_calibration_v1.run_openai_gold_benchmark",
        "--sample-size",
        str(config.sample_size),
        "--profiles",
        ",".join(config.profiles),
    ]
    if args.execute:
        visible.extend(("--execute", "--authorize-cost-usd", str(authorization)))
    if args.no_wait:
        visible.append("--no-wait")
    print("COMMAND " + " ".join(_quote(value) for value in visible), flush=True)
    return run_benchmark(
        config,
        execute=args.execute,
        authorized_cost_usd=authorization,
        no_wait=args.no_wait,
    )


def _quote(value: str) -> str:
    return f'"{value}"' if any(character.isspace() for character in value) else value


if __name__ == "__main__":
    raise SystemExit(main())
