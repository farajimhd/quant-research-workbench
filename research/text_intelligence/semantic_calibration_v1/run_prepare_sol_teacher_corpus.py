from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.paths import MLOpsPathConfig

from .sol_teacher_corpus import DEFAULT_SAMPLE_SIZE, build_teacher_corpus
from .storage import read_json


def default_root() -> Path:
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "sol_teacher_10000_v1"
    )


def default_ground_truth_root() -> Path:
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_1000"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a balanced immutable 2010-2026 Sol teacher corpus that "
            "excludes the complete 1,000-item human ground truth."
        )
    )
    parser.add_argument("--output-root", type=Path, default=default_root())
    parser.add_argument(
        "--ground-truth-root", type=Path, default=default_ground_truth_root()
    )
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo), verbose=True)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    result = build_teacher_corpus(
        client,
        args.output_root,
        ground_truth_root=args.ground_truth_root,
        sample_size=args.sample_size,
        report=lambda message: print(message, flush=True),
    )
    print(
        f"READY | root={result.root} sample={result.sample_count:,} "
        f"ground_truth_excluded={result.exclusion_count:,} overlap=0 "
        f"manifest={result.manifest_hash}",
        flush=True,
    )
    distribution = read_json(result.root / "sample_manifest.json")["distribution"]
    print(
        "DISTRIBUTION | "
        f"years={distribution['calendar_year']} "
        f"ticker_scope={distribution['ticker_scope']} "
        f"label_presence={distribution['v5_label_presence']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
