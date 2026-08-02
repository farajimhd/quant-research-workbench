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

from .fresh_acceptance import build_acceptance_sample


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the prediction-blind 100-article News acceptance extension "
            "outside the existing human and Sol supervision corpora."
        )
    )
    parser.add_argument("--output-root", type=Path, default=base / "news_acceptance_100_v1")
    parser.add_argument("--human-root", type=Path, default=base / "news_1000")
    parser.add_argument("--teacher-root", type=Path, default=base / "sol_teacher_10000_v1")
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo), verbose=True)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    result = build_acceptance_sample(
        client,
        args.output_root,
        human_root=args.human_root,
        teacher_root=args.teacher_root,
        report=lambda message: print(message, flush=True),
    )
    print(
        f"READY | root={result.root} fresh={result.sample_count:,} "
        f"prior_excluded={result.excluded_count:,} overlap=0 "
        f"manifest={result.manifest_hash}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
