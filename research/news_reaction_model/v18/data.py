from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

import numpy as np
import torch

from research.news_reaction_model.v18.config import LoaderConfig
from research.news_reaction_model.v18.episode_contract import CONTEXT_FEATURE_DIM
from research.news_reaction_model.v18.prepared import close_arrays, open_arrays
from research.news_reaction_model.v18.targets import RAW_METRIC_NAMES


def _timestamp_us(value: str) -> int:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(round(parsed.timestamp() * 1_000_000))


def _decode(value: Any) -> str:
    return bytes(value).rstrip(b"\x00").decode("utf-8")


@dataclass(slots=True)
class EpisodeBatch:
    x: dict[str, torch.Tensor]
    direction: torch.Tensor
    path: torch.Tensor
    flow: torch.Tensor
    regression_targets: torch.Tensor
    target_mask: torch.Tensor
    identity: dict[str, Any]
    sample_count: int

    def to(self, device: torch.device, *, non_blocking: bool = True) -> "EpisodeBatch":
        return EpisodeBatch(
            x={key: value.to(device, non_blocking=non_blocking) for key, value in self.x.items()},
            direction=self.direction.to(device, non_blocking=non_blocking),
            path=self.path.to(device, non_blocking=non_blocking),
            flow=self.flow.to(device, non_blocking=non_blocking),
            regression_targets=self.regression_targets.to(device, non_blocking=non_blocking),
            target_mask=self.target_mask.to(device, non_blocking=non_blocking),
            identity=self.identity,
            sample_count=self.sample_count,
        )


def _copy(value: np.ndarray, dtype: np.dtype[Any]) -> np.ndarray:
    return np.array(value, dtype=dtype, copy=True)


def batch_from_indices(
    v15: Mapping[str, np.ndarray],
    arrays: Mapping[str, np.ndarray],
    indices: np.ndarray,
) -> EpisodeBatch:
    indices = np.asarray(indices, dtype=np.int64)
    source = _copy(arrays["source_index"][indices], np.int64)
    context_mask = _copy(arrays["context_mask"][indices], np.bool_)
    context_source = _copy(arrays["context_source_indices"][indices], np.int64)
    safe_context_source = np.where(context_mask, context_source, 0)
    prior_embeddings = _copy(v15["openai_embedding"][safe_context_source], np.float32)
    prior_embeddings[~context_mask] = 0
    static = _copy(arrays["context_static"][indices], np.float32)
    context_rows = _copy(arrays["context_row_indices"][indices], np.int64)
    safe_context_rows = np.where(context_mask, context_rows, 0)
    prior_raw = _copy(arrays["raw_metrics"][safe_context_rows], np.float32)
    prior_valid = _copy(arrays["target_mask"][safe_context_rows], np.bool_)
    valid = context_mask & prior_valid
    target_features = np.zeros((*context_mask.shape, 7), dtype=np.float32)
    target_features[..., 0] = valid
    for feature_index, name, scale in (
        (1, "high_return", 100.0),
        (2, "low_return", 100.0),
        (3, "terminal_return", 100.0),
        (4, "vwap_return", 100.0),
        (5, "buy_notional_share", 1.0),
        (6, "sell_notional_share", 1.0),
    ):
        raw_index = RAW_METRIC_NAMES.index(name)
        target_features[..., feature_index] = np.where(
            valid, prior_raw[..., raw_index] * scale, 0.0
        )
    context_features = np.concatenate((static, target_features), axis=-1)
    if context_features.shape[-1] != CONTEXT_FEATURE_DIM:
        raise AssertionError(context_features.shape)
    current_embedding = _copy(v15["openai_embedding"][source], np.float32)
    stock_state = _copy(v15["stock_state"][source], np.float32)
    time_features = _copy(v15["time_features"][source], np.float32)
    batch_size = int(indices.size)
    return EpisodeBatch(
        x={
            "openai_embedding": torch.from_numpy(current_embedding),
            "stock_state": torch.from_numpy(stock_state),
            "time_features": torch.from_numpy(time_features),
            "current_episode_features": torch.from_numpy(
                _copy(arrays["current_episode_features"][indices], np.float32)
            ),
            "channel_mask": torch.stack(
                (
                    torch.from_numpy(np.any(current_embedding != 0, axis=1)),
                    torch.from_numpy(np.any(stock_state != 0, axis=1)),
                    torch.ones(batch_size, dtype=torch.bool),
                    torch.ones(batch_size, dtype=torch.bool),
                ),
                dim=1,
            ),
            "prior_openai_embeddings": torch.from_numpy(prior_embeddings),
            "prior_context_features": torch.from_numpy(context_features),
            "prior_context_mask": torch.from_numpy(context_mask),
        },
        direction=torch.from_numpy(_copy(arrays["direction"][indices], np.int64)),
        path=torch.from_numpy(_copy(arrays["path"][indices], np.int64)),
        flow=torch.from_numpy(_copy(arrays["flow"][indices], np.int64)),
        regression_targets=torch.from_numpy(
            _copy(arrays["regression_targets"][indices], np.float32)
        ),
        target_mask=torch.from_numpy(_copy(arrays["target_mask"][indices], np.bool_)),
        identity={
            "canonical_news_id": [_decode(value) for value in v15["canonical_news_id"][source]],
            "ticker": [_decode(value) for value in v15["ticker"][source]],
            "published_at_utc": [_decode(value) for value in v15["published_at_utc"][source]],
            "episode_id": [_decode(value) for value in arrays["episode_id"][indices]],
            "prepared_row_index": indices.tolist(),
        },
        sample_count=batch_size,
    )


class PreparedEpisodeDataset:
    def __init__(
        self,
        config: LoaderConfig,
        *,
        start: str,
        end_exclusive: str,
        shuffle: bool = False,
        seed: int = 17,
    ) -> None:
        self.config = config
        self.v15, self.arrays, self.v15_manifest, self.manifest = open_arrays(config)
        source = np.asarray(self.arrays["source_index"], dtype=np.int64)
        published = np.asarray(self.v15["published_at_us"][source], dtype=np.int64)
        valid = np.asarray(self.arrays["target_mask"], dtype=np.bool_)
        self.indices = np.flatnonzero(
            valid
            & (published >= _timestamp_us(start))
            & (published < _timestamp_us(end_exclusive))
        ).astype(np.int64)
        if not self.indices.size:
            close_arrays(self.v15, self.arrays)
            raise RuntimeError(f"V18 has no valid rows in [{start}, {end_exclusive}).")
        self.shuffle = shuffle
        self.seed = seed
        self._stopped = False

    def iter_batches(self, *, epoch: int = 0) -> Iterator[EpisodeBatch]:
        indices = np.array(self.indices, copy=True)
        if self.shuffle:
            np.random.default_rng(self.seed + epoch).shuffle(indices)
        for offset in range(0, indices.size, self.config.batch_size):
            if self._stopped:
                return
            yield batch_from_indices(
                self.v15, self.arrays, indices[offset : offset + self.config.batch_size]
            )

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            close_arrays(self.v15, self.arrays)
