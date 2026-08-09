from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files

from .forecast_eligibility_sampling import build_sampling_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a blind forecast-eligibility sampling run.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end-exclusive", required=True)
    parser.add_argument("--sample-size", type=int, default=5000)
    parser.add_argument("--seed", default="forecast-eligibility-2020-2026-v1")
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--exclude-ledger", type=Path, action="append", default=[])
    parser.add_argument("--exclude-predictions", type=Path, action="append", default=[])
    args = parser.parse_args()

    load_env_files(discover_clickhouse_env_files())
    excluded: set[str] = set()
    for path in args.exclude_ledger:
        excluded.update(_ids_from_jsonl(path))
    for path in args.exclude_predictions:
        if path.is_dir():
            for item in path.glob("*.json"):
                value = json.loads(item.read_text(encoding="utf-8"))
                if value.get("source_id"):
                    excluded.add(str(value["source_id"]))
        else:
            excluded.update(_ids_from_jsonl(path))

    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=300,
    )
    try:
        manifest = build_sampling_run(
            args.output_root.resolve(),
            client=client,
            database=args.database,
            start=args.start,
            end_exclusive=args.end_exclusive,
            sample_size=args.sample_size,
            seed=args.seed,
            excluded_source_ids=excluded,
        )
    finally:
        client.close()
    print(json.dumps(manifest, indent=2))
    return 0


def _ids_from_jsonl(path: Path) -> set[str]:
    values: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            source_id = row.get("source_id")
            if source_id:
                values.add(str(source_id))
    return values


if __name__ == "__main__":
    raise SystemExit(main())
