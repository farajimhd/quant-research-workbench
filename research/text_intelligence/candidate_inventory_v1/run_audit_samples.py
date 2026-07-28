from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files

from .audit_samples import AUDIT_VERSION, create_audits
from .config import CandidateInventoryConfig
from .pipeline import make_client


def parser() -> argparse.ArgumentParser:
    defaults = CandidateInventoryConfig()
    value = argparse.ArgumentParser(
        description=(
            "Create exactly five News V2 and five SEC V3 candidate-method Markdown "
            "audits under the machine runtime authority."
        )
    )
    value.add_argument(
        "--output-root",
        type=Path,
        default=defaults.runtime_root / "audits" / AUDIT_VERSION,
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    load_env_files(discover_clickhouse_env_files())
    config = CandidateInventoryConfig()
    print(
        "TEXT METHOD AUDIT"
        f" | news=5 | sec=5 | output={args.output_root}",
        flush=True,
    )
    client = make_client(config)
    try:
        files = create_audits(client, config, args.output_root)
    finally:
        client.close()
    print(
        f"COMPLETE | markdown={len(files)} | manifest={args.output_root / 'manifest.json'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
