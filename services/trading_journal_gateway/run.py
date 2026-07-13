from __future__ import annotations

import os
import time
from pathlib import Path

from src.trading_runtime.clickhouse import ClickHouseTradingSink
from src.trading_runtime.journal import TradingJournal


REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    journal_path = Path(os.environ.get("TRADING_JOURNAL_PATH", REPO_ROOT / "runtime" / "trading" / "journal.sqlite3"))
    journal = TradingJournal(journal_path)
    sink = ClickHouseTradingSink(
        endpoint_url=os.environ.get("TRADING_CLICKHOUSE_URL", os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")),
        user=os.environ.get("TRADING_CLICKHOUSE_USER", os.environ.get("CLICKHOUSE_USER", "default")),
        password=os.environ.get("TRADING_CLICKHOUSE_PASSWORD", os.environ.get("CLICKHOUSE_PASSWORD", "")),
    )
    sink.initialize()
    persisted_strategies: set[tuple[str, int]] = set()
    interval = max(0.1, float(os.environ.get("TRADING_JOURNAL_FLUSH_SECONDS", "1")))
    batch_size = max(1, min(int(os.environ.get("TRADING_JOURNAL_BATCH_SIZE", "500")), 10_000))
    try:
        while True:
            for strategy in journal.strategies(latest_only=False):
                key = (str(strategy["strategy_id"]), int(strategy["revision"]))
                if key not in persisted_strategies:
                    sink.persist_strategy(strategy)
                    persisted_strategies.add(key)
            flushed = sink.flush(journal, batch_size)
            if flushed == 0:
                time.sleep(interval)
    finally:
        journal.close()


if __name__ == "__main__":
    main()
