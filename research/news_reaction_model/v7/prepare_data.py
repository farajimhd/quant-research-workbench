from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import datetime as dt
import hashlib
import json
import signal
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v6.config import LoaderConfig as V6LoaderConfig
from research.news_reaction_model.v6.data import audit_prepared_dataset as audit_v6_dataset
from research.news_reaction_model.v7.config import LoaderConfig
from research.news_reaction_model.v7.data import audit_prepared_dataset, month_ranges, q, qi
from research.news_reaction_model.v6.numeric_features import load_representation_manifest as load_v6_manifest
from research.news_reaction_model.v7.stock_state import (
    EXCHANGE_TZ, SEC_CONCEPTS, SEC_TAGS, SEC_TAG_PRIORITY, STOCK_STATE_DIM, Observation, ObservationIndex,
    contract_payload, contract_sha256, encode_stock_state, parse_timestamp, period_feature,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def canonical_json_value(value: Any) -> Any:
    """Return exactly the JSON data model persisted to disk.

    This normalizes tuples to arrays before both hashing and equality checks, so
    a manifest written on the first run compares identically after JSON reload.
    """
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), default=list))


def build_v7_manifest(config: LoaderConfig, source_representation_sha256: str) -> dict[str, Any]:
    manifest = canonical_json_value({
        "representation_name": config.representation_name,
        "source_v6_representation_sha256": source_representation_sha256,
        "stock_state_contract": contract_payload(),
        "stock_state_contract_sha256": contract_sha256(),
    })
    manifest["representation_sha256"] = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return manifest


