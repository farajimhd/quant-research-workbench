from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from research.bar_gpt.v3.data import PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, build_target_clock_features
from research.bar_gpt.v3.features import project_stationary_features
from research.bar_gpt.v3.schema import FEATURE_INDEX, FEATURE_NAMES

from .cache import CALENDAR_VIEWS, INTRADAY_VIEW_US, CausalCache, RawBar
from .config import ReleaseConfig, ServiceConfig


ALL_VIEWS = (*INTRADAY_VIEW_US, *CALENDAR_VIEWS)


@dataclass(slots=True)
class PreparedBatch:
    tickers: tuple[str, ...]
    origins_us: tuple[int, ...]
    base_prices: tuple[dict[str, float], ...]
    real_lengths: dict[str, tuple[int, ...]]
    views: dict[str, torch.Tensor]
    view_masks: dict[str, torch.Tensor]
    origin_indices: torch.Tensor
    origin_timestamps_us: torch.Tensor
    asof_indices: dict[str, torch.Tensor]
    target_clock_features: torch.Tensor


@dataclass(slots=True)
class LoadedRelease:
    config: ReleaseConfig
    model: torch.nn.Module
    data_config: Any
    checkpoint_hash: str
    contract_hash: str
    device: torch.device
    dtype: torch.dtype

    def forward(self, batch: PreparedBatch) -> Any:
        kwargs: dict[str, Any] = {
            "timeframe_us": TIMEFRAME_US_BY_NAME,
            "pathway_ids": PATHWAY_ID_BY_NAME,
            "base_view": "1s",
            "origin_indices": batch.origin_indices,
            "asof_indices": batch.asof_indices,
            "view_masks": batch.view_masks,
            "attention_windows": self.data_config.attention_window_by_name,
            "horizon_ids": torch.arange(
                len(self.data_config.horizons_us), device=self.device, dtype=torch.long
            ),
        }
        if self.config.version == "v3":
            kwargs["target_clock_features"] = batch.target_clock_features
        autocast_enabled = self.device.type == "cuda" and self.dtype in {torch.float16, torch.bfloat16}
        with torch.inference_mode(), torch.autocast(
            device_type=self.device.type,
            dtype=self.dtype,
            enabled=autocast_enabled,
        ):
            return self.model(batch.views, **kwargs)


def load_releases(config: ServiceConfig) -> dict[str, LoadedRelease]:
    device = _resolve_device(config.device)
    dtype = _resolve_dtype(config.dtype, device)
    releases: dict[str, LoadedRelease] = {}
    for release in config.releases:
        if not release.enabled:
            continue
        if not release.checkpoint.is_file():
            raise FileNotFoundError(f"BarGPT checkpoint does not exist: {release.checkpoint}")
        if release.version == "v2":
            from research.bar_gpt.v2.inference import load_pretrained
        else:
            from research.bar_gpt.v3.inference import load_pretrained
        checkpoint_hash = _file_sha256(release.checkpoint)
        if release.expected_checkpoint_hash and checkpoint_hash != release.expected_checkpoint_hash:
            raise ValueError(
                f"BarGPT checkpoint hash mismatch for {release.model_id}: "
                f"expected {release.expected_checkpoint_hash}, observed {checkpoint_hash}"
            )
        model, data_config, payload = load_pretrained(release.checkpoint, device=device)
        contract_hash = str(payload["contract_hash"]).strip().lower()
        if release.expected_contract_hash and contract_hash != release.expected_contract_hash:
            raise ValueError(
                f"BarGPT contract hash mismatch for {release.model_id}: "
                f"expected {release.expected_contract_hash}, observed {contract_hash}"
            )
        model.eval()
        releases[release.model_id] = LoadedRelease(
            config=release,
            model=model,
            data_config=data_config,
            checkpoint_hash=checkpoint_hash,
            contract_hash=contract_hash,
            device=device,
            dtype=dtype,
        )
    return releases


