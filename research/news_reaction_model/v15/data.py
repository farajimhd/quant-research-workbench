from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
import torch

from research.news_reaction_model.v15.config import LoaderConfig
from research.news_reaction_model.v15.prepared import close_arrays, open_arrays
from research.news_reaction_model.v15.time_features import (
    encode_time_features,
    parse_published_at_utc,
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
            x={
                key: value.to(device, non_blocking=non_blocking)
                for key, value in self.x.items()
            },
            return_targets=self.return_targets.to(device, non_blocking=non_blocking),
            label_mask=self.label_mask.to(device, non_blocking=non_blocking),
            identity=self.identity,
            sample_count=self.sample_count,
        )


def _timestamp_us(value: Any) -> int:
    return int(round(parse_published_at_utc(value).timestamp() * 1_000_000.0))


def _decode_bytes(value: Any) -> str:
    return bytes(value).rstrip(b"\x00").decode("utf-8")


def _date_range_indices(
    published_at_us: np.ndarray,
    start: str,
    end_exclusive: str,
) -> tuple[int, int]:
    lower = int(np.searchsorted(published_at_us, _timestamp_us(start), side="left"))
    upper = int(np.searchsorted(published_at_us, _timestamp_us(end_exclusive), side="left"))
    return lower, upper


def audit_prepared_dataset(
    config: LoaderConfig,
    start: str,
    end_exclusive: str,
) -> dict[str, Any]:
    arrays, manifest = open_arrays(config)
    try:
        lower, upper = _date_range_indices(arrays["published_at_us"], start, end_exclusive)
        rows = upper - lower
        if rows <= 0:
            raise RuntimeError(
                f"Prepared V15 dataset has no rows in [{start}, {end_exclusive})."
            )
        context_mask = np.asarray(arrays["context_mask"][lower:upper])
        context_indices = np.asarray(arrays["context_indices"][lower:upper])
        row_ids = np.arange(lower, upper, dtype=np.int64)[:, None]
        invalid_context = int(
            np.count_nonzero(
                (context_mask & (context_indices < 0))
                | (context_mask & (context_indices.astype(np.int64) >= row_ids))
                | ((~context_mask) & (context_indices != -1))
            )
        )
        invalid_features = int(
            np.count_nonzero(
                ~np.isfinite(np.asarray(arrays["context_features"][lower:upper]))
            )
        )
        result = {
            "rows": rows,
            "articles": rows,
            "min_published_at_utc": _decode_bytes(arrays["published_at_utc"][lower]),
            "max_published_at_utc": _decode_bytes(arrays["published_at_utc"][upper - 1]),
            "context_articles": int(np.count_nonzero(context_mask.any(axis=1))),
            "context_slots": int(np.count_nonzero(context_mask)),
            "invalid_context": invalid_context,
            "invalid_feature_values": invalid_features,
            "representation_name": config.prepared_dataset_version,
            "representation_sha256": str(manifest["representation_sha256"]),
            "prepared_root": str(config.prepared_dataset_root),
        }
        if invalid_context or invalid_features:
            raise RuntimeError(f"Prepared V15 dataset integrity check failed: {result}.")
        return result
    finally:
        close_arrays(arrays)


def count_prepared_articles(
    config: LoaderConfig,
    start: str,
    end_exclusive: str,
) -> int:
    arrays, _ = open_arrays(config)
    try:
        lower, upper = _date_range_indices(arrays["published_at_us"], start, end_exclusive)
        return upper - lower
    finally:
        close_arrays(arrays)


def _numpy_copy(array: np.ndarray, *, dtype: np.dtype[Any] | None = None) -> np.ndarray:
    return np.array(array, dtype=dtype, copy=True)


