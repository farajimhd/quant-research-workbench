from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from src.trading_runtime.domain import (
    Execution,
    OrderState,
    PositionState,
    RoundTripTrade,
    TradeEpisode,
    json_safe,
)


ZERO = Decimal("0")
POSITION_EPSILON = Decimal("0.000001")
NEW_YORK = ZoneInfo("America/New_York")
PNL_CANDLE_TIMEFRAMES = ("30m", "1h", "1d", "1M")


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
    open_cost_notional: Decimal = ZERO
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

    @property
    def average_open_cost(self) -> Decimal:
        return self.open_cost_notional / abs(self.position) if self.position else ZERO


def derive_trade_episodes(executions: Iterable[Execution]) -> list[TradeEpisode]:
    """Derive deterministic flat-to-flat position episodes from executions.

    A scale-in or partial exit stays inside one episode. A reversal closes the
    prior episode and opens a new one with the unmatched quantity. This makes
    win rate count strategy decisions instead of FIFO fragments.
    """

    episodes, _states = _derive_episode_states(executions)
    return episodes


def derive_position_lifecycles(
    executions: Iterable[Execution],
    orders: Iterable[OrderState],
    positions: Iterable[PositionState] = (),
) -> list[dict[str, Any]]:
    """Project one operator position per flat-to-flat lifecycle.

    Broker positions remain the current-quantity authority and FIFO round trips
    remain immutable realization evidence.  This projection supplies the
    missing lifecycle relation used by Position Manager: a scale-in, partial
    exit, or protective-order replacement stays under one position until its
    executed quantity returns to zero.
    """

    episodes, open_states = _derive_episode_states(executions)
    rows = [_closed_lifecycle_row(row) for row in episodes]
    rows.extend(_open_lifecycle_row(key, state) for key, state in open_states.items())
    rows.extend(_snapshot_only_lifecycle_rows(rows, positions))
    _attach_lifecycle_orders(rows, orders)
    rows.sort(
        key=lambda row: (
            str(row.get("opened_at") or ""),
            str(row.get("lifecycle_id") or ""),
        ),
        reverse=True,
    )
    return json_safe(rows)


def _derive_episode_states(
    executions: Iterable[Execution],
) -> tuple[list[TradeEpisode], dict[tuple[str, str], _EpisodeState]]:
    states: dict[tuple[str, str], _EpisodeState] = {}
    episodes: list[TradeEpisode] = []
    sequences: dict[tuple[str, str], int] = defaultdict(int)
    ordered = sorted(executions, key=lambda row: (row.source_event_time, row.execution_id))
    for execution in ordered:
        quantity = abs(execution.quantity)
        if quantity <= POSITION_EPSILON:
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
        if remaining <= POSITION_EPSILON:
            remaining = ZERO
        if state.position == 0:
            sequences[key] += 1
            episodes.append(_close_episode(state, execution.source_event_time, sequences[key]))
            del states[key]
        if remaining > 0:
            states[key] = _open_state(execution, direction, remaining, fee_per_unit)
    return episodes, states


def _closed_lifecycle_row(row: TradeEpisode) -> dict[str, Any]:
    return {
        **asdict(row),
        "lifecycle_id": row.episode_id,
        "status": "closed",
        "current_quantity": ZERO,
        "peak_quantity": row.quantity,
    }


def _open_lifecycle_row(
    key: tuple[str, str], state: _EpisodeState
) -> dict[str, Any]:
    first_execution_id = state.execution_ids[0] if state.execution_ids else "open"
    lifecycle_id = str(
        uuid5(
            NAMESPACE_URL,
            ":".join((key[0], key[1], first_execution_id, "open")),
        )
    )
    return {
        "lifecycle_id": lifecycle_id,
        "episode_id": lifecycle_id,
        "account_id": state.account_id,
        "instrument": asdict(state.instrument),
        "opened_at": state.opened_at,
        "closed_at": None,
        "status": "open",
        "side": "LONG" if state.direction > 0 else "SHORT",
        "quantity": state.peak_quantity,
        "peak_quantity": state.peak_quantity,
        "current_quantity": abs(state.position),
        "entry_price": state.average_entry,
        "exit_price": (
            state.exit_notional / state.exit_quantity
            if state.exit_quantity
            else None
        ),
        "gross_pnl": state.gross_pnl,
        "fees": state.fees,
        "net_pnl": state.gross_pnl - state.fees,
        "strategy_id": state.strategy_id,
        "strategy_revision": state.strategy_revision,
        "run_id": state.run_id,
        "setup": state.setup,
        "exit_reason": state.exit_reason,
        "planned_risk": state.planned_risk,
        "execution_ids": tuple(state.execution_ids),
        "order_ids": tuple(state.order_ids),
        "opened_at_known": True,
    }


