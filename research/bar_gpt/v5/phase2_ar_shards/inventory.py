"""Frozen sampled UTC-month inventory and explicit canonical 2026 extension."""

import json
from pathlib import Path
import shutil

import numpy as np
import polars as pl

from ..shards import source as sip
from ..shards.contract import digest, file_hash, write_json
from ..shards.sql import literal


def check_validation_year(client):
    tables = sip.rows(
        client,
        "SELECT name,storage_policy,sorting_key FROM system.tables WHERE database='market_sip_compact' AND name='events_2026'",
    )
    if len(tables) != 1 or tables[0]["storage_policy"] != "sip_raw_ssd":
        raise ValueError("Canonical 2026 validation source/policy unavailable")
    if sip.rows(
        client,
        "SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='market_sip_compact' AND table IN ('events_2026','events_ticker_day_index') AND disk_name!='sip_raw_ssd'",
    ):
        raise ValueError("2026 canonical parts are on forbidden storage")
    columns = sip.rows(
        client,
        "SELECT table,name,type FROM system.columns WHERE database='market_sip_compact' AND table IN ('events_2025','events_2026') ORDER BY table,position",
    )
    schema = {
        year: [
            (r["name"], r["type"]) for r in columns if r["table"] == f"events_{year}"
        ]
        for year in (2025, 2026)
    }
    if not schema[2025] or schema[2025] != schema[2026]:
        raise ValueError("2026 source requires an explicit schema migration")
    return dict(tables=tables, schema=columns)


def action_chain(proofs, begin, end):
    """Minimal covering chain; an unreported action interval is never empty."""
    cursor, selected = begin, []
    while cursor < end:
        options = [
            p
            for p in proofs
            if p["coverage_start_date"] <= cursor < p["coverage_end_date"]
        ]
        if not options:
            raise ValueError(
                f"Corporate-action coverage gap at {cursor}; no 2026 build allowed"
            )
        choice = max(options, key=lambda p: p["coverage_end_date"])
        selected.append(choice)
        cursor = choice["coverage_end_date"]
    return selected


def extend_actions(client, original, end):
    begin = original["coverage"]["coverage_end_date"]
    if end <= begin:
        return original
    candidates = sip.rows(
        client,
        "SELECT source_run_id,coverage_start_date,coverage_end_date,rows_read,rows_written,rows_failed,details_json "
        "FROM q_live.market_reference_publication_coverage_v1 FINAL WHERE coverage_kind='massive_splits' "
        "AND source_system='massive' AND source_object='splits' AND status='completed' "
        f"AND coverage_end_date>{literal(begin)} AND coverage_start_date<{literal(end)}",
    )
    valid = []
    for p in candidates:
        details = json.loads(p.pop("details_json"))
        if (
            p["rows_failed"]
            or p["rows_read"] != p["rows_written"]
            or details.get("saturated") is not False
            or details.get("pages", 0) < 1
        ):
            continue
        p["provider_response_sha256"] = details["sha256"]
        valid.append(p)
    chain = action_chain(valid, begin, end)
    merged = {r["stock_split_id"]: r for r in original["rows"]}
    for proof in chain:
        rows = sip.rows(
            client,
            "SELECT stock_split_id,provider_ticker,execution_date,split_from,split_to,source_content_sha256 "
            "FROM q_live.market_stock_split_v1 FINAL "
            f"WHERE source_run_id={literal(proof['source_run_id'])} "
            f"AND execution_date>={literal(proof['coverage_start_date'])} "
            f"AND execution_date<{literal(proof['coverage_end_date'])} ORDER BY stock_split_id",
        )
        if len(rows) != proof["rows_written"] or len(
            {r["stock_split_id"] for r in rows}
        ) != len(rows):
            raise ValueError(
                "2026 corporate-action publication differs from coverage receipt"
            )
        for row in rows:
            if not all(
                np.isfinite(float(row[k])) and float(row[k]) > 0
                for k in ("split_from", "split_to")
            ):
                raise ValueError("Invalid corporate-action ratio")
            prior = merged.get(row["stock_split_id"])
            if prior is not None and prior != row:
                raise ValueError(
                    "Corporate-action extension changes frozen parent evidence"
                )
            if row["execution_date"] < end:
                merged[row["stock_split_id"]] = row
    rows = sorted(merged.values(), key=lambda r: r["stock_split_id"])
    return dict(
        parent_catalog_hash=original["catalog_hash"],
        coverage=original["coverage"],
        extension_coverage=chain,
        coverage_end=end,
        rows=rows,
        catalog_hash=digest(rows),
        policy="Frozen parent plus proved executed-action intervals; common origin basis",
    )


