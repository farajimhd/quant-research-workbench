"""Read-only audit of one V4 broker checkpoint's pinned liquidity quotes.

This does not resume a run, open SQLite, or publish ClickHouse rows.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import platform
import re
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from src.backend.backtest_market_data import _MarketCertificateReader
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_v4_broker_quote_restore import load_completed_broker_quotes
from src.trading_runtime.arte_journal_writer import (
    backtest_v4_operator_client_from_env, load_typed_run_context,
)
from src.trading_runtime.arte_market_day_cold_preflight import (
    sealed_certified_market_day_plan,
)
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    load_unattested_broker_match_snapshot,
)


def audit(*, run_id: str, build_id: str, session: date,
          checkpoint_sequence: int) -> tuple[int, int]:
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or str(UUID(run_id)) != run_id
            or re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", build_id) is None
            or type(checkpoint_sequence) is not int
            or checkpoint_sequence < 1):
        raise ValueError("Audit needs exact workstation run, build, and checkpoint")
    secrets = Path(r"D:\TradingML\secrets")
    read_file = secrets / "backtest_v3_read.env"
    journal_file = secrets / "backtest_v4_runner.env"
    if not read_file.is_file() or not journal_file.is_file():
        raise RuntimeError("Managed read-only audit credentials are unavailable")
    os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(read_file)
    os.environ["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] = str(journal_file)
    with closing(v3_client("read")) as market_http, closing(
            backtest_v4_operator_client_from_env()) as journal, closing(
            open_workstation_keeper_session()) as keeper_session:
        market = _MarketCertificateReader(market_http)
        configuration = {"strategy": {"strategy_number": 1,
                                      "execution_interval": "100ms"}}
        plan = sealed_certified_market_day_plan(
            market, MarketDayKeeperReader(keeper_session.client), build_id,
            sessions=(session.isoformat(),), tickers=(),
            configuration=configuration,
            read_client_factory=lambda: _MarketCertificateReader(v3_client("read")))
        context = load_typed_run_context(journal, run_id)
        if (context["mode"] != "backtest"
                or context["market_plan_token"] != plan.token):
            raise RuntimeError("Broker quote audit plan differs from pinned run")
        broker = load_unattested_broker_match_snapshot(
            journal, run_id=run_id,
            checkpoint_sequence=checkpoint_sequence)
        quotes = load_completed_broker_quotes(
            market, plan=plan, broker=broker)
        return len(broker.tickers), len(quotes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--session", required=True, type=date.fromisoformat)
    parser.add_argument("--checkpoint-sequence", required=True, type=int)
    args = parser.parse_args()
    tickers, quotes = audit(
        run_id=args.run_id, build_id=args.build_id, session=args.session,
        checkpoint_sequence=args.checkpoint_sequence)
    print(f"V4 broker quote audit passed: tickers={tickers} "
          f"pinned_quotes={quotes} writes=0")


if __name__ == "__main__":
    main()