def _snapshot_only_lifecycle_rows(
    lifecycle_rows: list[dict[str, Any]],
    positions: Iterable[PositionState],
) -> list[dict[str, Any]]:
    open_keys = {
        (
            str(row.get("account_id") or ""),
            _instrument_id(row.get("instrument")),
        )
        for row in lifecycle_rows
        if row.get("status") == "open"
    }
    result: list[dict[str, Any]] = []
    for position in positions:
        if position.quantity == 0:
            continue
        key = (position.account_id, position.instrument.instrument_id)
        if key in open_keys:
            continue
        lifecycle_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{position.account_id}:{position.instrument.instrument_id}:broker-snapshot",
            )
        )
        result.append(
            {
                "lifecycle_id": lifecycle_id,
                "episode_id": lifecycle_id,
                "account_id": position.account_id,
                "instrument": asdict(position.instrument),
                "opened_at": position.source_event_time,
                "closed_at": None,
                "status": "open",
                "side": "LONG" if position.quantity > 0 else "SHORT",
                "quantity": abs(position.quantity),
                "peak_quantity": abs(position.quantity),
                "current_quantity": abs(position.quantity),
                "entry_price": position.average_price,
                "exit_price": None,
                "gross_pnl": position.realized_pnl,
                "fees": ZERO,
                "net_pnl": position.realized_pnl,
                "strategy_id": "",
                "strategy_revision": 0,
                "run_id": "",
                "setup": "",
                "exit_reason": "",
                "planned_risk": None,
                "execution_ids": (),
                "order_ids": (),
                "opened_at_known": False,
            }
        )
    return result


def _instrument_id(instrument: Any) -> str:
    if isinstance(instrument, dict):
        return str(instrument.get("instrument_id") or "")
    return str(getattr(instrument, "instrument_id", "") or "")


def _attach_lifecycle_orders(
    rows: list[dict[str, Any]], orders: Iterable[OrderState]
) -> None:
    order_rows = list(orders)
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        instrument_id = _instrument_id(row.get("instrument"))
        by_key[(str(row.get("account_id") or ""), instrument_id)].append(row)
        row["order_ids"] = list(row.get("order_ids") or [])
    for candidates in by_key.values():
        candidates.sort(key=lambda row: row["opened_at"])

    lower_bounds: dict[int, datetime | None] = {}
    for candidates in by_key.values():
        previous_closed_at = None
        for candidate in candidates:
            lower_bounds[id(candidate)] = previous_closed_at
            if candidate.get("closed_at") is not None:
                previous_closed_at = candidate["closed_at"]

    for order in sorted(
        order_rows,
        key=lambda row: (
            row.source_event_time,
            row.broker_order_id or row.client_order_id,
        ),
    ):
        order_id = order.broker_order_id or order.client_order_id
        if not order_id:
            continue
        candidates = by_key.get(
            (order.account_id, order.instrument.instrument_id), []
        )
        if not candidates:
            continue
        exact = [row for row in candidates if order_id in row["order_ids"]]
        if exact:
            continue
        selected = None
        for candidate in candidates:
            closed_at = candidate.get("closed_at")
            if closed_at is None or order.source_event_time <= closed_at:
                selected = candidate
                break
        # Numeric broker order identifiers can be reused by later simulator or
        # broker sessions.  An order observed after the last completed
        # flat-to-flat lifecycle is not evidence for that earlier position.
        if selected is not None:
            selected["order_ids"].append(order_id)
    for row in rows:
        row["order_ids"] = tuple(dict.fromkeys(row["order_ids"]))
        linked_orders = [
            order
            for order in order_rows
            if (order.broker_order_id or order.client_order_id) in row["order_ids"]
            and _order_within_lifecycle(order, row, lower_bounds.get(id(row)))
        ]
        requested_times = [
            requested_at
            for order in linked_orders
            if (requested_at := _order_requested_at(order)) is not None
        ]
        row["requested_at"] = min(requested_times) if requested_times else None
        row["requested_at_known"] = bool(requested_times)


