from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.news_reaction_model.certified_targets_v1 import (  # noqa: E402
    DATASET_VERSION,
    HORIZONS,
    LABEL_VERSION,
)


def qi(value: str) -> str:
    return quote_ident(value)


def q(value: Any) -> str:
    return sql_string(str(value))


def table(database: str, name: str) -> str:
    return f"{qi(database)}.{qi(name)}"


def month_ranges(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    values: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while cursor < end:
        following = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        values.append((cursor, min(following, end)))
        cursor = following
    return values


@dataclass(frozen=True, slots=True)
class BuildSummary:
    status: str
    dataset_version: str
    label_version: str
    rows: int
    populated_targets: int
    excluded_targets: int
    corporate_action_targets: int
    source_signature: str
    elapsed_seconds: float


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the embedding-independent, article-aligned target sidecar from "
            "ordinal-correct and corporate-action-aware reaction labels."
        )
    )
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2027-01-01")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--source-database", default="market_sip_compact")
    parser.add_argument("--source-table", default="news_reaction_openai_stock_state_dataset_v8")
    parser.add_argument("--source-version", default="news_reaction_openai_stock_state_dataset_v8")
    parser.add_argument("--news-database", default="q_live")
    parser.add_argument("--reaction-table", default="news_reaction_labels_v3")
    parser.add_argument("--target-database", default="market_sip_compact")
    parser.add_argument("--target-table", default="news_reaction_certified_targets_v1")
    parser.add_argument("--status-table", default="news_reaction_certified_target_status_v1")
    parser.add_argument("--output-root", default=r"D:\market-data\prepared\news_reaction_model\certified_targets_v1")
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="24G")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    start = dt.date.fromisoformat(args.start_date)
    end = dt.date.fromisoformat(args.end_date)
    if start >= end:
        raise SystemExit("--start-date must be before exclusive --end-date")
    if args.max_threads <= 0:
        raise SystemExit("--max-threads must be positive")
    return start, end


def create_sql(args: argparse.Namespace) -> tuple[str, str]:
    return (
        f"""
CREATE TABLE IF NOT EXISTS {table(args.target_database, args.target_table)}
(
    dataset_version LowCardinality(String),
    canonical_news_id String,
    ticker LowCardinality(String),
    published_at_utc DateTime64(9, 'UTC'),
    publication_session LowCardinality(String),
    horizon_codes Array(String),
    return_targets Array(Array(Float32)),
    quality_statuses Array(String),
    anchor_prices Array(Float64),
    corporate_action_overlap Array(UInt8),
    label_version LowCardinality(String),
    label_source_revision String,
    source_signature String,
    built_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (dataset_version, published_at_utc, ticker, canonical_news_id)
""",
        f"""
CREATE TABLE IF NOT EXISTS {table(args.target_database, args.status_table)}
(
    dataset_version LowCardinality(String),
    chunk_start Date,
    chunk_end_exclusive Date,
    status LowCardinality(String),
    row_count UInt64,
    populated_targets UInt64,
    source_signature String,
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (dataset_version, chunk_start, chunk_end_exclusive)
""",
    )


