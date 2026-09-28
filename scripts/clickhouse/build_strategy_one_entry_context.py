#!/usr/bin/env python3
"""Publish normalized entry context for one completed Strategy 1 Backtest.

This is an offline producer. It is never called by the Backtest engine or a
saved-page GET. Dry-run is the default; --apply writes only the new context
table. No run-local file, SQLite database, or market-product table is written.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import sys
from uuid import UUID

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.mlops.clickhouse import ClickHouseHttpClient
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_v4_chart import certified_saved_run_plan
from src.backend.qmd_gateway_client import qmd_history_post_json
from src.backend.strategy_one_entry_context import (
    CREATE_TABLE, TABLE, build_entry_context_rows,
)
from src.backend.backtest_v4_saved_review import load_v4_performance_report
from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env


def _credentials(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"Credential file unavailable: {path}")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key not in values:
            values[key] = value.strip().strip('"')
    return values


def _admin_client(path: Path) -> ClickHouseHttpClient:
    values = _credentials(path)
    return ClickHouseHttpClient(
        values["REAL_LIVE_CLICKHOUSE_READ_URL"],
        values["CLICKHOUSE_WORKSTATION_USER"],
        values["CLICKHOUSE_WORKSTATION_PASSWORD"],
        timeout_seconds=120, persistent=True,
    )


def _storage_preflight(client: ClickHouseHttpClient, *, table_exists: bool) -> None:
    policies = client.execute(
        "SELECT policy_name,volume_name,disks FROM system.storage_policies "
        "FORMAT JSONEachRow")
    rows = [json.loads(line) for line in policies.splitlines() if line.strip()]
    if not any(row["policy_name"] == "live_market_ssd"
               and "live_market_ssd" in row["disks"] for row in rows):
        raise RuntimeError("Required live_market_ssd policy/disk is absent")
    if table_exists:
        storage = client.execute(
            "SELECT storage_policy FROM system.tables WHERE database='arte' "
            "AND name='strategy_one_entry_context_v1' FORMAT TabSeparatedRaw").strip()
        if storage != "live_market_ssd":
            raise RuntimeError("Entry context table is not on live_market_ssd")
        parts = client.execute(
            "SELECT DISTINCT disk_name FROM system.parts WHERE database='arte' "
            "AND table='strategy_one_entry_context_v1' AND active "
            "FORMAT TabSeparatedRaw").splitlines()
        if any(disk != "live_market_ssd" for disk in parts):
            raise RuntimeError("Entry context has a misplaced active part")


def _existing(client: ClickHouseHttpClient, run_id: str) -> dict[str, str]:
    rows = client.execute(
        "SELECT toString(episode_id) AS episode_id,content_hash "
        f"FROM {TABLE} WHERE run_id=toUUID('{run_id}') "
        "FORMAT JSONEachRow")
    found: dict[str, str] = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        identity = row["episode_id"]
        if identity in found and found[identity] != row["content_hash"]:
            raise RuntimeError("Entry context has conflicting episode versions")
        found[identity] = row["content_hash"]
    return found


def build(run_id: str, *, apply: bool, env_path: Path,
          journal_credential: Path, market_credential: Path) -> None:
    normalized = str(UUID(run_id))
    if not journal_credential.is_file() or not market_credential.is_file():
        raise RuntimeError("Dedicated journal or market reader credential is unavailable")
    os.environ["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] = str(journal_credential)
    os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(market_credential)
    managed_environment = json.loads((ROOT / "scripts" / "service_catalog.json")
                                     .read_text(encoding="utf-8"))["services"]["backend"]["environment"]
    for key, value in managed_environment.items():
        if key.startswith("TRADING_KEEPER_"):
            os.environ.setdefault(key, value)
    with ExitStack() as stack:
        journal = stack.enter_context(closing(backtest_v4_operator_client_from_env()))
        market = stack.enter_context(closing(v3_client("read")))
        admin = stack.enter_context(closing(_admin_client(env_path)))
        session, _, _, plan = certified_saved_run_plan(
            journal, market, run_id=normalized)
        report_page = load_v4_performance_report(journal, normalized)
        print(f"Run {normalized} · {session.isoformat()} · "
              f"{len(report_page['report']['episodes'])} verified episodes", flush=True)
        rows = build_entry_context_rows(
            run_id=normalized, report=report_page["report"], session=session,
            plan=plan, market_client=market, reference_client=admin,
            baseline_provider=lambda day, ticker: qmd_history_post_json(
                "/features/session-relative-volume-baseline",
                {"session_date": day, "tickers": [ticker]}, timeout=900),
        )
        complete = sum(row["float_shares"] is not None
                       and row["shares_outstanding"] is not None
                       and row["entry_rvol"] is not None for row in rows)
        print(f"Prepared {len(rows)} tabular entry rows; "
              f"{complete} have float, shares, and RVOL; "
              f"{len(rows)-complete} have unavailable source values", flush=True)
        for field in ("float_shares", "shares_outstanding", "entry_rvol"):
            missing = sorted({row["ticker"] for row in rows if row[field] is None})
            if missing:
                print(f"Unavailable {field}: {len(missing)} tickers "
                      f"({', '.join(missing)})", flush=True)
        exists = admin.execute(
            "SELECT count() FROM system.tables WHERE database='arte' "
            "AND name='strategy_one_entry_context_v1' FORMAT TabSeparatedRaw").strip() == "1"
        _storage_preflight(admin, table_exists=exists)
        if not apply:
            print("Dry-run complete; no ClickHouse rows or tables changed. "
                  "Use --apply to publish.", flush=True)
            return
        if not exists:
            admin.execute(CREATE_TABLE)
        _storage_preflight(admin, table_exists=True)
        expected = {row["episode_id"]: row["content_hash"] for row in rows}
        found = _existing(admin, normalized)
        if any(expected.get(identity) != digest for identity, digest in found.items()):
            raise RuntimeError("Existing entry context conflicts with verified inputs")
        pending = [row for row in rows if row["episode_id"] not in found]
        if pending:
            body = "\n".join(json.dumps(row, separators=(",", ":"),
                                         allow_nan=False) for row in pending)
            admin.execute(f"INSERT INTO {TABLE} FORMAT JSONEachRow\n{body}")
        if _existing(admin, normalized) != expected:
            raise RuntimeError("Entry context publish/readback differs from prepared rows")
        _storage_preflight(admin, table_exists=True)
        print(f"Verified {len(rows)} normalized rows on live_market_ssd; "
              f"inserted {len(pending)}, already present {len(found)}.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="completed Strategy 1 V4 run UUID")
    parser.add_argument("--apply", action="store_true", help="publish the new normalized ARTE table/rows")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    secret_root = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets")
    parser.add_argument("--journal-credential", type=Path,
                        default=secret_root / "backtest_v4_runner.env")
    parser.add_argument("--market-credential", type=Path,
                        default=secret_root / "backtest_v3_read.env")
    args = parser.parse_args()
    try:
        build(args.run_id, apply=args.apply, env_path=args.env,
              journal_credential=args.journal_credential,
              market_credential=args.market_credential)
    except (KeyError, RuntimeError, ValueError, OSError) as exc:
        print(f"Entry context failed: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
