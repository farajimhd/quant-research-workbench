from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files

from .config import MODEL_PROFILES, LabelingConfig
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    defaults = LabelingConfig()
    parser = argparse.ArgumentParser(
        description="Create and label a frozen, auditable news sample with a local gpt-oss model."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--profile", choices=sorted(MODEL_PROFILES), default=defaults.profile)
    parser.add_argument("--endpoint", default=defaults.endpoint)
    parser.add_argument("--model")
    parser.add_argument("--tokenizer-source")
    parser.add_argument("--sample-size", type=int, default=defaults.sample_size)
    parser.add_argument("--candidate-size", type=int, default=defaults.candidate_size)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--max-model-len", type=int, default=defaults.max_model_len)
    parser.add_argument("--max-output-tokens", type=int, default=defaults.max_output_tokens)
    parser.add_argument("--timeout-seconds", type=int, default=defaults.timeout_seconds)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--input-jsonl", type=Path)
    args = parser.parse_args(argv)
    load_env_files(discover_clickhouse_env_files())
    profile = MODEL_PROFILES[args.profile]
    runtime_root = args.runtime_root or defaults.runtime_root / "models" / profile.name
    config = replace(
        defaults,
        profile=profile.name,
        endpoint=args.endpoint,
        model=args.model or profile.model,
        tokenizer_source=args.tokenizer_source or profile.tokenizer,
        sample_size=args.sample_size,
        candidate_size=max(args.candidate_size, args.sample_size),
        workers=max(1, args.workers or profile.workers),
        max_model_len=args.max_model_len,
        max_output_tokens=args.max_output_tokens,
        timeout_seconds=args.timeout_seconds,
        runtime_root=runtime_root,
    )
    print(
        "COMMAND python -m research.news_labeling.gpt_oss_v1.run_sample "
        f"--profile {config.profile} --sample-size {config.sample_size} "
        f"--workers {config.workers} --runtime-root {config.runtime_root}"
        + (" --execute" if args.execute else ""),
        flush=True,
    )
    return run(config, execute=args.execute, input_jsonl=args.input_jsonl)


if __name__ == "__main__":
    raise SystemExit(main())
