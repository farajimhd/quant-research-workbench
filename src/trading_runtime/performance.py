from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from src.trading_runtime.domain import Execution, RoundTripTrade, TradeEpisode, json_safe


ZERO = Decimal("0")


@dataclass(slots=True)
class _EpisodeState:
    account_id: str
    instrument: Any
    direction: int
    opened_at: datetime
    strategy_id: str
    strategy_revision: int
    run_id: str
    setup: str
    position: Decimal = ZERO
    peak_quantity: Decimal = ZERO
    entry_quantity: Decimal = ZERO
    entry_notional: Decimal = ZERO
    exit_quantity: Decimal = ZERO
    exit_notional: Decimal = ZERO
    gross_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    planned_risk: Decimal | None = None
    exit_reason: str = ""
    execution_ids: list[str] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)

    @property
    def average_entry(self) -> Decimal:
        return self.entry_notional / self.entry_quantity if self.entry_quantity else ZERO


def derive_trade_episodes(executions: Iterable[Execution]) -> list[TradeEpisode]:
    """Derive deterministic flat-to-flat position episodes from executions.

    A scale-in or partial exit stays inside one episode. A reversal closes the
    prior episode and opens a new one with the unmatched quantity. This makes
    win rate count strategy decisions instead of FIFO fragments.
    """

    states: dict[tuple[str, str], _EpisodeState] = {}
    episodes: list[TradeEpisode] = []
    sequences: dict[tuple[str, str], int] = defaultdict(int)
    ordered = sorted(executions, key=lambda row: (row.source_event_time, row.execution_id))
    for execution in ordered:
        quantity = abs(execution.quantity)
        if quantity <= 0:
            continue
        direction = 1 if execution.side.upper() == "BUY" else -1
        key = (execution.account_id, execution.instrument.instrument_id)
        state = states.get(key)
        remaining = quantity
        fee_per_unit = (execution.commission or ZERO) / quantity
        if state is None:
            states[key] = _open_state(execution, direction, remaining, fee_per_unit)
            continue

        if state.direction == direction:
            _add_opening_fill(state, execution, remaining, fee_per_unit)
            continue

        closing = min(abs(state.position), remaining)
        _add_closing_fill(state, execution, closing, fee_per_unit)
        remaining -= closing
        if state.position == 0:
            sequences[key] += 1
            episodes.append(_close_episode(state, execution.source_event_time, sequences[key]))
            del states[key]
        if remaining > 0:
            states[key] = _open_state(execution, direction, remaining, fee_per_unit)
    return episodes


def episodes_from_round_trips(rows: Iterable[RoundTripTrade]) -> list[TradeEpisode]:
    """Adapt completed backtest trade artifacts that already represent episodes."""

    return [
        TradeEpisode(
            episode_id=row.trade_id,
            account_id=row.account_id,
            instrument=row.instrument,
            opened_at=row.opened_at,
            closed_at=row.closed_at,
            side=row.side,
            quantity=row.quantity,
            entry_price=row.entry_price,
            exit_price=row.exit_price,
            gross_pnl=row.gross_pnl,
            fees=row.fees,
            net_pnl=row.net_pnl,
            strategy_id=row.strategy_id,
            strategy_revision=row.strategy_revision,
            run_id=row.run_id,
            setup=row.setup,
            exit_reason=row.exit_reason,
            mae=row.mae,
            mfe=row.mfe,
            planned_risk=row.planned_risk,
            execution_ids=row.execution_ids,
        )
        for row in rows
    ]


