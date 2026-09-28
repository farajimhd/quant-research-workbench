"""Read-only workstation smoke for saved Strategy 1 daily/monthly charts.

No table creation, inserts, builder calls, or local output artifacts.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
from src.backend.backtest_market_data import readonly_clickhouse_client
from src.backend.backtest_v4_chart import cold_v4_chart_context_pair
from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()
    _load_private_credentials()
    with closing(backtest_v4_operator_client_from_env()) as journal, \
            closing(readonly_clickhouse_client(v3_read_principal=True)) as market:
        start = perf_counter()
        pair = cold_v4_chart_context_pair(
            journal, market, run_id=args.run_id, ticker=args.ticker)
        elapsed = perf_counter() - start
        for frame, key in (("1d", "daily"), ("1mo", "monthly")):
            page = pair[key]
            bars = page["bars"]
            if (page["timeframe"] != frame or not page["history_limited"]
                    or not bars or any("open" not in bar or "close" not in bar
                                       for bar in bars)):
                raise RuntimeError("Saved chart context is absent or malformed")
            print(f"{frame}: bars={len(bars)} "
                  f"first_session={page['history_first_session']} "
                  f"last_session={bars[-1]['session_date']} "
                  f"last_closed={bars[-1]['is_closed']} "
                  f"pair_wall_s={elapsed:.3f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
