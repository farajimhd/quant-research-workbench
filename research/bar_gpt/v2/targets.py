from __future__ import annotations

from dataclasses import dataclass

import torch

from research.bar_gpt.v2.schema import FEATURE_INDEX


PRICE_FAMILIES: tuple[str, ...] = ("trade", "bid", "ask")
OHLC_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")
OHLC_RETURN_TARGET_NAMES: tuple[str, ...] = tuple(
    f"{family}_{field}_return" for family in PRICE_FAMILIES for field in OHLC_FIELDS
)
DIRECTION_TARGET_NAMES: tuple[str, ...] = OHLC_RETURN_TARGET_NAMES
DIRECTION_TARGET_COUNT = len(DIRECTION_TARGET_NAMES)
RETURN_TARGET_NAMES: tuple[str, ...] = OHLC_RETURN_TARGET_NAMES
RETURN_TARGET_COUNT = len(RETURN_TARGET_NAMES)
RETURN_CLASS_NAMES: tuple[str, ...] = (
    "negative",
    "neutral",
    "positive",
)
RETURN_CLASS_COUNT = len(RETURN_CLASS_NAMES)

# One contract applies to every stored OHLC return: negative below -1 bp,
# neutral on the inclusive [-1 bp, +1 bp] interval, and positive above +1 bp.
# The shards remain unchanged; labels are derived from their reversible return
# transform by mapping this human-readable simple-percentage threshold into
# stored-target space.
RETURN_CLASS_NEUTRAL_BPS = 1.0
RETURN_CLASS_NEUTRAL_PERCENT = RETURN_CLASS_NEUTRAL_BPS / 100.0
PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT: dict[int, float] = {
    5_000_000: RETURN_CLASS_NEUTRAL_PERCENT,
    30_000_000: RETURN_CLASS_NEUTRAL_PERCENT,
    60_000_000: RETURN_CLASS_NEUTRAL_PERCENT,
    300_000_000: RETURN_CLASS_NEUTRAL_PERCENT,
    900_000_000: RETURN_CLASS_NEUTRAL_PERCENT,
    3_600_000_000: RETURN_CLASS_NEUTRAL_PERCENT,
}
AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT: dict[str, float] = {
    "1s": RETURN_CLASS_NEUTRAL_PERCENT,
    "5s": RETURN_CLASS_NEUTRAL_PERCENT,
    "10s": RETURN_CLASS_NEUTRAL_PERCENT,
    "30s": RETURN_CLASS_NEUTRAL_PERCENT,
    "1m": RETURN_CLASS_NEUTRAL_PERCENT,
    "5m": RETURN_CLASS_NEUTRAL_PERCENT,
    "30m": RETURN_CLASS_NEUTRAL_PERCENT,
    "1h": RETURN_CLASS_NEUTRAL_PERCENT,
}


def transformed_return_to_percent(target: torch.Tensor) -> torch.Tensor:
    """Invert asinh(log_return * 100) into exact simple percentage return."""
    return torch.expm1(torch.sinh(target.double()) / 100.0).mul(100.0)


def return_class_labels(
    target: torch.Tensor,
    *,
    neutral_percent: float,
) -> torch.Tensor:
    """Map transformed returns to negative, neutral, or positive."""
    neutral = float(neutral_percent)
    if not 0.0 < neutral < 100.0:
        raise ValueError("return neutral threshold must be between 0 and 100 percent")
    # The shard target is already a monotonic transform of simple percentage
    # return. Convert the human-readable thresholds through that same transform
    # and compare in stored-target space. This makes boundary membership stable
    # for float32 shard values instead of decoding and crossing a boundary due
    # only to a second floating-point round trip.
    def encoded_threshold(percent: float) -> torch.Tensor:
        value = torch.as_tensor(percent, dtype=target.dtype, device=target.device)
        return torch.asinh(torch.log1p(value / 100.0) * 100.0)

    negative_neutral = encoded_threshold(-neutral)
    positive_neutral = encoded_threshold(neutral)
    labels = torch.zeros_like(target, dtype=torch.long)
    labels = torch.where(target >= negative_neutral, torch.ones_like(labels), labels)
    labels = torch.where(target > positive_neutral, torch.full_like(labels, 2), labels)
    return labels


def autoregressive_return_class_labels(target: torch.Tensor, view: str) -> torch.Tensor:
    try:
        neutral = AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT[view]
    except KeyError as exc:
        raise KeyError(f"no v2 return-class thresholds for autoregressive view {view!r}") from exc
    return return_class_labels(target, neutral_percent=neutral)