def build_performance_report(
    episodes: Iterable[TradeEpisode],
    executions: Iterable[Execution],
    orders: Iterable[Any],
) -> dict[str, Any]:
    rows = sorted(episodes, key=lambda row: (row.closed_at, row.episode_id))
    fills = list(executions)
    order_rows = list(orders)
    summary = _summary(rows)
    equity_curve = _equity_curve(rows)
    strategies = [_group_summary(key, group) for key, group in _group_episodes(rows).items()]
    strategies.sort(key=lambda row: (row["net_pnl"], row["episode_count"]), reverse=True)
    venue_notional: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for fill in fills:
        venue_notional[fill.exchange or "Unknown"] += abs(fill.quantity * fill.price)
    total_notional = sum(venue_notional.values(), ZERO)
    execution = {
        "fill_count": len(fills),
        "order_count": len(order_rows),
        "pending_fee_count": sum(1 for row in fills if row.commission_status != "final"),
        "total_fees": sum((row.commission or ZERO for row in fills), ZERO),
        "fill_notional": sum((abs(row.quantity * row.price) for row in fills), ZERO),
        "average_fill_size": (sum((abs(row.quantity) for row in fills), ZERO) / len(fills)) if fills else ZERO,
        "rejected_order_count": sum(1 for row in order_rows if str(getattr(row, "lifecycle_state", "")).lower().endswith("rejected")),
        "venues": [
            {"venue": venue, "notional": value, "share": value / total_notional if total_notional else ZERO}
            for venue, value in sorted(venue_notional.items(), key=lambda item: item[1], reverse=True)
        ],
        "slippage_coverage": _coverage(fills, lambda row: row.signal_price is not None or row.arrival_midpoint is not None),
        "average_signal_slippage": _average_slippage(fills, "signal_price"),
        "average_arrival_slippage": _average_slippage(fills, "arrival_midpoint"),
    }
    report = {
        "schema_version": 1,
        "episode_definition": "flat_to_flat_position_lifecycle",
        "summary": summary,
        "episodes": [_episode_row(row) for row in reversed(rows)],
        "equity_curve": equity_curve,
        "strategies": strategies,
        "execution": execution,
        "risk": {
            "maximum_drawdown": summary["maximum_drawdown"],
            "maximum_losing_streak": _maximum_streak(rows, winning=False),
            "maximum_winning_streak": _maximum_streak(rows, winning=True),
            "average_duration_seconds": summary["average_duration_seconds"],
            "planned_risk_coverage": _coverage(rows, lambda row: row.planned_risk is not None and row.planned_risk > 0),
            "mae_coverage": _coverage(rows, lambda row: row.mae is not None),
            "mfe_coverage": _coverage(rows, lambda row: row.mfe is not None),
            "average_mae": _optional_average(row.mae for row in rows),
            "average_mfe": _optional_average(row.mfe for row in rows),
            "average_r_multiple": _optional_average(
                row.net_pnl / row.planned_risk for row in rows if row.planned_risk is not None and row.planned_risk > 0
            ),
        },
        "scope": {
            "first_opened_at": rows[0].opened_at if rows else None,
            "last_closed_at": rows[-1].closed_at if rows else None,
            "attribution_coverage": _coverage(rows, lambda row: bool(row.strategy_id)),
            "episode_count": len(rows),
        },
    }
    return json_safe(report)


def _open_state(execution: Execution, direction: int, quantity: Decimal, fee_per_unit: Decimal) -> _EpisodeState:
    state = _EpisodeState(
        account_id=execution.account_id,
        instrument=execution.instrument,
        direction=direction,
        opened_at=execution.source_event_time,
        strategy_id=execution.strategy_id,
        strategy_revision=execution.strategy_revision,
        run_id=execution.run_id,
        setup=execution.setup,
        planned_risk=execution.planned_risk,
    )
    _add_opening_fill(state, execution, quantity, fee_per_unit)
    return state


def _add_opening_fill(state: _EpisodeState, execution: Execution, quantity: Decimal, fee_per_unit: Decimal) -> None:
    state.position += quantity * state.direction
    state.entry_quantity += quantity
    state.entry_notional += quantity * execution.price
    state.fees += quantity * fee_per_unit
    state.peak_quantity = max(state.peak_quantity, abs(state.position))
    _append_identity(state, execution)


def _add_closing_fill(state: _EpisodeState, execution: Execution, quantity: Decimal, fee_per_unit: Decimal) -> None:
    state.exit_quantity += quantity
    state.exit_notional += quantity * execution.price
    state.fees += quantity * fee_per_unit
    state.gross_pnl += (execution.price - state.average_entry) * quantity * state.direction
    state.position -= quantity * state.direction
    state.exit_reason = execution.exit_reason or state.exit_reason
    _append_identity(state, execution)


def _append_identity(state: _EpisodeState, execution: Execution) -> None:
    if execution.execution_id and execution.execution_id not in state.execution_ids:
        state.execution_ids.append(execution.execution_id)
    if execution.broker_order_id and execution.broker_order_id not in state.order_ids:
        state.order_ids.append(execution.broker_order_id)


def _close_episode(state: _EpisodeState, closed_at: datetime, sequence: int) -> TradeEpisode:
    seed = ":".join((state.account_id, state.instrument.instrument_id, state.execution_ids[0], state.execution_ids[-1], str(sequence)))
    return TradeEpisode(
        episode_id=str(uuid5(NAMESPACE_URL, seed)),
        account_id=state.account_id,
        instrument=state.instrument,
        opened_at=state.opened_at,
        closed_at=closed_at,
        side="LONG" if state.direction > 0 else "SHORT",
        quantity=state.peak_quantity,
        entry_price=state.average_entry,
        exit_price=state.exit_notional / state.exit_quantity if state.exit_quantity else ZERO,
        gross_pnl=state.gross_pnl,
        fees=state.fees,
        net_pnl=state.gross_pnl - state.fees,
        strategy_id=state.strategy_id,
        strategy_revision=state.strategy_revision,
        run_id=state.run_id,
        setup=state.setup,
        exit_reason=state.exit_reason,
        planned_risk=state.planned_risk,
        execution_ids=tuple(state.execution_ids),
        order_ids=tuple(state.order_ids),
    )


