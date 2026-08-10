from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.bar_gpt.v1.schema import FEATURE_INDEX


AUTOREGRESSIVE_TARGET_NAMES: tuple[str, ...] = (
    "endpoint_return",
    "upper_excursion",
    "lower_excursion",
    "realized_volatility",
    "log_trade_volume",
    "log_trade_count",
    "trade_available",
    "bid_available",
    "ask_available",
    "quote_pair_available",
    "halt_pause_within_horizon",
    "resume_within_horizon",
    "news_risk_within_horizon",
    "luld_limit_state_within_horizon",
)
TARGET_NAMES: tuple[str, ...] = (
    "bid_return",
    "ask_return",
    "trade_return",
    "upper_excursion",
    "lower_excursion",
    "realized_volatility",
    "log_trade_volume",
    "log_trade_count",
    "trade_available",
    "bid_available",
    "ask_available",
    "quote_pair_available",
    "halt_pause_within_horizon",
    "resume_within_horizon",
    "news_risk_within_horizon",
    "luld_limit_state_within_horizon",
)
CONDITION_TARGET_NAMES: tuple[str, ...] = TARGET_NAMES[-4:]
BINARY_TARGET_NAMES: tuple[str, ...] = TARGET_NAMES[-8:]
CONTINUOUS_TARGET_NAMES: tuple[str, ...] = TARGET_NAMES[:-8]
AVAILABILITY_TARGET_NAMES: tuple[str, ...] = BINARY_TARGET_NAMES
CONTINUOUS_TARGET_COUNT = len(CONTINUOUS_TARGET_NAMES)
AVAILABILITY_TARGET_COUNT = len(AVAILABILITY_TARGET_NAMES)
AUTOREGRESSIVE_BINARY_TARGET_NAMES = AUTOREGRESSIVE_TARGET_NAMES[-8:]
AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES = AUTOREGRESSIVE_TARGET_NAMES[:-8]
AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT = len(AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES)
AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT = len(AUTOREGRESSIVE_BINARY_TARGET_NAMES)


@dataclass(slots=True)
class HorizonTargets:
    values: torch.Tensor
    mask: torch.Tensor


def build_next_bar_targets(
    raw: torch.Tensor,
    *,
    bar_start_us: torch.Tensor | None = None,
    expected_step_us: int | None = None,
) -> HorizonTargets:
    """Build next completed-bar targets for an arbitrary aggregated timeframe."""
    if raw.ndim not in {2, 3}:
        raise ValueError("raw bar features must have shape [T,F] or [B,T,F]")
    time_dim = raw.shape[-2]
    if time_dim < 2:
        shape = (*raw.shape[:-2], 0, len(AUTOREGRESSIVE_TARGET_NAMES))
        return HorizonTargets(raw.new_zeros(shape), torch.zeros(shape, dtype=torch.bool, device=raw.device))
    trade_present = _column(raw, "trade_present") > 0
    quote_present = _column(raw, "quote_pair_present") > 0
    trade_close = _column(raw, "trade_close")
    midpoint_close = _column(raw, "midpoint_close")
    reference_raw = torch.where(quote_present, midpoint_close, trade_close)
    reference_valid_raw = (quote_present & (midpoint_close > 0)) | (trade_present & (trade_close > 0))
    reference, reference_valid = _forward_fill(reference_raw, reference_valid_raw)
    base = reference[..., :-1]
    endpoint = reference[..., 1:]
    price_valid = reference_valid[..., :-1] & reference_valid[..., 1:] & (base > 0) & (endpoint > 0)
    endpoint_return = torch.where(price_valid, torch.log(endpoint / base), 0.0)
    next_trade = trade_present[..., 1:]
    high = _column(raw, "trade_high")[..., 1:]
    low = _column(raw, "trade_low")[..., 1:]
    excursion_valid = price_valid & next_trade & (high > 0) & (low > 0)
    upper = torch.where(excursion_valid, torch.log(high.clamp_min(1e-12) / base).clamp_min(0.0), 0.0)
    lower = torch.where(excursion_valid, torch.log(base / low.clamp_min(1e-12)).clamp_min(0.0), 0.0)
    availability = tuple((_column(raw, f"{family}_present")[..., 1:] > 0).float() for family in ("trade", "bid", "ask"))
    values = torch.stack(
        (
            torch.asinh(endpoint_return * 100.0),
            torch.asinh(upper * 100.0),
            torch.asinh(lower * 100.0),
            torch.asinh(endpoint_return.abs() * 100.0),
            torch.log1p(_column(raw, "trade_size_sum")[..., 1:]),
            torch.log1p(_column(raw, "trade_event_count")[..., 1:]),
            *availability,
            quote_present[..., 1:].float(),
            *raw.new_zeros((*raw.shape[:-2], 4, time_dim - 1)).unbind(dim=-2),
        ),
        dim=-1,
    )
    mask = torch.ones_like(values, dtype=torch.bool)
    mask[..., :4] &= price_valid[..., None]
    mask[..., 1:3] &= excursion_valid[..., None]
    mask[..., 3] = False  # exact intrabar realized volatility is unavailable after aggregation
    mask[..., -4:] = False  # exact event conditions are supervised only by physical-horizon sidecars
    if bar_start_us is not None or expected_step_us is not None:
        if bar_start_us is None or expected_step_us is None:
            raise ValueError("bar_start_us and expected_step_us must be supplied together")
        if bar_start_us.shape != raw.shape[:-1]:
            raise ValueError("bar_start_us must align with raw bar rows")
        continuous = bar_start_us[..., 1:] - bar_start_us[..., :-1] == int(expected_step_us)
        mask &= continuous[..., None]
    return HorizonTargets(values=values, mask=mask)


