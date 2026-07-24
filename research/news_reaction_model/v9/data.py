from __future__ import annotations

import base64
import datetime as dt
import json
import queue
import threading
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np
import torch

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.news_reaction_model.v9 import HORIZONS, SESSIONS
from research.news_reaction_model.v9.config import LoaderConfig


def q(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def qi(value: str) -> str:
    return "`" + str(value).replace("`", "``") + "`"


def float32_array_base64_sql(expression: str) -> str:
    """Encode every Float32 as exactly four little-endian bytes.

    ClickHouse ``reinterpretAsString(Float32)`` omits trailing zero bytes.
    Padding each scalar before concatenation preserves zero-valued and other
    byte-suffix-zero components without decimal JSON inflation.
    """
    return (
        "base64Encode(arrayStringConcat(arrayMap("
        f"x -> rightPad(reinterpretAsString(x), 4, char(0)), {expression}"
        ")))"
    )


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
    requested_start = dt.date.fromisoformat(start)
    end = dt.date.fromisoformat(end_exclusive)
    out: list[tuple[dt.date, dt.date]] = []
    while cursor < end:
        next_month = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        out.append((max(cursor, requested_start), min(next_month, end)))
        cursor = next_month
    return out


def prepared_batch_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    cursor_timestamp: str,
    cursor_ticker: str,
    cursor_id: str,
    limit: int,
) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    embedding_transport = float32_array_base64_sql("openai_embedding")
    return f"""
SELECT canonical_news_id AS source_id, ticker, published_at_utc,
 {embedding_transport} AS openai_embedding_b64,
 stock_state, publication_session, horizon_codes, return_targets
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


def prepared_dataset_audit_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT count(), uniqExact(canonical_news_id), min(published_at_utc), max(published_at_utc),
 countIf(length(openai_embedding) != {config.openai_embedding_dim}
      OR length(stock_state) != {config.stock_state_dim}),
 countIf(arrayExists(x -> NOT isFinite(x), openai_embedding)
      OR arrayExists(x -> NOT isFinite(x), stock_state)),
 countIf(length(horizon_codes) != length(return_targets)),
 uniqExact(representation_sha256), any(representation_name), any(representation_sha256),
 countIf(arrayExists(x -> x != 0, openai_embedding)),
 countIf(arrayExists(x -> x != 0, stock_state))
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
    except Exception as exc:
        raise RuntimeError(
            f"Required V8 OpenAI dataset {config.dataset_database}.{config.dataset_table} is unavailable. "
            "V9 intentionally reuses the completed V8 representation and does not build a separate dataset."
        ) from exc
    rows = int(fields[0]) if fields and fields[0] else 0
    result = {
        "rows": rows,
        "articles": int(fields[1]) if len(fields) > 1 and fields[1] else 0,
        "min_published_at_utc": fields[2] if len(fields) > 2 else "",
        "max_published_at_utc": fields[3] if len(fields) > 3 else "",
        "invalid_feature_shapes": int(fields[4]) if len(fields) > 4 and fields[4] else 0,
        "invalid_feature_values": int(fields[5]) if len(fields) > 5 and fields[5] else 0,
        "invalid_targets": int(fields[6]) if len(fields) > 6 and fields[6] else 0,
        "representation_versions": int(fields[7]) if len(fields) > 7 and fields[7] else 0,
        "representation_name": fields[8] if len(fields) > 8 else "",
        "representation_sha256": fields[9] if len(fields) > 9 else "",
        "embedding_articles": int(fields[10]) if len(fields) > 10 and fields[10] else 0,
        "state_articles": int(fields[11]) if len(fields) > 11 and fields[11] else 0,
    }
    if rows == 0:
        raise RuntimeError(
            f"Prepared dataset version {config.dataset_version!r} has no rows in [{start}, {end_exclusive})."
        )
    if (
        result["articles"] != rows
        or result["invalid_feature_shapes"]
        or result["invalid_feature_values"]
        or result["invalid_targets"]
        or result["representation_versions"] != 1
        or result["representation_name"] != config.representation_name
        or result["embedding_articles"] != rows
    ):
        raise RuntimeError(f"Prepared V9 dataset integrity check failed: {result}")
    return result


class ClickHouseNewsReactionDataset:
    def __init__(
        self,
        config: LoaderConfig,
        *,
        start: str,
        end_exclusive: str,
        shuffle_months: bool = False,
        seed: int = 17,
    ) -> None:
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
            client = ClickHouseHttpClient(
                default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
            )
            try:
                while not self._stop.is_set():
                    item = tasks.get()
                    if item is None:
                        break
                    start, end = item
                    cursor_timestamp, cursor_ticker, cursor_id = "1970-01-01", "", ""
                    while not self._stop.is_set():
                        text = client.execute(prepared_batch_sql(
                            self.config,
                            start,
                            end,
                            cursor_timestamp,
                            cursor_ticker,
                            cursor_id,
                            self.config.query_batch_articles,
                        ))
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

        threads = [
            threading.Thread(target=worker, name=f"news-v9-loader-{index}", daemon=True)
            for index in range(workers)
        ]
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
    b, h = len(rows), len(config.horizons)
    returns = np.zeros((b, h, 3), dtype=np.float32)
    label_mask = np.zeros((b, h), dtype=np.bool_)
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    ids, tickers, timestamps = [], [], []
    for row_index, row in enumerate(rows):
        ids.append(str(row["source_id"]))
        tickers.append(str(row["ticker"]))
        timestamps.append(str(row["published_at_utc"]))
        for code, target_returns in zip(row.get("horizon_codes", ()), row.get("return_targets", ())):
            hi = horizon_index.get(str(code))
            if hi is not None:
                returns[row_index, hi] = np.asarray(target_returns, dtype=np.float32)
                label_mask[row_index, hi] = (
                    np.isfinite(returns[row_index, hi]).all()
                    and bool((returns[row_index, hi] >= -1).all())
                )
    embedding_rows = []
    for row in rows:
        encoded = str(row.get("openai_embedding_b64") or "")
        if encoded:
            raw = base64.b64decode(encoded, validate=True)
            expected_bytes = config.openai_embedding_dim * np.dtype("<f4").itemsize
            if len(raw) != expected_bytes:
                raise ValueError(
                    "OpenAI embedding binary transport returned "
                    f"{len(raw)} bytes instead of {expected_bytes} for "
                    f"{row.get('source_id', '<unknown>')} / {row.get('ticker', '<unknown>')} / "
                    f"{row.get('published_at_utc', '<unknown>')}."
                )
            vector = np.frombuffer(raw, dtype="<f4")
        else:
            vector = np.asarray(row.get("openai_embedding", ()), dtype=np.float32)
        if vector.shape != (config.openai_embedding_dim,):
            raise ValueError(
                f"OpenAI embedding has shape {vector.shape} instead of "
                f"{(config.openai_embedding_dim,)} for "
                f"{row.get('source_id', '<unknown>')} / {row.get('ticker', '<unknown>')} / "
                f"{row.get('published_at_utc', '<unknown>')}."
            )
        embedding_rows.append(vector)
    openai_embedding = torch.from_numpy(
        np.stack(embedding_rows).astype(np.float32, copy=False)
        if embedding_rows
        else np.empty((0, config.openai_embedding_dim), dtype=np.float32)
    )
    if openai_embedding.shape != (b, config.openai_embedding_dim):
        raise ValueError(
            f"Expected openai_embedding shape {(b, config.openai_embedding_dim)}, "
            f"got {tuple(openai_embedding.shape)}."
        )
    stock_state = torch.tensor(
        [[float(value) for value in row.get("stock_state", ())] for row in rows],
        dtype=torch.float32,
    )
    if stock_state.shape != (b, config.stock_state_dim):
        raise ValueError(f"Expected stock_state shape {(b, config.stock_state_dim)}, got {tuple(stock_state.shape)}.")
    return NewsReactionBatch(
        x={
            "openai_embedding": openai_embedding,
            "stock_state": stock_state,
            "channel_mask": torch.stack((
                openai_embedding.ne(0).any(dim=1),
                stock_state.ne(0).any(dim=1),
            ), dim=1),
        },
        return_targets=torch.from_numpy(returns),
        label_mask=torch.from_numpy(label_mask),
        identity={
            "canonical_news_id": ids,
            "ticker": tickers,
            "published_at_utc": timestamps,
        },
        sample_count=b,
    )


def make_dummy_batch(
    batch_size: int,
    config: LoaderConfig,
    *,
    device: torch.device | str = "cpu",
) -> NewsReactionBatch:
    rows = []
    for index in range(batch_size):
        embedding = np.zeros(config.openai_embedding_dim, dtype=np.float32)
        embedding[index % config.openai_embedding_dim] = 1.0
        rows.append({
            "source_id": f"dummy-{index}",
            "ticker": "DUMMY",
            "published_at_utc": "2025-01-01 12:00:00",
            "openai_embedding": embedding.tolist(),
            "stock_state": [0.1 * ((index + value) % 3) for value in range(config.stock_state_dim)],
            "publication_session": SESSIONS[index % len(SESSIONS)],
            "horizon_codes": list(config.horizons),
            "return_targets": [[0.001, 0.002 + index * 0.00001, -0.001] for _ in config.horizons],
        })
    return rows_to_batch(rows, config).to(torch.device(device))
