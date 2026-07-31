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

from .sampling import build_sample


def default_root() -> Path:
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_1000"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the blinded News semantic sample.")
    parser.add_argument("--output-root", type=Path, default=default_root())
    parser.add_argument("--sample-size", type=int, default=1_000)
    parser.add_argument("--pilot-size", type=int, default=100)
    parser.add_argument("--rare-supplement", type=int, default=150)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo))
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    result = build_sample(
        client,
        args.output_root,
        sample_size=args.sample_size,
        pilot_size=args.pilot_size,
        rare_supplement=args.rare_supplement,
        report=lambda message: print(message, flush=True),
    )
    print(
        f"READY | root={result.root} sample={result.sample_count:,} "
        f"pilot={result.pilot_count:,} manifest={result.manifest_hash}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
