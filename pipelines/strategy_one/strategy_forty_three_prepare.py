"""Bounded producer extraction of one certified listing's completed history.

Reads persisted bars and execution notional, joins a dense completed-second
grid in Polars, and leaves missing prices missing. Backtest never calls this
producer or calculates its swing/movement features.
"""
import polars as pl

from src.backend.backtest_market_data import CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal
from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan
from pipelines.strategy_one.strategy_forty_three_publication import PublicationSource


def _frame(reader, query):
    batches = tuple(reader.iter_arrow_record_batches(query + " FORMAT ArrowStream"))
    if not batches:
        raise RuntimeError("Strategy 43 producer source returned no Arrow schema")
    return pl.from_arrow(__import__("pyarrow").Table.from_batches(batches))


def completed_seconds(*, source, market, identity, structure, reader):
    """Read only one pinned unit, with no raw SIP or latest-attempt fallback."""
    if (type(source) is not PublicationSource or type(market) is not CertifiedMarketDayPlan
            or type(identity) is not CertifiedIdentityPlan or type(structure) is not CertifiedV7IntervalPlan):
        raise ValueError("Strategy 43 preparation requires typed certified source plans")
    source.validate()
    units = {row.stage: row for row in market.units
             if row.ticker == source.ticker and row.session_date == source.session_date}
    if (source.build_id != market.build_id or market.sessions != (source.session_date,)
            or source.ticker not in market.tickers or source.ticker not in identity.tickers
            or identity.source_build_id != market.build_id or identity.market_token != market.token
            or identity.session_date != source.session_date or source.conid != identity.conid_for(source.ticker)
            or market.execution_interval.milliseconds != 100
            or source.source_market_token != market.token or source.source_v7_token != structure.token
            or structure.source_build_id != source.build_id or structure.session_date != source.session_date
            or source.ticker not in tuple(row.ticker for row in structure.coverage)
            or set(units) != {"bars", "technical", "broker_100ms"}
            or source.bars_attempt_id != units["bars"].attempt_id
            or source.liquidity_attempt_id != units["broker_100ms"].attempt_id):
        raise ValueError("Strategy 43 producer changed its market/identity/structure parent")
    common = (f"build_id={_literal(source.build_id)} AND session_date=toDate({_literal(source.session_date)}) "
              f"AND ticker={_literal(source.ticker)}")
    first, last = SESSION_OPEN_OFFSET_MS // 1000, (SESSION_OPEN_OFFSET_MS + source.session_end_ms) // 1000
    bars = _frame(reader, "SELECT (toInt64(bucket_index)+1)*1000-14400000 AS boundary_ms,"
        "price_valid=1 AND extremes_valid=1 AS observed,close_int/10000. AS close,"
        "low_int/10000. AS low,high_int/10000. AS high FROM arte.bars_v1 WHERE "
        + common + f" AND attempt_id=toUUID({_literal(source.bars_attempt_id)}) AND resolution_ms=1000 "
        + f"AND bucket_index>={first} AND bucket_index<{last} ORDER BY bucket_index")
    liquidity = _frame(reader,
        "SELECT (intDiv(toInt64(bucket_index),10)+1)*1000-14400000 AS boundary_ms,"
        "arraySum(arrayMap(x->x.2,arraySort(x->x.1,groupArray((bucket_index,execution_notional))))) "
        "AS dollar_volume FROM arte.liquidity_100ms_v1 WHERE "
        + common + f" AND attempt_id=toUUID({_literal(source.liquidity_attempt_id)}) "
        + f"AND bucket_index>={first * 10} AND bucket_index<{last * 10} "
        + "GROUP BY boundary_ms ORDER BY boundary_ms")
    for frame in (bars, liquidity):
        if (frame["boundary_ms"].null_count() or frame["boundary_ms"].n_unique() != frame.height
                or frame.filter((pl.col("boundary_ms") < 1000)
                    | (pl.col("boundary_ms") > source.session_end_ms)
                    | (pl.col("boundary_ms") % 1000 != 0)).height):
            raise RuntimeError("Strategy 43 extraction returned duplicate or out-of-scope seconds")
    return (pl.DataFrame({"boundary_ms": pl.int_range(1000, source.session_end_ms + 1000,
                                                    step=1000, eager=True)})
        .join(bars.with_columns(pl.col("boundary_ms").cast(pl.Int64)), on="boundary_ms", how="left", validate="1:1")
        .join(liquidity.with_columns(pl.col("boundary_ms").cast(pl.Int64)), on="boundary_ms", how="left", validate="1:1")
        .with_columns(pl.col("observed").fill_null(False).cast(pl.Boolean),
                      pl.col("dollar_volume").fill_null(0.).cast(pl.Float64)))