def units_from_index(paths, classes, seed):
    """Random month order follows the NY session label; clocks remain UTC."""
    rows = pl.concat(
        [
            pl.scan_parquet(p).select(
                "ticker",
                "source_date",
                "first_sip_timestamp_us",
                "last_sip_timestamp_us",
            )
            for p in paths
        ]
    )
    units = (
        rows.with_columns(pl.col("source_date").str.slice(0, 7).alias("month"))
        .group_by("ticker", "month")
        .agg(
            pl.col("first_sip_timestamp_us").min().alias("first_us"),
            pl.col("last_sip_timestamp_us").max().alias("last_us"),
        )
    )
    frame = units.collect(engine="streaming").filter(
        pl.col("ticker").is_in(classes["ticker"].unique().to_list())
    )
    frame = frame.with_columns(
        pl.col("month").str.slice(0, 4).cast(pl.Int64).alias("year")
    )
    frame = frame.filter(pl.col("year").is_between(2019, 2026)).join(
        classes, on=["ticker", "year"], how="left"
    )
    if frame["liquid"].null_count():
        raise ValueError("Sampled session month has no frozen liquidity class")
    return frame.with_columns(
        (pl.col("year") == 2026).cast(pl.UInt8).alias("split"),
        pl.struct("ticker", "month").hash(seed=seed).alias("random_order"),
    ).sort("random_order")


def extend_plan(root, parent, body, manifest, client, seed):
    root, parent = Path(root), Path(parent)
    output = root / "inventory/plan"
    output.mkdir(parents=True, exist_ok=True)
    parts = list(manifest["partitions"])
    for part in parts:
        source, target = parent / "plan" / part["name"], output / part["name"]
        if file_hash(source) != part["sha256"]:
            raise ValueError("Parent ordinal snapshot changed")
        shutil.copyfile(source, target)
        if file_hash(target) != part["sha256"]:
            raise ValueError("Ordinal snapshot copy failed")
    extension = check_validation_year(client)
    all_2026 = []
    excluded_rows = 0
    universe = {j["ticker"] for j in body["jobs"]}
    for bucket in range(16):
        sql = "SELECT ticker,toString(source_date) source_date,event_count,first_ordinal,next_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,build_step "
        sql += "FROM market_sip_compact.events_ticker_day_index FINAL WHERE source_date>='2026-01-01' AND source_date<'2027-01-01' "
        sql += f"AND cityHash64(ticker)%16={bucket} ORDER BY ticker,source_date FORMAT ArrowStream"
        table = sip.arrow(
            client, sql, f"ar-validation-index-2026-{bucket}", allow_empty=True
        )
        selected = table.filter(pl.col("ticker").is_in(universe))
        excluded_rows += len(table) - len(selected)
        table = selected
        if (
            table.height
            and (
                (table["next_ordinal"] - table["first_ordinal"]) != table["event_count"]
            ).any()
        ):
            raise ValueError("2026 ordinal count mismatch")
        path = output / f"2026-{bucket:02d}.parquet"
        table.write_parquet(path, compression="zstd")
        parts.append(
            dict(
                name=path.name,
                rows=len(table),
                bytes=path.stat().st_size,
                sha256=file_hash(path),
            )
        )
        if len(table):
            all_2026.append(table.with_columns(pl.lit(bucket).alias("bucket")))
    if not all_2026:
        raise ValueError("No canonical 2026 validation coverage in retained universe")
    validation = pl.concat(all_2026)
    stats = validation.group_by("ticker", "bucket").agg(
        pl.len().alias("days"),
        pl.col("event_count").sum().alias("events"),
        pl.col("first_ordinal").min(),
        pl.col("next_ordinal").max(),
    )
    density = stats["events"] / stats["days"]
    threshold = float(density.quantile(0.75, interpolation="linear"))
    stats = stats.with_columns(
        (pl.col("events") / pl.col("days") >= threshold).cast(pl.UInt8).alias("liquid"),
        pl.lit(2026).alias("year"),
    )
    jobs = {j["ticker"]: dict(j, years=list(j["years"])) for j in body["jobs"]}
    for row in stats.to_dicts():
        jobs[row["ticker"]]["years"].append(row)
    body["jobs"] = list(jobs.values())
    classes = pl.DataFrame(
        [
            dict(ticker=j["ticker"], year=int(y["year"]), liquid=int(y["liquid"]))
            for j in body["jobs"]
            for y in j["years"]
        ]
    )
    units = units_from_index([output / p["name"] for p in parts], classes, seed)
    units_path = root / "sample-units.parquet"
    units.write_parquet(units_path, compression="zstd")
    from datetime import date, timedelta

    end = (
        date.fromisoformat(validation["source_date"].max()) + timedelta(days=1)
    ).isoformat()
    body["authority"]["corporate_actions"] = extend_actions(
        client, body["authority"]["corporate_actions"], end
    )
    body.update(
        seed=seed,
        validation_authority=extension,
        partitions=parts,
        sample_units=dict(
            name=units_path.name, sha256=file_hash(units_path), rows=len(units)
        ),
        randomization="Frozen ticker/session-month order; seeded feasible per-session origin offsets",
        polars_version=pl.__version__,
        source_history_start="2019-01-01",
        validation_available_through=validation["source_date"].max(),
        validation_index_excluded_rows=excluded_rows,
    )
    body.pop("hash", None)
    body["hash"] = digest(body)
    write_json(
        output / "manifest.json",
        dict(jobs=body["jobs"], partitions=parts, hash=body["hash"]),
    )
    write_json(root / "plan.json", body)
    return body


def verify_inventory(root, plan):
    root = Path(root)
    for p in plan["partitions"]:
        if file_hash(root / "inventory/plan" / p["name"]) != p["sha256"]:
            raise ValueError("Frozen ordinal inventory changed")
    if file_hash(root / plan["sample_units"]["name"]) != plan["sample_units"]["sha256"]:
        raise ValueError("Frozen sampled-unit order changed")