def _column(raw: torch.Tensor, name: str) -> torch.Tensor:
    return raw[..., FEATURE_INDEX[name]].float()


def _forward_fill(values: torch.Tensor, valid: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    rows = torch.arange(values.shape[-1], device=values.device, dtype=torch.long)
    rows = rows.view(*([1] * (values.ndim - 1)), -1).expand_as(values)
    last = torch.where(valid, rows, torch.full_like(rows, -1)).cummax(dim=-1).values
    available = last >= 0
    return torch.gather(values, -1, last.clamp(min=0)), available


def _window_max(values: torch.Tensor, steps: int) -> torch.Tensor:
    length = values.shape[-1]
    if length <= steps:
        return values.new_empty((*values.shape[:-1], 0))
    flattened = values[..., 1:].reshape(-1, 1, length - 1)
    pooled = F.max_pool1d(flattened, kernel_size=steps, stride=1)
    return pooled.reshape(*values.shape[:-1], pooled.shape[-1])


def _gather_time(values: torch.Tensor, indices: torch.Tensor) -> torch.Tensor:
    if values.ndim == 1:
        return values[indices]
    return torch.gather(values, -1, indices)


def _range_extreme(
    values: torch.Tensor,
    starts: torch.Tensor,
    ends: torch.Tensor,
    *,
    reducer: str,
) -> torch.Tensor:
    """Vectorized half-open range min/max using an idempotent sparse table."""
    if values.ndim not in {1, 2} or starts.ndim != 1 or ends.shape != starts.shape:
        raise ValueError("range extrema require values [T]/[T,C] and aligned one-dimensional bounds")
    if reducer not in {"min", "max"}:
        raise ValueError("reducer must be min or max")
    lengths = ends - starts
    valid = lengths > 0
    safe_lengths = lengths.clamp_min(1)
    levels = torch.floor(torch.log2(safe_lengths.float())).long()
    tables = [values]
    offset = 1
    while offset * 2 <= values.shape[0]:
        left = tables[-1][:-offset]
        right = tables[-1][offset:]
        tables.append(torch.maximum(left, right) if reducer == "max" else torch.minimum(left, right))
        offset *= 2
    output_shape = (starts.shape[0],) if values.ndim == 1 else (starts.shape[0], values.shape[1])
    fill = -torch.inf if reducer == "max" else torch.inf
    output = torch.full(output_shape, fill, dtype=values.dtype, device=values.device)
    for level, table in enumerate(tables):
        selected = valid & (levels == level)
        if not bool(selected.any()):
            continue
        width = 1 << level
        left_index = starts[selected]
        right_index = ends[selected] - width
        left_value = table[left_index]
        right_value = table[right_index]
        output[selected] = (
            torch.maximum(left_value, right_value)
            if reducer == "max"
            else torch.minimum(left_value, right_value)
        )
    return output


def build_physical_horizon_targets(
    raw_one_second: torch.Tensor,
    origin_indices: torch.Tensor,
    horizons_us: torch.Tensor,
    *,
    base_timeframe_us: int = 1_000_000,
    available_at_us: torch.Tensor | None = None,
    coverage_end_us: int | torch.Tensor | None = None,
    share_factors: torch.Tensor | None = None,
    condition_available_at_us: torch.Tensor | None = None,
    condition_flags: torch.Tensor | None = None,
) -> HorizonTargets:
    """Build sparse event-bar targets over physical timestamp horizons."""
    if raw_one_second.ndim == 3:
        if origin_indices.ndim != 2 or origin_indices.shape[0] != raw_one_second.shape[0]:
            raise ValueError("batched physical targets require aligned raw and origin batches")
        built = []
        for row in range(raw_one_second.shape[0]):
            built.append(build_physical_horizon_targets(
                raw_one_second[row],
                origin_indices[row],
                horizons_us,
                base_timeframe_us=base_timeframe_us,
                available_at_us=None if available_at_us is None else available_at_us[row],
                coverage_end_us=(
                    coverage_end_us
                    if coverage_end_us is None or torch.as_tensor(coverage_end_us).numel() == 1
                    else torch.as_tensor(coverage_end_us)[row]
                ),
                share_factors=None if share_factors is None else share_factors[row],
                condition_available_at_us=(
                    None if condition_available_at_us is None else condition_available_at_us[row]
                ),
                condition_flags=None if condition_flags is None else condition_flags[row],
            ))
        return HorizonTargets(
            values=torch.stack([item.values for item in built]),
            mask=torch.stack([item.mask for item in built]),
        )
    if raw_one_second.ndim != 2 or origin_indices.ndim != 1 or horizons_us.ndim != 1:
        raise ValueError("physical targets require raw [T,F], origins [N], and horizons [H]")
    if raw_one_second.shape[0] == 0:
        raise ValueError("physical targets require at least one sparse event bar")
    if torch.any(horizons_us <= 0):
        raise ValueError("physical horizons must be positive")
    if available_at_us is None:
        available_at_us = torch.arange(raw_one_second.shape[0], device=raw_one_second.device, dtype=torch.long)
        available_at_us = (available_at_us + 1) * int(base_timeframe_us)
    if available_at_us.shape != (raw_one_second.shape[0],):
        raise ValueError("available_at_us must align one-to-one with sparse event bars")
    if torch.any(available_at_us[..., 1:] <= available_at_us[..., :-1]):
        raise ValueError("sparse event-bar availability timestamps must be strictly increasing")
    if coverage_end_us is None:
        coverage_end_us = available_at_us[-1]
    coverage_end = torch.as_tensor(coverage_end_us, device=available_at_us.device, dtype=torch.long)
    if coverage_end.numel() != 1:
        raise ValueError("coverage_end_us must be scalar for one sparse support stream")
    if share_factors is None:
        share_factors = torch.ones(raw_one_second.shape[:-1], dtype=torch.float64, device=raw_one_second.device)
    if share_factors.shape != raw_one_second.shape[:-1]:
        raise ValueError("share_factors must align one-to-one with raw rows")
    if torch.any(~torch.isfinite(share_factors)) or torch.any(share_factors <= 0):
        raise ValueError("share_factors must be finite and positive")
    share_factors = share_factors.to(device=raw_one_second.device, dtype=raw_one_second.dtype)
    if condition_flags is None:
        condition_flags = torch.zeros((0, 4), dtype=torch.float32, device=raw_one_second.device)
        condition_available_at_us = torch.zeros(0, dtype=torch.long, device=raw_one_second.device)
        condition_authoritative = False
    else:
        if condition_flags.ndim != 2 or condition_flags.shape[-1] != 4:
            raise ValueError("condition_flags must have shape [C,4]")
        if condition_available_at_us is None and condition_flags.shape[0] == available_at_us.shape[0]:
            condition_available_at_us = available_at_us
        if condition_available_at_us is None or condition_available_at_us.shape != condition_flags.shape[:-1]:
            raise ValueError("condition timestamps must align with condition flags")
        condition_flags = condition_flags.to(device=raw_one_second.device, dtype=torch.float32)
        condition_available_at_us = condition_available_at_us.to(device=raw_one_second.device, dtype=torch.long)
        condition_authoritative = True
    trade_present = _column(raw_one_second, "trade_present") > 0
    quote_present = _column(raw_one_second, "quote_pair_present") > 0
    canonical_bid = _column(raw_one_second, "bid_close") * share_factors
    canonical_ask = _column(raw_one_second, "ask_close") * share_factors
    canonical_trade = _column(raw_one_second, "trade_close") * share_factors
    family_values = (canonical_bid, canonical_ask, canonical_trade)
    family_valid = (
        _column(raw_one_second, "bid_present") > 0,
        _column(raw_one_second, "ask_present") > 0,
        trade_present,
    )
    family_reference = tuple(_forward_fill(value, valid) for value, valid in zip(family_values, family_valid, strict=True))
    trade_reference, trade_reference_valid = family_reference[2]
    previous_trade = torch.cat((trade_reference[:1], trade_reference[:-1]), dim=-1)
    returns = torch.where(
        trade_reference_valid & (trade_reference > 0) & (previous_trade > 0),
        torch.log(trade_reference / previous_trade),
        0.0,
    ).float()
    # Additive horizon statistics use float64 prefixes. Parallel float32 scans
    # can produce adjacent, slightly non-monotone prefixes after large earlier
    # activity; subtracting them for a zero-activity window then makes log1p NaN.
    # The exact volume/count invariant is nonnegative, so restore that invariant
    # after the stable prefix difference before returning model-dtype targets.
    prefix_shape = (1,)
    variance_prefix = torch.cat((returns.new_zeros(prefix_shape, dtype=torch.float64), returns.square().double().cumsum(-1)), dim=-1)
    canonical_volume = _column(raw_one_second, "trade_size_sum").double() / share_factors.double()
    volume_prefix = torch.cat((canonical_volume.new_zeros(prefix_shape), canonical_volume.cumsum(-1)), dim=-1)
    trade_count_values = _column(raw_one_second, "trade_event_count").double()
    trade_count_prefix = torch.cat((trade_count_values.new_zeros(prefix_shape), trade_count_values.cumsum(-1)), dim=-1)
    availability_prefix = {
        family: torch.cat(
            (
                torch.zeros(prefix_shape, dtype=torch.int64, device=raw_one_second.device),
                (_column(raw_one_second, f"{family}_present") > 0).to(torch.int64).cumsum(-1),
            ), dim=-1
        )
        for family in ("trade", "bid", "ask")
    }
    quote_availability_prefix = torch.cat(
        (
            torch.zeros(prefix_shape, dtype=torch.int64, device=raw_one_second.device),
            quote_present.to(torch.int64).cumsum(-1),
        ), dim=-1
    )
    canonical_trade_high = _column(raw_one_second, "trade_high") * share_factors
    canonical_trade_low = _column(raw_one_second, "trade_low") * share_factors
    trade_high = torch.where(trade_present, canonical_trade_high, torch.full_like(canonical_trade_high, -torch.inf))
    trade_low = torch.where(trade_present, canonical_trade_low, torch.full_like(canonical_trade_low, torch.inf))
    origin_times = available_at_us[origin_indices]
    horizon_values: list[torch.Tensor] = []
    horizon_masks: list[torch.Tensor] = []
    total = raw_one_second.shape[-2]
    for horizon in horizons_us.tolist():
        requested = origin_times + int(horizon)
        in_range = requested <= coverage_end
        endpoint = torch.searchsorted(available_at_us.contiguous(), requested.contiguous(), right=True) - 1
        safe_endpoint = endpoint.clamp(min=0, max=max(total - 1, 0))
        starts = origin_indices + 1
        ends = safe_endpoint + 1
        family_returns: list[torch.Tensor] = []
        family_return_masks: list[torch.Tensor] = []
        for (values, valid), direct_valid in zip(family_reference, family_valid, strict=True):
            base_price = values[origin_indices]
            endpoint_price = values[safe_endpoint]
            valid_prefix = torch.cat((
                torch.zeros((*direct_valid.shape[:-1], 1), dtype=torch.int64, device=direct_valid.device),
                direct_valid.to(torch.int64).cumsum(-1),
            ), dim=-1)
            updated = valid_prefix[ends] - valid_prefix[starts] > 0
            mask = in_range & updated & valid[origin_indices] & (base_price > 0) & (endpoint_price > 0)
            family_returns.append(torch.where(mask, torch.log(endpoint_price / base_price), 0.0))
            family_return_masks.append(mask)
        trade_base = trade_reference[origin_indices]
        max_price = _range_extreme(trade_high, starts, ends, reducer="max")
        min_price = _range_extreme(trade_low, starts, ends, reducer="min")
        excursion_valid = in_range & family_return_masks[2] & torch.isfinite(max_price) & torch.isfinite(min_price)
        upper = torch.where(excursion_valid, torch.log(max_price.clamp_min(1e-12) / trade_base.clamp_min(1e-12)).clamp_min(0.0), 0.0)
        lower = torch.where(excursion_valid, torch.log(trade_base.clamp_min(1e-12) / min_price.clamp_min(1e-12)).clamp_min(0.0), 0.0)
        realized = (variance_prefix[ends] - variance_prefix[starts]).clamp_min(0.0).sqrt().to(raw_one_second.dtype)
        realized = torch.where(family_return_masks[2], realized, torch.zeros_like(realized))
        volume = (
            (volume_prefix[ends] - volume_prefix[starts]).clamp_min(0.0)
            * share_factors[origin_indices].double()
        ).to(raw_one_second.dtype)
        trade_count = (trade_count_prefix[ends] - trade_count_prefix[starts]).clamp_min(0.0).to(raw_one_second.dtype)
        availability = [
            (availability_prefix[family][ends] - availability_prefix[family][starts] > 0).float()
            for family in ("trade", "bid", "ask")
        ]
        quote_available = (quote_availability_prefix[ends] - quote_availability_prefix[starts] > 0).float()
        condition_starts = torch.searchsorted(condition_available_at_us, origin_times, right=True)
        condition_ends = torch.searchsorted(condition_available_at_us, requested, right=True)
        condition_window = _range_extreme(condition_flags, condition_starts, condition_ends, reducer="max")
        condition_window = torch.where(torch.isfinite(condition_window), condition_window, torch.zeros_like(condition_window))
        condition_window = torch.where(in_range[..., None], condition_window, torch.zeros_like(condition_window))
        values = torch.stack(
            (
                *(torch.asinh(value * 100.0) for value in family_returns),
                torch.asinh(upper * 100.0),
                torch.asinh(lower * 100.0),
                torch.asinh(realized * 100.0),
                torch.log1p(volume),
                torch.log1p(trade_count),
                *availability,
                quote_available,
                *condition_window.unbind(dim=-1),
            ),
            dim=-1,
        )
        values = torch.where(in_range[..., None], values, torch.zeros_like(values))
        masks = in_range[..., None].expand(*in_range.shape, len(TARGET_NAMES)).clone()
        for family_index, family_mask in enumerate(family_return_masks):
            masks[..., family_index] &= family_mask
        masks[..., 3:5] &= excursion_valid[..., None]
        masks[..., 5] &= family_return_masks[2]
        masks[..., -4:] &= condition_authoritative
        horizon_values.append(values)
        horizon_masks.append(masks)
    return HorizonTargets(values=torch.stack(horizon_values, dim=-2), mask=torch.stack(horizon_masks, dim=-2))