def chunk_insert_sql(
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
    source_signature: str,
) -> str:
    source = table(args.source_database, args.source_table)
    reactions = table(args.news_database, args.reaction_table)
    target = table(args.target_database, args.target_table)
    horizons = ", ".join(q(value) for value in HORIZONS)
    horizon_names = ", ".join(q(value) for value in HORIZONS)
    horizon_indices = ", ".join(str(index) for index, _ in enumerate(HORIZONS))
    return f"""
INSERT INTO {target}
WITH
source_rows AS
(
    SELECT canonical_news_id, ticker, published_at_utc, publication_session
    FROM {source} FINAL
    WHERE dataset_version = {q(args.source_version)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
),
label_rows AS
(
    SELECT
        *,
        transform(horizon_code, [{horizon_names}], [{horizon_indices}], toUInt8(255)) AS horizon_order,
        toUInt8(
            applicable = 1
            AND quality_status = 'clean'
            AND corporate_action_overlap = 0
            AND isFinite(abnormal_target_return)
            AND isFinite(abnormal_high_return)
            AND isFinite(abnormal_low_return)
            AND isFinite(target_return)
            AND isFinite(high_return)
            AND isFinite(low_return)
            AND low_return <= target_return
            AND target_return <= high_return
        ) AS certified
    FROM {reactions} FINAL
    WHERE label_version = {q(LABEL_VERSION)}
      AND horizon_code IN ({horizons})
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
),
grouped AS
(
    SELECT
        s.canonical_news_id,
        s.ticker,
        s.published_at_utc,
        s.publication_session,
        arraySort(
            x -> tupleElement(x, 1),
            groupArrayIf(
                tuple(
                    r.horizon_order,
                    r.horizon_code,
                    [toFloat32(r.abnormal_target_return), toFloat32(r.abnormal_high_return), toFloat32(r.abnormal_low_return)],
                    r.quality_status,
                    toFloat64(r.anchor_price),
                    r.corporate_action_overlap
                ),
                r.certified
            )
        ) AS ordered,
        arrayStringConcat(arraySort(groupUniqArrayIf(r.source_revision, r.certified)), ',') AS label_source_revision
    FROM source_rows AS s
    LEFT JOIN label_rows AS r
      ON r.canonical_news_id = s.canonical_news_id
     AND r.ticker = s.ticker
     AND r.published_at_utc = s.published_at_utc
    GROUP BY s.canonical_news_id, s.ticker, s.published_at_utc, s.publication_session
)
SELECT
    {q(DATASET_VERSION)} AS dataset_version,
    canonical_news_id,
    ticker,
    published_at_utc,
    publication_session,
    arrayMap(x -> tupleElement(x, 2), ordered) AS horizon_codes,
    arrayMap(x -> tupleElement(x, 3), ordered) AS return_targets,
    arrayMap(x -> tupleElement(x, 4), ordered) AS quality_statuses,
    arrayMap(x -> tupleElement(x, 5), ordered) AS anchor_prices,
    arrayMap(x -> tupleElement(x, 6), ordered) AS corporate_action_overlap,
    {q(LABEL_VERSION)} AS label_version,
    label_source_revision,
    {q(source_signature)} AS source_signature,
    now64(6) AS built_at
FROM grouped
SETTINGS max_threads={int(args.max_threads)}, max_memory_usage={q(args.max_memory_usage)}
"""


