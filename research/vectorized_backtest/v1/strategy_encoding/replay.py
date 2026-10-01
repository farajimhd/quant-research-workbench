"""Causal replay on resident sparse data, vectorized across listing slots.

The only market-time Python loop advances clocks. No ticker/row callback decides
actions or fills. Orders submitted inside a coarse broker interval first become
eligible for the next complete interval, never its already elapsed liquidity.
"""

from __future__ import annotations

from collections import deque
from math import gcd, isfinite
from time import perf_counter

import polars as pl

from .clickhouse import PreparedSession, _clocks
from .config import Broker
from .core import CompiledStrategy, EncodingError

STATE = {
    "listing_id": pl.String,
    "quantity": pl.Int64,
    "book_cost": pl.Float64,
    "side": pl.Int64,
    "remaining": pl.Int64,
    "submitted_us": pl.Int64,
    "stop": pl.Float64,
    "target": pl.Float64,
    "mark": pl.Float64,
}


def _events(frame, step_us):
    """Coalesce completed updates into the next replay boundary, not before it.

    Each field keeps its latest nonnull update, retaining its own source clock.
    100ms inputs can therefore feed a 1s strategy without losing as-of evidence.
    """
    if frame.is_empty():
        return {}
    keys = ["listing_id", "event_us"]
    columns = [name for name in frame.columns if name not in {"listing_id", "time_us"}]
    projected = frame.sort("time_us").with_columns(
        (((pl.col("time_us") + step_us - 1) // step_us) * step_us).alias("event_us")
    )
    projected = projected.group_by(keys).agg(
        *[pl.col(name).drop_nulls().last() for name in columns]
    )
    return {
        int(key[0]): value.drop("event_us")
        for key, value in projected.partition_by("event_us", as_dict=True).items()
    }


def _update(latest, update):
    if latest is None:
        return update
    joined = latest.join(update, on="listing_id", how="full", coalesce=True)
    return joined.select(
        "listing_id",
        *[
            pl.coalesce(pl.col(name + "_right"), pl.col(name)).alias(name)
            for name in latest.columns
            if name != "listing_id"
        ],
    )


def _fill(state, evidence, cash, broker, clock, interval_us):
    """One broker boundary: capacity, pro-rata shared cash, cost and quantity."""
    if state.is_empty():
        return state, cash, 0.0, 0.0, 0, pl.DataFrame()
    rows = (
        state.lazy()
        .join(evidence.lazy(), on="listing_id", how="left")
        .with_columns(
            pl.col("valid").fill_null(False),
            pl.col("volume").fill_null(0.0),
        )
        .with_columns(
            pl.when(pl.col("valid"))
            .then(pl.col("close"))
            .otherwise(pl.col("mark"))
            .alias("mark"),
            pl.when(
                pl.col("valid")
                & (pl.col("submitted_us") <= clock - interval_us)
                & (pl.col("volume") > 0)
            )
            .then((pl.col("volume") * broker.participation).floor())
            .otherwise(0)
            .cast(pl.Int64)
            .alias("capacity"),
            pl.when(pl.col("quantity") > 0)
            .then(pl.col("book_cost") / pl.col("quantity"))
            .otherwise(0.0)
            .alias("average"),
        )
        .with_columns(
            pl.when(pl.col("side") == -1)
            .then(pl.min_horizontal("quantity", "remaining", "capacity"))
            .otherwise(0)
            .alias("sell"),
            pl.when(pl.col("side") == 1)
            .then(pl.min_horizontal("remaining", "capacity"))
            .otherwise(0)
            .alias("candidate"),
        )
        .with_columns(
            (
                pl.col("sell")
                * pl.col("vwap").fill_null(0)
                * (1 - broker.fee_bps / 10000)
            ).alias("sale"),
            (
                pl.col("candidate")
                * pl.col("vwap").fill_null(0)
                * (1 + broker.fee_bps / 10000)
            ).alias("need"),
        )
        .collect()
    )
    sale, need = rows.select(pl.col("sale").sum(), pl.col("need").sum()).row(0)
    scale = min(1.0, (cash + sale) / need) if need > 0 else 1.0
    rows = (
        rows.lazy()
        .with_columns((pl.col("candidate") * scale).floor().cast(pl.Int64).alias("buy"))
        .with_columns(
            (pl.col("quantity") + pl.col("buy") - pl.col("sell")).alias("new_quantity"),
            (pl.col("remaining") - pl.col("buy") - pl.col("sell")).alias(
                "new_remaining"
            ),
            (
                pl.col("book_cost")
                + pl.col("buy")
                * pl.col("vwap").fill_null(0)
                * (1 + broker.fee_bps / 10000)
                - pl.col("sell") * pl.col("average")
            ).alias("new_cost"),
            (
                pl.col("sale")
                - pl.col("buy")
                * pl.col("vwap").fill_null(0)
                * (1 + broker.fee_bps / 10000)
            ).alias("cash_delta"),
            (
                pl.col("sell")
                * (
                    pl.col("vwap").fill_null(0) * (1 - broker.fee_bps / 10000)
                    - pl.col("average")
                )
            ).alias("realized_delta"),
            (
                (pl.col("buy") + pl.col("sell"))
                * pl.col("vwap").fill_null(0)
                * broker.fee_bps
                / 10000
            ).alias("fee"),
        )
        .with_columns(
            pl.when((pl.col("quantity") == 0) & (pl.col("buy") > 0))
            .then(pl.col("vwap") * (1 - broker.initial_stop_return))
            .otherwise(pl.col("stop"))
            .alias("stop"),
            pl.when((pl.col("quantity") == 0) & (pl.col("buy") > 0))
            .then(pl.col("vwap") * (1 + broker.initial_target_return))
            .otherwise(pl.col("target"))
            .alias("target"),
        )
        .collect()
    )
    delta, realized, fee, filled = rows.select(
        pl.col("cash_delta").sum(),
        pl.col("realized_delta").sum(),
        pl.col("fee").sum(),
        (pl.col("buy") + pl.col("sell")).sum(),
    ).row(0)
    cash += delta
    if cash < -1e-7:
        raise AssertionError("Pro-rata allocation exceeded shared cash")
    next_state = rows.select(
        "listing_id",
        pl.col("new_quantity").alias("quantity"),
        pl.col("new_cost").alias("book_cost"),
        pl.col("side"),
        pl.col("new_remaining").alias("remaining"),
        "submitted_us",
        "stop",
        "target",
        "mark",
        pl.col("valid").alias("__valid_price"),
    )
    # Broker-owned close-based protection continues independently of strategy
    # inference. A hit queues a sale for a later complete broker interval.
    next_state = next_state.with_columns(
        (
            pl.col("__valid_price")
            & (pl.col("quantity") > 0)
            & (pl.col("side") != -1)
            & (
                (pl.col("mark") <= pl.col("stop"))
                | (pl.col("mark") >= pl.col("target"))
            )
        ).alias("protect")
    )
    next_state = next_state.with_columns(
        pl.when(pl.col("protect")).then(-1).otherwise(pl.col("side")).alias("side"),
        pl.when(pl.col("protect"))
        .then(pl.col("quantity"))
        .otherwise(pl.col("remaining"))
        .alias("remaining"),
        pl.when(pl.col("protect"))
        .then(clock)
        .otherwise(pl.col("submitted_us"))
        .alias("submitted_us"),
    ).drop("protect", "__valid_price")
    next_state = next_state.filter((pl.col("quantity") > 0) | (pl.col("remaining") > 0))
    evidence = rows.filter((pl.col("buy") + pl.col("sell")) > 0).select(
        "listing_id",
        pl.lit(clock).alias("time_us"),
        "submitted_us",
        "buy",
        "sell",
        "capacity",
        "vwap",
        "cash_delta",
        "fee",
    )
    return next_state, cash, realized, fee, filled, evidence


def _actions(state, proposals, reference, cash, clock):
    """Resolve simultaneous proposals deterministically: exits before entries.

    Multiple actions of the same kind are rejected by evaluate(); arbitrary
    conflicts are not silently resolved by instruction order.
    """
    rows = (
        proposals.join(state, on="listing_id", how="left")
        .join(reference, on="listing_id", how="left")
        .with_columns(
            pl.col("quantity", "side", "remaining", "submitted_us").fill_null(0),
            pl.col("book_cost", "stop", "target", "mark").fill_null(0.0),
        )
    )
    names = {
        kind: next(
            (name for name in proposals.columns if name.endswith("_" + kind)), None
        )
        for kind in ("enter", "exit", "add_position", "set_stop", "set_target")
    }
    entry = pl.col(names["enter"]) if names["enter"] else pl.lit(False)
    exit_ = pl.col(names["exit"]) if names["exit"] else pl.lit(False)
    add = pl.col(names["add_position"]) if names["add_position"] else pl.lit(False)
    ref_ok = pl.col("ref_valid").fill_null(False) & (pl.col("ref_price") > 0)
    can_buy = (
        ref_ok
        & (pl.col("remaining") == 0)
        & (((pl.col("quantity") == 0) & entry) | ((pl.col("quantity") > 0) & add))
    )
    fraction = (
        pl.when(pl.col("quantity") == 0)
        .then(pl.col(names["enter"] + "_value"))
        .otherwise(pl.col(names["add_position"] + "_value"))
        if names["enter"] and names["add_position"]
        else pl.col((names["enter"] or names["add_position"]) + "_value")
        if names["enter"] or names["add_position"]
        else pl.lit(0.0)
    )
    rows = rows.with_columns(
        (exit_ & (pl.col("quantity") > 0) & (pl.col("side") != -1)).alias(
            "sell_request"
        ),
        can_buy.alias("buy_request"),
        pl.when(ref_ok)
        .then((cash * fraction / pl.col("ref_price")).floor())
        .otherwise(0)
        .cast(pl.Int64)
        .fill_null(0)
        .alias("requested"),
    )
    for kind, column in (("set_stop", "stop"), ("set_target", "target")):
        if names[kind]:
            rows = rows.with_columns(
                pl.when(pl.col(names[kind]) & (pl.col("quantity") > 0))
                .then(pl.col(names[kind] + "_value"))
                .otherwise(pl.col(column))
                .alias(column)
            )
    rows = rows.with_columns(
        pl.when(pl.col("sell_request"))
        .then(-1)
        .when(pl.col("buy_request"))
        .then(1)
        .otherwise(pl.col("side"))
        .alias("side"),
        pl.when(pl.col("sell_request"))
        .then(pl.col("quantity"))
        .when(pl.col("buy_request"))
        .then(pl.col("requested"))
        .otherwise(pl.col("remaining"))
        .alias("remaining"),
        pl.when(pl.col("sell_request") | pl.col("buy_request"))
        .then(clock)
        .otherwise(pl.col("submitted_us"))
        .alias("submitted_us"),
        pl.when((pl.col("quantity") == 0) & ref_ok)
        .then(pl.col("ref_price"))
        .otherwise(pl.col("mark"))
        .alias("mark"),
    )
    return (
        rows.filter((pl.col("quantity") > 0) | (pl.col("remaining") > 0))
        .select(*STATE)
        .sort("listing_id")
    )


def evaluate(
    compiled: CompiledStrategy,
    prepared: PreparedSession,
    broker: Broker | None = None,
    *,
    keep_fills=False,
    progress=None,
):
    """Return a scalar objective and accounting evidence without network/file I/O."""
    broker = Broker() if broker is None else broker
    started = perf_counter()
    config = prepared.config
    day, origin_us, start_us, end_us = _clocks(config)
    if not all(isfinite(x) for x in vars(broker).values()) or not (
        broker.initial_cash > 0
        and 0 <= broker.participation <= 1
        and 0 <= broker.fee_bps < 10000
        and 0 < broker.initial_stop_return < 1
        and broker.initial_target_return > 0
        and broker.drawdown_weight >= 0
    ):
        raise EncodingError("Invalid broker/objective model")
    prepared_inputs = {feature.label: feature for feature in prepared.dependencies}
    if any(
        feature.source != "state" and prepared_inputs.get(feature.label) != feature
        for feature in compiled.dependencies
    ):
        raise EncodingError(
            "Candidate references data outside the prepared search envelope"
        )
    action_names = [expr.meta.output_name() for expr in compiled.expressions]
    for kind in ("enter", "exit", "add_position", "set_stop", "set_target"):
        if sum(name.endswith("_" + kind) for name in action_names) > 1:
            raise EncodingError(
                "One proposal per action kind is supported; combine predicates explicitly"
            )
    step_us = gcd(config.strategy_ms, config.broker_ms) * 1000
    feeds = {
        resolution: _events(data, step_us)
        for resolution, data in prepared.features.items()
    }
    latest = {}
    b = prepared.broker_bars.sort("time_us")
    broker_rows = {
        int(k[0]): v.drop("time_us")
        for k, v in b.partition_by("time_us", as_dict=True).items()
    }
    broker_feed = _events(
        b.select(
            "listing_id",
            "time_us",
            pl.col("close").alias("ref_price"),
            pl.col("valid").alias("ref_valid"),
        ),
        step_us,
    )
    latest_reference = None
    state = pl.DataFrame(schema=STATE)
    cash = broker.initial_cash
    realized = fees = 0.0
    filled = decisions = broker_steps = 0
    accounts, fill_batches = [], []
    history = deque()
    # Temporal operators are bounded observation windows on the strategy grid.
    # Retain a bounded prefix, including pre-admission values for warm-up; action
    # masks below enforce admission and the requested trading start.
    history_limit = max(2, compiled.history_rows)
    first = max(
        origin_us + 14_400_000_000, start_us - config.warmup_seconds * 1_000_000
    )
    first = ((first + step_us - 1) // step_us) * step_us
    universe = prepared.watchlist.select("listing_id", "admitted_at_us")
    empty_broker = b.head(0).drop("time_us")
    for clock in range(first, end_us + 1, step_us):
        for resolution, events in feeds.items():
            if clock in events:
                latest[resolution] = _update(latest.get(resolution), events[clock])
        if clock in broker_feed:
            latest_reference = _update(latest_reference, broker_feed[clock])
        if clock > start_us and (clock - origin_us) % (config.broker_ms * 1000) == 0:
            state, cash, pnl, fee, n, evidence = _fill(
                state,
                broker_rows.get(clock, empty_broker),
                cash,
                broker,
                clock,
                config.broker_ms * 1000,
            )
            realized += pnl
            fees += fee
            filled += n
            broker_steps += 1
            if keep_fills and not evidence.is_empty():
                fill_batches.append(evidence)
        if (clock - origin_us) % (
            config.strategy_ms * 1000
        ) == 0 and not universe.is_empty():
            observation = universe.with_columns(
                pl.lit(clock).cast(pl.Int64).alias("time_us"),
                pl.lit(str(day)).alias("session_date"),
                (pl.col("admitted_at_us") <= clock).alias("watchlisted"),
            )
            for data in latest.values():
                observation = observation.join(data, on="listing_id", how="left")
            # Empty lanes still expose their declared schema as null, never as a
            # fabricated completed observation. Future admission cannot trade.
            for name in compiled.required_columns:
                if (
                    name not in observation.columns
                    and name not in STATE
                    and name not in {"cash", "average_entry"}
                ):
                    observation = observation.with_columns(
                        pl.lit(None)
                        .cast(pl.Int64 if "_at_" in name else pl.Float64)
                        .alias(name)
                    )
            observation = (
                observation.join(state, on="listing_id", how="left")
                .with_columns(
                    pl.col("quantity", "remaining").fill_null(0),
                    pl.col("stop", "target").fill_null(0.0),
                    pl.lit(cash).alias("cash"),
                )
                .with_columns(
                    pl.when(pl.col("quantity") > 0)
                    .then(pl.col("book_cost") / pl.col("quantity"))
                    .otherwise(0)
                    .alias("average_entry")
                )
            )
            if compiled.has_temporal:
                history.append(observation.select(compiled.required_columns))
                while len(history) > history_limit:
                    history.popleft()
                inputs = pl.concat(history, how="vertical_relaxed")
                if inputs.estimated_size() > config.max_prepared_gib * 1024**3:
                    raise MemoryError(
                        "Temporal observation history exceeds memory guard"
                    )
            else:
                inputs = observation
            if clock >= start_us and clock < end_us:
                proposals = (
                    compiled.evaluate(inputs)
                    .filter(pl.col("time_us") == clock)
                    .join(
                        universe.filter(pl.col("admitted_at_us") <= clock).select(
                            "listing_id"
                        ),
                        on="listing_id",
                        how="inner",
                    )
                )
                reference = (
                    latest_reference
                    if latest_reference is not None
                    else pl.DataFrame(
                        schema={
                            "listing_id": pl.String,
                            "ref_price": pl.Float64,
                            "ref_valid": pl.Boolean,
                        }
                    )
                )
                state = _actions(state, proposals, reference, cash, clock)
                decisions += 1
        if clock >= start_us:
            value = (
                state.select((pl.col("quantity") * pl.col("mark")).sum()).item() or 0.0
            )
            accounts.append((clock, cash, realized, cash + value, value))
            if progress and (clock - start_us) % (3600 * 1_000_000) == 0:
                progress(
                    {
                        "stage": "replay",
                        "completed_seconds": (clock - start_us) // 1_000_000,
                        "total_seconds": (end_us - start_us) // 1_000_000,
                    }
                )
    account = pl.DataFrame(
        accounts,
        schema=["time_us", "cash", "realized_pnl", "equity", "market_value"],
        orient="row",
    )
    account = account.with_columns(
        pl.col("equity").cum_max().clip(lower_bound=broker.initial_cash).alias("peak")
    )
    ret, drawdown = account.select(
        (pl.col("equity").last() / broker.initial_cash - 1).alias("return"),
        (1 - pl.col("equity") / pl.col("peak")).max().alias("drawdown"),
    ).row(0)
    return {
        "objective": ret - broker.drawdown_weight * drawdown,
        "return_fraction": ret,
        "max_drawdown": drawdown,
        "wall_seconds": perf_counter() - started,
        "strategy_calls": decisions,
        "broker_slots": broker_steps,
        "filled_shares": filled,
        "fees": fees,
        "open_positions": state.filter(pl.col("quantity") > 0).height,
        "pending_orders": state.filter(pl.col("remaining") > 0).height,
        "accounts": account,
        "state": state,
        "fills": pl.concat(fill_batches) if fill_batches else pl.DataFrame(),
        "program_fingerprint": compiled.fingerprint,
        "source_key": prepared.source_key,
    }
