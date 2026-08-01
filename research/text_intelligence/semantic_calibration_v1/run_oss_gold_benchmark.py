from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .oss_gold_benchmark import OSS_PROFILES, OssBenchmarkConfig, run_profile


DEFAULT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\oss_gold_100_v1"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one local GPT-OSS model on the frozen 100-article gold benchmark."
    )
    parser.add_argument("--profile", choices=sorted(OSS_PROFILES), required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:8000/v1/chat/completions"
    )
    parser.add_argument("--workers", type=int)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--max-model-len", type=int, default=65_536)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--shared-root", type=Path)
    args = parser.parse_args(argv)
    profile = OSS_PROFILES[args.profile]
    workers = args.workers or profile.workers
    if workers < 1 or workers > 16:
        parser.error("--workers must be between 1 and 16")
    if args.max_model_len < 16_384:
        parser.error("--max-model-len must be at least 16384")
    config = OssBenchmarkConfig(
        shared_root=args.shared_root or args.runtime_root / "shared",
        runtime_root=args.runtime_root,
        profile=args.profile,
        endpoint=args.endpoint,
        workers=workers,
        timeout_seconds=args.timeout_seconds,
        attempts=args.attempts,
        max_model_len=args.max_model_len,
    )
    command = [
        sys.executable,
        "-m",
        "research.text_intelligence.semantic_calibration_v1.run_oss_gold_benchmark",
        "--profile",
        args.profile,
        "--workers",
        str(workers),
        "--max-model-len",
        str(args.max_model_len),
    ]
    if args.execute:
        command.append("--execute")
    print("COMMAND " + " ".join(_quote(value) for value in command), flush=True)
    return run_profile(config, execute=args.execute)


def _quote(value: str) -> str:
    return f'"{value}"' if any(character.isspace() for character in value) else value


if __name__ == "__main__":
    raise SystemExit(main())
