"""V4 uncapped, cost-aware hindsight allocation on completed-second labels.

Candidate scoring and order sizing are vectorized. The account transition is
necessarily chronological because sale proceeds fund later entries. This is
an approximate teacher, not a proof of the globally best portfolio path.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from research.rl_trading.v1.costs import FixedOrderCosts


VERSION = "hindsight-phase3-dynamic-close-v4"


@dataclass(frozen=True)
class Config:
    initial_cash: float = 10_000.
    min_net_return: float = .01
    window_seconds: int = 30

    def validate(self):
        if (not np.isfinite(self.initial_cash) or self.initial_cash <= 0 or
                not np.isfinite(self.min_net_return) or self.min_net_return < 0 or
                type(self.window_seconds) is not int or not 0 <= self.window_seconds <= 300):
            raise ValueError("Invalid dynamic teacher configuration")


def _fees(quantity: np.ndarray, price: np.ndarray, model: FixedOrderCosts) -> np.ndarray:
    value = quantity * price
    return np.minimum(np.maximum(quantity * model.per_share, model.minimum_order),
                      value * model.maximum_fraction_of_value)


def _buy_for_budgets(prices: np.ndarray, budgets: np.ndarray,
                     model: FixedOrderCosts) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized monotone inversion of quantity*price + fixed commission."""
    lower = np.zeros_like(budgets)
    upper = budgets / prices
    for _ in range(48):
        mid = (lower + upper) / 2
        affordable = mid * prices + _fees(mid, prices, model) <= budgets
        lower = np.where(affordable, mid, lower)
        upper = np.where(affordable, upper, mid)
    return lower, _fees(lower, prices, model)


def future_first_scores(rows: pl.DataFrame, config: Config,
                        times=None) -> dict[int, float]:
    """One first-eligible score per episode prevents second-by-second repeats."""
    first = (rows.filter(pl.col("can_open") &
                         pl.col("open_value_per_dollar").is_finite() &
                         (pl.col("open_value_per_dollar") >= config.min_net_return))
        .sort("time_us").unique("episode_uid", keep="first", maintain_order=True)
        .group_by("time_us").agg(pl.col("open_value_per_dollar").max().alias("best")))
    seconds = (rows.select("time_us").unique(maintain_order=True).sort("time_us")
        if times is None else pl.DataFrame({"time_us":np.asarray(times,dtype=np.int64)}))
    scores = seconds.join(first,on="time_us",how="left").get_column("best").fill_null(0).to_numpy()
    future = np.zeros_like(scores)
    for offset in range(1, config.window_seconds + 1):
        future[:-offset] = np.maximum(future[:-offset], scores[offset:])
    return dict(zip(seconds["time_us"].to_list(), future.tolist()))


def run(rows: pl.DataFrame, config: Config = Config()) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """In-memory bounded canary for the streaming teacher."""
    config.validate()
    needed = {"time_us","ticker","listing_id","side","episode_uid","target_us",
              "close_price","can_close","entry_price","target_price","can_open",
              "open_value_per_share","open_value_per_dollar"}
    if not needed <= set(rows.columns):
        raise ValueError(f"Missing Phase 2 V7 fields: {sorted(needed-set(rows.columns))}")
    long = rows.filter(pl.col("side") == "long").sort("time_us","ticker")
    if long.is_empty() or long.select("time_us","ticker").n_unique() != long.height:
        raise ValueError("Empty or duplicate long decision grid")
    times = long["time_us"].unique(maintain_order=True).to_numpy()
    if len(times) > 1 and not np.all(np.diff(times) == 1_000_000):
        raise ValueError("Decision grid must advance by one second")
    counts = long.group_by("time_us").len()["len"]
    if counts.min() != counts.max():
        raise ValueError("Every decision second needs the same listing population")
    future = future_first_scores(long,config)
    return run_stream(times, long.partition_by("time_us",maintain_order=True),future,config)


