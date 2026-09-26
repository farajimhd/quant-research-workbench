"""Publish complete dated Strategy 1 broker identities into normalized ARTE.

This producer reads the certified ARTE market scope and the pre-cutoff dated
q_live universe. Backtest receives SELECT only. No market or journal table is
written by this command.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import platform
import sys
import traceback

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from pipelines.strategy_one.identity_publication import publish_identity
from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_strategy_one_candidate_producer import (
    PRINCIPAL, WORKSTATION_IPV4, _GRANTS, _credential, _grant_set,
)
from scripts.clickhouse.publish_strategy_one_candidates import _certified_plan
from src.backend.backtest_market_data import readonly_clickhouse_client
from src.trading_runtime.strategy_one_identity_schema import verify_tables


def publish_session(*, session_date: str, build_id: str) -> str:
    market = _certified_plan(session_date=session_date, build_id=build_id)
    with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
        verify_tables(reader)
    password = _credential(account_exists=True)
    with closing(ClickHouseHttpClient(
            f"http://{WORKSTATION_IPV4}:18123", PRINCIPAL, password,
            timeout_seconds=60, persistent=False)) as writer:
        if (writer.execute("SELECT currentUser()").strip() != PRINCIPAL
                or _grant_set(writer) != _GRANTS):
            raise RuntimeError("Strategy 1 identity producer lacks exact grants")
        return publish_identity(writer, market)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default="2026-08-18")
    parser.add_argument("--build-id", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-identity-publication", action="store_true")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
    except ValueError as exc:
        parser.error(str(exc))
    if not args.apply:
        print(f"DRY RUN: {day}; no connection or write. Apply on the managed "
              "workstation with --apply --confirm-identity-publication.")
        return 0
    if not args.confirm_identity_publication:
        parser.error("--apply requires --confirm-identity-publication")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: identity producer requires the managed workstation.",
              file=sys.stderr)
        return 1
    try:
        result = publish_session(session_date=day, build_id=args.build_id)
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{frame.name}:{frame.lineno}" for frame in reversed(frames)
                      if frame.filename == __file__), "identity_dependency")
        print(f"Identity publication stopped: {type(exc).__name__} at {stage}; "
              "inspect private diagnostics before retrying.", file=sys.stderr)
        return 1
    print(f"Strategy 1 dated identity {result}: {day}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
