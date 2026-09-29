"""SELECT-only proof that a saved Strategy 1 definition still round-trips.

This does not grant interrupted-run resume or write to ClickHouse or disk.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from src.backend.replay_run_service import ReplayRunService


def audit(run_id: str) -> tuple[str, str, int]:
    if platform.node().upper() != "DESKTOP-SAAI85T" or str(UUID(run_id)) != run_id:
        raise ValueError("Cold definition audit needs a canonical workstation run ID")
    journal_credential = Path(r"D:\TradingML\secrets\backtest_v4_runner.env")
    market_credential = Path(r"D:\TradingML\secrets\backtest_v3_read.env")
    if not journal_credential.is_file() or not market_credential.is_file():
        raise RuntimeError("Managed read-only audit credentials are unavailable")
    os.environ["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] = str(journal_credential)
    os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(market_credential)
    definition = ReplayRunService._load_typed_backtest_resume_definition(run_id)
    if definition is None:
        raise RuntimeError("Typed Backtest run is absent")
    return (definition.session_date.isoformat(),
            definition.market_data_plan["token"],
            len(definition.tickers))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Canonical UUID of a V4 Backtest")
    args = parser.parse_args()
    try:
        session, market_token, selected_tickers = audit(args.run_id)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"Cold definition audit failed: {exc}\n")
    print("Cold definition verified: "
          f"run={args.run_id} session={session} "
          f"explicit_tickers={selected_tickers} "
          f"market_token={market_token[:12]}... writes=0")


if __name__ == "__main__":
    main()
