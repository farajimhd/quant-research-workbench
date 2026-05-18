from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date
from itertools import product
from pathlib import Path
from typing import Iterable

import polars as pl

from src.data_provider.config import DEFAULT_PROCESSED_ROOT, DataProviderConfig
from src.data_provider.provider import MarketDataProvider


@dataclass(frozen=True)
class OracleV6Candidate:
    min_oracle_entry_score: float
    min_oracle_expected_profit: float
    max_oracle_drawdown_before_best: float
    min_oracle_exit_score: float
    min_oracle_exit_realized_profit: float
    short_supervision_exit_score: float
    max_active_positions: int


def default_grid() -> list[OracleV6Candidate]:
    return [
        OracleV6Candidate(*values)
        for values in product(
            [65.0, 75.0, 85.0],
            [0.015, 0.03, 0.05],
            [0.04, 0.08],
            [45.0, 60.0],
            [0.008, 0.02],
            [75.0, 85.0],
            [1, 3],
        )
    ]


def load_training_frame(processed_root: Path, start: date, end: date) -> pl.DataFrame:
    provider = MarketDataProvider(DataProviderConfig(processed_root=processed_root))
    bars = provider.load_bars(
        start_date=start,
        end_date=end,
        timeframe="1m",
        feature_groups=["core", "momentum", "session", "volume_liquidity"],
    )
    supervision = provider.load_supervision(
        start_date=start,
        end_date=end,
        timeframe="1m",
        supervision_type="oracle",
    )
    if bars.is_empty() or supervision.is_empty():
        raise FileNotFoundError("Bars and oracle supervision must be built before hyperparameterization.")
    duplicate_columns = [column for column in supervision.columns if column != "bar_id" and column in bars.columns]
    if duplicate_columns:
        supervision = supervision.drop(duplicate_columns)
    return bars.join(supervision, on="bar_id", how="inner", coalesce=True).sort(["bar_time_market", "ticker"])