def source_signature(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    source = table(args.source_database, args.source_table)
    reactions = table(args.news_database, args.reaction_table)
    text = client.execute(f"""
SELECT concat(
    toString(s.rows), ':', toString(s.identity_hash), ':',
    toString(r.rows), ':', toString(r.identity_hash), ':',
    toString(r.value_hash), ':', toString(r.max_finalized)
)
FROM
(
    SELECT
        count() AS rows,
        groupBitXor(cityHash64(canonical_news_id, ticker, toString(published_at_utc))) AS identity_hash
    FROM {source} FINAL
    WHERE dataset_version = {q(args.source_version)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
) AS s
CROSS JOIN
(
    SELECT
        count() AS rows,
        groupBitXor(cityHash64(canonical_news_id, ticker, toString(published_at_utc), horizon_code)) AS identity_hash,
        groupBitXor(cityHash64(
            ifNull(abnormal_target_return, nan),
            ifNull(abnormal_high_return, nan),
            ifNull(abnormal_low_return, nan),
            quality_status,
            corporate_action_overlap,
            arrayStringConcat(arraySort(corporate_action_ids), ',')
        )) AS value_hash,
        max(finalized_at) AS max_finalized
    FROM {reactions} FINAL
    WHERE label_version = {q(LABEL_VERSION)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
) AS r
""").strip()
    if not text:
        raise RuntimeError("Could not calculate certified-target source signature.")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_authority_coverage(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
) -> dict[str, int]:
    """Prove that the label authority is complete before certifying any targets.

    A target sidecar is allowed to mask an unusable horizon, but it must never
    turn a missing label row into an apparently valid empty target. Every V8
    article identity must therefore have exactly one row for every contracted
    horizon in the new label authority.
    """

    source = table(args.source_database, args.source_table)
    reactions = table(args.news_database, args.reaction_table)
    horizons = ", ".join(q(value) for value in HORIZONS)
    expected = len(HORIZONS)
    text = client.execute(f"""
WITH
source_rows AS
(
    SELECT canonical_news_id, ticker, published_at_utc
    FROM {source} FINAL
    WHERE dataset_version = {q(args.source_version)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
),
label_rows AS
(
    SELECT canonical_news_id, ticker, published_at_utc, horizon_code
    FROM {reactions} FINAL
    WHERE label_version = {q(LABEL_VERSION)}
      AND horizon_code IN ({horizons})
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
),
label_groups AS
(
    SELECT
        canonical_news_id,
        ticker,
        published_at_utc,
        count() AS label_rows,
        uniqExact(horizon_code) AS unique_horizons
    FROM label_rows
    GROUP BY canonical_news_id, ticker, published_at_utc
)
SELECT
    (SELECT count() FROM source_rows) AS source_rows,
    (SELECT count() FROM label_rows) AS label_rows,
    (
        SELECT count()
        FROM label_rows AS l
        INNER JOIN source_rows AS s
          ON s.canonical_news_id = l.canonical_news_id
         AND s.ticker = l.ticker
         AND s.published_at_utc = l.published_at_utc
    ) AS matched_label_rows,
    (
        SELECT uniqExact(tuple(
            l.canonical_news_id, l.ticker, l.published_at_utc, l.horizon_code
        ))
        FROM label_rows AS l
        INNER JOIN source_rows AS s
          ON s.canonical_news_id = l.canonical_news_id
         AND s.ticker = l.ticker
         AND s.published_at_utc = l.published_at_utc
    ) AS unique_matched_label_rows,
    countIf(ifNull(g.label_rows, 0) != {expected}
            OR ifNull(g.unique_horizons, 0) != {expected}) AS incomplete_source_rows
FROM source_rows AS s
LEFT JOIN label_groups AS g
  ON g.canonical_news_id = s.canonical_news_id
 AND g.ticker = s.ticker
 AND g.published_at_utc = s.published_at_utc
FORMAT JSONEachRow
""").strip()
    if not text:
        raise RuntimeError("Reaction-label authority coverage audit returned no row.")
    result = {key: int(value) for key, value in json.loads(text).items()}
    expected_labels = result["source_rows"] * expected
    if (
        result["source_rows"] == 0
        or result["matched_label_rows"] != expected_labels
        or result["unique_matched_label_rows"] != expected_labels
        or result["incomplete_source_rows"] != 0
    ):
        raise RuntimeError(
            "Reaction-label authority is not complete and cannot certify a sidecar: "
            f"{result}, expected_label_rows={expected_labels}"
        )
    return result


def chunk_completed(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
    signature: str,
) -> bool:
    value = client.execute(f"""
SELECT count()
FROM {table(args.target_database, args.status_table)} FINAL
WHERE dataset_version = {q(DATASET_VERSION)}
  AND chunk_start = toDate({q(start.isoformat())})
  AND chunk_end_exclusive = toDate({q(end.isoformat())})
  AND status = 'completed'
  AND source_signature = {q(signature)}
""").strip()
    return int(value or 0) == 1


def audit_sql(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    source = table(args.source_database, args.source_table)
    reactions = table(args.news_database, args.reaction_table)
    target = table(args.target_database, args.target_table)
    return f"""
WITH
source_rows AS
(
    SELECT canonical_news_id, ticker, published_at_utc
    FROM {source} FINAL
    WHERE dataset_version = {q(args.source_version)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
),
sidecar AS
(
    SELECT *
    FROM {target} FINAL
    WHERE dataset_version = {q(DATASET_VERSION)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
),
expanded AS
(
    SELECT
        canonical_news_id,
        ticker,
        published_at_utc,
        horizon_codes[index] AS horizon_code,
        return_targets[index] AS target,
        quality_statuses[index] AS quality_status,
        corporate_action_overlap[index] AS split_overlap
    FROM sidecar
    ARRAY JOIN arrayEnumerate(horizon_codes) AS index
),
authority AS
(
    SELECT r.*
    FROM
    (
        SELECT *
        FROM {reactions} FINAL
        WHERE label_version = {q(LABEL_VERSION)}
          AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
          AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
          AND applicable = 1
          AND quality_status = 'clean'
          AND corporate_action_overlap = 0
          AND isFinite(abnormal_target_return)
          AND isFinite(abnormal_high_return)
          AND isFinite(abnormal_low_return)
          AND isFinite(target_return)
          AND isFinite(high_return)
          AND isFinite(low_return)
          AND low_return <= target_return
          AND target_return <= high_return
    ) AS r
    INNER JOIN source_rows AS s
      ON s.canonical_news_id = r.canonical_news_id
     AND s.ticker = r.ticker
     AND s.published_at_utc = r.published_at_utc
)
SELECT
    (SELECT count() FROM source_rows) AS source_rows,
    count() AS sidecar_rows,
    uniqExact(tuple(canonical_news_id, ticker, published_at_utc)) AS unique_rows,
    sum(length(horizon_codes)) AS populated_targets,
    countIf(
        length(horizon_codes) != length(return_targets)
        OR length(horizon_codes) != length(quality_statuses)
        OR length(horizon_codes) != length(anchor_prices)
        OR length(horizon_codes) != length(corporate_action_overlap)
        OR length(horizon_codes) != length(arrayDistinct(horizon_codes))
    ) AS invalid_array_rows,
    (SELECT count() FROM authority) AS authority_targets,
    (SELECT count() FROM expanded) AS expanded_targets,
    (
        SELECT count()
        FROM expanded AS e
        LEFT JOIN authority AS a
          ON a.canonical_news_id = e.canonical_news_id
         AND a.ticker = e.ticker
         AND a.published_at_utc = e.published_at_utc
         AND a.horizon_code = e.horizon_code
        WHERE a.canonical_news_id = ''
           OR e.quality_status != 'clean'
           OR e.split_overlap != 0
           OR length(e.target) != 3
           OR NOT arrayAll(x -> isFinite(x), e.target)
           OR abs(e.target[1] - a.abnormal_target_return) > 1e-6
           OR abs(e.target[2] - a.abnormal_high_return) > 1e-6
           OR abs(e.target[3] - a.abnormal_low_return) > 1e-6
    ) AS value_mismatches
FROM sidecar
FORMAT JSONEachRow
"""


def audit(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    start: dt.date,
    end: dt.date,
) -> dict[str, Any]:
    text = client.execute(audit_sql(args, start, end)).strip()
    if not text:
        raise RuntimeError("Certified-target audit returned no row.")
    result = json.loads(text)
    for key in ("source_rows", "sidecar_rows", "unique_rows", "populated_targets", "authority_targets", "expanded_targets"):
        result[key] = int(result[key])
    result["invalid_array_rows"] = int(result["invalid_array_rows"])
    result["value_mismatches"] = int(result["value_mismatches"])
    if result["source_rows"] != result["sidecar_rows"] or result["sidecar_rows"] != result["unique_rows"]:
        raise RuntimeError(f"Certified target identity coverage failed: {result}")
    if result["populated_targets"] != result["authority_targets"] or result["authority_targets"] != result["expanded_targets"]:
        raise RuntimeError(f"Certified target coverage failed: {result}")
    if result["invalid_array_rows"] or result["value_mismatches"]:
        raise RuntimeError(f"Certified target integrity failed: {result}")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args(argv)
    start, end = validate_args(args)
    if not args.clickhouse_url:
        args.clickhouse_url = default_clickhouse_url()
    if not args.user:
        args.user = default_clickhouse_user()
    if not args.password:
        args.password = default_clickhouse_password()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    authority_coverage = audit_authority_coverage(client, args, start, end)
    signature = source_signature(client, args, start, end)
    print(
        f"CERTIFIED TARGETS plan range=[{start},{end}) version={DATASET_VERSION} "
        f"labels={LABEL_VERSION} source_signature={signature} "
        f"source_rows={authority_coverage['source_rows']:,} "
        f"label_rows={authority_coverage['label_rows']:,}",
        flush=True,
    )
    if not args.execute:
        print("PLAN VALIDATED; pass --execute to build the target-only sidecar.", flush=True)
        return 0

    started = time.perf_counter()
    client.execute(f"CREATE DATABASE IF NOT EXISTS {qi(args.target_database)}")
    for statement in create_sql(args):
        client.execute(statement)
    if args.replace_existing:
        client.execute(
            f"ALTER TABLE {table(args.target_database, args.target_table)} DELETE "
            f"WHERE dataset_version = {q(DATASET_VERSION)} "
            f"AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC') "
            f"AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC') "
            "SETTINGS mutations_sync=2"
        )
        client.execute(
            f"ALTER TABLE {table(args.target_database, args.status_table)} DELETE "
            f"WHERE dataset_version = {q(DATASET_VERSION)} "
            f"AND chunk_start >= toDate({q(start.isoformat())}) "
            f"AND chunk_start < toDate({q(end.isoformat())}) SETTINGS mutations_sync=2"
        )

    for index, (chunk_start, chunk_end) in enumerate(month_ranges(start, end), start=1):
        if chunk_completed(client, args, chunk_start, chunk_end, signature):
            print(f"[{index:02d}] {chunk_start:%Y-%m} SKIPPED certified", flush=True)
            continue
        client.execute(
            f"ALTER TABLE {table(args.target_database, args.target_table)} DELETE "
            f"WHERE dataset_version = {q(DATASET_VERSION)} "
            f"AND published_at_utc >= toDateTime64({q(chunk_start.isoformat())}, 9, 'UTC') "
            f"AND published_at_utc < toDateTime64({q(chunk_end.isoformat())}, 9, 'UTC') "
            "SETTINGS mutations_sync=2"
        )
        client.execute(chunk_insert_sql(args, chunk_start, chunk_end, signature))
        row = client.execute(f"""
SELECT count(), sum(length(horizon_codes))
FROM {table(args.target_database, args.target_table)} FINAL
WHERE dataset_version = {q(DATASET_VERSION)}
  AND published_at_utc >= toDateTime64({q(chunk_start.isoformat())}, 9, 'UTC')
  AND published_at_utc < toDateTime64({q(chunk_end.isoformat())}, 9, 'UTC')
FORMAT TSV
""").strip().split("\t")
        client.execute(
            f"INSERT INTO {table(args.target_database, args.status_table)} VALUES "
            f"({q(DATASET_VERSION)}, toDate({q(chunk_start.isoformat())}), "
            f"toDate({q(chunk_end.isoformat())}), 'completed', {int(row[0])}, "
            f"{int(row[1])}, {q(signature)}, now64(6))"
        )
        print(
            f"[{index:02d}] {chunk_start:%Y-%m} rows={int(row[0]):,} targets={int(row[1]):,}",
            flush=True,
        )

    result = audit(client, args, start, end)
    corporate_actions = int(client.execute(f"""
SELECT count()
FROM
(
    SELECT canonical_news_id, ticker, published_at_utc, corporate_action_overlap
    FROM {table(args.news_database, args.reaction_table)} FINAL
    WHERE label_version = {q(LABEL_VERSION)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
) AS r
INNER JOIN
(
    SELECT canonical_news_id, ticker, published_at_utc
    FROM {table(args.source_database, args.source_table)} FINAL
    WHERE dataset_version = {q(args.source_version)}
      AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
      AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
) AS s USING (canonical_news_id, ticker, published_at_utc)
WHERE corporate_action_overlap = 1
""").strip() or 0)
    summary = BuildSummary(
        status="complete",
        dataset_version=DATASET_VERSION,
        label_version=LABEL_VERSION,
        rows=result["sidecar_rows"],
        populated_targets=result["populated_targets"],
        excluded_targets=(result["source_rows"] * len(HORIZONS)) - result["populated_targets"],
        corporate_action_targets=corporate_actions,
        source_signature=signature,
        elapsed_seconds=time.perf_counter() - started,
    )
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(
            {
                **asdict(summary),
                "audit": result,
                "source_table": f"{args.source_database}.{args.source_table}",
                "reaction_table": f"{args.news_database}.{args.reaction_table}",
                "target_table": f"{args.target_database}.{args.target_table}",
                "horizons": list(HORIZONS),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("COMPLETED " + json.dumps(asdict(summary), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
