from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files

from .config import LabelingConfig
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    defaults = LabelingConfig()
    parser = argparse.ArgumentParser(
        description="Create and label a stratified, auditable news sample with local gpt-oss-20b."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--endpoint", default=defaults.endpoint)
    parser.add_argument("--model", default=defaults.model)
    parser.add_argument("--sample-size", type=int, default=defaults.sample_size)
    parser.add_argument("--candidate-size", type=int, default=defaults.candidate_size)
    parser.add_argument("--workers", type=int, default=defaults.workers)
    parser.add_argument("--runtime-root", type=Path, default=defaults.runtime_root)
    parser.add_argument("--input-jsonl", type=Path)
    args = parser.parse_args(argv)
    load_env_files(discover_clickhouse_env_files())
    config = replace(
        defaults,
        endpoint=args.endpoint,
        model=args.model,
        sample_size=args.sample_size,
        candidate_size=max(args.candidate_size, args.sample_size),
        workers=max(1, args.workers),
        runtime_root=args.runtime_root,
    )
    print(
        "COMMAND python -m research.news_labeling.gpt_oss_v1.run_sample "
        f"--sample-size {config.sample_size} --workers {config.workers}"
        + (" --execute" if args.execute else ""),
        flush=True,
    )
    return run(config, execute=args.execute, input_jsonl=args.input_jsonl)


if __name__ == "__main__":
    raise SystemExit(main())