def evaluate_candidate(frame: pl.DataFrame, candidate: OracleV6Candidate, initial_cash: float = 10_000.0) -> dict:
    event_frame = frame.filter(
        (
            (pl.col("oracle_long_enter_signal") == True)
            & (pl.col("oracle_long_supervision_score") >= candidate.min_oracle_entry_score)
            & (pl.col("long_expected_profit") >= candidate.min_oracle_expected_profit)
        )
        | (
            (pl.col("oracle_long_exit_signal") == True)
            & (pl.col("oracle_long_supervision_score") >= candidate.min_oracle_exit_score)
            & (pl.col("long_exit_realized_profit") >= candidate.min_oracle_exit_realized_profit)
        )
        | (
            ((pl.col("oracle_short_enter_signal") == True) | (pl.col("oracle_short_supervision") == True))
            & (pl.col("oracle_short_supervision_score") >= candidate.short_supervision_exit_score)
        )
    ).sort(["bar_time_market", "ticker"])
    if event_frame.is_empty():
        return {
            **asdict(candidate),
            "objective": -1_000_000.0,
            "pnl": 0.0,
            "return_pct": 0.0,
            "trade_count": 0,
            "win_rate": 0.0,
            "profit_factor": None,
            "max_drawdown_pct": 0.0,
        }
    cash = float(initial_cash)
    positions: dict[str, dict] = {}
    trades: list[float] = []
    equity_peak = float(initial_cash)
    max_drawdown = 0.0
    timestamps = event_frame.partition_by("bar_time_market", as_dict=True, maintain_order=True)
    for _, slice_frame in timestamps.items():
        rows = slice_frame.to_dicts()
        for row in rows:
            symbol = str(row.get("ticker") or "").upper()
            position = positions.get(symbol)
            if position is None:
                continue
            if oracle_exit(row, candidate):
                exit_price = float(row.get("best_exit_price") or row.get("open") or row.get("close") or position["entry_price"])
                pnl = (exit_price - position["entry_price"]) * position["quantity"]
                cash += exit_price * position["quantity"]
                trades.append(pnl)
                del positions[symbol]

        available_slots = max(0, candidate.max_active_positions - len(positions))
        if available_slots > 0 and cash > 0:
            entries = [row for row in rows if oracle_entry(row, candidate) and str(row.get("ticker") or "").upper() not in positions]
            entries.sort(
                key=lambda row: (
                    float(row.get("oracle_long_supervision_score") or row.get("oracle_long_enter_score") or 0.0),
                    float(row.get("long_expected_profit") or row.get("expected_profit") or 0.0),
                    float(row.get("recent_dollar_volume_5") or row.get("last_recent_dollar_volume_5") or 0.0),
                ),
                reverse=True,
            )
            for row in entries[:available_slots]:
                price = float(row.get("open") or row.get("current_open") or row.get("close") or 0.0)
                if price <= 0:
                    continue
                budget = cash / max(1, available_slots)
                quantity = int(budget / price)
                if quantity <= 0:
                    continue
                symbol = str(row.get("ticker") or "").upper()
                cash -= quantity * price
                positions[symbol] = {"entry_price": price, "quantity": quantity}

        marked_equity = cash
        for symbol, position in positions.items():
            row = next((item for item in rows if str(item.get("ticker") or "").upper() == symbol), None)
            mark = float(row.get("close") or position["entry_price"]) if row else position["entry_price"]
            marked_equity += mark * position["quantity"]
        equity_peak = max(equity_peak, marked_equity)
        if equity_peak > 0:
            max_drawdown = max(max_drawdown, (equity_peak - marked_equity) / equity_peak)

    for position in list(positions.values()):
        cash += position["entry_price"] * position["quantity"]
    total_pnl = cash - initial_cash
    wins = [pnl for pnl in trades if pnl > 0]
    losses = [pnl for pnl in trades if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    turnover_penalty = max(0, len(trades) - 80) * 2.0
    objective = total_pnl - (max_drawdown * initial_cash * 2.0) - turnover_penalty
    return {
        **asdict(candidate),
        "objective": objective,
        "pnl": total_pnl,
        "return_pct": total_pnl / initial_cash,
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown_pct": max_drawdown,
    }


def oracle_entry(row: dict, candidate: OracleV6Candidate) -> bool:
    if not bool(row.get("oracle_long_enter_signal")):
        return False
    if float(row.get("oracle_long_supervision_score") or row.get("oracle_long_enter_score") or 0.0) < candidate.min_oracle_entry_score:
        return False
    if float(row.get("long_expected_profit") or row.get("expected_profit") or 0.0) < candidate.min_oracle_expected_profit:
        return False
    drawdown = abs(min(0.0, float(row.get("long_drawdown_before_best") or row.get("expected_drawdown") or 0.0)))
    return drawdown <= candidate.max_oracle_drawdown_before_best


def oracle_exit(row: dict, candidate: OracleV6Candidate) -> bool:
    if bool(row.get("oracle_long_exit_signal")):
        score = float(row.get("oracle_long_supervision_score") or row.get("oracle_long_exit_score") or 0.0)
        profit = float(row.get("long_exit_realized_profit") or 0.0)
        if score >= candidate.min_oracle_exit_score and profit >= candidate.min_oracle_exit_realized_profit:
            return True
    if bool(row.get("oracle_short_enter_signal")) or bool(row.get("oracle_short_supervision")):
        return float(row.get("oracle_short_supervision_score") or 0.0) >= candidate.short_supervision_exit_score
    return False


def optimize(frame: pl.DataFrame, candidates: Iterable[OracleV6Candidate]) -> pl.DataFrame:
    event_frame = frame.filter(
        (pl.col("oracle_long_enter_signal") == True)
        | (pl.col("oracle_long_exit_signal") == True)
        | (pl.col("oracle_short_enter_signal") == True)
        | (pl.col("oracle_short_supervision") == True)
    ).select(
        [
            column
            for column in [
                "ticker",
                "bar_time_market",
                "open",
                "close",
                "oracle_long_enter_signal",
                "oracle_long_exit_signal",
                "oracle_short_enter_signal",
                "oracle_short_supervision",
                "oracle_long_supervision_score",
                "oracle_short_supervision_score",
                "oracle_long_enter_score",
                "long_expected_profit",
                "long_drawdown_before_best",
                "long_exit_realized_profit",
                "best_exit_price",
                "expected_profit",
                "expected_drawdown",
                "recent_dollar_volume_5",
                "last_recent_dollar_volume_5",
            ]
            if column in frame.columns
        ]
    )
    rows = [evaluate_candidate(event_frame, candidate) for candidate in candidates]
    return pl.DataFrame(rows).sort(["objective", "pnl"], descending=[True, True])


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperparameterize Long Momentum v6 against oracle supervision.")
    parser.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT))
    parser.add_argument("--start-date", default="2024-05-01")
    parser.add_argument("--end-date", default="2024-05-01")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    frame = load_training_frame(Path(args.processed_root), date.fromisoformat(args.start_date), date.fromisoformat(args.end_date))
    result = optimize(frame, default_grid())
    print(json.dumps(result.head(max(1, args.top)).to_dicts(), indent=2, default=str))


if __name__ == "__main__":
    main()
