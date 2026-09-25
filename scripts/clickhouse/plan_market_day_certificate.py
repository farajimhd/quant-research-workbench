"""Read-only producer audit of a saved market-day build's typed certificate.

This command never connects to ClickHouse or Keeper and never publishes rows.
It reads the producer's archived manifest and SQLite ledger only to establish
whether a later, separately authorized certificate publication is possible.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.build_market_day import digest
from src.trading_runtime.arte_market_day_certification import (
    prepare_market_day_certificate,
)


DEFAULT_RUNTIME = Path(r"D:\TradingML\runtimes")


class ReadOnlyLedger:
    """The producer's exact unit/seed facts, with SQLite writes impossible."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def unit(self, build_id: str, day: str, ticker: str, stage: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT attempt_id,source_hash,output_rows,output_hash,status FROM units "
            "WHERE build_id=? AND session_date=? AND ticker=? AND stage=?",
            (build_id, day, ticker, stage),
        ).fetchone()
        return dict(zip(("attempt_id", "source_hash", "output_rows",
                         "output_hash", "status"), row)) if row else None

    def seed(self, build_id: str, day: str, ticker: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT attempt_id,mode,predecessor_date,prior_build_id,prior_state_hash "
            "FROM seeds WHERE build_id=? AND session_date=? AND ticker=?",
            (build_id, day, ticker),
        ).fetchone()
        return dict(zip(("attempt_id", "mode", "predecessor_date",
                         "prior_build_id", "prior_state_hash"), row)) if row else None


def prepare_saved_build(runtime: Path, build_id: str) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Read a completed producer archive; never use this in Backtest."""
    runtime = runtime.resolve(strict=True)
    ledger_path = runtime / "build-ledger-v2.sqlite3"
    manifest_path = runtime / "market-day" / f"{build_id}.json"
    if not ledger_path.is_file() or not manifest_path.is_file():
        raise ValueError("Market-day ledger or archived build manifest is unavailable")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    definition = manifest.get("definition")
    definition_hash = digest(definition) if isinstance(definition, dict) else ""
    if (manifest.get("build_id") != build_id or manifest.get("status") != "core_complete"
            or not isinstance(definition, dict)
            or build_id.split("-", 1)[0] != definition_hash):
        raise ValueError("Archived build is not an exact core-complete definition")
    uri = (f"file:{ledger_path}?mode=ro" if str(ledger_path).startswith("\\\\")
           else f"{ledger_path.as_uri()}?mode=ro")
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT definition_hash,version,database_name,status FROM builds WHERE build_id=?",
            (build_id,),
        ).fetchone()
        if row != (definition_hash, "market-day-core-v5", "arte", "core_complete"):
            raise ValueError("Producer ledger lacks the matching core-complete build")
        prepared = prepare_market_day_certificate(definition, build_id,
                                                   ReadOnlyLedger(connection))
    finally:
        connection.close()
    return prepared, tuple(definition["plan"]["requested"])


def audit_saved_build(runtime: Path, build_id: str) -> dict[str, Any]:
    """Prepare and internally cold-verify every typed family without side effects."""
    prepared, sessions = prepare_saved_build(runtime, build_id)
    fence = prepared["market_day_build_fence_v1"][0]
    return {
        "build_id": build_id,
        "sessions": sessions,
        "family_rows": {name: len(rows) for name, rows in sorted(prepared.items())},
        "definition_hash": fence["definition_hash"],
        "source_plan_hash": fence["source_plan_hash"],
        "source_inventory_hash": fence["source_inventory_hash"],
        "scope_hash": fence["scope_hash"],
        "stage_hash": fence["stage_hash"],
        "seed_hash": fence["seed_hash"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(audit_saved_build(args.runtime, args.build_id), sort_keys=True))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, TypeError) as exc:
        print(f"Market-day certificate audit failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
