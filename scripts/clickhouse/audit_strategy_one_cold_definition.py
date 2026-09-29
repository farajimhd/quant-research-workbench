"""SELECT-only proof that a saved Strategy 1 definition still round-trips.

This does not grant interrupted-run resume or write to ClickHouse or disk.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, time, timedelta
import os
from pathlib import Path
import platform
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from src.backend.backtest_strategy_one_configuration import (
    selected_strategy_one_revision,
)
from src.backend.backtest_v3_clients import v3_client
from src.backend.replay_run_service import backtest_preflight
from src.trading_runtime.arte_backtest_definition import (
    load_backtest_definition, reconstruct_backtest_definition_from_arte,
)
from src.trading_runtime.arte_journal_writer import (
    backtest_v4_operator_client_from_env, load_typed_run_context,
)


def _time(milliseconds: int) -> time:
    if type(milliseconds) is not int or not 0 <= milliseconds < 86_400_000:
        raise ValueError("Saved Backtest local clock is invalid")
    hour, remaining = divmod(milliseconds, 3_600_000)
    minute, remaining = divmod(remaining, 60_000)
    second, remaining = divmod(remaining, 1_000)
    return time(hour, minute, second, remaining * 1_000)


def audit(run_id: str) -> tuple[str, str, int]:
    if platform.node().upper() != "DESKTOP-SAAI85T" or str(UUID(run_id)) != run_id:
        raise ValueError("Cold definition audit needs a canonical workstation run ID")
    journal_credential = Path(r"D:\TradingML\secrets\backtest_v4_runner.env")
    market_credential = Path(r"D:\TradingML\secrets\backtest_v3_read.env")
    if not journal_credential.is_file() or not market_credential.is_file():
        raise RuntimeError("Managed read-only audit credentials are unavailable")
    os.environ["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] = str(journal_credential)
    os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(market_credential)
    with closing(backtest_v4_operator_client_from_env()) as journal, closing(
            v3_client("read")) as market:
        if (journal.execute("SELECT getSetting('readonly')").strip() != "1"
                or market.execute("SELECT getSetting('readonly')").strip() != "1"):
            raise RuntimeError("Cold definition audit requires SELECT-only principals")
        context = load_typed_run_context(journal, run_id)
        saved = load_backtest_definition(journal, run_id, run_context=context)
        parent = saved["definition"]
        session = date.fromisoformat(context["session_date"])
        revision = selected_strategy_one_revision(
            revision_id=parent["configuration_revision_id"], client=market)
        preflight = backtest_preflight(
            anchor_date=session + timedelta(days=1), session_count=1,
            start_time=_time(parent["start_local_ms"]),
            end_time=_time(parent["end_local_ms"]),
            initial_cash=float(parent["initial_cash"]),
            tickers=tuple(row["ticker"] for row in saved["tickers"]),
            configuration_revision=revision,
            experimental_structure_book=parent["structure_book"])
        definition = reconstruct_backtest_definition_from_arte(
            saved, context, revision, preflight)
        return (definition.session_date.isoformat(),
                definition.market_data_plan["token"],
                len(saved["tickers"]))


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
