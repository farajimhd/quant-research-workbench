"""Read-only selected-session market plan audit for Strategy 1.

This diagnostic neither launches a Backtest nor publishes any market or
journal data. It proves the sealed day's exact product hashes and plan token.
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
from time import perf_counter
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from src.backend.backtest_market_data import (
    _MarketCertificateReader, readonly_clickhouse_client,
)
from src.trading_runtime.arte_market_day_cold_preflight import (
    cold_certified_market_day_plan, sealed_certified_market_day_plan,
)
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from scripts.clickhouse.provision_fixed_backtest_v3_principals import _secret_path
from scripts.clickhouse.provision_trading_journal import _restrict_secret_file


def audit(build_id: str, day: date, *, compare_global: bool = False
          ) -> tuple[int, int, str]:
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", build_id) is None):
        raise ValueError("Selected-day audit requires workstation and exact build ID")
    path = _secret_path("read")
    if not path.is_file():
        raise RuntimeError("Private Backtest reader credential is absent")
    _restrict_secret_file(path)
    if not any(os.environ.get(key) for key in (
            "BACKTEST_V3_READ_CREDENTIAL_FILE", "BACKTEST_V3_READ_CLICKHOUSE_URL",
            "BACKTEST_V3_READ_CLICKHOUSE_USER",
            "BACKTEST_V3_READ_CLICKHOUSE_PASSWORD")):
        os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(path)
    with closing(readonly_clickhouse_client(v3_read_principal=True)) as http:
        reader = _MarketCertificateReader(http)
        with closing(open_workstation_keeper_session()) as session:
            keeper = MarketDayKeeperReader(session.client)
            configuration = {"strategy": {"strategy_number": 1,
                                          "execution_interval": "100ms"}}
            plan = sealed_certified_market_day_plan(
                reader, keeper, build_id, sessions=(day.isoformat(),), tickers=(),
                configuration=configuration,
                read_client_factory=lambda: _MarketCertificateReader(
                    readonly_clickhouse_client(v3_read_principal=True)),
            )
            if compare_global:
                global_plan = cold_certified_market_day_plan(
                    reader, keeper, build_id, sessions=(day.isoformat(),),
                    tickers=(), configuration=configuration,
                    read_client_factory=lambda: _MarketCertificateReader(
                        readonly_clickhouse_client(v3_read_principal=True)),
                )
                if plan != global_plan:
                    raise RuntimeError("Selected and global certified plans differ")
    return len(plan.tickers), len(plan.units), plan.token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--session", required=True, type=date.fromisoformat)
    parser.add_argument("--compare-global", action="store_true",
                        help="also run the slower whole-build audit and require exact plan parity")
    args = parser.parse_args(argv)
    started = perf_counter()
    try:
        tickers, units, token = audit(args.build_id, args.session,
                                     compare_global=args.compare_global)
    except KeyboardInterrupt:
        print("Selected-day audit interrupted; no data was changed.", file=sys.stderr)
        return 130
    except Exception as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(f"Selected-day audit blocked: {type(exc).__name__} "
              f"at {Path(frame.filename).name}:{frame.name}:{frame.lineno}; "
              "inspect read-only principal, seal, Keeper, and product coverage.",
              file=sys.stderr)
        return 1
    print(f"Selected-day audit passed: session={args.session.isoformat()} "
          f"tickers={tickers} product_units={units} token={token} "
          f"elapsed_s={perf_counter() - started:.1f} "
          f"global_parity={'passed' if args.compare_global else 'not_run'}; writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
