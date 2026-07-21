from __future__ import annotations

import datetime as dt
import json
import queue
import threading
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np
import torch

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.news_reaction_model.v2 import HORIZONS, SESSIONS
from research.news_reaction_model.v2.config import LoaderConfig


def q(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def qi(value: str) -> str:
    return "`" + str(value).replace("`", "``") + "`"


@dataclass(slots=True)
class NewsReactionBatch:
    x: dict[str, torch.Tensor]
    return_targets: torch.Tensor
    label_mask: torch.Tensor
    identity: dict[str, Any]
    sample_count: int

    def to(self, device: torch.device, *, non_blocking: bool = True) -> "NewsReactionBatch":
        return NewsReactionBatch(
            x={key: value.to(device, non_blocking=non_blocking) for key, value in self.x.items()},
            return_targets=self.return_targets.to(device, non_blocking=non_blocking),
            label_mask=self.label_mask.to(device, non_blocking=non_blocking),
            identity=self.identity,
            sample_count=self.sample_count,
        )


def month_ranges(start: str, end_exclusive: str) -> list[tuple[dt.date, dt.date]]:
    cursor = dt.date.fromisoformat(start).replace(day=1)
    end = dt.date.fromisoformat(end_exclusive)
    out: list[tuple[dt.date, dt.date]] = []
    while cursor < end:
        next_month = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        out.append((max(cursor, dt.date.fromisoformat(start)), min(next_month, end)))
        cursor = next_month
    return out


def prepared_batch_sql(config: LoaderConfig, start: dt.date, end: dt.date, cursor_timestamp: str, cursor_ticker: str, cursor_id: str, limit: int) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT canonical_news_id AS source_id, ticker, published_at_utc, chunks, publication_session,
 horizon_codes, return_targets
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 AND (published_at_utc, ticker, canonical_news_id) >
     (toDateTime64({q(cursor_timestamp)}, 9, 'UTC'), {q(cursor_ticker)}, {q(cursor_id)})
ORDER BY published_at_utc, ticker, canonical_news_id
LIMIT {int(limit)}
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


def source_batch_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    cursor: str = "",
    limit: int = 0,
    *,
    include_format: bool = True,
    include_settings: bool = True,
) -> str:
    edb, ndb = qi(config.embedding_database), qi(config.news_database)
    horizon_values = ",".join(q(value) for value in config.horizons)
    settings = f"SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}"
    cursor_clause = f"AND e.source_id > {q(cursor)}" if cursor else ""
    limit_clause = f"LIMIT {int(limit)}" if limit > 0 else ""
    settings_clause = settings if include_settings else ""
    format_clause = "FORMAT JSONEachRow" if include_format else ""
    return f"""
WITH
single_ticker AS
(
 SELECT t.canonical_news_id, any(upperUTF8(t.ticker)) AS ticker, any(t.published_at_utc) AS published_at_utc
 FROM {ndb}.{qi(config.ticker_table)} AS t FINAL
 WHERE t.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
   AND t.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 GROUP BY t.canonical_news_id
 HAVING uniqExact(upperUTF8(t.ticker)) = 1
),
scales AS
(
 SELECT ticker, horizon_code, publication_session, argMax(robust_scale, built_at) AS robust_scale
 FROM {ndb}.{qi(config.scale_table)} FINAL
 WHERE scale_version = {q(config.scale_version)}
 GROUP BY ticker, horizon_code, publication_session
),
eligible_keys AS
(
 SELECT DISTINCT r.canonical_news_id AS news_id, r.ticker AS label_ticker, r.published_at_utc AS label_published_at_utc
 FROM {ndb}.{qi(config.reaction_table)} AS r FINAL
 INNER JOIN {ndb}.{qi(config.quality_table)} AS quality FINAL
  ON quality.label_version = r.label_version AND quality.canonical_news_id = r.canonical_news_id
   AND quality.ticker = r.ticker AND quality.horizon_code = r.horizon_code
   AND quality.published_at_utc = r.published_at_utc
 LEFT JOIN scales AS ts ON ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN scales AS gs ON gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {q(config.label_version)} AND quality.quality_version = {q(config.quality_version)}
  AND quality.eligible_for_statistics = 1 AND r.applicable = 1
  AND r.abnormal_target_return IS NOT NULL AND r.abnormal_high_return IS NOT NULL AND r.abnormal_low_return IS NOT NULL
  AND (ts.robust_scale > 0 OR gs.robust_scale > 0)
  AND r.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
  AND r.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
),
selected AS
(
 SELECT e.source_id AS selected_id, e.ticker AS selected_ticker, e.published_at_utc AS selected_published_at_utc
 FROM {edb}.{qi(config.embedding_table)} AS e FINAL
 INNER JOIN single_ticker AS s
  ON s.canonical_news_id = e.source_id AND s.ticker = upperUTF8(e.ticker)
   AND s.published_at_utc = e.published_at_utc
 INNER JOIN eligible_keys AS k
  ON k.news_id = e.source_id AND k.label_ticker = e.ticker AND k.label_published_at_utc = e.published_at_utc
 WHERE e.embedding_model = {q(config.embedding_model)} AND e.embedding_dim = {config.embedding_dim}
   AND e.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
   AND e.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
   {cursor_clause}
 GROUP BY selected_id, selected_ticker, selected_published_at_utc
 ORDER BY selected_id
 {limit_clause}
),
embedded AS
(
 SELECT e.source_id, e.ticker, e.published_at_utc,
  arraySort(x -> tupleElement(x, 1), groupArray((e.token_chunk_index, e.embedding))) AS chunks
 FROM {edb}.{qi(config.embedding_table)} AS e FINAL
 INNER JOIN selected AS s
  ON s.selected_id = e.source_id AND s.selected_ticker = e.ticker AND s.selected_published_at_utc = e.published_at_utc
 GROUP BY e.source_id, e.ticker, e.published_at_utc
 HAVING length(chunks) BETWEEN 1 AND {config.max_chunks}
),
labels AS
(
 SELECT r.canonical_news_id AS news_id, r.ticker AS label_ticker, r.published_at_utc AS label_published_at_utc, any(r.publication_session) AS publication_session,
  groupArray(r.horizon_code) AS horizon_codes,
  groupArray(multiIf(r.abnormal_target_return < -{config.reaction_z_threshold} * if(ts.robust_scale > 0, ts.robust_scale, gs.robust_scale), 0,
                     r.abnormal_target_return >  {config.reaction_z_threshold} * if(ts.robust_scale > 0, ts.robust_scale, gs.robust_scale), 2, 1)) AS class_targets,
  groupArray([toFloat32(r.abnormal_target_return), toFloat32(r.abnormal_high_return), toFloat32(r.abnormal_low_return)]) AS return_targets
 FROM {ndb}.{qi(config.reaction_table)} AS r FINAL
 INNER JOIN {ndb}.{qi(config.quality_table)} AS quality FINAL
  ON quality.label_version = r.label_version AND quality.canonical_news_id = r.canonical_news_id
   AND quality.ticker = r.ticker AND quality.horizon_code = r.horizon_code
   AND quality.published_at_utc = r.published_at_utc
 LEFT JOIN scales AS ts ON ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN scales AS gs ON gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {q(config.label_version)} AND quality.quality_version = {q(config.quality_version)}
   AND quality.eligible_for_statistics = 1 AND r.applicable = 1
   AND r.abnormal_target_return IS NOT NULL AND r.abnormal_high_return IS NOT NULL AND r.abnormal_low_return IS NOT NULL
   AND r.horizon_code IN ({horizon_values})
   AND (ts.robust_scale > 0 OR gs.robust_scale > 0)
   AND r.canonical_news_id IN (SELECT source_id FROM embedded)
 GROUP BY news_id, label_ticker, label_published_at_utc
)
SELECT e.source_id, e.ticker, e.published_at_utc, e.chunks, l.publication_session,
 l.horizon_codes, l.class_targets, l.return_targets
FROM embedded AS e
INNER JOIN labels AS l ON l.news_id = e.source_id AND l.label_ticker = e.ticker AND l.label_published_at_utc = e.published_at_utc
ORDER BY e.source_id
{settings_clause}
{format_clause}
"""


