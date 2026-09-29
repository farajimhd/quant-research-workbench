"""Read-only ASGI probe for a completed Strategy 1 Backtest review.

Runs on the workstation with managed credentials. No HTTP server, market
product writer, run-local file, or ClickHouse INSERT is created.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import os
from pathlib import Path
import sys
from time import perf_counter
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from httpx import ASGITransport, AsyncClient  # noqa: E402

from scripts.clickhouse.smoke_strategy_one_backtest import (  # noqa: E402
    _load_private_credentials,
)
def _ticker(run_id: str) -> str:
    from src.backend.backtest_market_data import readonly_clickhouse_client
    from src.backend.backtest_v4_chart import certified_saved_run_plan
    from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env

    with closing(backtest_v4_operator_client_from_env()) as journal_client, \
            closing(readonly_clickhouse_client(v3_read_principal=True)) as market_client:
        _, _, _, plan = certified_saved_run_plan(
            journal_client, market_client, run_id=run_id)
    tickers = plan.tickers
    if not tickers or len(set(tickers)) != len(tickers):
        raise RuntimeError("Saved run has no unique certified chart ticker")
    return tickers[0]


async def _probe(run_id: str, ticker: str) -> None:
    from src.backend.app import app

    root = f"/api/trading/backtest/runs/{run_id}"
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://backtest-probe",
                           timeout=120.0) as http:
        async def page(label: str, path: str, *, params=None) -> dict:
            started = perf_counter()
            response = await http.get(path, params=params)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Saved {label} HTTP {response.status_code}: "
                    f"{response.text[:300]}")
            result = response.json()
            print(f"{label}: wall_s={perf_counter() - started:.3f}", flush=True)
            return result

        history = await page("history", "/api/trading/backtest/runs",
                             params={"strategy_one_only": "true"})
        selected = next((row for row in history["rows"]
                         if row["run_id"] == run_id), None)
        if (selected is None or selected.get("journal_backend") !=
                "arte_typed_journal_v4" or
                selected.get("v4_review_available") is not True):
            raise RuntimeError("Saved run is not reviewable in HTTP history")
        terminal = await page("terminal", f"{root}/v4-terminal-page",
                              params={"after_sequence": 0, "limit": 100})
        if (terminal.get("schema_version") !=
                "strategy-one-v4-terminal-review-page-v1"
                or terminal["run"]["run_id"] != run_id
                or terminal.get("status") != "completed"
                or terminal.get("market_cursor_verified") is not True):
            raise RuntimeError("Saved terminal HTTP page lacks verified authority")
        trade = await page("trades", f"{root}/v4-trade-history",
                           params={"limit": 100})
        orders = await page("orders", f"{root}/v4-order-history",
                            params={"limit": 100})
        chart = await page("chart", f"{root}/v4-chart",
                           params={"ticker": ticker, "timeframe": "1s",
                                   "row_limit": 100,
                                   "indicator_columns": "macd_line,macd_signal"})
        if (trade.get("run_id") != run_id or orders.get("run_id") != run_id
                or chart.get("schema_version") !=
                "strategy-one-v4-chart-page-v1"
                or chart.get("run_id") != run_id
                or chart.get("ticker") != ticker
                or chart.get("market_plan_token") !=
                terminal["run"]["market_plan_token"]):
            raise RuntimeError("Saved HTTP pages differ from the selected run")
        print(f"Saved HTTP review passed: run_id={run_id} ticker={ticker} "
              f"journal_sequence={terminal['verified_sequence']} "
              f"chart_bars={len(chart['bars'])}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    run_id = str(UUID(args.run_id))
    _load_private_credentials()
    asyncio.run(_probe(run_id, _ticker(run_id)))


if __name__ == "__main__":
    main()
