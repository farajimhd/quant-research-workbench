"""Bounded read-only transport, frozen inventories, and source preflight."""

import json
import math
from pathlib import Path

import polars as pl
import pyarrow as pa

from research.mlops.clickhouse import (
    ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import (
    default_clickhouse_url_with_network_fallback,
)
from .contract import digest, seed_for, write_json
from .sql import literal


def client(endpoint=None, memory_gib=3):
    """One transport per worker; one bounded query at a time, never a writer."""
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    return ClickHouseHttpClient(
        endpoint or default_clickhouse_url_with_network_fallback(),
        default_clickhouse_user(), default_clickhouse_password(),
        timeout_seconds=330, persistent=True, max_persistent_idle_seconds=2,
        default_query_params={
            "readonly": 1, "max_threads": 2, "max_execution_time": 300,
            "max_memory_usage": int(memory_gib * 1024**3),
            "max_result_bytes": 128 * 1024**2, "result_overflow_mode": "throw",
            "max_block_size": 65536, "prefer_column_name_to_alias": 1,
            "output_format_arrow_low_cardinality_as_dictionary": 0,
        },
    )


def rows(c, statement):
    """Small metadata queries only; event responses use typed Arrow."""
    return [json.loads(line) for line in c.execute(statement + " FORMAT JSONEachRow").splitlines()]


def arrow(c, statement, query_id):
    """Collect only the bounded final bar result, never canonical raw events."""
    # Reuse the shared coalesced HTTP connection implementation. Its combined
    # header/body write avoids the workstation endpoint's split-write reset.
    connection = getattr(c, "_bar_arrow_connection", None)
    if connection is None:
        connection = c._new_connection()
        c._bar_arrow_connection = connection
    headers = {"X-ClickHouse-User": c.user, "X-ClickHouse-Key": c.password}
    params = dict(c.default_query_params, query_id=query_id)
    try:
        connection.request("POST", c._request_path(params), statement.encode(), headers)
        response = connection.getresponse()
        if response.status != 200:
            message = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ClickHouse {response.status}: {message[:1800]}")
        with pa.ipc.open_stream(response) as reader:
            batches = [pl.from_arrow(batch) for batch in reader]
        # Consume HTTP completion before allowing connection reuse.
        trailer = response.read()
        if trailer.strip():
            raise RuntimeError("Unexpected trailing bytes after Arrow stream.")
        response.close()
    except Exception:
        connection.close()
        c._bar_arrow_connection = None
        raise
    if not batches:
        raise ValueError("Query returned no scheduled bars.")
    return pl.concat(batches, rechunk=True)


def preflight(c):
    """Pin schema/dictionary and verify canonical source disk placement."""
    tables = rows(c, "SELECT name,storage_policy,sorting_key FROM system.tables "
                 "WHERE database='market_sip_compact' AND (match(name,'^events_20(19|2[0-5])$') "
                 "OR name='events_ticker_day_index') ORDER BY name")
    if len(tables) != 8 or any(r["storage_policy"] != "sip_raw_ssd" for r in tables):
        raise ValueError("Required 2019–2025 canonical/index tables or SIP policy missing.")
    policy = rows(c, "SELECT disks FROM system.storage_policies WHERE policy_name='sip_raw_ssd'")
    if policy != [{"disks": ["sip_raw_ssd"]}]:
        raise ValueError("Canonical SIP storage must resolve only to sip_raw_ssd.")
    misplaced = rows(c, "SELECT DISTINCT table,disk_name FROM system.parts WHERE active "
                     "AND database='market_sip_compact' AND match(table,'^events_20(19|2[0-5])$') "
                     "AND disk_name!='sip_raw_ssd'")
    if misplaced:
        raise ValueError("Canonical event parts are on forbidden backup storage.")
    rules = rows(c, "SELECT token_id,source_family,modifier_int,condition,update_last,"
                 "update_high_low,update_volume FROM market_sip_compact.event_condition_token_reference "
                 "WHERE is_join_canonical=1 ORDER BY token_id,source_family")
    if len({r["token_id"] for r in rules}) != len(rules):
        raise ValueError("Canonical token dictionary contains ambiguous IDs.")
    schema = rows(c, "SELECT table,name,type FROM system.columns WHERE database='market_sip_compact' "
                  "AND match(table,'^events_20(19|2[0-5])$') ORDER BY table,position")
    return dict(tables=tables, schema=schema, rules=rules, dictionary_hash=digest(rules))


def split_authority(c, years):
    """Freeze a completed retrospective corporate-action catalog covering years.

    Reads existing reference tables only, never vendor APIs or flatfiles. A
    complete acquisition receipt plus exact row count establishes coverage;
    absence of a ticker action is meaningful only inside that covered range.
    These are executed-date adjustments, not public-announcement-time features.
    """
    start, end = f"{min(years)}-01-01", f"{max(years)+1}-01-01"
    coverage = rows(c, "SELECT source_run_id,coverage_start_date,coverage_end_date,"
                    "rows_read,rows_written,rows_failed,details_json FROM "
                    "q_live.market_reference_publication_coverage_v1 FINAL "
                    "WHERE coverage_kind='massive_splits' AND source_system='massive' "
                    "AND source_object='splits' AND status='completed' "
                    f"AND coverage_start_date<={literal(start)} AND coverage_end_date>={literal(end)} "
                    "ORDER BY finished_at_utc DESC LIMIT 1")
    if len(coverage) != 1:
        raise ValueError("No complete corporate-action acquisition covers the requested years.")
    proof = coverage[0]
    details = json.loads(proof.pop("details_json"))
    if (proof["rows_failed"] or proof["rows_read"] != proof["rows_written"]
            or details.get("saturated") is not False or details.get("pages", 0) < 1):
        raise ValueError("Incomplete corporate-action acquisition receipt.")
    # Retain only provenance fields, never source URLs or credential-bearing details.
    proof["provider_response_sha256"] = details["sha256"]
    catalog = rows(c, "SELECT stock_split_id,provider_ticker,execution_date,split_from,split_to,"
                   "source_content_sha256 FROM q_live.market_stock_split_v1 FINAL "
                   f"WHERE source_run_id={literal(proof['source_run_id'])} "
                   f"AND execution_date>={literal(proof['coverage_start_date'])} "
                   f"AND execution_date<{literal(proof['coverage_end_date'])} ORDER BY stock_split_id")
    if len(catalog) != int(proof["rows_written"]) or len({r["stock_split_id"] for r in catalog}) != len(catalog):
        raise ValueError("Corporate-action catalog differs from its complete acquisition receipt.")
    for row in catalog:
        before, after = float(row["split_from"]), float(row["split_to"])
        if not (math.isfinite(before) and math.isfinite(after) and before > 0 and after > 0):
            raise ValueError("Invalid corporate-action ratio.")
        # Distinct event IDs can execute on the same date (reverse then forward).
        # Preserve and compound them, matching ARTE's split_factor implementation.
    return dict(coverage=proof, rows=catalog, catalog_hash=digest(catalog),
                policy="ARTE split_from/split_to before execution; inverse for sizes; sample-end basis")


def plan(c, root, seed, tickers=(), years=range(2019, 2026)):
    """Freeze ticker-year candidates, uniformly shuffled inside each year.

    The year lists are interleaved, so no single year exhausts the byte budget.
    Symbols come from the historical index, not a current scanner or watchlist.
    """
    path = Path(root) / "plan.json"
    if path.exists():
        return json.loads(path.read_text())
    predicate = " AND ticker IN (" + ",".join(map(literal, tickers)) + ")" if tickers else ""
    groups = []
    for year in years:
        inventory = rows(c, f"""SELECT ticker,toUInt16({year}) year,count() days,
          sum(event_count) events,min(first_ordinal) first_ordinal,max(next_ordinal) next_ordinal
          FROM market_sip_compact.events_ticker_day_index FINAL
          WHERE source_date>=toDate('{year}-01-01') AND source_date<toDate('{year+1}-01-01')
          {predicate} GROUP BY ticker ORDER BY ticker""")
        groups.append(sorted(inventory, key=lambda r: seed_for(seed, year, r["ticker"])))
    jobs = [group[i] for i in range(max(map(len, groups), default=0)) for group in groups if i < len(group)]
    if not jobs:
        raise ValueError("Historical index selection is empty.")
    write_json(path, jobs)
    return jobs


def index(c, ticker, year):
    """Freeze exact adjacent-day ordinal bounds, including the UTC year edge."""
    result = rows(c, f"""SELECT ticker,toString(source_date) source_date,event_count,
        first_ordinal,last_ordinal,next_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,build_step
      FROM market_sip_compact.events_ticker_day_index FINAL WHERE ticker={literal(ticker)}
        AND source_date>=toDate('{year}-01-01')-2 AND source_date<=toDate('{year+1}-01-01')
      ORDER BY source_date""")
    for row in result:
        first, last, next_ordinal = (int(row[k]) for k in ("first_ordinal", "last_ordinal", "next_ordinal"))
        if last + 1 != next_ordinal or next_ordinal - first != int(row["event_count"]):
            raise ValueError("Ordinal index continuity/count mismatch.")
    return result
