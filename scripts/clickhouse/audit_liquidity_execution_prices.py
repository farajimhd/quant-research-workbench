"""Read-only audit of certified eligible prices for a Backtest session.

This uses the exact V3 SELECT-only principal and the market-day Keeper proof.
It never creates a table, repairs a row, or opens a market-data disk ledger.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_fixed_backtest_v3_principals import _secret_path
from src.backend.backtest_liquidity_price import certify_price_level_plan
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_v3_clients import v3_client
from src.trading_runtime.arte_market_day_cold_preflight import (
    audit_attested_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def audit(build_id: str, day: date, tickers: tuple[str, ...]) -> tuple[int, str]:
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Eligible-price audit uses workstation V3 credentials")
    credential = _secret_path("read")
    if not credential.is_file():
        raise RuntimeError("Private V3 reader credential is unavailable")
    with closing(v3_client("read", environment={
            "BACKTEST_V3_READ_CREDENTIAL_FILE": str(credential)},
            market_stream=True)) as client, closing(
            open_workstation_keeper_session()) as keeper:
        attested = audit_attested_market_day_certificate(
            client, MarketDayKeeperReader(keeper.client), build_id,
            sessions=(day.isoformat(),))
        available = {(scope_day, ticker) for scope_day, ticker
                     in attested.certificate.scopes if scope_day == day.isoformat()}
        selected = ({(day.isoformat(), ticker) for ticker in tickers}
                    if tickers else available)
        if not selected or not selected <= available:
            raise ValueError("Requested ticker is outside attested market-day scope")
        units = tuple(MarketDayUnit(build_id, scope_day, ticker, stage, attempt,
                                   "attested", rows, content_hash)
                      for scope_day, ticker, stage, attempt, rows, content_hash
                      in attested.certificate.stages
                      if (scope_day, ticker) in selected)
        market = CertifiedMarketDayPlan(
            ExecutionInterval.fixed(100), build_id,
            attested.certificate.definition_hash, (day.isoformat(),),
            tuple(sorted({ticker for _, ticker in selected})), units,
            (100,), "attested-child-audit")
        result = certify_price_level_plan(market, client)
        return len(result.units), result.token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--tickers", default="",
                        help="Comma-separated certified canary symbols; default all")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", args.build_id):
        parser.error("Invalid market-day build ID")
    tickers = tuple(sorted(set(part.strip().upper() for part in
                               args.tickers.split(",") if part.strip())))
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", ticker)
           for ticker in tickers):
        parser.error("Invalid canary ticker")
    try:
        count, token = audit(args.build_id, args.date, tickers)
    except Exception as exc:
        print(f"Eligible-price audit blocked: {exc}", file=sys.stderr)
        return 1
    print(f"Eligible prices verified | {args.date} | {count} ticker-days | "
          f"token {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