def _summary(rows: list[TradeEpisode]) -> dict[str, Any]:
    wins = [row for row in rows if row.net_pnl > 0]
    losses = [row for row in rows if row.net_pnl < 0]
    gross_profit = sum((row.net_pnl for row in wins), ZERO)
    gross_loss = abs(sum((row.net_pnl for row in losses), ZERO))
    average_win = gross_profit / len(wins) if wins else ZERO
    average_loss = gross_loss / len(losses) if losses else ZERO
    win_rate = Decimal(len(wins)) / len(rows) if rows else ZERO
    loss_rate = Decimal(len(losses)) / len(rows) if rows else ZERO
    expectancy = win_rate * average_win - loss_rate * average_loss
    durations = [Decimal(str((row.closed_at - row.opened_at).total_seconds())) for row in rows]
    return {
        "episode_count": len(rows),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": win_rate,
        "net_pnl": sum((row.net_pnl for row in rows), ZERO),
        "gross_pnl": sum((row.gross_pnl for row in rows), ZERO),
        "total_fees": sum((row.fees for row in rows), ZERO),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": average_win / average_loss if average_loss else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expectancy": expectancy,
        "largest_win": max((row.net_pnl for row in wins), default=ZERO),
        "largest_loss": min((row.net_pnl for row in losses), default=ZERO),
        "average_duration_seconds": sum(durations, ZERO) / len(durations) if durations else ZERO,
        "maximum_drawdown": _maximum_drawdown(rows),
    }


def _equity_curve(rows: list[TradeEpisode]) -> list[dict[str, Any]]:
    cumulative = ZERO
    peak = ZERO
    result = []
    for row in rows:
        cumulative += row.net_pnl
        peak = max(peak, cumulative)
        result.append({"time": row.closed_at, "value": cumulative, "drawdown": cumulative - peak})
    return result


def _maximum_drawdown(rows: list[TradeEpisode]) -> Decimal:
    curve = _equity_curve(rows)
    return abs(min((row["drawdown"] for row in curve), default=ZERO))


def _group_episodes(rows: list[TradeEpisode]) -> dict[tuple[str, int], list[TradeEpisode]]:
    groups: dict[tuple[str, int], list[TradeEpisode]] = defaultdict(list)
    for row in rows:
        groups[(row.strategy_id or "Unattributed", row.strategy_revision)].append(row)
    return groups


def _group_summary(key: tuple[str, int], rows: list[TradeEpisode]) -> dict[str, Any]:
    result = _summary(rows)
    return {"strategy_id": key[0], "strategy_revision": key[1], **result}


def _episode_row(row: TradeEpisode) -> dict[str, Any]:
    duration = max(0.0, (row.closed_at - row.opened_at).total_seconds())
    risk_multiple = row.net_pnl / row.planned_risk if row.planned_risk is not None and row.planned_risk > 0 else None
    return {
        **json_safe(asdict(row)),
        "duration_seconds": duration,
        "risk_multiple": risk_multiple,
    }


def _maximum_streak(rows: list[TradeEpisode], *, winning: bool) -> int:
    longest = current = 0
    for row in rows:
        matches = row.net_pnl > 0 if winning else row.net_pnl < 0
        current = current + 1 if matches else 0
        longest = max(longest, current)
    return longest


def _coverage(rows: Iterable[Any], predicate: Any) -> Decimal:
    materialized = list(rows)
    return Decimal(sum(1 for row in materialized if predicate(row))) / len(materialized) if materialized else ZERO


def _optional_average(values: Iterable[Decimal | None]) -> Decimal | None:
    materialized = [value for value in values if value is not None]
    return sum(materialized, ZERO) / len(materialized) if materialized else None


def _average_slippage(fills: list[Execution], field_name: str) -> Decimal | None:
    values: list[Decimal] = []
    for row in fills:
        reference = getattr(row, field_name)
        if reference is None or reference <= 0:
            continue
        direction = Decimal("1") if row.side.upper() == "BUY" else Decimal("-1")
        values.append(((row.price - reference) / reference) * direction * Decimal("10000"))
    return sum(values, ZERO) / len(values) if values else None
