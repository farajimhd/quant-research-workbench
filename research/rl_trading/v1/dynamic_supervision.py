"""Vectorized order and account supervision from the dynamic Phase 3 ledger.

All fields here are label-side hindsight, not causal model observations.
"""
from __future__ import annotations

import polars as pl

VERSION = "rl-dynamic-order-supervision-v1"


def order_labels(trajectory: pl.DataFrame, positions: pl.DataFrame,
                 initial_cash: float) -> pl.DataFrame:
    """Emit sized buy/sell targets and normalize each buy against available cash.

    Sales precede buys at a shared second in the teacher. The available cash
    is reconstructed from the post-action account and same-second debits.
    """
    schema = {"time_us":pl.Int64,"ticker":pl.String,"episode_uid":pl.String,
        "action":pl.String,"quantity":pl.Float64,"price":pl.Float64,
        "fee":pl.Float64,"cash_amount":pl.Float64,"allocation_weight":pl.Float64,
        "net_pnl":pl.Float64,"forced_terminal":pl.Boolean}
    if positions.is_empty():
        if int(trajectory["bought"].sum()) or int(trajectory["sold"].sum()):
            raise ValueError("Teacher counts do not match empty position ledger")
        return pl.DataFrame(schema=schema)
    if (positions.select("episode_uid").n_unique() != positions.height or
            positions.filter((pl.col("quantity") <= 0) |
                (pl.col("exit_us") < pl.col("entry_us"))).height):
        raise ValueError("Invalid or duplicated completed positions")
    buys = positions.select(pl.col("entry_us").alias("time_us"),"ticker","episode_uid",
        pl.lit("buy").alias("action"),"quantity",pl.col("entry_price").alias("price"),
        pl.col("entry_fee").alias("fee"),
        (pl.col("quantity")*pl.col("entry_price")+pl.col("entry_fee")).alias("cash_amount"),
        pl.col("net_pnl"),pl.col("forced_terminal"))
    sells = positions.select(pl.col("exit_us").alias("time_us"),"ticker","episode_uid",
        pl.lit("sell").alias("action"),"quantity",pl.col("exit_price").alias("price"),
        pl.col("exit_fee").alias("fee"),
        (pl.col("quantity")*pl.col("exit_price")-pl.col("exit_fee")).alias("cash_amount"),
        pl.col("net_pnl"),pl.col("forced_terminal"))
    debit = buys.group_by("time_us").agg(pl.col("cash_amount").sum().alias("buy_debit"))
    account = trajectory.select("time_us","cash","bought","sold").join(
        debit,on="time_us",how="left").with_columns(
            pl.col("buy_debit").fill_null(0.)).with_columns(
            (pl.col("cash")+pl.col("buy_debit")).alias("cash_before_buys"))
    buy_labels = buys.join(account.select("time_us","cash_before_buys"),
        on="time_us",how="left",validate="m:1").with_columns(
        (pl.col("cash_amount")/pl.col("cash_before_buys"))
            .alias("allocation_weight")).drop("cash_before_buys")
    sell_labels = sells.with_columns(pl.lit(None,dtype=pl.Float64).alias("allocation_weight"))
    labels = pl.concat((buy_labels,sell_labels)).sort("time_us","action","ticker")
    counts = labels.group_by("time_us").agg(
        (pl.col("action")=="buy").sum().alias("n_buy"),
        (pl.col("action")=="sell").sum().alias("n_sell"))
    checked = account.join(counts,on="time_us",how="left").with_columns(
        pl.col("n_buy").fill_null(0),pl.col("n_sell").fill_null(0))
    if (checked.filter((pl.col("bought") != pl.col("n_buy")) |
                       (pl.col("sold") != pl.col("n_sell"))).height or
            labels.filter(~pl.col("cash_amount").is_finite()).height or
            buy_labels.filter(~pl.col("allocation_weight").is_between(0.,1.+1e-8)).height or
                abs(float(positions["net_pnl"].sum())-
                    (float(trajectory["cash"][-1])+
                     (float(trajectory["profit_bank"][-1])
                      if "profit_bank" in trajectory.columns else 0.)-initial_cash)) > 1e-5):
        raise ValueError("Order labels do not reconcile to the teacher account")
    weights = buy_labels.group_by("time_us").agg(pl.col("allocation_weight").sum())
    if weights.filter(pl.col("allocation_weight") > 1.+1e-7).height:
        raise ValueError("Buy allocations exceed available cash")
    return labels.select(list(schema))
