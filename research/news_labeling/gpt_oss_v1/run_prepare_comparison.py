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
        description="Freeze one stratified sample for a controlled GPT-OSS 20B/120B comparison."
    )
    parser.add_argument("--sample-size", type=int, default=defaults.sample_size)
    parser.add_argument("--candidate-size", type=int, default=defaults.candidate_size)
    parser.add_argument("--tokenizer-source", default=MODEL_PROFILES["20b"].tokenizer)
    parser.add_argument("--runtime-root", type=Path, default=defaults.runtime_root / "shared")
    args = parser.parse_args(argv)
    load_env_files(discover_clickhouse_env_files())
    config = replace(
        defaults,
        profile="shared",
        model=MODEL_PROFILES["20b"].model,
        tokenizer_source=args.tokenizer_source,
        sample_size=args.sample_size,
        candidate_size=max(args.candidate_size, args.sample_size),
        runtime_root=args.runtime_root,
    )
    print(
        "COMMAND python -m research.news_labeling.gpt_oss_v1.run_prepare_comparison "
        f"--sample-size {config.sample_size} --candidate-size {config.candidate_size} "
        f"--runtime-root {config.runtime_root}",
        flush=True,
    )
    return run(config, execute=False)


if __name__ == "__main__":
    raise SystemExit(main())