def physical_return_class_labels(target: torch.Tensor, horizons_us: tuple[int, ...]) -> torch.Tensor:
    if target.shape[-2] != len(horizons_us):
        raise ValueError("target horizon axis does not match horizons_us")
    labels = torch.empty_like(target, dtype=torch.long)
    for index, horizon_us in enumerate(horizons_us):
        try:
            neutral = PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT[int(horizon_us)]
        except KeyError as exc:
            raise KeyError(f"no v2 return-class thresholds for physical horizon {horizon_us}") from exc
        labels[..., index, :] = return_class_labels(
            target[..., index, :], neutral_percent=neutral
        )
    return labels

AUTOREGRESSIVE_TARGET_NAMES: tuple[str, ...] = (
    *OHLC_RETURN_TARGET_NAMES,
    "log_trade_volume",
    "log_trade_count",
    "trade_available",
    "bid_available",
    "ask_available",
    "quote_pair_available",
)
TARGET_NAMES: tuple[str, ...] = (
    *OHLC_RETURN_TARGET_NAMES,
    "trade_realized_volatility",
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
AUTOREGRESSIVE_BINARY_TARGET_NAMES = AUTOREGRESSIVE_TARGET_NAMES[-4:]
AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES = AUTOREGRESSIVE_TARGET_NAMES[:-4]
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
) -> HorizonTargets:
    """Build targets for the next stored nonempty completed bar.

    Sparse event time is deliberate: a wall-clock gap does not invalidate the
    transition. Every price-family OHLC return uses that family's current
    forward-filled close as its base and the next bar's direct OHLC values.
    """
    if raw.ndim not in {2, 3}:
        raise ValueError("raw bar features must have shape [T,F] or [B,T,F]")
    time_dim = raw.shape[-2]
    if time_dim < 2:
        shape = (*raw.shape[:-2], 0, len(AUTOREGRESSIVE_TARGET_NAMES))
        return HorizonTargets(raw.new_zeros(shape), torch.zeros(shape, dtype=torch.bool, device=raw.device))
    quote_present = _column(raw, "quote_pair_present") > 0
    price_returns: list[torch.Tensor] = []
    price_masks: list[torch.Tensor] = []
    for family in PRICE_FAMILIES:
        present = _column(raw, f"{family}_present") > 0
        direct_close = _column(raw, f"{family}_close")
        close_source_valid = (direct_close > 0) if family == "trade" else present
        close, close_valid = _forward_fill(direct_close, close_source_valid)
        base = close[..., :-1]
        family_values = tuple(_column(raw, f"{family}_{field}")[..., 1:] for field in OHLC_FIELDS)
        for value in family_values:
            field_mask = close_valid[..., :-1] & (base > 0) & torch.isfinite(value) & (value > 0)
            price_returns.append(torch.where(field_mask, torch.log(value / base), 0.0))
            price_masks.append(field_mask)
    availability = tuple((_column(raw, f"{family}_present")[..., 1:] > 0).float() for family in ("trade", "bid", "ask"))
    values = torch.stack(
        (
            *(torch.asinh(value * 100.0) for value in price_returns),
            torch.log1p(_column(raw, "trade_size_sum")[..., 1:]),
            torch.log1p(_column(raw, "trade_event_count")[..., 1:]),
            *availability,
            quote_present[..., 1:].float(),
        ),
        dim=-1,
    )
    mask = torch.ones_like(values, dtype=torch.bool)
    mask[..., :DIRECTION_TARGET_COUNT] = torch.stack(price_masks, dim=-1)
    if bar_start_us is not None:
        if bar_start_us.shape != raw.shape[:-1]:
            raise ValueError("bar_start_us must align with raw bar rows")
        if torch.any(bar_start_us[..., 1:] <= bar_start_us[..., :-1]):
            raise ValueError("bar_start_us must be strictly increasing")
    return HorizonTargets(values=values, mask=mask)


def _column(raw: torch.Tensor, name: str) -> torch.Tensor:
    return raw[..., FEATURE_INDEX[name]].float()