def batch_from_indices(
    arrays: Mapping[str, np.ndarray],
    indices: np.ndarray,
    config: LoaderConfig,
) -> NewsReactionBatch:
    indices = np.asarray(indices, dtype=np.int64)
    if indices.ndim != 1:
        raise ValueError(f"Expected one-dimensional row indices, got {indices.shape}.")
    b = int(indices.size)
    context_indices = _numpy_copy(arrays["context_indices"][indices], dtype=np.int64)
    context_mask = _numpy_copy(arrays["context_mask"][indices], dtype=np.bool_)
    safe_context_indices = np.where(context_mask, context_indices, 0)
    prior_embeddings = _numpy_copy(
        arrays["openai_embedding"][safe_context_indices],
        dtype=np.float32,
    )
    prior_embeddings[~context_mask] = 0.0
    x = {
        "openai_embedding": torch.from_numpy(
            _numpy_copy(arrays["openai_embedding"][indices], dtype=np.float32)
        ),
        "stock_state": torch.from_numpy(
            _numpy_copy(arrays["stock_state"][indices], dtype=np.float32)
        ),
        "time_features": torch.from_numpy(
            _numpy_copy(arrays["time_features"][indices], dtype=np.float32)
        ),
        "channel_mask": torch.stack(
            (
                torch.from_numpy(
                    _numpy_copy(
                        np.any(arrays["openai_embedding"][indices] != 0, axis=1),
                        dtype=np.bool_,
                    )
                ),
                torch.from_numpy(
                    _numpy_copy(
                        np.any(arrays["stock_state"][indices] != 0, axis=1),
                        dtype=np.bool_,
                    )
                ),
                torch.ones(b, dtype=torch.bool),
            ),
            dim=1,
        ),
        "prior_openai_embeddings": torch.from_numpy(prior_embeddings),
        "prior_context_features": torch.from_numpy(
            _numpy_copy(arrays["context_features"][indices], dtype=np.float32)
        ),
        "prior_context_mask": torch.from_numpy(context_mask),
    }
    return NewsReactionBatch(
        x=x,
        return_targets=torch.from_numpy(
            _numpy_copy(arrays["return_targets"][indices], dtype=np.float32)
        ),
        label_mask=torch.from_numpy(
            _numpy_copy(arrays["label_mask"][indices], dtype=np.bool_)
        ),
        identity={
            "canonical_news_id": [
                _decode_bytes(value) for value in arrays["canonical_news_id"][indices]
            ],
            "ticker": [_decode_bytes(value) for value in arrays["ticker"][indices]],
            "published_at_utc": [
                _decode_bytes(value) for value in arrays["published_at_utc"][indices]
            ],
            "publication_session": [
                _decode_bytes(value) for value in arrays["publication_session"][indices]
            ],
            "prepared_row_index": indices.tolist(),
        },
        sample_count=b,
    )


class PreparedNewsReactionDataset:
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
        self.arrays, self.manifest = open_arrays(config)
        self.lower, self.upper = _date_range_indices(
            self.arrays["published_at_us"], start, end_exclusive
        )
        self._stopped = False

    def iter_batches(self) -> Iterator[NewsReactionBatch]:
        indices = np.arange(self.lower, self.upper, dtype=np.int64)
        if self.shuffle_months:
            # This option exists only for compatibility with diagnostic callers.
            # Training uses deterministic_buffered_batches below.
            np.random.default_rng(self.seed).shuffle(indices)
        for offset in range(0, len(indices), self.config.batch_size):
            if self._stopped:
                return
            yield batch_from_indices(
                self.arrays,
                indices[offset : offset + self.config.batch_size],
                self.config,
            )

    def stop(self) -> None:
        self._stopped = True
        close_arrays(self.arrays)


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
        x={
            key: value.index_select(0, tensor_indices)
            for key, value in batch.x.items()
        },
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
    """Match V12's bounded deterministic article shuffle over local row indices."""
    if config.shuffle_buffer_articles < config.batch_size:
        raise ValueError("shuffle_buffer_articles must be at least batch_size")
    arrays, _ = open_arrays(config)
    try:
        lower, upper = _date_range_indices(arrays["published_at_us"], start, end_exclusive)
        rng = np.random.default_rng(int(seed) + int(epoch))
        remaining_skip = max(0, int(skip_articles))
        for block_start in range(lower, upper, config.shuffle_buffer_articles):
            block_end = min(upper, block_start + config.shuffle_buffer_articles)
            indices = np.arange(block_start, block_end, dtype=np.int64)
            rng.shuffle(indices)
            for offset in range(0, len(indices), config.batch_size):
                selected = indices[offset : offset + config.batch_size]
                if remaining_skip >= len(selected):
                    remaining_skip -= len(selected)
                    continue
                if remaining_skip:
                    raise RuntimeError(
                        "Resume offset is not aligned to a deterministic V15 batch boundary."
                    )
                yield batch_from_indices(arrays, selected, config)
        if remaining_skip:
            raise RuntimeError(
                f"Resume requested {skip_articles} articles beyond the available V15 epoch."
            )
    finally:
        close_arrays(arrays)


