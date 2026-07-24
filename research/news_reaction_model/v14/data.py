from __future__ import annotations

import datetime as dt
import json
import queue
import threading
from dataclasses import dataclass, replace
from typing import Any, Iterator

import numpy as np
import torch

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.news_reaction_model.v14 import HORIZONS, SESSIONS
from research.news_reaction_model.v14.config import LoaderConfig
from research.news_reaction_model.v14.time_features import encode_time_features


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
SELECT canonical_news_id AS source_id, ticker, published_at_utc,
 word_ids, word_weights, char_ids, char_weights,
 numeric_ids, numeric_weights, numeric_dense, stock_state, publication_session,
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


def prepared_dataset_audit_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    session_values = ", ".join(q(value) for value in SESSIONS)
    return f"""
SELECT count(), uniqExact(canonical_news_id), min(published_at_utc), max(published_at_utc),
 countIf(length(word_ids) != length(word_weights)
      OR length(char_ids) != length(char_weights)
      OR length(numeric_ids) != length(numeric_weights)
      OR length(numeric_dense) != {config.numeric_dense_dim}
      OR length(stock_state) != {config.stock_state_dim}),
 countIf(arrayExists(x -> x >= {config.word_vocab_size}, word_ids)
      OR arrayExists(x -> x >= {config.char_vocab_size}, char_ids)
      OR arrayExists(x -> x >= {config.numeric_vocab_size}, numeric_ids)),
 countIf(arrayExists(x -> NOT isFinite(x), word_weights)
      OR arrayExists(x -> NOT isFinite(x), char_weights)
      OR arrayExists(x -> NOT isFinite(x), numeric_weights)
      OR arrayExists(x -> NOT isFinite(x), numeric_dense)
      OR arrayExists(x -> NOT isFinite(x), stock_state)),
 countIf(length(horizon_codes) != length(return_targets)),
 countIf(publication_session NOT IN ({session_values})),
 uniqExact(representation_sha256), any(representation_name), any(representation_sha256),
 countIf(length(numeric_ids) > 0), countIf(arrayExists(x -> x != 0, stock_state))
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
            f"Required V7 TF-IDF stock-state dataset "
            f"{config.dataset_database}.{config.dataset_table} is unavailable. "
            "V14 intentionally reuses the completed V7 representation and does not build a duplicate dataset."
        ) from exc
    rows = int(fields[0]) if fields and fields[0] else 0
    result = {
        "rows": rows,
        "articles": int(fields[1]) if len(fields) > 1 and fields[1] else 0,
        "min_published_at_utc": fields[2] if len(fields) > 2 else "",
        "max_published_at_utc": fields[3] if len(fields) > 3 else "",
        "invalid_sparse_pairs": int(fields[4]) if len(fields) > 4 and fields[4] else 0,
        "invalid_feature_ids": int(fields[5]) if len(fields) > 5 and fields[5] else 0,
        "invalid_feature_weights": int(fields[6]) if len(fields) > 6 and fields[6] else 0,
        "invalid_targets": int(fields[7]) if len(fields) > 7 and fields[7] else 0,
        "invalid_publication_sessions": int(fields[8]) if len(fields) > 8 and fields[8] else 0,
        "representation_versions": int(fields[9]) if len(fields) > 9 and fields[9] else 0,
        "representation_name": fields[10] if len(fields) > 10 else "",
        "representation_sha256": fields[11] if len(fields) > 11 else "",
        "numeric_articles": int(fields[12]) if len(fields) > 12 and fields[12] else 0,
        "state_articles": int(fields[13]) if len(fields) > 13 and fields[13] else 0,
    }
    if rows == 0:
        raise RuntimeError(
            f"Prepared dataset version {config.dataset_version!r} has no rows in [{start}, {end_exclusive}). "
            "Run the preparation command for this range before training."
        )
    if (
        result["articles"] != rows
        or result["invalid_sparse_pairs"]
        or result["invalid_feature_ids"]
        or result["invalid_feature_weights"]
        or result["invalid_targets"]
        or result["invalid_publication_sessions"]
        or result["representation_versions"] != 1
        or result["representation_name"] != config.representation_name
    ):
        raise RuntimeError(f"Prepared V14 source-dataset integrity check failed: {result}")
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
    b, h = len(rows), len(config.horizons)
    returns = np.zeros((b, h, 3), dtype=np.float32)
    label_mask = np.zeros((b, h), dtype=np.bool_)
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    ids, tickers, timestamps, sessions = [], [], [], []
    for row_index, row in enumerate(rows):
        ids.append(str(row["source_id"]))
        tickers.append(str(row["ticker"]))
        timestamps.append(str(row["published_at_utc"]))
        sessions.append(str(row["publication_session"]))
        for code, target_returns in zip(row.get("horizon_codes", ()), row.get("return_targets", ())):
            hi = horizon_index.get(str(code))
            if hi is not None:
                returns[row_index, hi] = np.asarray(target_returns, dtype=np.float32)
                label_mask[row_index, hi] = (
                    np.isfinite(returns[row_index, hi]).all()
                    and bool((returns[row_index, hi] >= -1).all())
                )
    word_ids, word_weights, word_mask = _pack_top_weight_rows(
        rows, "word", config.max_word_tokens, config.word_vocab_size
    )
    char_ids, char_weights, char_mask = _pack_top_weight_rows(
        rows, "char", config.max_char_tokens, config.char_vocab_size
    )
    numeric_ids, numeric_weights, numeric_mask = _pack_top_weight_rows(
        rows, "numeric", config.max_numeric_tokens, config.numeric_vocab_size
    )
    numeric_dense = torch.tensor(
        [[float(value) for value in row.get("numeric_dense", ())] for row in rows], dtype=torch.float32,
    )
    if numeric_dense.shape != (b, config.numeric_dense_dim):
        raise ValueError(
            f"Expected numeric_dense shape {(b, config.numeric_dense_dim)}, got {tuple(numeric_dense.shape)}."
        )
    stock_state = torch.tensor(
        [[float(value) for value in row.get("stock_state", ())] for row in rows], dtype=torch.float32,
    )
    if stock_state.shape != (b, config.stock_state_dim):
        raise ValueError(f"Expected stock_state shape {(b, config.stock_state_dim)}, got {tuple(stock_state.shape)}.")
    time_features = torch.tensor(
        [
            encode_time_features(row["published_at_utc"], row["publication_session"])
            for row in rows
        ],
        dtype=torch.float32,
    )
    if time_features.shape != (b, config.time_feature_dim):
        raise ValueError(
            f"Expected time_features shape {(b, config.time_feature_dim)}, "
            f"got {tuple(time_features.shape)}."
        )
    return NewsReactionBatch(
        x={
            "word_ids": word_ids,
            "word_weights": word_weights,
            "word_mask": word_mask,
            "char_ids": char_ids,
            "char_weights": char_weights,
            "char_mask": char_mask,
            "numeric_ids": numeric_ids,
            "numeric_weights": numeric_weights,
            "numeric_mask": numeric_mask,
            "numeric_dense": numeric_dense,
            "stock_state": stock_state,
            "time_features": time_features,
        },
        return_targets=torch.from_numpy(returns),
        label_mask=torch.from_numpy(label_mask),
        identity={
            "canonical_news_id": ids,
            "ticker": tickers,
            "published_at_utc": timestamps,
            "publication_session": sessions,
        },
        sample_count=b,
    )


def _pack_top_weight_rows(
    rows: list[dict[str, Any]],
    prefix: str,
    max_tokens: int,
    vocab_size: int,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Pack a deterministic bounded set of the strongest sparse features.

    IDs are deliberately not treated as text positions. TF-IDF is an unordered
    representation, so selected features are sorted by descending absolute
    weight with the feature ID as a stable tie-breaker.
    """
    if max_tokens < 1:
        raise ValueError(f"{prefix} max_tokens must be positive")
    packed_ids = np.full((len(rows), max_tokens), vocab_size, dtype=np.int64)
    packed_weights = np.zeros((len(rows), max_tokens), dtype=np.float32)
    packed_mask = np.zeros((len(rows), max_tokens), dtype=np.bool_)
    for row_index, row in enumerate(rows):
        ids = np.asarray(row.get(f"{prefix}_ids", ()), dtype=np.int64)
        weights = np.asarray(row.get(f"{prefix}_weights", ()), dtype=np.float32)
        if ids.shape != weights.shape:
            raise ValueError(f"Mismatched {prefix} sparse feature IDs and weights.")
        if ids.ndim != 1:
            raise ValueError(f"{prefix} sparse features must be one-dimensional.")
        if ids.size == 0:
            continue
        if np.any(ids < 0) or np.any(ids >= vocab_size):
            raise ValueError(f"{prefix} sparse feature ID is outside [0, {vocab_size}).")
        if not np.isfinite(weights).all():
            raise ValueError(f"{prefix} sparse feature weight is not finite.")
        count = min(max_tokens, ids.size)
        magnitudes = np.abs(weights)
        if ids.size > count:
            boundary = np.partition(magnitudes, ids.size - count)[ids.size - count]
            stronger = np.flatnonzero(magnitudes > boundary)
            tied = np.flatnonzero(magnitudes == boundary)
            tied = tied[np.argsort(ids[tied], kind="stable")]
            selected = np.concatenate((stronger, tied[: count - stronger.size]))
            ids, weights, magnitudes = (
                ids[selected],
                weights[selected],
                magnitudes[selected],
            )
        # Stable display order is not a positional signal; it only guarantees
        # byte-identical batches and checkpoints across resumes.
        order = np.lexsort((ids, -magnitudes))[:count]
        ids, weights = ids[order], weights[order]
        packed_ids[row_index, :count] = ids
        packed_weights[row_index, :count] = weights
        packed_mask[row_index, :count] = True
    return (
        torch.from_numpy(packed_ids),
        torch.from_numpy(packed_weights),
        torch.from_numpy(packed_mask),
    )


