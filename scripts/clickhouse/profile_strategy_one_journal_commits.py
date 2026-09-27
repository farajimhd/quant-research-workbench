"""Read-only V4 commit-size profile for one completed Strategy 1 Backtest.

Use the dedicated workstation journal principal. No INSERT, disk output, or
full detail-row scan is performed; this is a bounded commit-header inventory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import statistics
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
from src.trading_runtime.arte_journal_writer import (
    _literal, _rows, backtest_v4_operator_client_from_env,
)


# Matches the authoritative V4 commit verifier's per-commit event limit.
_MAX_COMMIT_EVENTS = 1024


def _profile(rows: list[dict]) -> tuple[str, ...]:
    if not rows:
        raise ValueError("No V4 commits exist for the requested run")
    previous_id = str(UUID(int=0))
    previous_sequence = 0
    previous_cursor = "start"
    sizes = []
    changes = 0
    families = []
    statuses = Counter()
    for row in rows:
        batch_id = str(UUID(str(row["batch_id"])))
        size = int(row["event_count"])
        first = int(row["first_sequence"])
        last = int(row["last_sequence"])
        if (str(UUID(str(row["prior_batch_id"]))) != previous_id
                or first != previous_sequence + 1 or last - first + 1 != size
                or not 1 <= size <= _MAX_COMMIT_EVENTS):
            raise ValueError(
                "V4 commit header differs from the diagnostic's chain/batch bound: "
                f"first={first} expected_first={previous_sequence + 1} "
                f"last={last} events={size} bound={_MAX_COMMIT_EVENTS} "
                f"prior_matches={str(UUID(str(row['prior_batch_id']))) == previous_id}")
        cursor = str(row["source_cursor"])
        changes += cursor != previous_cursor
        previous_id, previous_sequence, previous_cursor = batch_id, last, cursor
        sizes.append(size)
        families.append(int(row["family_count"]))
        statuses[str(row["status"])] += 1
    bins = (sum(size == 1 for size in sizes),
            sum(2 <= size <= 7 for size in sizes),
            sum(8 <= size <= 63 for size in sizes),
            sum(64 <= size <= 255 for size in sizes),
            sum(256 <= size <= 1024 for size in sizes))
    return (
        f"V4 commits={len(rows)} events={sum(sizes)} "
        f"terminal={sum(statuses[state] for state in ('completed', 'stopped', 'failed'))}",
        f"Commit events: min={min(sizes)} median={statistics.median(sizes):g} "
        f"max={max(sizes)} mean={statistics.mean(sizes):.1f}",
        "Commit size bins (1, 2-7, 8-63, 64-255, 256-1024): "
        + ", ".join(str(count) for count in bins),
        f"Cursor changes={changes} family_count_median={statistics.median(families):g}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=lambda value: str(UUID(value)))
    args = parser.parse_args()
    _load_private_credentials()
    client = backtest_v4_operator_client_from_env()
    try:
        rows = _rows(client,
            "SELECT batch_id,prior_batch_id,first_sequence,last_sequence,"
            "event_count,family_count,source_cursor,status "
            "FROM arte.trading_commit_v4 "
            f"WHERE run_id={_literal(args.run_id)} "
            "ORDER BY first_sequence,batch_id LIMIT 10001 FORMAT JSONEachRow")
        singleton_families = _rows(client,
            "SELECT e.category,e.entity_type,count() AS commits "
            "FROM arte.trading_event_v1 e "
            "INNER JOIN arte.trading_commit_v4 c "
            "ON e.run_id=c.run_id AND e.batch_id=c.batch_id "
            f"WHERE c.run_id={_literal(args.run_id)} AND c.event_count=1 "
            "GROUP BY e.category,e.entity_type "
            "ORDER BY commits DESC,e.category,e.entity_type "
            "LIMIT 33 FORMAT JSONEachRow")
    finally:
        client.close()
    if len(rows) > 10_000:
        raise RuntimeError("V4 commit profile exceeds its 10,000-row bound")
    for line in _profile(rows):
        print(line, flush=True)
    if len(singleton_families) > 32:
        raise RuntimeError("V4 singleton family inventory exceeds its bound")
    for row in singleton_families:
        if (set(row) != {"category", "entity_type", "commits"}
                or not isinstance(row["commits"], int)
                or row["commits"] < 1):
            raise RuntimeError("V4 singleton family inventory is malformed")
        print(f"Singleton {row['category']}/{row['entity_type']}: "
              f"commits={row['commits']}", flush=True)


if __name__ == "__main__":
    main()
