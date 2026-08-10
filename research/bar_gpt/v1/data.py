from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import torch

from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES, FEATURE_SPECS
from research.bar_gpt.v1.features import project_stationary_features
from research.bar_gpt.v1.targets import TARGET_NAMES, build_next_bar_targets, build_physical_horizon_targets


@dataclass(slots=True)
class BarView:
    features: torch.Tensor
    bar_start_us: torch.Tensor
    bar_end_us: torch.Tensor
    available_at_us: torch.Tensor


class FixedBucketHistoryCache:
    """Bounded causal one-second history shared by fixed-bucket rollups."""

    def __init__(self, *, max_rows: int) -> None:
        if int(max_rows) <= 0:
            raise ValueError("max_rows must be positive")
        self.max_rows = int(max_rows)
        # Keep session-sized chunks instead of repeatedly concatenating the
        # complete warmup on every append.  The contiguous view is materialized
        # only when a consumer actually needs it.
        self._chunks: list[BarView] = []
        self._rows = 0
        self._view: BarView | None = None

    @property
    def view(self) -> BarView | None:
        if self._view is None and self._chunks:
            if len(self._chunks) == 1:
                self._view = self._chunks[0]
            else:
                self._view = BarView(
                    features=torch.cat(tuple(item.features for item in self._chunks), dim=0),
                    bar_start_us=torch.cat(tuple(item.bar_start_us for item in self._chunks), dim=0),
                    bar_end_us=torch.cat(tuple(item.bar_end_us for item in self._chunks), dim=0),
                    available_at_us=torch.cat(tuple(item.available_at_us for item in self._chunks), dim=0),
                )
                # The contiguous view is the new cache authority.  Retaining
                # both it and every source session doubles the resident
                # history without preserving any information.
                self._chunks = [self._view]
        return self._view

    @property
    def rows(self) -> int:
        return self._rows

    def append(self, value: BarView, *, materialize: bool = True) -> BarView | None:
        if value.features.ndim != 2 or value.bar_start_us.ndim != 1:
            raise ValueError("history cache accepts aligned one-dimensional bar metadata")
        if value.features.shape[0] != value.bar_start_us.shape[0] or value.features.shape[0] != value.available_at_us.shape[0]:
            raise ValueError("history cache metadata must align with features")
        previous = self._chunks[-1] if self._chunks else None
        if previous is not None and int(previous.available_at_us[-1]) >= int(value.bar_start_us[0]):
            raise ValueError("history cache append must be strictly chronological")
        self._chunks.append(value)
        self._rows += int(value.features.shape[0])
        while self._chunks and self._rows - int(self._chunks[0].features.shape[0]) >= self.max_rows:
            removed = self._chunks.pop(0)
            self._rows -= int(removed.features.shape[0])
        if self._chunks and self._rows > self.max_rows:
            first = self._chunks[0]
            trim = self._rows - self.max_rows
            self._chunks[0] = BarView(
                features=first.features[trim:],
                bar_start_us=first.bar_start_us[trim:],
                bar_end_us=first.bar_end_us[trim:],
                available_at_us=first.available_at_us[trim:],
            )
            self._rows = self.max_rows
        self._view = None
        return self.view if materialize else None


@dataclass(slots=True)
class MultiscaleBlock:
    views: dict[str, BarView]
    asof_indices: dict[str, torch.Tensor]
    origin_indices: torch.Tensor
    origin_timestamps_us: torch.Tensor
    target_indices: torch.Tensor
    target_mask: torch.Tensor


TIMEFRAME_US_BY_NAME: dict[str, int] = {
    "1s": 1_000_000, "5s": 5_000_000, "10s": 10_000_000, "30s": 30_000_000,
    "1m": 60_000_000, "5m": 300_000_000, "30m": 1_800_000_000, "1h": 3_600_000_000,
    "1D": 86_400_000_000,
    "1W": 604_800_000_000,
    "1MO": 2_629_800_000_000,
}
PATHWAY_ID_BY_NAME: dict[str, int] = {
    "1s": 0, "5s": 0, "10s": 0, "30s": 0,
    "1m": 1, "5m": 1, "30m": 1, "1h": 1,
    "1D": 2, "1W": 2, "1MO": 2,
}
AUTOREGRESSIVE_VIEW_NAMES: tuple[str, ...] = ("1s", "5s", "10s", "30s", "1m", "5m", "30m", "1h")