def _forward_fill(values: torch.Tensor, valid: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    rows = torch.arange(values.shape[-1], device=values.device, dtype=torch.long)
    rows = rows.view(*([1] * (values.ndim - 1)), -1).expand_as(values)
    last = torch.where(valid, rows, torch.full_like(rows, -1)).cummax(dim=-1).values
    available = last >= 0
    return torch.gather(values, -1, last.clamp(min=0)), available


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
    family_valid = {
        family: (
            _column(raw_one_second, f"{family}_close") > 0
            if family == "trade"
            else _column(raw_one_second, f"{family}_present") > 0
        )
        for family in PRICE_FAMILIES
    }
    family_reference = {
        family: _forward_fill(
            _column(raw_one_second, f"{family}_close") * share_factors,
            family_valid[family],
        )
        for family in PRICE_FAMILIES
    }
    canonical_ohlc = {
        (family, field): _column(raw_one_second, f"{family}_{field}") * share_factors
        for family in PRICE_FAMILIES
        for field in OHLC_FIELDS
    }
    field_valid = {
        (family, field): (
            _column(raw_one_second, f"{family}_{field}") > 0
            if family == "trade"
            else family_valid[family]
        )
        for family in PRICE_FAMILIES
        for field in OHLC_FIELDS
    }
    field_valid_indices = {
        key: torch.nonzero(valid, as_tuple=False).flatten()
        for key, valid in field_valid.items()
    }
    family_high = torch.stack(tuple(
        torch.where(
            field_valid[(family, "high")],
            canonical_ohlc[(family, "high")],
            torch.full_like(canonical_ohlc[(family, "high")], -torch.inf),
        )
        for family in PRICE_FAMILIES
    ), dim=-1)
    family_low = torch.stack(tuple(
        torch.where(
            field_valid[(family, "low")],
            canonical_ohlc[(family, "low")],
            torch.full_like(canonical_ohlc[(family, "low")], torch.inf),
        )
        for family in PRICE_FAMILIES
    ), dim=-1)
    trade_reference, trade_reference_valid = family_reference["trade"]
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
        maximum_prices = _range_extreme(family_high, starts, ends, reducer="max")
        minimum_prices = _range_extreme(family_low, starts, ends, reducer="min")
        ohlc_returns: list[torch.Tensor] = []
        ohlc_masks: list[torch.Tensor] = []
        family_close_window_masks: dict[str, torch.Tensor] = {}
        for family_index, family in enumerate(PRICE_FAMILIES):
            reference, reference_valid = family_reference[family]
            base_price = reference[origin_indices]
            base_valid = reference_valid[origin_indices] & torch.isfinite(base_price) & (base_price > 0)
            open_indices = field_valid_indices[(family, "open")]
            open_positions = torch.searchsorted(open_indices, starts)
            has_open = open_positions < open_indices.numel()
            safe_open_positions = open_positions.clamp(max=max(open_indices.numel() - 1, 0))
            first_open_indices = (
                open_indices[safe_open_positions]
                if open_indices.numel()
                else torch.zeros_like(starts)
            )
            has_open &= first_open_indices < ends
            close_prefix = torch.cat((
                torch.zeros(1, dtype=torch.int64, device=raw_one_second.device),
                field_valid[(family, "close")].to(torch.int64).cumsum(-1),
            ))
            has_close = close_prefix[ends] - close_prefix[starts] > 0
            high_valid = torch.isfinite(maximum_prices[:, family_index]) & (maximum_prices[:, family_index] > 0)
            low_valid = torch.isfinite(minimum_prices[:, family_index]) & (minimum_prices[:, family_index] > 0)
            family_close_window_masks[family] = in_range & base_valid & has_close
            field_values = {
                "open": canonical_ohlc[(family, "open")][first_open_indices],
                "high": maximum_prices[:, family_index],
                "low": minimum_prices[:, family_index],
                "close": reference[safe_endpoint],
            }
            field_window_masks = {
                "open": in_range & base_valid & has_open,
                "high": in_range & base_valid & high_valid,
                "low": in_range & base_valid & low_valid,
                "close": in_range & base_valid & has_close,
            }
            for field in OHLC_FIELDS:
                value = field_values[field]
                mask = field_window_masks[field] & torch.isfinite(value) & (value > 0)
                ohlc_returns.append(torch.where(mask, torch.log(value / base_price), 0.0))
                ohlc_masks.append(mask)
        realized = (variance_prefix[ends] - variance_prefix[starts]).clamp_min(0.0).sqrt().to(raw_one_second.dtype)
        realized = torch.where(family_close_window_masks["trade"], realized, torch.zeros_like(realized))
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
                *(torch.asinh(value * 100.0) for value in ohlc_returns),
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
        masks[..., :DIRECTION_TARGET_COUNT] = torch.stack(ohlc_masks, dim=-1)
        masks[..., DIRECTION_TARGET_COUNT] &= family_close_window_masks["trade"]
        masks[..., -4:] &= condition_authoritative
        horizon_values.append(values)
        horizon_masks.append(masks)
    return HorizonTargets(values=torch.stack(horizon_values, dim=-2), mask=torch.stack(horizon_masks, dim=-2))
