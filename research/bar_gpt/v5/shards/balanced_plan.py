"""Freeze the complete ordinal summary before dispatching extraction workers.

The plan stores metadata only, in bounded Parquet partitions. Canonical market
events remain in ClickHouse. Liquidity is an explicit event-density proxy:
top-quartile ticker-year mean event count versus the remaining ticker-years.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import numpy as np
import polars as pl

from . import source
from .contract import digest, file_hash, seed_for, write_json

VERSION = "bargpt-ordinal-balanced-64-v5"
YEARS = tuple(range(2019, 2026))


def freeze(root, endpoint=None, progress=None, tickers=()):
    """Freeze all selected ticker-day ranges and deterministic execution order.

    root is an operational dataset directory. tickers is empty for the full
    universe, or an explicit pilot-only tuple. A sealed plan is immutable and
    reused on restart. Each partition is bounded to one year and hash bucket.
    """
    root = Path(root)
    output = root / "plan"
    output.mkdir(parents=True, exist_ok=True)
    final = output / "manifest.json"
    if final.exists():
        result = json.loads(final.read_text())
        if result["hash"] != digest({k: v for k, v in result.items() if k != "hash"}):
            raise ValueError("Frozen plan seal changed")
        return result
    predicate = (
        ""
        if not tickers
        else " AND ticker IN (" + ",".join(source.literal(t) for t in tickers) + ")"
    )
    c = source.client(endpoint)
    statistics = []
    try:
        for year in YEARS:
            statistics.extend(
                source.rows(
                    c,
                    f"""SELECT ticker,toUInt16({year}) year,
              count() days,sum(event_count) events,min(first_ordinal) first_ordinal,
              max(next_ordinal) next_ordinal,toUInt8(cityHash64(ticker)%16) bucket
              FROM market_sip_compact.events_ticker_day_index FINAL
              WHERE source_date>='{year}-01-01' AND source_date<'{year+1}-01-01'
              {predicate} GROUP BY ticker ORDER BY ticker""",
                )
            )
    finally:
        source.close(c)
    for year in YEARS:
        selected = [r for r in statistics if r["year"] == year]
        threshold = (
            float(np.quantile([r["events"] / r["days"] for r in selected], 0.75))
            if selected
            else 0
        )
        for row in selected:
            row["liquid"] = int(row["events"] / row["days"] >= threshold)
            row["threshold"] = threshold

    def partition(pair):
        year, bucket = pair
        path = output / f"{year}-{bucket:02d}.parquet"
        # Requery unsealed partitions after interruption; never trust a partial file.
        transport = source.client(endpoint)
        try:
            frame = source.arrow(
                transport,
                f"""SELECT ticker,toString(source_date) source_date,
              event_count,first_ordinal,next_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,build_step
              FROM market_sip_compact.events_ticker_day_index FINAL
              WHERE source_date>='{year}-01-01' AND source_date<'{year+1}-01-01'
              AND cityHash64(ticker)%16={bucket} {predicate}
              ORDER BY ticker,source_date FORMAT ArrowStream""",
                f"bargpt-plan-{year}-{bucket}",
                allow_empty=True,
            )
            if (
                frame.height
                and (
                    frame["next_ordinal"] - frame["first_ordinal"]
                    != frame["event_count"]
                ).any()
            ):
                raise ValueError("Canonical ordinal summary has inconsistent counts")
            temporary = path.with_suffix(".partial")
            frame.write_parquet(temporary, compression="zstd")
            temporary.replace(path)
            return dict(
                name=path.name,
                rows=frame.height,
                bytes=path.stat().st_size,
                sha256=file_hash(path),
            )
        finally:
            source.close(transport)

    partitions = []
    with ThreadPoolExecutor(4, thread_name_prefix="ordinal-plan") as pool:
        for item in pool.map(
            partition, [(year, b) for year in YEARS for b in range(16)]
        ):
            partitions.append(item)
            if progress:
                progress(len(partitions), 112)
    # Interleave liquidity groups; the writer enforces the actual saved 75/25
    # quotas, independently of query scheduling or candidate rejection rates.
    grouped = {}
    for row in statistics:
        grouped.setdefault(row["ticker"], []).append(row)
    jobs = [
        dict(ticker=ticker, years=rows, key=digest(ticker)[:20])
        for ticker, rows in grouped.items()
    ]
    jobs.sort(key=lambda row: seed_for(20261107, row["ticker"]))
    result = dict(
        version=VERSION,
        years=YEARS,
        jobs=jobs,
        partitions=partitions,
        rows=sum(r["rows"] for r in partitions),
        tickers=list(tickers),
        liquidity="top quartile of ticker-year mean event_count; 75/25 saved-sample target",
    )
    if result["rows"] != sum(r["days"] for r in statistics):
        raise ValueError("Plan partition counts differ from the ticker-year inventory")
    result["hash"] = digest(result)
    write_json(final, result)
    return result


def ticker_bounds(root, job):
    """Read only the frozen hash partitions containing one ticker, using pushdown."""
    frames = []
    for row in job["years"]:
        path = Path(root) / "plan" / f"{row['year']}-{row['bucket']:02d}.parquet"
        frames.append(
            pl.scan_parquet(path).filter(pl.col("ticker") == job["ticker"]).collect()
        )
    result = pl.concat(frames).sort("source_date").to_dicts()
    if sum(r["event_count"] for r in result) != sum(r["events"] for r in job["years"]):
        raise ValueError("Frozen ticker bounds no longer agree with the plan")
    return result