def load_or_create_v7_manifest(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    expected = canonical_json_value(expected)
    if path.exists():
        persisted = canonical_json_value(json.loads(path.read_text(encoding="utf-8")))
        if persisted != expected:
            changed = sorted(key for key in set(persisted) | set(expected) if persisted.get(key) != expected.get(key))
            raise RuntimeError(
                f"V7 representation manifest genuinely differs from the code contract at {path}; "
                f"changed top-level fields={changed}. Use a new dataset version for a real contract change."
            )
        return persisted
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return expected


class QueryCancellationController:
    """Track every active ClickHouse query and cancel them as one bounded run."""

    def __init__(self) -> None:
        self.run_id = "news_v7_prepare_" + uuid.uuid4().hex
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._cancelled: set[str] = set()
        self._cancellation_error = ""

    def query_id(self) -> str:
        return f"{self.run_id}_{uuid.uuid4().hex}"

    def register(self, query_id: str) -> None:
        with self._lock:
            self._active.add(query_id)

    def unregister(self, query_id: str) -> None:
        with self._lock:
            self._active.discard(query_id)

    def active(self) -> list[str]:
        with self._lock:
            return sorted(self._active)

    def raise_if_cancelled(self) -> None:
        if self.stop.is_set():
            raise InterruptedError("V7 preparation was cancelled")

    def cancel(self) -> tuple[int, str]:
        self.stop.set()
        query_ids = self.active()
        if not query_ids:
            with self._lock:
                return len(self._cancelled), self._cancellation_error
        with self._lock:
            self._cancelled.update(query_ids)
        client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
        try:
            client.execute(f"KILL QUERY WHERE query_id IN ({_in(query_ids)}) ASYNC")
            with self._lock:
                return len(self._cancelled), self._cancellation_error
        except Exception as exc:  # noqa: BLE001 - interruption must retain the cancellation failure
            with self._lock:
                self._cancellation_error = f"{type(exc).__name__}: {exc}"
                return len(self._cancelled), self._cancellation_error


class TrackedClickHouseClient:
    def __init__(self, controller: QueryCancellationController) -> None:
        self.controller = controller
        self.client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        self.controller.raise_if_cancelled()
        active_id = query_id or self.controller.query_id()
        self.controller.register(active_id)
        try:
            return self.client.execute(sql, query_id=active_id)
        finally:
            self.controller.unregister(active_id)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    loader = LoaderConfig()
    parser = argparse.ArgumentParser(
        description="Reuse exact V6 features and add a causal point-in-time stock-state channel."
    )
    parser.add_argument("--start", default=loader.train_start)
    parser.add_argument("--end-exclusive", default=loader.validation_end_exclusive)
    parser.add_argument("--dataset-database", default=loader.dataset_database)
    parser.add_argument("--dataset-table", default=loader.dataset_table)
    parser.add_argument("--dataset-version", default=loader.dataset_version)
    parser.add_argument("--source-dataset-table", default=loader.source_dataset_table)
    parser.add_argument("--source-dataset-version", default=loader.source_dataset_version)
    parser.add_argument("--representation-artifact-root", default=str(loader.representation_artifact_root))
    parser.add_argument("--v6-feature-artifact-root", default=str(loader.v6_feature_artifact_root))
    parser.add_argument("--workers", type=int, default=loader.workers)
    parser.add_argument("--query-batch-articles", type=int, default=loader.query_batch_articles)
    parser.add_argument("--insert-batch-articles", type=int, default=128)
    parser.add_argument("--max-threads-per-query", type=int, default=loader.max_threads_per_query)
    parser.add_argument("--max-memory-usage", default=loader.max_memory_usage)
    parser.add_argument("--rebuild", action="store_true", help="Replace completed V7 months.")
    parser.add_argument("--execute", action="store_true", help="Without this flag, print the non-mutating plan.")
    parser.add_argument("--status-path", default="")
    return parser.parse_args(list(argv) if argv is not None else None)


def loader_from_args(args: argparse.Namespace) -> LoaderConfig:
    return LoaderConfig(
        dataset_database=args.dataset_database,
        dataset_table=args.dataset_table,
        dataset_version=args.dataset_version,
        source_dataset_table=args.source_dataset_table,
        source_dataset_version=args.source_dataset_version,
        representation_artifact_root=Path(args.representation_artifact_root),
        v6_feature_artifact_root=Path(args.v6_feature_artifact_root),
        workers=max(1, args.workers),
        query_batch_articles=max(1, args.query_batch_articles),
        max_threads_per_query=max(1, args.max_threads_per_query),
        max_memory_usage=args.max_memory_usage,
    )


def create_table_sql(config: LoaderConfig) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
CREATE TABLE IF NOT EXISTS {table}
(
 dataset_version LowCardinality(String),
 split LowCardinality(String),
 canonical_news_id String,
 ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'),
 publication_session LowCardinality(String),
 representation_name LowCardinality(String),
 representation_sha256 FixedString(64),
 word_ids Array(UInt32) CODEC(ZSTD(3)),
 word_weights Array(Float32) CODEC(ZSTD(3)),
 char_ids Array(UInt32) CODEC(ZSTD(3)),
 char_weights Array(Float32) CODEC(ZSTD(3)),
 numeric_ids Array(UInt32) CODEC(ZSTD(3)),
 numeric_weights Array(Float32) CODEC(ZSTD(3)),
 numeric_dense Array(Float32) CODEC(ZSTD(3)),
 stock_state Array(Float32) CODEC(ZSTD(3)),
 horizon_codes Array(String) CODEC(ZSTD(3)),
 return_targets Array(Array(Float32)) CODEC(ZSTD(3)),
 built_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (dataset_version, split, published_at_utc, ticker, canonical_news_id)
SETTINGS index_granularity = 8192
"""


def create_manifest_sql(config: LoaderConfig) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
CREATE TABLE IF NOT EXISTS {table}
(
 dataset_version LowCardinality(String),
 representation_sha256 FixedString(64),
 range_start Date,
 range_end_exclusive Date,
 split LowCardinality(String),
 status LowCardinality(String),
 rows UInt64,
 built_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(built_at)
ORDER BY (dataset_version, range_start, range_end_exclusive)
SETTINGS index_granularity = 8192
"""


def source_rows_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    cursor_timestamp: str = "1970-01-01",
    cursor_ticker: str = "",
    cursor_id: str = "",
    limit: int = 4096,
) -> str:
    source = f"{qi(config.dataset_database)}.{qi(config.source_dataset_table)}"
    return f"""
SELECT
 p.canonical_news_id, p.ticker, p.published_at_utc, p.publication_session,
 p.word_ids, p.word_weights, p.char_ids, p.char_weights,
 p.numeric_ids, p.numeric_weights, p.numeric_dense,
 p.representation_sha256 AS source_representation_sha256,
 p.horizon_codes, p.return_targets,
 a.causal_anchor_price AS anchor_price, a.causal_anchor_timestamp_utc AS anchor_timestamp_utc
FROM {source} AS p FINAL
LEFT JOIN (
 SELECT canonical_news_id, ticker, published_at_utc,
  argMaxIf(anchor_price, finalized_at, applicable = 1 AND anchor_price > 0) AS causal_anchor_price,
  argMaxIf(anchor_timestamp_utc, finalized_at, applicable = 1 AND anchor_price > 0) AS causal_anchor_timestamp_utc
 FROM {qi(config.news_database)}.{qi(config.reaction_table)} FINAL
 WHERE label_version = {q(config.label_version)}
   AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
   AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 GROUP BY canonical_news_id, ticker, published_at_utc
) AS a USING (canonical_news_id, ticker, published_at_utc)
WHERE p.dataset_version = {q(config.source_dataset_version)}
 AND p.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND p.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 AND (p.published_at_utc, p.ticker, p.canonical_news_id) >
     (toDateTime64({q(cursor_timestamp)}, 9, 'UTC'), {q(cursor_ticker)}, {q(cursor_id)})
ORDER BY p.published_at_utc, p.ticker, p.canonical_news_id
LIMIT {int(limit)}
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


def iter_source_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    batch_articles: int,
) -> Iterator[list[dict[str, Any]]]:
    cursor_timestamp, cursor_ticker, cursor_id = "1970-01-01", "", ""
    while True:
        text = client.execute(source_rows_sql(
            config, start, end, cursor_timestamp, cursor_ticker, cursor_id, batch_articles,
        ))
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not rows:
            break
        yield rows
        cursor_timestamp = str(rows[-1]["published_at_utc"])
        cursor_ticker = str(rows[-1]["ticker"])
        cursor_id = str(rows[-1]["canonical_news_id"])
        if len(rows) < batch_articles:
            break


def _in(values: Iterable[Any]) -> str:
    unique = sorted({str(value) for value in values if value not in (None, "")})
    return ",".join(q(value) for value in unique) or "''"


def _json_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def load_bridge_rows(client: ClickHouseHttpClient, config: LoaderConfig, tickers: Iterable[str], start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    return _json_rows(client, f"""
SELECT ticker, cik, symbol_id, valid_from_date, valid_to_date_exclusive, confidence_score
FROM {qi(config.news_database)}.{qi(config.sec_bridge_table)} FINAL
WHERE ticker IN ({_in(tickers)}) AND cik != ''
 AND (valid_from_date IS NULL OR valid_from_date < toDate({q(end.isoformat())}))
 AND (valid_to_date_exclusive IS NULL OR valid_to_date_exclusive > toDate({q(start.isoformat())}))
ORDER BY ticker, confidence_score DESC
FORMAT JSONEachRow
""")


def resolve_bridge(rows: list[dict[str, Any]], ticker: str, day: dt.date) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        if str(row.get("ticker") or "") != ticker:
            continue
        valid_from = dt.date.fromisoformat(str(row["valid_from_date"])[:10]) if row.get("valid_from_date") else dt.date.min
        valid_to = dt.date.fromisoformat(str(row["valid_to_date_exclusive"])[:10]) if row.get("valid_to_date_exclusive") else dt.date.max
        if valid_from <= day < valid_to:
            candidates.append(row)
    return max(candidates, key=lambda row: float(row.get("confidence_score") or 0.0), default=None)


def load_sec_indexes(client: ClickHouseHttpClient, config: LoaderConfig, ciks: Iterable[str], start: dt.date, end: dt.date) -> dict[tuple[str, str], ObservationIndex]:
    table = f"{qi(config.news_database)}.{qi(config.sec_fact_table)}"
    fields = "cik, tag, value, fiscal_period, available_at AS filed_at_utc"
    rows = _json_rows(client, f"""
SELECT {fields} FROM (
 SELECT cik, tag,
  argMax(value, tuple(filed_at_utc, period_end_date, inserted_at)) AS value,
  argMax(fiscal_period, tuple(filed_at_utc, period_end_date, inserted_at)) AS fiscal_period,
  max(filed_at_utc) AS available_at
 FROM {table} FINAL
 WHERE cik IN ({_in(ciks)}) AND taxonomy = 'us-gaap' AND tag IN ({_in(SEC_TAGS)})
   AND filed_at_utc < toDateTime64({q(start.isoformat())}, 3, 'UTC')
 GROUP BY cik, tag
 UNION ALL
 SELECT cik, tag,
  argMax(value, tuple(period_end_date, inserted_at)) AS value,
  argMax(fiscal_period, tuple(period_end_date, inserted_at)) AS fiscal_period,
  filed_at_utc AS available_at
 FROM {table} FINAL
 WHERE cik IN ({_in(ciks)}) AND taxonomy = 'us-gaap' AND tag IN ({_in(SEC_TAGS)})
   AND filed_at_utc >= toDateTime64({q(start.isoformat())}, 3, 'UTC')
   AND filed_at_utc < toDateTime64({q(end.isoformat())}, 3, 'UTC')
 GROUP BY cik, tag, filed_at_utc
)
WHERE available_at IS NOT NULL
FORMAT JSONEachRow
""")
    grouped: dict[tuple[str, str], list[Observation]] = {}
    for row in rows:
        tag = str(row["tag"])
        concept_priority = SEC_TAG_PRIORITY.get(tag)
        if concept_priority is None:
            continue
        concept, priority = concept_priority
        grouped.setdefault((str(row["cik"]), concept), []).append(Observation(
            at=parse_timestamp(row["filed_at_utc"]), value=float(row["value"]),
            period=period_feature(row.get("fiscal_period")), priority=-priority,
        ))
    return {key: ObservationIndex(values) for key, values in grouped.items()}


def load_macro_indexes(client: ClickHouseHttpClient, config: LoaderConfig, tickers: Iterable[str], start: dt.date, end: dt.date) -> dict[str, tuple[list[dt.datetime], list[dict[str, Any]]]]:
    lookback = start - dt.timedelta(days=21)
    rows = _json_rows(client, f"""
SELECT sym, bar_end, close, size_sum AS volume
FROM {qi(config.macro_database)}.{qi(config.macro_bar_table)} FINAL
WHERE timeframe = '1d' AND bar_family = 'trade' AND sym IN ({_in(tickers)})
 AND bar_end >= toDateTime64({q(lookback.isoformat())}, 3, 'UTC')
 AND bar_end < toDateTime64({q(end.isoformat())}, 3, 'UTC')
ORDER BY sym, bar_end
FORMAT JSONEachRow
""")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row["bar_end"] = parse_timestamp(row["bar_end"])
        grouped.setdefault(str(row["sym"]), []).append(row)
    return {ticker: ([row["bar_end"] for row in values], values) for ticker, values in grouped.items()}


def load_short_indexes(client: ClickHouseHttpClient, config: LoaderConfig, symbol_ids: Iterable[str], start: dt.date, end: dt.date) -> dict[str, tuple[list[dt.date], list[dict[str, Any]]]]:
    lookback = start - dt.timedelta(days=21)
    rows = _json_rows(client, f"""
SELECT symbol_id, trade_date,
 argMax(short_volume, inserted_at) AS short_volume,
 argMax(total_volume, inserted_at) AS total_volume,
 argMax(exempt_volume, inserted_at) AS exempt_volume,
 argMax(short_volume_ratio, inserted_at) AS short_volume_ratio
FROM {qi(config.news_database)}.{qi(config.short_volume_table)} FINAL
WHERE symbol_id IN ({_in(symbol_ids)}) AND trade_date >= toDate({q(lookback.isoformat())})
 AND trade_date < toDate({q(end.isoformat())})
GROUP BY symbol_id, trade_date ORDER BY symbol_id, trade_date
FORMAT JSONEachRow
""")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row["trade_date"] = dt.date.fromisoformat(str(row["trade_date"])[:10])
        grouped.setdefault(str(row["symbol_id"]), []).append(row)
    return {symbol: ([row["trade_date"] for row in values], values) for symbol, values in grouped.items()}


def latest_before(index: tuple[list[Any], list[dict[str, Any]]] | None, key: Any) -> dict[str, Any] | None:
    if index is None:
        return None
    keys, rows = index
    position = bisect.bisect_left(keys, key) - 1
    return rows[position] if position >= 0 else None


def build_month_state(client: ClickHouseHttpClient, config: LoaderConfig, rows: list[dict[str, Any]], start: dt.date, end: dt.date) -> list[list[float]]:
    bridge_rows = load_bridge_rows(client, config, (row["ticker"] for row in rows), start, end)
    resolved = [resolve_bridge(bridge_rows, str(row["ticker"]), parse_timestamp(row["published_at_utc"]).date()) for row in rows]
    sec_indexes = load_sec_indexes(client, config, (row["cik"] for row in resolved if row), start, end)
    macro_indexes = load_macro_indexes(client, config, (row["ticker"] for row in rows), start, end)
    short_indexes = load_short_indexes(client, config, (row.get("symbol_id") for row in resolved if row), start, end)
    output: list[list[float]] = []
    for row, bridge in zip(rows, resolved):
        published = parse_timestamp(row["published_at_utc"])
        cik = str(bridge["cik"]) if bridge else ""
        sec = {concept: sec_indexes.get((cik, concept)).before(published) if sec_indexes.get((cik, concept)) else None for concept in SEC_CONCEPTS}
        anchor_at = parse_timestamp(row["anchor_timestamp_utc"]) if row.get("anchor_timestamp_utc") else None
        prior_bar = latest_before(macro_indexes.get(str(row["ticker"])), published)
        symbol_id = str(bridge.get("symbol_id") or "") if bridge else ""
        prior_short = latest_before(short_indexes.get(symbol_id), published.astimezone(EXCHANGE_TZ).date())
        output.append(encode_stock_state(
            published, sec, anchor_price=row.get("anchor_price"), anchor_at=anchor_at,
            prior_bar=prior_bar, short_volume=prior_short,
        ))
    return output


def insert_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    source_rows: list[dict[str, Any]],
    state_rows: list[list[float]],
    representation_sha256: str,
    source_representation_sha256: str,
    insert_batch_articles: int,
) -> None:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    columns = (
        "dataset_version, split, canonical_news_id, ticker, published_at_utc, publication_session, "
        "representation_name, representation_sha256, word_ids, word_weights, char_ids, char_weights, "
        "numeric_ids, numeric_weights, numeric_dense, stock_state, horizon_codes, return_targets"
    )
    for offset in range(0, len(source_rows), insert_batch_articles):
        rows = source_rows[offset : offset + insert_batch_articles]
        payload = []
        for local_index, row in enumerate(rows, start=offset):
            if str(row["source_representation_sha256"]) != source_representation_sha256:
                raise RuntimeError(
                    "V7 source row was not built by the frozen V6 representation: "
                    f"{row['source_representation_sha256']} != {source_representation_sha256}."
                )
            payload.append(json.dumps({
                "dataset_version": config.dataset_version,
                "split": "train" if str(row["published_at_utc"]) < config.train_end_exclusive else "validation",
                "canonical_news_id": row["canonical_news_id"],
                "ticker": row["ticker"],
                "published_at_utc": row["published_at_utc"],
                "publication_session": row["publication_session"],
                "representation_name": config.representation_name,
                "representation_sha256": representation_sha256,
                "word_ids": row["word_ids"], "word_weights": row["word_weights"],
                "char_ids": row["char_ids"], "char_weights": row["char_weights"],
                "numeric_ids": row["numeric_ids"], "numeric_weights": row["numeric_weights"],
                "numeric_dense": row["numeric_dense"], "stock_state": state_rows[local_index],
                "horizon_codes": row["horizon_codes"], "return_targets": row["return_targets"],
            }, separators=(",", ":"), allow_nan=False))
        client.execute(f"INSERT INTO {table} ({columns}) FORMAT JSONEachRow\n" + "\n".join(payload))


def month_count_sql(config: LoaderConfig, start: dt.date, end: dt.date, representation_sha256: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT count(), uniqExact(canonical_news_id)
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND representation_sha256 = {q(representation_sha256)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
FORMAT TSV
"""


def source_range_count_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.source_dataset_table)}"
    return f"""
SELECT count(), uniqExact(canonical_news_id)
FROM {table} FINAL
WHERE dataset_version = {q(config.source_dataset_version)}
 AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end_exclusive)}, 9, 'UTC')
FORMAT TSV
"""


