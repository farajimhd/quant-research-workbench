"""Explicit operational input exclusions, pinned into new Backtest runs.

The dated JSON file is operator-owned under the runtime root. It cannot
certify missing data. Original market and candidate products are verified
before scope projection; retained run reconstruction uses its pinned list.
"""
from datetime import date
import json
import os
from pathlib import Path

from src.backend.backtest_strategy_one_candidate_store import (
    certify_candidate_plan, exclude_candidate_tickers,
)


def input_exclusions(session_date: str) -> tuple[str, ...]:
    path = Path(os.environ.get("BACKTEST_INPUT_EXCLUSIONS_FILE",
                               r"D:\TradingML\runtimes\backtest-preparation\input-exclusions.json"))
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("Input exclusion policy must be an explicit dated list")
    selected = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"ticker", "start", "end", "reason"}
                or not all(isinstance(v, str) and v for v in row.values())
                or date.fromisoformat(row["start"]) > date.fromisoformat(row["end"])):
            raise ValueError("Input exclusion needs a symbol, valid date range, and reason")
        if row["start"] <= session_date <= row["end"]:
            selected.append(row["ticker"])
    return tuple(sorted(set(selected)))


def certify_scoped_candidate_plan(market, **kwargs):
    full = certify_candidate_plan(market, **kwargs)
    excluded = input_exclusions(market.sessions[0])
    if excluded:
        print(f"Backtest input exclusions | {market.sessions[0]} | {','.join(excluded)}", flush=True)
    return exclude_candidate_tickers(full, excluded)