def _decode_row_embedding(row: Mapping[str, Any], config: LoaderConfig) -> np.ndarray:
    encoded = str(row.get("openai_embedding_b64") or "")
    if encoded:
        raw = base64.b64decode(encoded, validate=True)
        expected_bytes = config.openai_embedding_dim * np.dtype("<f4").itemsize
        if len(raw) != expected_bytes:
            raise ValueError(
                f"OpenAI embedding binary transport returned {len(raw)} bytes; "
                f"expected {expected_bytes}."
            )
        vector = np.frombuffer(raw, dtype="<f4")
    else:
        vector = np.asarray(row.get("openai_embedding", ()), dtype=np.float32)
    if vector.shape != (config.openai_embedding_dim,):
        raise ValueError(
            f"OpenAI embedding has shape {vector.shape}; "
            f"expected {(config.openai_embedding_dim,)}."
        )
    return vector


def rows_to_batch(
    rows: list[dict[str, Any]],
    config: LoaderConfig,
) -> NewsReactionBatch:
    """Encode explicit rows for live inference and focused tests.

    Production training uses the indexed prepared store. Live callers may pass
    prebuilt prior-context tensors using the named optional fields below.
    """
    b, h = len(rows), len(config.horizons)
    returns = np.zeros((b, h, 3), dtype=np.float32)
    label_mask = np.zeros((b, h), dtype=np.bool_)
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    embeddings = np.stack(
        [_decode_row_embedding(row, config) for row in rows]
    ).astype(np.float32, copy=False) if rows else np.empty((0, config.openai_embedding_dim), np.float32)
    stock_state = np.asarray(
        [row.get("stock_state", ()) for row in rows], dtype=np.float32
    )
    time_features = np.asarray(
        [
            encode_time_features(row["published_at_utc"], row["publication_session"])
            for row in rows
        ],
        dtype=np.float32,
    )
    prior_embeddings = np.zeros(
        (b, config.context_size, config.openai_embedding_dim), dtype=np.float32
    )
    prior_features = np.zeros(
        (b, config.context_size, config.context_feature_dim), dtype=np.float32
    )
    prior_mask = np.zeros((b, config.context_size), dtype=np.bool_)
    for row_index, row in enumerate(rows):
        for code, target in zip(row.get("horizon_codes", ()), row.get("return_targets", ())):
            index = horizon_index.get(str(code))
            if index is None:
                continue
            values = np.asarray(target, dtype=np.float32)
            returns[row_index, index] = values
            label_mask[row_index, index] = bool(
                values.shape == (3,)
                and np.isfinite(values).all()
                and (values >= -1.0).all()
            )
        explicit_embeddings = np.asarray(
            row.get("prior_openai_embeddings", ()), dtype=np.float32
        )
        explicit_features = np.asarray(
            row.get("prior_context_features", ()), dtype=np.float32
        )
        explicit_mask = np.asarray(row.get("prior_context_mask", ()), dtype=np.bool_)
        if explicit_embeddings.size:
            expected_embeddings = (
                config.context_size,
                config.openai_embedding_dim,
            )
            expected_features = (config.context_size, config.context_feature_dim)
            expected_mask = (config.context_size,)
            if (
                explicit_embeddings.shape != expected_embeddings
                or explicit_features.shape != expected_features
                or explicit_mask.shape != expected_mask
            ):
                raise ValueError(
                    "Explicit prior-news context has invalid shapes: "
                    f"{explicit_embeddings.shape}, {explicit_features.shape}, "
                    f"{explicit_mask.shape}."
                )
            prior_embeddings[row_index] = explicit_embeddings
            prior_features[row_index] = explicit_features
            prior_mask[row_index] = explicit_mask
    if stock_state.shape != (b, config.stock_state_dim):
        raise ValueError(
            f"Expected stock_state shape {(b, config.stock_state_dim)}, got {stock_state.shape}."
        )
    return NewsReactionBatch(
        x={
            "openai_embedding": torch.from_numpy(embeddings),
            "stock_state": torch.from_numpy(stock_state),
            "time_features": torch.from_numpy(time_features),
            "channel_mask": torch.from_numpy(
                np.stack(
                    (
                        np.any(embeddings != 0, axis=1),
                        np.any(stock_state != 0, axis=1),
                        np.ones(b, dtype=np.bool_),
                    ),
                    axis=1,
                )
            ),
            "prior_openai_embeddings": torch.from_numpy(prior_embeddings),
            "prior_context_features": torch.from_numpy(prior_features),
            "prior_context_mask": torch.from_numpy(prior_mask),
        },
        return_targets=torch.from_numpy(returns),
        label_mask=torch.from_numpy(label_mask),
        identity={
            "canonical_news_id": [
                str(row.get("source_id") or row.get("canonical_news_id") or "")
                for row in rows
            ],
            "ticker": [str(row.get("ticker") or "") for row in rows],
            "published_at_utc": [str(row.get("published_at_utc") or "") for row in rows],
            "publication_session": [
                str(row.get("publication_session") or "") for row in rows
            ],
            "prepared_row_index": [-1 for _ in rows],
        },
        sample_count=b,
    )


