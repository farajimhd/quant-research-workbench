from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.mlops.paths import MLOpsPathConfig
from research.text_intelligence.candidate_inventory_v1.config import CandidateInventoryConfig
from research.text_intelligence.candidate_inventory_v1.pipeline import make_client

from .audit import create_audits


def default_output() -> Path:
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_label_authority_v1"
        / "audits"
        / "text_semantic_label_audit_v1"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate five News and five SEC semantic audits.")
    parser.add_argument("--output-root", type=Path, default=default_output())
    args = parser.parse_args()
    load_env_files(discover_clickhouse_env_files())
    config = CandidateInventoryConfig()
    client = make_client(config)
    try:
        files = create_audits(client, config, args.output_root)
    finally:
        client.close()
    print(f"COMPLETED | audits={len(files)} | root={args.output_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