def prepare_batch(
    release: LoadedRelease,
    cache: CausalCache,
    tickers: list[str],
    requested_origin_us: int | None,
) -> PreparedBatch:
    rows_by_view: dict[str, list[list[RawBar]]] = {view: [] for view in ALL_VIEWS}
    origins: list[int] = []
    selected: list[str] = []
    base_prices: list[dict[str, float]] = []
    for ticker in tickers:
        one_second = cache.rows(ticker, "1s", requested_origin_us or 2**63 - 1)
        if not one_second:
            continue
        origin = min(requested_origin_us or one_second[-1].available_at_us, one_second[-1].available_at_us)
        per_view = {view: cache.rows(ticker, view, origin) for view in ALL_VIEWS}
        if any(not per_view[view] for view in ALL_VIEWS):
            continue
        selected.append(ticker)
        origins.append(origin)
        for view in ALL_VIEWS:
            rows_by_view[view].append(per_view[view])
        base_prices.append(_base_prices(per_view["1s"]))
    if not selected:
        raise ValueError("no requested ticker has a complete warm causal context")

    device = release.device
    model_views: dict[str, torch.Tensor] = {}
    masks: dict[str, torch.Tensor] = {}
    lengths: dict[str, tuple[int, ...]] = {}
    for view in ALL_VIEWS:
        sequences = rows_by_view[view]
        real_lengths = tuple(len(values) for values in sequences)
        lengths[view] = real_lengths
        maximum = max(real_lengths) + 1
        raw = torch.zeros((len(selected), maximum, len(FEATURE_NAMES)), dtype=torch.float32)
        starts = torch.zeros((len(selected), maximum), dtype=torch.long)
        mask = torch.zeros((len(selected), maximum), dtype=torch.bool)
        for row_index, values in enumerate(sequences):
            count = len(values)
            raw[row_index, :count] = torch.as_tensor([row.values for row in values], dtype=torch.float32)
            starts[row_index, :count] = torch.as_tensor([row.bar_start_us for row in values], dtype=torch.long)
            mask[row_index, :count] = True
        projected = project_stationary_features(
            raw,
            starts,
            timeframe_us=TIMEFRAME_US_BY_NAME[view],
        ) * mask.unsqueeze(-1)
        model_views[view] = projected.to(device=device, non_blocking=True)
        masks[view] = mask.to(device=device, non_blocking=True)
    origin_indices = torch.as_tensor(
        [[length - 1] for length in lengths["1s"]], dtype=torch.long, device=device
    )
    origin_timestamps = torch.as_tensor(origins, dtype=torch.long, device=device).unsqueeze(1)
    asof_indices = {
        view: torch.as_tensor([[length - 1] for length in lengths[view]], dtype=torch.long, device=device)
        for view in ALL_VIEWS if view != "1s"
    }
    return PreparedBatch(
        tickers=tuple(selected),
        origins_us=tuple(origins),
        base_prices=tuple(base_prices),
        real_lengths=lengths,
        views=model_views,
        view_masks=masks,
        origin_indices=origin_indices,
        origin_timestamps_us=origin_timestamps,
        asof_indices=asof_indices,
        target_clock_features=build_target_clock_features(
            origin_timestamps, tuple(release.data_config.horizons_us)
        ).to(device=device),
    )


def release_summary(release: LoadedRelease) -> dict[str, Any]:
    parameter_count = sum(parameter.numel() for parameter in release.model.parameters())
    return {
        "model_id": release.config.model_id,
        "version": release.config.version,
        "role": release.config.role,
        "artifact_name": release.config.checkpoint.name,
        "checkpoint_hash": release.checkpoint_hash,
        "contract_hash": release.contract_hash,
        "device": str(release.device),
        "dtype": str(release.dtype).removeprefix("torch."),
        "parameter_count": parameter_count,
        "horizons_us": list(release.data_config.horizons_us),
        "context_bars": release.data_config.attention_window_by_name,
        "kv_cache": "disabled_full_prefix_authority",
    }


def _base_prices(rows: list[RawBar]) -> dict[str, float]:
    result: dict[str, float] = {}
    for family in ("trade", "bid", "ask"):
        present_index = FEATURE_INDEX[f"{family}_present"]
        close_index = FEATURE_INDEX[f"{family}_close"]
        for row in reversed(rows):
            if row.values[present_index] > 0 and row.values[close_index] > 0:
                result[family] = float(row.values[close_index])
                break
    return result


def _resolve_device(value: str) -> torch.device:
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("BAR_GPT_DEVICE requests CUDA but CUDA is unavailable")
    return device


def _resolve_dtype(value: str, device: torch.device) -> torch.dtype:
    aliases = {"float32": torch.float32, "fp32": torch.float32, "float16": torch.float16, "fp16": torch.float16, "bfloat16": torch.bfloat16, "bf16": torch.bfloat16}
    if value not in aliases:
        raise ValueError(f"unsupported BAR_GPT_DTYPE {value!r}")
    dtype = aliases[value]
    return torch.float32 if device.type == "cpu" and dtype == torch.float16 else dtype


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
