from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import torch

from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES, FEATURE_SPECS


@dataclass(slots=True)
class BarView:
    features: torch.Tensor
    bar_start_us: torch.Tensor
    bar_end_us: torch.Tensor
    available_at_us: torch.Tensor


@dataclass(slots=True)
class MultiscaleBlock:
    views: dict[str, BarView]
    asof_indices: dict[str, torch.Tensor]
    origin_indices: torch.Tensor
    target_indices: torch.Tensor
    target_mask: torch.Tensor


def causal_asof_indices(coarse_available_at_us: torch.Tensor, fine_anchor_us: torch.Tensor) -> torch.Tensor:
    """Return last coarse index with available_at <= each fine anchor."""
    if coarse_available_at_us.ndim != 1 or fine_anchor_us.ndim != 1:
        raise ValueError("as-of timestamps must be one-dimensional")
    return torch.searchsorted(coarse_available_at_us.contiguous(), fine_anchor_us.contiguous(), right=True) - 1


def densify_one_second_view(sparse: BarView, *, step_us: int = 1_000_000) -> BarView:
    """Create causal empty-clock rows for one ticker/session without fabricating family values."""
    if sparse.features.ndim != 2 or sparse.features.shape[0] == 0:
        raise ValueError("a non-empty single-session sparse view is required")
    if step_us <= 0:
        raise ValueError("step_us must be positive")
    first = int(sparse.bar_start_us[0])
    last = int(sparse.bar_start_us[-1])
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
        target_indices=target_indices,
        target_mask=target_mask,
    )


def feature_reducers() -> Mapping[str, str]:
    return {spec.name: spec.reducer for spec in FEATURE_SPECS}
