from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import torch

from research.mlops.rolling_loader.ticker_month_dataset import TickerMonthTrainingBatch
from research.temporal_event_model.v3.config import (
    BAR_FAMILIES,
    BAR_FEATURE_DIMS,
    CORPORATE_ACTION_FLAGS,
    DEFAULT_EVENT_FEATURE_NAMES,
    EXTERNAL_ARRIVAL_FLAGS,
    INTRADAY_EVENT_FLAGS,
    LoaderConfig,
    ModelConfig,
)


@dataclass(slots=True)
class TemporalBatch:
    x: dict[str, Any]
    y: dict[str, Any]
    identity: dict[str, Any]
    profile: dict[str, float | int]
    sample_count: int


def batch_to_torch(
    batch: TickerMonthTrainingBatch,
    *,
    model_config: ModelConfig,
    device: torch.device,
    non_blocking: bool = True,
) -> TemporalBatch:
    sample_count = int(batch.sample_count)
    x: dict[str, Any] = {
        "raw_event_stream": _event_stream(batch, model_config, device, non_blocking),
        "raw_event_mask": _tensor(batch.raw_event_mask, device=device, dtype=torch.bool, non_blocking=non_blocking),
        "event_feature_names": tuple(batch.raw_event_stream_feature_names or model_config.event_feature_names),
        "text_inputs": _nested_arrays_to_torch(batch.text_inputs, device=device, non_blocking=non_blocking),
        "xbrl_inputs": _array_dict_to_torch(batch.xbrl_inputs, device=device, non_blocking=non_blocking),
        "corporate_action_inputs": _array_dict_to_torch(batch.corporate_action_inputs, device=device, non_blocking=non_blocking),
        "bar_inputs": _nested_arrays_to_torch(batch.bar_inputs, device=device, non_blocking=non_blocking),
        "input_availability": _array_dict_to_torch(batch.input_availability, device=device, non_blocking=non_blocking),
    }
    y = {
        "future_bar_values": _array_dict_to_torch(batch.future_bar_values, device=device, non_blocking=non_blocking),
        "future_bar_masks": _array_dict_to_torch(batch.future_bar_masks, device=device, dtype=torch.bool, non_blocking=non_blocking),
        "future_bar_feature_names": batch.future_bar_feature_names,
        "intraday_labels": _array_dict_to_torch(batch.intraday_labels, device=device, non_blocking=non_blocking),
        "corporate_action_labels": _array_dict_to_torch(batch.corporate_action_labels, device=device, non_blocking=non_blocking),
        "corporate_action_label_days": tuple(int(v) for v in batch.corporate_action_label_days),
        "future_intraday_bar_horizons": tuple(str(v) for v in batch.future_intraday_bar_horizons),
    }
    identity = {
        "ticker": batch.ticker,
        "origin_ordinal": batch.origin_ordinal.copy(),
        "origin_timestamp_us": batch.origin_timestamp_us.copy(),
        "source_part_key": batch.source_part_key.copy(),
    }
    return TemporalBatch(x=x, y=y, identity=identity, profile=dict(batch.profile), sample_count=sample_count)