def _order_within_lifecycle(
    order: OrderState,
    row: dict[str, Any],
    previous_closed_at: datetime | None,
) -> bool:
    """Reject reused order identifiers whose event time is outside a lifecycle."""

    closed_at = row.get("closed_at")
    return (
        (previous_closed_at is None or order.source_event_time > previous_closed_at)
        and (closed_at is None or order.source_event_time <= closed_at)
    )


def _order_requested_at(order: OrderState) -> datetime | None:
    for key in ("submitted_at", "created_at", "order_time", "orderTime"):
        raw_value = order.raw.get(key)
        if isinstance(raw_value, datetime) and raw_value.tzinfo is not None:
            return raw_value
        if isinstance(raw_value, str) and raw_value.strip():
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is not None:
                return parsed
    return None


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
        "schema_version": 2,
        "episode_definition": "flat_to_flat_position_lifecycle",
        "summary": summary,
        "episodes": [_episode_row(row) for row in reversed(rows)],
        "equity_curve": equity_curve,
        "pnl_candles": {timeframe: _pnl_candles(rows, timeframe) for timeframe in PNL_CANDLE_TIMEFRAMES},
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
    state.open_cost_notional += quantity * execution.price
    state.fees += quantity * fee_per_unit
    state.peak_quantity = max(state.peak_quantity, abs(state.position))
    _append_identity(state, execution)


def _add_closing_fill(state: _EpisodeState, execution: Execution, quantity: Decimal, fee_per_unit: Decimal) -> None:
    average_open_cost = state.average_open_cost
    state.exit_quantity += quantity
    state.exit_notional += quantity * execution.price
    state.fees += quantity * fee_per_unit
    state.gross_pnl += (execution.price - average_open_cost) * quantity * state.direction
    state.open_cost_notional -= average_open_cost * quantity
    state.position -= quantity * state.direction
    if abs(state.position) <= POSITION_EPSILON:
        state.position = ZERO
        state.open_cost_notional = ZERO
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


def _pnl_candles(rows: list[TradeEpisode], timeframe: str) -> list[dict[str, Any]]:
    """Aggregate cumulative realized net P&L into exchange-time OHLC buckets.

    The candle opens at cumulative P&L immediately before the first episode in
    its bucket. High and low include that opening value and every subsequent
    closed-episode update. Empty market-time buckets are intentionally omitted
    so overnight and non-trading gaps do not create synthetic activity.
    """

    result: list[dict[str, Any]] = []
    cumulative = ZERO
    current: dict[str, Any] | None = None
    for row in rows:
        bucket_start = _pnl_bucket_start(row.closed_at, timeframe)
        if current is None or current["bucket_start"] != bucket_start:
            if current is not None:
                result.append(current)
            current = {
                "bucket_start": bucket_start,
                "bucket_end": _pnl_bucket_end(bucket_start, timeframe),
                "open": cumulative,
                "high": cumulative,
                "low": cumulative,
                "close": cumulative,
                "net_change": ZERO,
                "episode_count": 0,
            }
        cumulative += row.net_pnl
        current["high"] = max(current["high"], cumulative)
        current["low"] = min(current["low"], cumulative)
        current["close"] = cumulative
        current["net_change"] += row.net_pnl
        current["episode_count"] += 1
    if current is not None:
        result.append(current)
    return result


def _pnl_bucket_start(value: datetime, timeframe: str) -> datetime:
    local = value.astimezone(NEW_YORK)
    if timeframe == "30m":
        return local.replace(minute=(local.minute // 30) * 30, second=0, microsecond=0)
    if timeframe == "1h":
        return local.replace(minute=0, second=0, microsecond=0)
    if timeframe == "1d":
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == "1M":
        return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"unsupported P&L candle timeframe: {timeframe}")


def _pnl_bucket_end(start: datetime, timeframe: str) -> datetime:
    if timeframe == "30m":
        return start + timedelta(minutes=30)
    if timeframe == "1h":
        return start + timedelta(hours=1)
    if timeframe == "1d":
        return start + timedelta(days=1)
    if timeframe == "1M":
        return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    raise ValueError(f"unsupported P&L candle timeframe: {timeframe}")


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