@dataclass(slots=True)
class BarGPTExample:
    ticker: str
    local_date: str
    raw_views: dict[str, torch.Tensor]
    raw_view_start_us: dict[str, torch.Tensor]
    raw_view_end_us: dict[str, torch.Tensor]
    raw_view_available_at_us: dict[str, torch.Tensor]
    origin_indices: torch.Tensor
    origin_timestamps_us: torch.Tensor
    asof_indices: dict[str, torch.Tensor]
    target_support: torch.Tensor
    target_support_available_at_us: torch.Tensor
    target_coverage_end_us: int
    target_share_factors: torch.Tensor
    target_condition_available_at_us: torch.Tensor
    target_condition_flags: torch.Tensor
    support_origin_indices: torch.Tensor
    horizon_targets: torch.Tensor | None
    horizon_mask: torch.Tensor | None
    horizons_us: tuple[int, ...]
    base_timeframe_us: int
    activity_regime: int
    worker_id: int = 0
    unit_index: int = -1
    block_offset: int = -1
    session_phase: str = "unknown"
    has_condition_target: bool = False
    loader_stage_seconds: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class BarGPTBatch:
    views: dict[str, torch.Tensor]
    origin_indices: torch.Tensor
    origin_timestamps_us: torch.Tensor
    origin_mask: torch.Tensor
    asof_indices: dict[str, torch.Tensor]
    autoregressive_targets: dict[str, torch.Tensor]
    autoregressive_mask: dict[str, torch.Tensor]
    target_support: torch.Tensor
    target_support_lengths: torch.Tensor
    target_support_available_at_us: torch.Tensor
    target_coverage_end_us: torch.Tensor
    target_share_factors: torch.Tensor
    target_condition_available_at_us: torch.Tensor
    target_condition_lengths: torch.Tensor
    target_condition_flags: torch.Tensor
    support_origin_indices: torch.Tensor
    horizons_us: tuple[int, ...]
    base_timeframe_us: int
    horizon_targets: torch.Tensor | None
    horizon_mask: torch.Tensor | None
    sample_weights: torch.Tensor
    tickers: tuple[str, ...]
    local_dates: tuple[str, ...]
    worker_ids: tuple[int, ...]
    unit_indices: tuple[int, ...]
    block_offsets: tuple[int, ...]
    session_phases: tuple[str, ...]
    condition_blocks: tuple[bool, ...]
    # CPU-side loader timings are deliberately retained off-device.  They are
    # diagnostic evidence only and never participate in model computation.
    loader_stage_seconds: dict[str, float] = field(default_factory=dict)

    @property
    def origin_count(self) -> int:
        return int(self.origin_mask.sum().item())

    def record_stream(self, stream: torch.Stream) -> None:
        """Keep asynchronously staged tensors alive on their consuming stream."""
        for values in (
            self.views,
            self.asof_indices,
            self.autoregressive_targets,
            self.autoregressive_mask,
        ):
            for value in values.values():
                value.record_stream(stream)
        for value in (
            self.origin_indices,
            self.origin_timestamps_us,
            self.origin_mask,
            self.target_support,
            self.target_support_lengths,
            self.target_support_available_at_us,
            self.target_coverage_end_us,
            self.target_share_factors,
            self.target_condition_available_at_us,
            self.target_condition_lengths,
            self.target_condition_flags,
            self.support_origin_indices,
            self.horizon_targets,
            self.horizon_mask,
            self.sample_weights,
        ):
            if value is not None:
                value.record_stream(stream)

    def pin_memory(self) -> "BarGPTBatch":
        """Pin every CPU tensor so the CUDA prefetch stream can copy asynchronously."""
        def pin_map(values: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
            return {key: value.pin_memory() for key, value in values.items()}

        return BarGPTBatch(
            views=pin_map(self.views),
            origin_indices=self.origin_indices.pin_memory(),
            origin_timestamps_us=self.origin_timestamps_us.pin_memory(),
            origin_mask=self.origin_mask.pin_memory(),
            asof_indices=pin_map(self.asof_indices),
            autoregressive_targets=pin_map(self.autoregressive_targets),
            autoregressive_mask=pin_map(self.autoregressive_mask),
            target_support=self.target_support.pin_memory(),
            target_support_lengths=self.target_support_lengths.pin_memory(),
            target_support_available_at_us=self.target_support_available_at_us.pin_memory(),
            target_coverage_end_us=self.target_coverage_end_us.pin_memory(),
            target_share_factors=self.target_share_factors.pin_memory(),
            target_condition_available_at_us=self.target_condition_available_at_us.pin_memory(),
            target_condition_lengths=self.target_condition_lengths.pin_memory(),
            target_condition_flags=self.target_condition_flags.pin_memory(),
            support_origin_indices=self.support_origin_indices.pin_memory(),
            horizons_us=self.horizons_us,
            base_timeframe_us=self.base_timeframe_us,
            horizon_targets=self.horizon_targets.pin_memory() if self.horizon_targets is not None else None,
            horizon_mask=self.horizon_mask.pin_memory() if self.horizon_mask is not None else None,
            sample_weights=self.sample_weights.pin_memory(),
            tickers=self.tickers,
            local_dates=self.local_dates,
            worker_ids=self.worker_ids,
            unit_indices=self.unit_indices,
            block_offsets=self.block_offsets,
            session_phases=self.session_phases,
            condition_blocks=self.condition_blocks,
            loader_stage_seconds=dict(self.loader_stage_seconds),
        )
    def to(self, device: torch.device | str, *, non_blocking: bool = True) -> "BarGPTBatch":
        def move_map(values: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
            return {key: value.to(device, non_blocking=non_blocking) for key, value in values.items()}
        support = self.target_support.to(device, non_blocking=non_blocking)
        share_factors = self.target_share_factors.to(device, non_blocking=non_blocking)
        condition_flags = self.target_condition_flags.to(device, non_blocking=non_blocking)
        support_lengths = self.target_support_lengths.to(device, non_blocking=non_blocking)
        support_available = self.target_support_available_at_us.to(device, non_blocking=non_blocking)
        coverage_ends = self.target_coverage_end_us.to(device, non_blocking=non_blocking)
        condition_available = self.target_condition_available_at_us.to(device, non_blocking=non_blocking)
        condition_lengths = self.target_condition_lengths.to(device, non_blocking=non_blocking)
        support_origins = self.support_origin_indices.to(device, non_blocking=non_blocking)
        if self.horizon_targets is None or self.horizon_mask is None:
            support_length_values = self.target_support_lengths.tolist()
            origin_count_values = self.origin_mask.sum(dim=1).tolist()
            built = [
                build_physical_horizon_targets(
                    support[row, : int(support_length_values[row])],
                    support_origins[row, : int(origin_count_values[row])],
                    torch.as_tensor(self.horizons_us, dtype=torch.long, device=support.device),
                    base_timeframe_us=self.base_timeframe_us,
                    available_at_us=support_available[row, : int(support_length_values[row])],
                    coverage_end_us=coverage_ends[row],
                    share_factors=share_factors[row, : int(support_length_values[row])],
                    condition_available_at_us=condition_available[row, : int(condition_lengths[row])],
                    condition_flags=condition_flags[row, : int(condition_lengths[row])],
                )
                for row in range(support.shape[0])
            ]
            horizon_targets = _pad_first_dimension([item.values for item in built])
            horizon_mask = _pad_first_dimension([item.mask for item in built], fill=False)
        else:
            horizon_targets = self.horizon_targets.to(device, non_blocking=non_blocking)
            horizon_mask = self.horizon_mask.to(device, non_blocking=non_blocking)
        return BarGPTBatch(
            views=move_map(self.views),
            origin_indices=self.origin_indices.to(device, non_blocking=non_blocking),
            origin_timestamps_us=self.origin_timestamps_us.to(device, non_blocking=non_blocking),
            origin_mask=self.origin_mask.to(device, non_blocking=non_blocking),
            asof_indices=move_map(self.asof_indices),
            autoregressive_targets=move_map(self.autoregressive_targets),
            autoregressive_mask=move_map(self.autoregressive_mask),
            target_support=support,
            target_support_lengths=support_lengths,
            target_support_available_at_us=support_available,
            target_coverage_end_us=coverage_ends,
            target_share_factors=share_factors,
            target_condition_available_at_us=condition_available,
            target_condition_lengths=condition_lengths,
            target_condition_flags=condition_flags,
            support_origin_indices=support_origins,
            horizons_us=self.horizons_us,
            base_timeframe_us=self.base_timeframe_us,
            horizon_targets=horizon_targets,
            horizon_mask=horizon_mask,
            sample_weights=self.sample_weights.to(device, non_blocking=non_blocking),
            tickers=self.tickers,
            local_dates=self.local_dates,
            worker_ids=self.worker_ids,
            unit_indices=self.unit_indices,
            block_offsets=self.block_offsets,
            session_phases=self.session_phases,
            condition_blocks=self.condition_blocks,
            loader_stage_seconds=dict(self.loader_stage_seconds),
        )


def _pad_first_dimension(values: list[torch.Tensor], *, fill: float | int | bool = 0) -> torch.Tensor:
    maximum = max(value.shape[0] for value in values)
    shape = (len(values), maximum, *values[0].shape[1:])
    output = torch.full(shape, fill, dtype=values[0].dtype, device=values[0].device)
    for row, value in enumerate(values):
        output[row, : value.shape[0]] = value
    return output


def collate_examples(examples: Sequence[BarGPTExample], *, balance_activity_regimes: bool = True) -> BarGPTBatch:
    if not examples:
        raise ValueError("cannot collate an empty BarGPT batch")
    view_names = tuple(examples[0].raw_views)
    if any(tuple(example.raw_views) != view_names for example in examples):
        raise ValueError("all examples in a batch must expose the same ordered views")
    if any(example.horizons_us != examples[0].horizons_us or example.base_timeframe_us != examples[0].base_timeframe_us for example in examples):
        raise ValueError("all examples in a batch must use the same physical target contract")
    raw_by_view = {name: [example.raw_views[name] for example in examples] for name in view_names}
    views = {
        name: _pad_first_dimension([
            project_stationary_features(value, example.raw_view_start_us[name], timeframe_us=TIMEFRAME_US_BY_NAME[name])
            for value, example in zip(values, examples, strict=True)
        ])
        for name, values in raw_by_view.items()
    }
    ar_targets: dict[str, torch.Tensor] = {}
    ar_masks: dict[str, torch.Tensor] = {}
    for name, values in raw_by_view.items():
        if name not in AUTOREGRESSIVE_VIEW_NAMES:
            continue
        built = []
        for value, example in zip(values, examples, strict=True):
            item = build_next_bar_targets(
                value,
                bar_start_us=example.raw_view_start_us[name] if name in AUTOREGRESSIVE_VIEW_NAMES else None,
                expected_step_us=TIMEFRAME_US_BY_NAME[name] if name in AUTOREGRESSIVE_VIEW_NAMES else None,
            )
            available = example.raw_view_available_at_us[name]
            if available.shape != (value.shape[0],):
                raise ValueError(f"{name} availability timestamps must align with raw rows")
            if item.mask.shape[0]:
                newly_available = (
                    (available[1:] >= int(example.origin_timestamps_us[0]))
                    & (available[1:] <= int(example.origin_timestamps_us[-1]))
                )
                item.mask &= newly_available[:, None]
            built.append(item)
        ar_targets[name] = _pad_first_dimension([item.values for item in built])
        ar_masks[name] = _pad_first_dimension([item.mask for item in built], fill=False)
    origin_indices = _pad_first_dimension([example.origin_indices for example in examples], fill=0)
    origin_timestamps_us = _pad_first_dimension([example.origin_timestamps_us for example in examples], fill=0)
    origin_mask = _pad_first_dimension(
        [torch.ones(example.origin_indices.shape[0], dtype=torch.bool) for example in examples],
        fill=False,
    )
    asof = {
        name: _pad_first_dimension([example.asof_indices[name] for example in examples], fill=-1)
        for name in view_names if name != "1s"
    }
    regimes = torch.as_tensor([example.activity_regime for example in examples], dtype=torch.long)
    weights = torch.ones(len(examples), dtype=torch.float32)
    if balance_activity_regimes:
        for regime in range(3):
            selected = regimes == regime
            count = int(selected.sum())
            if count:
                weights[selected] = len(examples) / (3.0 * count)
        weights /= weights.mean().clamp_min(1e-12)
    loader_stage_seconds: dict[str, float] = {}
    for example in examples:
        for name, seconds in example.loader_stage_seconds.items():
            loader_stage_seconds[name] = loader_stage_seconds.get(name, 0.0) + float(seconds)
    return BarGPTBatch(
        views=views,
        origin_indices=origin_indices,
        origin_timestamps_us=origin_timestamps_us,
        origin_mask=origin_mask,
        asof_indices=asof,
        autoregressive_targets=ar_targets,
        autoregressive_mask=ar_masks,
        target_support=_pad_first_dimension([example.target_support for example in examples]),
        target_support_lengths=torch.as_tensor([example.target_support.shape[0] for example in examples], dtype=torch.long),
        target_support_available_at_us=_pad_first_dimension([example.target_support_available_at_us for example in examples]),
        target_coverage_end_us=torch.as_tensor([example.target_coverage_end_us for example in examples], dtype=torch.long),
        target_share_factors=_pad_first_dimension([example.target_share_factors for example in examples], fill=1.0),
        target_condition_available_at_us=_pad_first_dimension([example.target_condition_available_at_us for example in examples]),
        target_condition_lengths=torch.as_tensor([example.target_condition_flags.shape[0] for example in examples], dtype=torch.long),
        target_condition_flags=_pad_first_dimension([example.target_condition_flags for example in examples]),
        support_origin_indices=_pad_first_dimension([example.support_origin_indices for example in examples], fill=0),
        horizons_us=examples[0].horizons_us,
        base_timeframe_us=examples[0].base_timeframe_us,
        horizon_targets=(
            _pad_first_dimension([example.horizon_targets for example in examples if example.horizon_targets is not None])
            if all(example.horizon_targets is not None for example in examples)
            else None
        ),
        horizon_mask=(
            _pad_first_dimension(
                [example.horizon_mask for example in examples if example.horizon_mask is not None], fill=False
            )
            if all(example.horizon_mask is not None for example in examples)
            else None
        ),
        sample_weights=weights,
        tickers=tuple(example.ticker for example in examples),
        local_dates=tuple(example.local_date for example in examples),
        worker_ids=tuple(example.worker_id for example in examples),
        unit_indices=tuple(example.unit_index for example in examples),
        block_offsets=tuple(example.block_offset for example in examples),
        session_phases=tuple(example.session_phase for example in examples),
        condition_blocks=tuple(example.has_condition_target for example in examples),
        loader_stage_seconds=loader_stage_seconds,
    )


def causal_asof_indices(coarse_available_at_us: torch.Tensor, fine_anchor_us: torch.Tensor) -> torch.Tensor:
    """Return last coarse index with available_at <= each fine anchor."""
    if coarse_available_at_us.ndim != 1 or fine_anchor_us.ndim != 1:
        raise ValueError("as-of timestamps must be one-dimensional")
    return torch.searchsorted(coarse_available_at_us.contiguous(), fine_anchor_us.contiguous(), right=True) - 1


def densify_one_second_view(
    sparse: BarView,
    *,
    step_us: int = 1_000_000,
    clock_start_us: int | None = None,
    clock_end_us: int | None = None,
) -> BarView:
    """Create causal empty-clock rows for one ticker/session without fabricating family values."""
    if sparse.features.ndim != 2 or sparse.features.shape[0] == 0:
        raise ValueError("a non-empty single-session sparse view is required")
    if step_us <= 0:
        raise ValueError("step_us must be positive")
    first = int(sparse.bar_start_us[0]) if clock_start_us is None else int(clock_start_us)
    exclusive_end = int(sparse.bar_start_us[-1]) + step_us if clock_end_us is None else int(clock_end_us)
    last = exclusive_end - step_us
    if first > int(sparse.bar_start_us[0]) or exclusive_end <= int(sparse.bar_start_us[-1]):
        raise ValueError("requested dense clock does not contain every sparse bar")
    if last - first > 24 * 60 * 60 * 1_000_000:
        raise ValueError("densify_one_second_view accepts one ticker/session at a time")
    starts = torch.arange(first, last + step_us, step_us, device=sparse.bar_start_us.device, dtype=sparse.bar_start_us.dtype)
    positions = torch.searchsorted(starts, sparse.bar_start_us)
    if not torch.equal(starts[positions], sparse.bar_start_us):
        raise ValueError("sparse bar starts are not aligned to the requested clock")
    features = torch.zeros((starts.numel(), sparse.features.shape[1]), device=sparse.features.device, dtype=sparse.features.dtype)
    features[positions] = sparse.features
    ends = starts + int(step_us)
    return BarView(features=features, bar_start_us=starts, bar_end_us=ends, available_at_us=ends)


def horizon_target_indices(
    support_available_at_us: torch.Tensor,
    anchor_us: torch.Tensor,
    horizons_us: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Find the last completed support bar at or before anchor+horizon."""
    if support_available_at_us.ndim != 1 or anchor_us.ndim != 1 or horizons_us.ndim != 1:
        raise ValueError("support, anchors, and horizons must be one-dimensional")
    requested = anchor_us[:, None] + horizons_us[None, :]
    flat = requested.reshape(-1).contiguous()
    indices = torch.searchsorted(support_available_at_us.contiguous(), flat, right=True) - 1
    indices = indices.reshape(requested.shape)
    valid = (indices >= 0) & (requested <= support_available_at_us[-1])
    return indices, valid


def _feature_validity(features: torch.Tensor, validity: str) -> torch.Tensor:
    if validity == "always":
        return torch.ones(features.shape[0], dtype=torch.bool, device=features.device)
    return features[:, FEATURE_INDEX[validity]] > 0


def _segment_reduce_column(
    values: torch.Tensor,
    inverse: torch.Tensor,
    group_count: int,
    reducer: str,
    valid: torch.Tensor,
) -> torch.Tensor:
    dtype = values.dtype
    device = values.device
    if reducer == "sum":
        output = torch.zeros(group_count, dtype=dtype, device=device)
        output.scatter_add_(0, inverse, torch.where(valid, values, torch.zeros_like(values)))
        return output
    if reducer in {"max", "min"}:
        fill = -torch.inf if reducer == "max" else torch.inf
        output = torch.full((group_count,), fill, dtype=dtype, device=device)
        output.scatter_reduce_(
            0,
            inverse,
            torch.where(valid, values, torch.full_like(values, fill)),
            reduce="amax" if reducer == "max" else "amin",
            include_self=True,
        )
        return torch.where(torch.isfinite(output), output, torch.zeros_like(output))
    row_index = torch.arange(values.shape[0], device=device, dtype=torch.long)
    sentinel = values.shape[0] if reducer == "first" else -1
    selected = torch.full((group_count,), sentinel, device=device, dtype=torch.long)
    selected.scatter_reduce_(
        0,
        inverse,
        torch.where(valid, row_index, torch.full_like(row_index, sentinel)),
        reduce="amin" if reducer == "first" else "amax",
        include_self=True,
    )
    available = selected.ge(0) & selected.lt(values.shape[0])
    safe = selected.clamp(min=0, max=max(values.shape[0] - 1, 0))
    return torch.where(available, values[safe], torch.zeros(group_count, dtype=dtype, device=device))


def rollup_intraday_view(base: BarView, timeframe_us: int) -> BarView:
    """Roll completed rich 1s sufficient statistics into a coarser fixed interval."""
    if base.features.ndim != 2 or base.features.shape[1] != len(FEATURE_NAMES):
        raise ValueError(f"base features must have shape [T,{len(FEATURE_NAMES)}]")
    if timeframe_us <= 0:
        raise ValueError("timeframe_us must be positive")
    bucket_ids = torch.div(base.bar_start_us, int(timeframe_us), rounding_mode="floor")
    unique_ids, inverse = torch.unique_consecutive(bucket_ids, return_inverse=True)
    group_count = int(unique_ids.numel())
    columns: list[torch.Tensor] = []
    for column_index, spec in enumerate(FEATURE_SPECS):
        columns.append(
            _segment_reduce_column(
                base.features[:, column_index],
                inverse,
                group_count,
                spec.reducer,
                _feature_validity(base.features, spec.validity),
            )
        )
    result = torch.stack(columns, dim=-1)
    starts = unique_ids * int(timeframe_us)
    ends = starts + int(timeframe_us)
    complete = ends <= base.available_at_us[-1]
    return BarView(
        features=result[complete],
        bar_start_us=starts[complete],
        bar_end_us=ends[complete],
        available_at_us=ends[complete],
    )


def rollup_calendar_view(daily: BarView, period_ids: torch.Tensor) -> BarView:
    """Aggregate completed daily bars into caller-defined ISO-week or calendar-month periods."""
    if period_ids.ndim != 1 or period_ids.shape[0] != daily.features.shape[0]:
        raise ValueError("period_ids must align one-to-one with daily bars")
    unique_ids, inverse = torch.unique_consecutive(period_ids, return_inverse=True)
    group_count = int(unique_ids.numel())
    columns: list[torch.Tensor] = []
    for column_index, spec in enumerate(FEATURE_SPECS):
        columns.append(
            _segment_reduce_column(
                daily.features[:, column_index],
                inverse,
                group_count,
                spec.reducer,
                _feature_validity(daily.features, spec.validity),
            )
        )
    group_rows = torch.arange(daily.features.shape[0], device=daily.features.device, dtype=torch.long)
    first = torch.full((group_count,), daily.features.shape[0], device=daily.features.device, dtype=torch.long)
    last = torch.full((group_count,), -1, device=daily.features.device, dtype=torch.long)
    first.scatter_reduce_(0, inverse, group_rows, reduce="amin", include_self=True)
    last.scatter_reduce_(0, inverse, group_rows, reduce="amax", include_self=True)
    return BarView(
        features=torch.stack(columns, dim=-1),
        bar_start_us=daily.bar_start_us[first],
        bar_end_us=daily.bar_end_us[last],
        available_at_us=daily.available_at_us[last],
    )


def build_multiscale_block(
    one_second: BarView,
    *,
    intraday_timeframes_us: Sequence[int],
    origin_slice: slice,
    horizons_us: Sequence[int],
) -> MultiscaleBlock:
    one_second = densify_one_second_view(one_second)
    views: dict[str, BarView] = {"1s": one_second}
    for timeframe_us in intraday_timeframes_us:
        if int(timeframe_us) <= 1_000_000:
            continue
        views[f"{int(timeframe_us)}us"] = rollup_intraday_view(one_second, int(timeframe_us))
    origin_indices = torch.arange(one_second.features.shape[0], device=one_second.features.device)[origin_slice]
    anchors = one_second.available_at_us[origin_indices]
    asof = {
        name: causal_asof_indices(view.available_at_us, anchors)
        for name, view in views.items()
        if name != "1s"
    }
    target_indices, target_mask = horizon_target_indices(
        one_second.available_at_us,
        anchors,
        torch.as_tensor(tuple(horizons_us), device=anchors.device, dtype=anchors.dtype),
    )
    return MultiscaleBlock(
        views=views,
        asof_indices=asof,
        origin_indices=origin_indices,
        origin_timestamps_us=anchors,
        target_indices=target_indices,
        target_mask=target_mask,
    )


def feature_reducers() -> Mapping[str, str]:
    return {spec.name: spec.reducer for spec in FEATURE_SPECS}