def prepared_dataset_audit_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT count(), uniqExact(canonical_news_id), min(published_at_utc), max(published_at_utc),
 countIf(length(chunks) < 1 OR length(chunks) > {config.max_chunks}),
 countIf(length(horizon_codes) != length(class_targets) OR length(horizon_codes) != length(return_targets))
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end_exclusive)}, 9, 'UTC')
FORMAT TSV
"""


def audit_prepared_dataset(config: LoaderConfig, start: str, end_exclusive: str) -> dict[str, Any]:
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    try:
        fields = client.execute(prepared_dataset_audit_sql(config, start, end_exclusive)).strip().split("\t")
    except Exception as exc:  # noqa: BLE001 - normalize database errors into an actionable preflight
        raise RuntimeError(
            f"Prepared dataset {config.dataset_database}.{config.dataset_table} is unavailable. "
            "Run python -m research.news_reaction_model.v2.run_prepare_data --execute first."
        ) from exc
    rows = int(fields[0]) if fields and fields[0] else 0
    result = {
        "rows": rows,
        "articles": int(fields[1]) if len(fields) > 1 and fields[1] else 0,
        "min_published_at_utc": fields[2] if len(fields) > 2 else "",
        "max_published_at_utc": fields[3] if len(fields) > 3 else "",
        "invalid_chunks": int(fields[4]) if len(fields) > 4 and fields[4] else 0,
        "invalid_targets": int(fields[5]) if len(fields) > 5 and fields[5] else 0,
    }
    if rows == 0:
        raise RuntimeError(
            f"Prepared dataset version {config.dataset_version!r} has no rows in [{start}, {end_exclusive}). "
            "Run the preparation command for this range before training."
        )
    if result["articles"] != rows or result["invalid_chunks"] or result["invalid_targets"]:
        raise RuntimeError(f"Prepared dataset integrity check failed: {result}")
    return result


class ClickHouseNewsReactionDataset:
    def __init__(self, config: LoaderConfig, *, start: str, end_exclusive: str, shuffle_months: bool = False, seed: int = 17) -> None:
        self.config = config
        self.start = start
        self.end_exclusive = end_exclusive
        self.shuffle_months = shuffle_months
        self.seed = seed
        self._stop = threading.Event()

    def iter_batches(self) -> Iterator[NewsReactionBatch]:
        months = month_ranges(self.start, self.end_exclusive)
        if self.shuffle_months:
            rng = np.random.default_rng(self.seed)
            rng.shuffle(months)
        tasks: queue.Queue[tuple[dt.date, dt.date] | None] = queue.Queue()
        output: queue.Queue[Any] = queue.Queue(maxsize=max(1, self.config.prefetch_batches))
        for item in months:
            tasks.put(item)
        workers = max(1, min(self.config.workers, len(months)))
        for _ in range(workers):
            tasks.put(None)

        def safe_put(value: Any) -> bool:
            while not self._stop.is_set():
                try:
                    output.put(value, timeout=0.25)
                    return True
                except queue.Full:
                    continue
            return False

        def worker() -> None:
            client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
            try:
                while not self._stop.is_set():
                    item = tasks.get()
                    if item is None:
                        break
                    start, end = item
                    cursor_timestamp, cursor_ticker, cursor_id = "1970-01-01", "", ""
                    while not self._stop.is_set():
                        text = client.execute(prepared_batch_sql(self.config, start, end, cursor_timestamp, cursor_ticker, cursor_id, self.config.query_batch_articles))
                        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
                        if not rows:
                            break
                        for offset in range(0, len(rows), self.config.batch_size):
                            if not safe_put(rows_to_batch(rows[offset:offset + self.config.batch_size], self.config)):
                                return
                        cursor_timestamp = str(rows[-1]["published_at_utc"])
                        cursor_ticker = str(rows[-1]["ticker"])
                        cursor_id = str(rows[-1]["source_id"])
                        if len(rows) < self.config.query_batch_articles:
                            break
            except BaseException as exc:
                safe_put(exc)
                self._stop.set()
            finally:
                safe_put(None)

        threads = [threading.Thread(target=worker, name=f"news-loader-{index}", daemon=True) for index in range(workers)]
        for thread in threads:
            thread.start()
        done = 0
        while done < workers:
            item = output.get()
            if item is None:
                done += 1
            elif isinstance(item, BaseException):
                self.stop()
                raise item
            else:
                yield item
        for thread in threads:
            thread.join()

    def stop(self) -> None:
        self._stop.set()


def rows_to_batch(rows: list[dict[str, Any]], config: LoaderConfig) -> NewsReactionBatch:
    b, c, d, h = len(rows), config.max_chunks, config.embedding_dim, len(config.horizons)
    embeddings = np.zeros((b, c, d), dtype=np.float32)
    chunk_mask = np.zeros((b, c), dtype=np.bool_)
    returns = np.zeros((b, h, 3), dtype=np.float32)
    label_mask = np.zeros((b, h), dtype=np.bool_)
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    ids, tickers, timestamps = [], [], []
    for row_index, row in enumerate(rows):
        ids.append(str(row["source_id"]))
        tickers.append(str(row["ticker"]))
        timestamps.append(str(row["published_at_utc"]))
        for chunk_index, vector in row["chunks"]:
            ci = int(chunk_index)
            if 0 <= ci < c and len(vector) == d:
                embeddings[row_index, ci] = np.asarray(vector, dtype=np.float32)
                chunk_mask[row_index, ci] = True
        for code, target_returns in zip(row.get("horizon_codes", ()), row.get("return_targets", ())):
            hi = horizon_index.get(str(code))
            if hi is not None:
                returns[row_index, hi] = np.asarray(target_returns, dtype=np.float32)
                label_mask[row_index, hi] = np.isfinite(returns[row_index, hi]).all()
    return NewsReactionBatch(
        x={"embeddings": torch.from_numpy(embeddings), "chunk_mask": torch.from_numpy(chunk_mask)},
        return_targets=torch.from_numpy(returns), label_mask=torch.from_numpy(label_mask),
        identity={"canonical_news_id": ids, "ticker": tickers, "published_at_utc": timestamps}, sample_count=b,
    )


def make_dummy_batch(batch_size: int, config: LoaderConfig, *, device: torch.device | str = "cpu") -> NewsReactionBatch:
    rows = []
    for index in range(batch_size):
        rows.append({
            "source_id": f"dummy-{index}", "ticker": "DUMMY", "published_at_utc": "2025-01-01 12:00:00",
            "chunks": [[0, np.random.default_rng(index).normal(size=config.embedding_dim).astype(np.float32).tolist()]],
            "publication_session": SESSIONS[index % len(SESSIONS)], "horizon_codes": list(config.horizons),
            "return_targets": [[0.001, 0.002, -0.001] for _ in config.horizons],
        })
    return rows_to_batch(rows, config).to(torch.device(device))