def make_dummy_batch(
    batch_size: int,
    config: LoaderConfig,
    *,
    device: str | torch.device = "cpu",
) -> NewsReactionBatch:
    rows: list[dict[str, Any]] = []
    for index in range(batch_size):
        embedding = np.zeros(config.openai_embedding_dim, dtype=np.float32)
        embedding[index % config.openai_embedding_dim] = 1.0
        prior_embeddings = np.zeros(
            (config.context_size, config.openai_embedding_dim), dtype=np.float32
        )
        prior_features = np.zeros(
            (config.context_size, config.context_feature_dim), dtype=np.float32
        )
        prior_mask = np.zeros(config.context_size, dtype=np.bool_)
        available = min(config.context_size, index)
        for slot in range(available):
            prior_embeddings[slot, (index + slot + 1) % config.openai_embedding_dim] = 1.0
            prior_features[slot, -1] = float(slot + 1) / max(1, config.context_size)
            prior_mask[slot] = True
        rows.append(
            {
                "source_id": f"dummy-{index}",
                "ticker": "DUMMY",
                "published_at_utc": f"2025-01-01 12:{index % 60:02d}:00",
                "publication_session": "premarket",
                "openai_embedding": embedding,
                "stock_state": [
                    0.1 * ((index + value) % 3)
                    for value in range(config.stock_state_dim)
                ],
                "horizon_codes": list(config.horizons),
                "return_targets": [
                    [0.001, 0.002 + index * 0.00001, -0.001]
                    for _ in config.horizons
                ],
                "prior_openai_embeddings": prior_embeddings,
                "prior_context_features": prior_features,
                "prior_context_mask": prior_mask,
            }
        )
    return rows_to_batch(rows, config).to(torch.device(device))