def completed_range_sql(config: LoaderConfig, start: dt.date, end: dt.date, representation_sha256: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    return f"""
SELECT status, rows FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND representation_sha256 = {q(representation_sha256)}
 AND range_start = toDate({q(start.isoformat())})
 AND range_end_exclusive = toDate({q(end.isoformat())})
LIMIT 1 FORMAT TSV
"""


def delete_month_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
ALTER TABLE {table} DELETE
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
SETTINGS mutations_sync = 2
"""


def record_completed_sql(config: LoaderConfig, start: dt.date, end: dt.date, representation_sha256: str, rows: int) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table + '_manifest')}"
    split = "train" if start < dt.date.fromisoformat(config.train_end_exclusive) else "validation"
    return f"""
INSERT INTO {table}
(dataset_version, representation_sha256, range_start, range_end_exclusive, split, status, rows)
VALUES ({q(config.dataset_version)}, {q(representation_sha256)}, toDate({q(start.isoformat())}),
 toDate({q(end.isoformat())}), {q(split)}, 'completed', {int(rows)})
"""


def parse_count(text: str) -> tuple[int, int]:
    fields = text.strip().split("\t")
    return (int(fields[0]), int(fields[1])) if len(fields) >= 2 and fields[0] else (0, 0)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    config = loader_from_args(args)
    months = month_ranges(args.start, args.end_exclusive)
    print(
        f"{'BUILD' if args.execute else 'PLAN'} V7 POINT-IN-TIME STOCK STATE | months={len(months)} | "
        f"source={config.dataset_database}.{config.source_dataset_table} | target={config.dataset_table} | "
        f"state_dim={config.stock_state_dim}",
        flush=True,
    )
    if not args.execute:
        print("Read-only plan complete. Add --execute to reuse V6 rows and materialize V7.", flush=True)
        return 0

    v6_loader = V6LoaderConfig(
        representation_artifact_root=config.v6_feature_artifact_root,
        v5_feature_artifact_root=config.v5_feature_artifact_root,
        dataset_database=config.dataset_database,
        dataset_table=config.source_dataset_table,
        dataset_version=config.source_dataset_version,
    )
    v6_manifest = load_v6_manifest(v6_loader)
    source_representation_sha256 = str(v6_manifest["representation_sha256"])
    expected_manifest = build_v7_manifest(config, source_representation_sha256)
    manifest_path = config.representation_artifact_root / "manifest.json"
    manifest = load_or_create_v7_manifest(manifest_path, expected_manifest)
    representation_sha256 = str(manifest["representation_sha256"])

    source_audit = audit_v6_dataset(v6_loader, args.start, args.end_exclusive)
    if source_audit["representation_sha256"] != source_representation_sha256:
        raise RuntimeError(
            "V7 source table does not match the frozen V6 representation: "
            f"table={source_audit['representation_sha256']} bundle={source_representation_sha256}."
        )

    status_path = Path(args.status_path) if args.status_path else Path("runtime/news-reaction-model/v7/prepare/status.jsonl")
    append_jsonl(status_path, {
        "event": "start", "stage": "stock_state_materialization", "loader": asdict(config),
        "months": len(months), "representation_sha256": representation_sha256,
    })
    controller = QueryCancellationController()
    client = TrackedClickHouseClient(controller)
    client.execute(create_table_sql(config))
    client.execute(create_manifest_sql(config))

    def interrupt_handler(signum: int, _frame: Any) -> None:
        active, error = controller.cancel()
        message = f"INTERRUPT signal={signum}; cancellation requested for {active} active ClickHouse queries"
        if error:
            message += f"; cancellation_error={error}"
        print(message, flush=True)
        raise KeyboardInterrupt

    previous_sigint = signal.signal(signal.SIGINT, interrupt_handler)
    previous_sigbreak = None
    if hasattr(signal, "SIGBREAK"):
        previous_sigbreak = signal.signal(signal.SIGBREAK, interrupt_handler)

    def build_month(item: tuple[dt.date, dt.date]) -> dict[str, Any]:
        start, end = item
        controller.raise_if_cancelled()
        local = TrackedClickHouseClient(controller)
        completed = local.execute(completed_range_sql(config, start, end, representation_sha256)).strip().split("\t")
        if completed and completed[0] == "completed" and not args.rebuild:
            return {"month": start.strftime("%Y-%m"), "status": "skipped", "rows": int(completed[1])}
        if args.rebuild:
            local.execute(delete_month_sql(config, start, end))
        rows_written = 0
        started = time.perf_counter()
        source_rows = [row for batch in iter_source_rows(local, config, start, end, args.query_batch_articles) for row in batch]
        controller.raise_if_cancelled()
        state_rows = build_month_state(local, config, source_rows, start, end) if source_rows else []
        for offset in range(0, len(source_rows), args.query_batch_articles):
            controller.raise_if_cancelled()
            batch_rows = source_rows[offset:offset + args.query_batch_articles]
            batch_state = state_rows[offset:offset + args.query_batch_articles]
            insert_rows(
                local, config, batch_rows, batch_state, representation_sha256,
                source_representation_sha256, args.insert_batch_articles,
            )
            rows_written += len(batch_rows)
        count, unique = parse_count(local.execute(month_count_sql(config, start, end, representation_sha256)))
        if count != rows_written or unique != rows_written:
            raise RuntimeError(
                f"V7 month verification failed for {start:%Y-%m}: wrote={rows_written} count={count} unique={unique}."
            )
        local.execute(record_completed_sql(config, start, end, representation_sha256, rows_written))
        return {
            "month": start.strftime("%Y-%m"), "status": "completed", "rows": rows_written,
            "state_articles": sum(any(value != 0 for value in state) for state in state_rows),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    workers = max(1, min(args.workers, len(months)))
    completed_rows = 0
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="news-v7-prepare")
    futures: dict[concurrent.futures.Future[dict[str, Any]], tuple[dt.date, dt.date]] = {}
    try:
        futures = {executor.submit(build_month, month): month for month in months}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            completed_rows += int(result["rows"])
            append_jsonl(status_path, {"event": "month", **result})
            print(
                f"[{index}/{len(months)}] {result['month']} {str(result['status']).upper()} "
                f"rows={int(result['rows']):,} total={completed_rows:,}", flush=True,
            )
    except (KeyboardInterrupt, InterruptedError):
        active, cancellation_error = controller.cancel()
        cancelled_futures = sum(future.cancel() for future in futures)
        append_jsonl(status_path, {
            "event": "interrupted", "completed_rows": completed_rows,
            "cancelled_futures": cancelled_futures, "active_queries_cancelled": active,
            "cancellation_error": cancellation_error,
        })
        print(
            f"INTERRUPTED | completed_rows={completed_rows:,} pending_cancelled={cancelled_futures} "
            f"active_queries_cancelled={active}. Completed months are retained; rerun the same command to resume.",
            flush=True,
        )
        executor.shutdown(wait=False, cancel_futures=True)
        return 130
    except BaseException:
        controller.cancel()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if previous_sigbreak is not None:
            signal.signal(signal.SIGBREAK, previous_sigbreak)

    audit = audit_prepared_dataset(config, args.start, args.end_exclusive)
    if audit["representation_sha256"] != representation_sha256:
        raise RuntimeError(
            "Prepared V7 rows do not match the active representation manifest: "
            f"table={audit['representation_sha256']} manifest={representation_sha256}."
        )
    source_rows, source_articles = parse_count(client.execute(source_range_count_sql(config, args.start, args.end_exclusive)))
    if source_rows != audit["rows"] or source_articles != audit["articles"]:
        raise RuntimeError(
            "V7 population does not exactly match V6: "
            f"source_rows={source_rows:,} source_articles={source_articles:,} "
            f"v7_rows={audit['rows']:,} v7_articles={audit['articles']:,}."
        )
    audit["source_rows"] = source_rows
    audit["source_articles"] = source_articles
    append_jsonl(status_path, {"event": "audit", **audit})
    print(
        f"COMPLETED V7 rows={completed_rows:,} state_coverage={audit['state_articles']:,}/{audit['rows']:,} "
        f"representation_sha256={representation_sha256} status={status_path}", flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