def make_dummy_temporal_batch(
    *,
    model_config: ModelConfig | None = None,
    batch_size: int = 2,
    device: torch.device | str = "cpu",
) -> TemporalBatch:
    config = model_config or ModelConfig()
    device = torch.device(device)
    b = int(batch_size)
    h = int(config.intraday_horizons)
    d = len(config.corporate_action_days)
    raw = torch.randn(b, config.event_stream_length, config.event_feature_count, device=device)
    raw[..., 0] = torch.randint(0, 64, (b, config.event_stream_length), device=device).float()
    mask = torch.ones(b, config.event_stream_length, dtype=torch.bool, device=device)
    text_inputs = {
        "ticker_news": _dummy_text(b, config.ticker_news_items, config.ticker_news_chunks, config.text_embedding_dim, device),
        "market_news": _dummy_text(b, config.market_news_items, config.market_news_chunks, config.text_embedding_dim, device),
        "sec_filings": _dummy_text(b, config.sec_filing_items, config.sec_filing_chunks, config.text_embedding_dim, device),
    }
    bar_inputs = {
        "ticker_intraday_bars": _dummy_bars(b, config.intraday_horizons, config.bar_feature_count, config.bar_time_feature_count, device),
        "ticker_daily_bars": _dummy_bars(b, config.ticker_bar_offsets, config.bar_feature_count, config.bar_time_feature_count, device),
        "global_daily_bars": _dummy_global_bars(b, config.global_symbols, config.global_bar_offsets, config.bar_feature_count, config.bar_time_feature_count, device),
    }
    xbrl_inputs = {
        "value": torch.randn(b, config.xbrl_max_items, device=device),
        "mask": torch.ones(b, config.xbrl_max_items, dtype=torch.bool, device=device),
        "mapping_confidence": torch.ones(b, config.xbrl_max_items, device=device),
        "time_features": torch.randn(b, config.xbrl_max_items, config.xbrl_time_feature_count, device=device),
        "period_end_time_features": torch.randn(b, config.xbrl_max_items, config.xbrl_period_time_feature_count, device=device),
        "fiscal_period_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
        "calendar_period_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
        "taxonomy_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
        "tag_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
        "unit_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
        "form_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
        "row_kind_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
        "location_id": torch.zeros(b, config.xbrl_max_items, dtype=torch.long, device=device),
    }
    corporate_inputs = {
        "mask": torch.ones(b, config.corporate_action_max_items, dtype=torch.bool, device=device),
        "numeric_features": torch.randn(b, config.corporate_action_max_items, config.corporate_action_numeric_dim, device=device),
        "time_features": torch.randn(b, config.corporate_action_max_items, config.corporate_action_time_dim, device=device),
        "effective_time_features": torch.randn(b, config.corporate_action_max_items, config.corporate_action_effective_time_dim, device=device),
        "action_type_id": torch.zeros(b, config.corporate_action_max_items, dtype=torch.long, device=device),
        "dividend_type_id": torch.zeros(b, config.corporate_action_max_items, dtype=torch.long, device=device),
        "currency_id": torch.zeros(b, config.corporate_action_max_items, dtype=torch.long, device=device),
        "frequency_id": torch.zeros(b, config.corporate_action_max_items, dtype=torch.long, device=device),
    }
    y = {
        "future_bar_values": {
            family: torch.rand(b, h, BAR_FEATURE_DIMS[family], device=device) * 100.0
            for family in BAR_FAMILIES
        },
        "future_bar_masks": {family: torch.ones(b, h, dtype=torch.bool, device=device) for family in BAR_FAMILIES},
        "future_bar_feature_names": {},
        "intraday_labels": {
            "available": torch.ones(b, h, dtype=torch.bool, device=device),
            "event_count": torch.randint(0, 10, (b, h), device=device).float(),
            "size_primary_sum": torch.rand(b, h, device=device) * 100.0,
            "size_secondary_sum": torch.rand(b, h, device=device) * 100.0,
            **{name: torch.randint(0, 2, (b, h), dtype=torch.bool, device=device) for name in (*INTRADAY_EVENT_FLAGS, *EXTERNAL_ARRIVAL_FLAGS)},
        },
        "corporate_action_labels": {
            name: torch.randint(0, 2, (b, d), dtype=torch.bool, device=device) for name in CORPORATE_ACTION_FLAGS
        },
        "corporate_action_label_days": config.corporate_action_days,
        "future_intraday_bar_horizons": tuple(f"h_{i}" for i in range(h)),
    }
    x = {
        "raw_event_stream": raw,
        "raw_event_mask": mask,
        "event_feature_names": config.event_feature_names,
        "text_inputs": text_inputs,
        "xbrl_inputs": xbrl_inputs,
        "corporate_action_inputs": corporate_inputs,
        "bar_inputs": bar_inputs,
        "input_availability": {},
    }
    identity = {
        "ticker": np.asarray(["DUMMY"] * b, dtype=object),
        "origin_ordinal": np.arange(b, dtype=np.int64),
        "origin_timestamp_us": np.arange(b, dtype=np.int64),
        "source_part_key": np.asarray(["dummy|part"] * b, dtype=object),
    }
    return TemporalBatch(x=x, y=y, identity=identity, profile={}, sample_count=b)


def loader_config_from_v3(config: LoaderConfig) -> Any:
    from research.mlops.rolling_loader.ticker_month_dataset import TickerMonthLoaderConfig

    return TickerMonthLoaderConfig(
        cache_root=config.cache_root,
        split=config.split,
        start_utc=config.start_utc,
        end_utc=config.end_utc,
        months=config.months,
        tickers=config.tickers,
        batch_size=config.batch_size,
        seed=config.seed,
        data_groups=config.data_groups,
        event_columns=config.event_columns,
        event_output_mode="raw_stream",
        event_stream_length=config.event_stream_length,
        loaded_parts_per_group=config.loaded_parts_per_group,
        read_workers=config.read_workers,
        materialize_workers=config.materialize_workers,
        materialize_chunk_size=config.materialize_chunk_size,
        max_origins_per_epoch=config.max_origins_per_epoch,
        sample_fraction=config.sample_fraction,
        sample_hash_modulus=config.sample_hash_modulus,
        sample_hash_buckets=config.sample_hash_buckets,
        randomize_seed=config.randomize_seed,
        shuffle_parts=config.shuffle_parts,
        shuffle_within_loaded_group=config.shuffle_within_loaded_group,
        include_external_context=False,
        strict_audit=True,
        preserve_batch_order=True,
    )