def concatenate_batches(batches: list[NewsReactionBatch]) -> NewsReactionBatch:
    if not batches:
        raise ValueError("At least one batch is required.")
    return NewsReactionBatch(
        x={
            key: torch.cat([batch.x[key] for batch in batches], dim=0)
            for key in batches[0].x
        },
        return_targets=torch.cat([batch.return_targets for batch in batches], dim=0),
        label_mask=torch.cat([batch.label_mask for batch in batches], dim=0),
        identity={
            key: [value for batch in batches for value in batch.identity[key]]
            for key in batches[0].identity
        },
        sample_count=sum(batch.sample_count for batch in batches),
    )


def index_batch(batch: NewsReactionBatch, indices: np.ndarray) -> NewsReactionBatch:
    tensor_indices = torch.from_numpy(np.asarray(indices, dtype=np.int64))
    selected = tensor_indices.tolist()
    return NewsReactionBatch(
        x={key: value.index_select(0, tensor_indices) for key, value in batch.x.items()},
        return_targets=batch.return_targets.index_select(0, tensor_indices),
        label_mask=batch.label_mask.index_select(0, tensor_indices),
        identity={
            key: [values[index] for index in selected]
            for key, values in batch.identity.items()
        },
        sample_count=len(selected),
    )


def deterministic_buffered_batches(
    config: LoaderConfig,
    *,
    start: str,
    end_exclusive: str,
    epoch: int,
    seed: int,
    skip_articles: int = 0,
) -> Iterator[NewsReactionBatch]:
    """Yield V10's bounded deterministic article-level shuffle unchanged."""
    if config.shuffle_buffer_articles < config.batch_size:
        raise ValueError("shuffle_buffer_articles must be at least batch_size")
    source_config = replace(config, workers=1)
    source = ClickHouseNewsReactionDataset(
        source_config,
        start=start,
        end_exclusive=end_exclusive,
        shuffle_months=False,
    )
    rng = np.random.default_rng(int(seed) + int(epoch))
    buffered: list[NewsReactionBatch] = []
    buffered_articles = 0
    remaining_skip = max(0, int(skip_articles))

    def shuffled(blocks: list[NewsReactionBatch]) -> Iterator[NewsReactionBatch]:
        nonlocal remaining_skip
        merged = concatenate_batches(blocks)
        permutation = rng.permutation(merged.sample_count)
        for offset in range(0, merged.sample_count, config.batch_size):
            batch = index_batch(merged, permutation[offset : offset + config.batch_size])
            if remaining_skip:
                if remaining_skip < batch.sample_count:
                    raise RuntimeError(
                        "Resume cursor does not align with a deterministic training batch: "
                        f"remaining={remaining_skip}, batch={batch.sample_count}."
                    )
                remaining_skip -= batch.sample_count
                continue
            yield batch

    try:
        for batch in source.iter_batches():
            buffered.append(batch)
            buffered_articles += batch.sample_count
            if buffered_articles >= config.shuffle_buffer_articles:
                yield from shuffled(buffered)
                buffered, buffered_articles = [], 0
        if buffered:
            yield from shuffled(buffered)
    finally:
        source.stop()
    if remaining_skip:
        raise RuntimeError(
            f"Resume cursor exceeds the reconstructed epoch by {remaining_skip} articles."
        )


def make_dummy_batch(batch_size: int, config: LoaderConfig, *, device: torch.device | str = "cpu") -> NewsReactionBatch:
    rows = []
    for index in range(batch_size):
        rows.append({
            "source_id": f"dummy-{index}", "ticker": "DUMMY", "published_at_utc": "2025-01-01 12:00:00",
            "word_ids": [index % config.word_vocab_size, (index + 1) % config.word_vocab_size],
            "word_weights": [0.8, 0.6],
            "char_ids": [index % config.char_vocab_size, (index + 3) % config.char_vocab_size],
            "char_weights": [0.7, 0.7],
            "numeric_ids": [index % config.numeric_vocab_size, (index + 5) % config.numeric_vocab_size],
            "numeric_weights": [0.6, 0.8],
            "numeric_dense": [0.1 * ((index + value) % 5) for value in range(config.numeric_dense_dim)],
            "stock_state": [0.1 * ((index + value) % 3) for value in range(config.stock_state_dim)],
            "publication_session": SESSIONS[index % len(SESSIONS)], "horizon_codes": list(config.horizons),
            "return_targets": [[0.001, 0.002 + index * 0.00001, -0.001] for _ in config.horizons],
        })
    return rows_to_batch(rows, config).to(torch.device(device))