def run_stream(times, snapshots, future: dict[int,float],
               config: Config = Config(), *, resume: dict | None = None,
               on_checkpoint=None) -> tuple[pl.DataFrame,pl.DataFrame,dict]:
    """Stream one full session with vectorized per-second sizing and fees.

    A candidate can be bought once per episode and exits at its completed-close
    target clock. Future first-eligible episodes in the next 30 seconds reserve
    part of cash. This heuristic is explicitly approximate.
    """
    config.validate()
    times = np.asarray(times,dtype=np.int64)
    if not len(times) or (len(times)>1 and not np.all(np.diff(times)==1_000_000)):
        raise ValueError("Invalid one-second timeline")
    model = FixedOrderCosts()
    held = pl.DataFrame(schema={"ticker":pl.String,"episode_uid":pl.String,
        "entry_us":pl.Int64,"target_us":pl.Int64,"entry_price":pl.Float64,
        "quantity":pl.Float64,"entry_fee":pl.Float64})
    cash = float(config.initial_cash) if resume is None else float(resume['cash'])
    realized = 0. if resume is None else float(resume['realized'])
    histories = [] if resume is None else list(resume['histories'])
    trades = [] if resume is None else list(resume['trades'])
    consumed = set() if resume is None else set(resume['consumed'])
    processed = 0 if resume is None else int(resume['processed'])
    if not 0 <= processed <= len(times) or len(histories)!=processed:
        raise ValueError('Invalid teacher restart cursor')
    if resume is not None:
        held=pl.DataFrame(resume['held'],schema=held.schema) if resume['held'] else held
    population = None
    for second in snapshots:
        if processed >= len(times):
            raise ValueError("Market stream exceeds declared timeline")
        now = int(second["time_us"][0])
        if now != int(times[processed]):
            raise ValueError("Missing or out-of-order market snapshot")
        if population is None:
            population = second.height
        if (second.height != population or second["ticker"].n_unique()!=population or
                not (second["side"] == "long").all()):
            raise ValueError("Incomplete long market snapshot")
        processed += 1
        terminal = now == int(times[-1])
        prices = second.select("ticker","close_price","can_close")
        marked = held.join(prices,on="ticker",how="left",validate="m:1")
        if marked.height and (marked["close_price"].null_count() or
                              (marked["close_price"] <= 0).any()):
            raise ValueError("Held listing lacks a valid completed close")
        if terminal and marked.height and not marked['can_close'].all():
            raise ValueError('Terminal holding lacks an executable close')
        sold = marked if terminal else marked.filter(
            (pl.col("target_us") <= now) & pl.col("can_close"))
        if sold.height:
            qty = sold["quantity"].to_numpy()
            price = sold["close_price"].to_numpy()
            fees = _fees(qty,price,model)
            proceeds = qty*price-fees
            pnl = proceeds-qty*sold["entry_price"].to_numpy()-sold["entry_fee"].to_numpy()
            cash += float(proceeds.sum())
            realized += float(pnl.sum())
            trades.extend(sold.with_columns(
                pl.lit(now).alias("exit_us"),pl.Series("exit_price",price),
                pl.Series("exit_fee",fees),pl.Series("net_pnl",pnl),
                pl.lit(terminal).alias("forced_terminal"))
                .drop("close_price","can_close").to_dicts())
            held = held.filter(~pl.col("episode_uid").is_in(sold["episode_uid"].to_list()))
        bought = 0
        reserve = 0.
        if not terminal and cash > 0:
            candidates = second.filter(
                pl.col("can_open") & pl.col("episode_uid").is_not_null() &
                pl.col("open_value_per_dollar").is_finite() &
                (pl.col("open_value_per_dollar") >= config.min_net_return) &
                (pl.col("target_us") >= now + 2_000_000) &
                (pl.col("entry_price") > 0) & (pl.col("target_price") > 0) &
                ~pl.col("ticker").is_in(held["ticker"].to_list()) &
                ~pl.col("episode_uid").is_in(held["episode_uid"].to_list()) &
                ~pl.col("episode_uid").is_in(list(consumed)))
            if candidates.height:
                scores = candidates["open_value_per_dollar"].to_numpy()
                ahead = future[now]
                reserve = cash * ahead/(float(scores.sum())+ahead) if ahead > 0 else 0.
                budget = (cash-reserve) * scores/scores.sum()
                price = candidates["entry_price"].to_numpy()
                target = candidates["target_price"].to_numpy()
                gross = candidates["open_value_per_share"].to_numpy()
                quantity, buy_fee = _buy_for_budgets(price,budget,model)
                sell_fee = _fees(quantity,target,model)
                expected = np.divide(quantity*gross-buy_fee-sell_fee,
                    quantity*price+buy_fee,out=np.full_like(quantity,-np.inf),
                    where=(quantity*price+buy_fee)>0)
                accept = (quantity>1e-8) & (expected>=config.min_net_return)
                if accept.any():
                    debit = quantity[accept]*price[accept]+buy_fee[accept]
                    cash -= float(debit.sum())
                    chosen = candidates.filter(pl.Series(accept))
                    new = chosen.select("ticker","episode_uid","target_us").with_columns(
                        pl.lit(now).alias("entry_us"),
                        pl.Series("entry_price",price[accept]),
                        pl.Series("quantity",quantity[accept]),
                        pl.Series("entry_fee",buy_fee[accept]))
                    held = pl.concat((held,new.select(held.columns).cast(held.schema)))
                    consumed.update(new['episode_uid'].to_list())
                    bought = new.height
        marked = held.join(prices,on="ticker",how="left",validate="m:1")
        equity = cash + (float((marked["quantity"]*marked["close_price"]).sum())
                         if marked.height else 0.)
        histories.append(dict(time_us=now,cash=cash,equity=equity,
            open_lots=held.height,bought=bought,sold=sold.height,
            reserved_for_future=reserve,realized_net_pnl=realized))
        if on_checkpoint is not None and (processed % 60 == 0 or processed == len(times)):
            on_checkpoint(dict(processed=processed,cash=cash,realized=realized,
                held=held.to_dicts(),histories=histories,trades=trades,
                consumed=sorted(consumed)))
    trajectory = pl.DataFrame(histories).with_columns(
        (pl.col("equity")/pl.col("equity").cum_max()
            .clip(lower_bound=config.initial_cash)-1).alias("drawdown"))
    positions = pl.DataFrame(trades) if trades else pl.DataFrame()
    report = dict(version=VERSION,optimality="approximate_normalized_window",
        initial_cash=config.initial_cash,terminal_cash=cash,
        net_profit=cash-config.initial_cash,
        max_drawdown=float(-trajectory["drawdown"].min()),
        buys=int(trajectory["bought"].sum()),sells=len(trades),
        max_open_lots=int(trajectory["open_lots"].max()),
        fee_model=model.plan(),min_net_return=config.min_net_return,
        window_seconds=config.window_seconds)
    if held.height or abs(realized-report["net_profit"]) > 1e-5:
        raise ValueError("Teacher failed terminal liquidation or P&L reconciliation")
    if processed != len(times):
        raise ValueError("Market stream ended before the declared timeline")
    return trajectory,positions,report
