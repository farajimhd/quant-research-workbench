from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.bar_gpt.v1.schema import FEATURE_INDEX


TARGET_NAMES: tuple[str, ...] = (
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
CONDITION_TARGET_NAMES: tuple[str, ...] = TARGET_NAMES[-4:]
BINARY_TARGET_NAMES: tuple[str, ...] = TARGET_NAMES[-8:]
CONTINUOUS_TARGET_NAMES: tuple[str, ...] = TARGET_NAMES[:-8]
AVAILABILITY_TARGET_NAMES: tuple[str, ...] = BINARY_TARGET_NAMES
CONTINUOUS_TARGET_COUNT = len(CONTINUOUS_TARGET_NAMES)
AVAILABILITY_TARGET_COUNT = len(AVAILABILITY_TARGET_NAMES)


@dataclass(slots=True)
class HorizonTargets:
    values: torch.Tensor
    mask: torch.Tensor


def build_next_bar_targets(raw: torch.Tensor) -> HorizonTargets:
    """Build next completed-bar targets for an arbitrary aggregated timeframe."""
    if raw.ndim != 2:
        raise ValueError("raw bar features must have shape [T,F]")
    if raw.shape[0] < 2:
        shape = (0, len(TARGET_NAMES))
        return HorizonTargets(raw.new_zeros(shape), torch.zeros(shape, dtype=torch.bool, device=raw.device))
    trade_present = _column(raw, "trade_present") > 0
    quote_present = _column(raw, "quote_pair_present") > 0
    trade_close = _column(raw, "trade_close")
    midpoint_close = _column(raw, "midpoint_close")
    reference_raw = torch.where(quote_present, midpoint_close, trade_close)
    reference_valid_raw = (quote_present & (midpoint_close > 0)) | (trade_present & (trade_close > 0))
    reference, reference_valid = _forward_fill(reference_raw, reference_valid_raw)
    base = reference[:-1]
    endpoint = reference[1:]
    price_valid = reference_valid[:-1] & reference_valid[1:] & (base > 0) & (endpoint > 0)
    endpoint_return = torch.where(price_valid, torch.log(endpoint / base), 0.0)
    next_trade = trade_present[1:]
    high = _column(raw, "trade_high")[1:]
    low = _column(raw, "trade_low")[1:]
    excursion_valid = price_valid & next_trade & (high > 0) & (low > 0)
    upper = torch.where(excursion_valid, torch.log(high.clamp_min(1e-12) / base).clamp_min(0.0), 0.0)
    lower = torch.where(excursion_valid, torch.log(base / low.clamp_min(1e-12)).clamp_min(0.0), 0.0)
    availability = tuple((_column(raw, f"{family}_present")[1:] > 0).float() for family in ("trade", "bid", "ask"))
    values = torch.stack(
        (
            torch.asinh(endpoint_return * 100.0),
            torch.asinh(upper * 100.0),
            torch.asinh(lower * 100.0),
            torch.asinh(endpoint_return.abs() * 100.0),
            torch.log1p(_column(raw, "trade_size_sum")[1:]),
            torch.log1p(_column(raw, "trade_event_count")[1:]),
            *availability,
            quote_present[1:].float(),
            *raw.new_zeros((4, raw.shape[0] - 1)),
        ),
        dim=-1,
    )
    mask = torch.ones_like(values, dtype=torch.bool)
    mask[:, :4] &= price_valid[:, None]
    mask[:, 1:3] &= excursion_valid[:, None]
    mask[:, 3] = False  # exact intrabar realized volatility is unavailable after aggregation
    mask[:, -4:] = False  # exact event conditions are supervised only by physical-horizon sidecars
    return HorizonTargets(values=values, mask=mask)


def _column(raw: torch.Tensor, name: str) -> torch.Tensor:
    return raw[:, FEATURE_INDEX[name]].float()


def _forward_fill(values: torch.Tensor, valid: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    rows = torch.arange(values.shape[0], device=values.device, dtype=torch.long)
    last = torch.where(valid, rows, torch.full_like(rows, -1)).cummax(dim=0).values
    available = last >= 0
    return values[last.clamp(min=0)], available


def _window_max(values: torch.Tensor, steps: int) -> torch.Tensor:
    if values.numel() <= steps:
        return values.new_empty((0,))
    return F.max_pool1d(values[1:].view(1, 1, -1), kernel_size=steps, stride=1).view(-1)


def build_physical_horizon_targets(
    raw_one_second: torch.Tensor,
    origin_indices: torch.Tensor,
    horizons_us: torch.Tensor,
    *,
    base_timeframe_us: int = 1_000_000,
    share_factors: torch.Tensor | None = None,
    condition_flags: torch.Tensor | None = None,
) -> HorizonTargets:
    """Build direct horizons in each origin's share basis without copied windows."""
    if raw_one_second.ndim != 2 or origin_indices.ndim != 1 or horizons_us.ndim != 1:
        raise ValueError("expected raw [T,F], origins [N], and horizons [H]")
    if torch.any(horizons_us % int(base_timeframe_us) != 0):
        raise ValueError("physical horizons must be integral multiples of the base timeframe")
    if share_factors is None:
        share_factors = torch.ones(raw_one_second.shape[0], dtype=torch.float64, device=raw_one_second.device)
    if share_factors.ndim != 1 or share_factors.shape[0] != raw_one_second.shape[0]:
        raise ValueError("share_factors must align one-to-one with raw rows")
    if torch.any(~torch.isfinite(share_factors)) or torch.any(share_factors <= 0):
        raise ValueError("share_factors must be finite and positive")
    share_factors = share_factors.to(device=raw_one_second.device, dtype=raw_one_second.dtype)
    if condition_flags is None:
        condition_flags = torch.zeros((raw_one_second.shape[0], 4), dtype=torch.float32, device=raw_one_second.device)
        condition_authoritative = False
    else:
        if condition_flags.shape != (raw_one_second.shape[0], 4):
            raise ValueError("condition_flags must have shape [T,4] aligned to raw rows")
        condition_flags = condition_flags.to(device=raw_one_second.device, dtype=torch.float32)
        condition_authoritative = True
    trade_present = _column(raw_one_second, "trade_present") > 0
    quote_present = _column(raw_one_second, "quote_pair_present") > 0
    trade_close = _column(raw_one_second, "trade_close")
    midpoint_close = _column(raw_one_second, "midpoint_close")
    reference_raw = torch.where(quote_present, midpoint_close, trade_close)
    reference_valid_raw = (quote_present & (midpoint_close > 0)) | (trade_present & (trade_close > 0))
    reference, reference_valid = _forward_fill(reference_raw, reference_valid_raw)
    canonical_reference = reference * share_factors
    previous = torch.cat((canonical_reference[:1], canonical_reference[:-1]))
    returns = torch.where(
        reference_valid & (canonical_reference > 0) & (previous > 0),
        torch.log(canonical_reference / previous),
        0.0,
    ).float()
    variance_prefix = torch.cat((returns.new_zeros(1), returns.square().cumsum(0)))
    canonical_volume = _column(raw_one_second, "trade_size_sum") / share_factors
    volume_prefix = torch.cat((canonical_volume.new_zeros(1), canonical_volume.cumsum(0)))
    trade_count_prefix = torch.cat((returns.new_zeros(1), _column(raw_one_second, "trade_event_count").cumsum(0)))
    availability_prefix = {
        family: torch.cat((returns.new_zeros(1), (_column(raw_one_second, f"{family}_present") > 0).float().cumsum(0)))
        for family in ("trade", "bid", "ask")
    }
    quote_availability_prefix = torch.cat((returns.new_zeros(1), quote_present.float().cumsum(0)))
    canonical_trade_high = _column(raw_one_second, "trade_high") * share_factors
    canonical_trade_low = _column(raw_one_second, "trade_low") * share_factors
    trade_high = torch.where(trade_present, canonical_trade_high, torch.full_like(canonical_reference, -torch.inf))
    trade_low_for_max = torch.where(trade_present, -canonical_trade_low, torch.full_like(canonical_reference, -torch.inf))
    horizon_values: list[torch.Tensor] = []
    horizon_masks: list[torch.Tensor] = []
    total = raw_one_second.shape[0]
    for horizon in horizons_us.tolist():
        steps = int(horizon) // int(base_timeframe_us)
        endpoint = origin_indices + steps
        in_range = endpoint < total
        safe_endpoint = endpoint.clamp(max=max(total - 1, 0))
        base_price = canonical_reference[origin_indices]
        endpoint_price = canonical_reference[safe_endpoint]
        price_valid = in_range & reference_valid[origin_indices] & reference_valid[safe_endpoint] & (base_price > 0) & (endpoint_price > 0)
        endpoint_return = torch.where(price_valid, torch.log(endpoint_price / base_price), 0.0)
        maxima = _window_max(trade_high, steps)
        minima = -_window_max(trade_low_for_max, steps)
        range_available = origin_indices < maxima.numel()
        if maxima.numel():
            safe_origin = origin_indices.clamp(max=maxima.numel() - 1)
            max_price = torch.where(range_available, maxima[safe_origin], base_price)
            min_price = torch.where(range_available, minima[safe_origin], base_price)
        else:
            max_price = base_price
            min_price = base_price
        excursion_valid = price_valid & range_available & torch.isfinite(max_price) & torch.isfinite(min_price)
        upper = torch.where(excursion_valid, torch.log(max_price.clamp_min(1e-12) / base_price).clamp_min(0.0), 0.0)
        lower = torch.where(excursion_valid, torch.log(base_price / min_price.clamp_min(1e-12)).clamp_min(0.0), 0.0)
        starts = origin_indices + 1
        ends = safe_endpoint + 1
        realized = (variance_prefix[ends] - variance_prefix[starts]).clamp_min(0.0).sqrt()
        volume = (volume_prefix[ends] - volume_prefix[starts]) * share_factors[origin_indices]
        trade_count = trade_count_prefix[ends] - trade_count_prefix[starts]
        availability = [
            (availability_prefix[family][ends] - availability_prefix[family][starts] > 0).float()
            for family in ("trade", "bid", "ask")
        ]
        quote_available = (quote_availability_prefix[ends] - quote_availability_prefix[starts] > 0).float()
        condition_window = torch.stack(
            [
                _window_max(condition_flags[:, column], steps)[origin_indices.clamp(max=max(total - steps - 1, 0))]
                if total > steps else torch.zeros_like(origin_indices, dtype=torch.float32)
                for column in range(4)
            ],
            dim=-1,
        )
        condition_window = torch.where(in_range[:, None], condition_window, torch.zeros_like(condition_window))
        values = torch.stack(
            (
                torch.asinh(endpoint_return * 100.0),
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
        masks = in_range[:, None].expand(-1, len(TARGET_NAMES)).clone()
        masks[:, :4] &= price_valid[:, None]
        masks[:, 1:3] &= excursion_valid[:, None]
        masks[:, -4:] &= condition_authoritative
        horizon_values.append(values)
        horizon_masks.append(masks)
    return HorizonTargets(values=torch.stack(horizon_values, dim=1), mask=torch.stack(horizon_masks, dim=1))