def validation_loader_config_from_v3(config: LoaderConfig) -> Any:
    val = loader_config_from_v3(config)
    return type(val)(
        **{
            **asdict(val),
            "split": config.val_split,
            "start_utc": config.val_start_utc or config.start_utc,
            "end_utc": config.val_end_utc or config.end_utc,
            "sample_hash_buckets": config.val_sample_hash_buckets or config.sample_hash_buckets,
            "randomize_seed": False,
            "shuffle_parts": False,
            "shuffle_within_loaded_group": False,
        }
    )


def _event_stream(batch: TickerMonthTrainingBatch, config: ModelConfig, device: torch.device, non_blocking: bool) -> torch.Tensor:
    arr = batch.raw_event_stream
    if arr.size == 0:
        arr = np.zeros((batch.sample_count, config.event_stream_length, config.event_feature_count), dtype=np.float32)
    arr = np.asarray(arr, dtype=np.float32)
    if arr.shape[1] != config.event_stream_length:
        arr = _pad_or_trim_axis(arr, axis=1, size=config.event_stream_length)
    if arr.shape[2] != config.event_feature_count:
        arr = _pad_or_trim_axis(arr, axis=2, size=config.event_feature_count)
    return _tensor(arr, device=device, dtype=torch.float32, non_blocking=non_blocking)


def _array_dict_to_torch(
    values: Mapping[str, Any],
    *,
    device: torch.device,
    dtype: torch.dtype | None = None,
    non_blocking: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, np.ndarray) and value.dtype != object:
            out[key] = _tensor(value, device=device, dtype=dtype, non_blocking=non_blocking)
        elif torch.is_tensor(value):
            out[key] = value.to(device=device, dtype=dtype or value.dtype, non_blocking=non_blocking)
        else:
            out[key] = value
    return out


def _nested_arrays_to_torch(values: Mapping[str, Mapping[str, Any]], *, device: torch.device, non_blocking: bool = True) -> dict[str, dict[str, Any]]:
    return {key: _array_dict_to_torch(payload, device=device, non_blocking=non_blocking) for key, payload in values.items()}


def _tensor(value: Any, *, device: torch.device, dtype: torch.dtype | None = None, non_blocking: bool = True) -> torch.Tensor:
    if torch.is_tensor(value):
        return value.to(device=device, dtype=dtype or value.dtype, non_blocking=non_blocking)
    array = np.asarray(value)
    if dtype is None:
        if array.dtype == np.bool_:
            dtype = torch.bool
        elif np.issubdtype(array.dtype, np.integer):
            dtype = torch.long
        else:
            dtype = torch.float32
    return torch.as_tensor(array, dtype=dtype, device=device)


def _pad_or_trim_axis(array: np.ndarray, *, axis: int, size: int) -> np.ndarray:
    current = int(array.shape[axis])
    if current == int(size):
        return array
    if current > int(size):
        slices = [slice(None)] * array.ndim
        slices[axis] = slice(0, int(size))
        return array[tuple(slices)]
    shape = list(array.shape)
    shape[axis] = int(size) - current
    pad = np.zeros(shape, dtype=array.dtype)
    return np.concatenate([array, pad], axis=axis)


def _dummy_text(batch: int, items: int, chunks: int, dim: int, device: torch.device) -> dict[str, torch.Tensor]:
    return {
        "embeddings": torch.randn(batch, items, chunks, dim, device=device),
        "item_mask": torch.ones(batch, items, dtype=torch.bool, device=device),
        "chunk_mask": torch.ones(batch, items, chunks, dtype=torch.bool, device=device),
        "item_time_features": torch.randn(batch, items, 13, device=device),
    }


def _dummy_bars(batch: int, offsets: int, features: int, time_features: int, device: torch.device) -> dict[str, torch.Tensor]:
    payload = {
        "values": torch.randn(batch, offsets, features, device=device),
        "mask": torch.ones(batch, offsets, dtype=torch.bool, device=device),
        "time_features": torch.randn(batch, offsets, time_features, device=device),
    }
    for family, dim in BAR_FEATURE_DIMS.items():
        payload[f"{family}_values"] = torch.randn(batch, offsets, dim, device=device)
        payload[f"{family}_mask"] = torch.ones(batch, offsets, dtype=torch.bool, device=device)
        payload[f"{family}_time_features"] = torch.randn(batch, offsets, time_features, device=device)
    return payload


def _dummy_global_bars(batch: int, symbols: int, offsets: int, features: int, time_features: int, device: torch.device) -> dict[str, torch.Tensor]:
    payload = {
        "values": torch.randn(batch, symbols, offsets, features, device=device),
        "mask": torch.ones(batch, symbols, offsets, dtype=torch.bool, device=device),
        "time_features": torch.randn(batch, symbols, offsets, time_features, device=device),
    }
    for family, dim in BAR_FEATURE_DIMS.items():
        payload[f"{family}_values"] = torch.randn(batch, symbols, offsets, dim, device=device)
        payload[f"{family}_mask"] = torch.ones(batch, symbols, offsets, dtype=torch.bool, device=device)
        payload[f"{family}_time_features"] = torch.randn(batch, symbols, offsets, time_features, device=device)
    return payload
