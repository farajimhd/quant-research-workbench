from __future__ import annotations

import torch

from research.bar_gpt.v1.schema import FEATURE_INDEX


def _family_names(prefix: str) -> tuple[str, ...]:
    return (
        f"{prefix}_present",
        f"{prefix}_close_return",
        f"{prefix}_open_gap",
        f"{prefix}_upper_excursion",
        f"{prefix}_lower_excursion",
        f"{prefix}_log_size",
        f"{prefix}_log_count",
        f"{prefix}_vwap_deviation_bps",
        f"{prefix}_size_cv",
    )


MODEL_FEATURE_NAMES: tuple[str, ...] = (
    *_family_names("trade"),
    *_family_names("bid"),
    *_family_names("ask"),
    "quote_pair_present",
    "log_quote_pair_count",
    "spread_close_bps",
    "spread_mean_bps",
    "spread_std_bps",
    "spread_range_bps",
    "midpoint_return",
    "microprice_lean_close_bps",
    "microprice_lean_mean_bps",
    "microprice_lean_std_bps",
    "queue_imbalance_close",
    "queue_imbalance_mean",
    "queue_imbalance_std",
    "locked_quote_fraction",
    "crossed_quote_fraction",
    "log_condition_count",
    "log_source_event_count",
)


def _column(raw: torch.Tensor, name: str) -> torch.Tensor:
    return raw[..., FEATURE_INDEX[name]].float()


def _previous_valid(value: torch.Tensor, valid: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    if value.ndim != 2:
        raise ValueError("stationary projection expects batched [B,T] columns")
    batch, length = value.shape
    row = torch.arange(length, device=value.device, dtype=torch.long).view(1, -1).expand(batch, -1)
    last = torch.where(valid, row, torch.full_like(row, -1)).cummax(dim=1).values
    previous = torch.cat((torch.full_like(last[:, :1], -1), last[:, :-1]), dim=1)
    available = previous >= 0
    safe = previous.clamp(min=0)
    gathered = torch.gather(value, 1, safe)
    return gathered, available


def _safe_log_ratio(numerator: torch.Tensor, denominator: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    ratio_valid = valid & (numerator > 0) & (denominator > 0)
    result = torch.zeros_like(numerator)
    result[ratio_valid] = torch.log(numerator[ratio_valid] / denominator[ratio_valid])
    return result


def project_stationary_features(raw_features: torch.Tensor) -> torch.Tensor:
    """Convert raw composable bar statistics to causal scale-stable model channels."""
    squeeze = raw_features.ndim == 2
    raw = raw_features.unsqueeze(0) if squeeze else raw_features
    if raw.ndim != 3:
        raise ValueError("raw_features must have shape [T,F] or [B,T,F]")
    output: list[torch.Tensor] = []
    eps = 1e-12
    for prefix in ("trade", "bid", "ask"):
        present = _column(raw, f"{prefix}_present") > 0
        open_price = _column(raw, f"{prefix}_open")
        high = _column(raw, f"{prefix}_high")
        low = _column(raw, f"{prefix}_low")
        close = _column(raw, f"{prefix}_close")
        size = _column(raw, f"{prefix}_size_sum")
        size_squared = _column(raw, f"{prefix}_size_squared_sum")
        price_size = _column(raw, f"{prefix}_price_size_sum")
        count = _column(raw, f"{prefix}_event_count")
        previous_close, previous_available = _previous_valid(close, present)
        valid_previous = present & previous_available
        close_return = _safe_log_ratio(close, previous_close, valid_previous)
        open_gap = _safe_log_ratio(open_price, previous_close, valid_previous)
        upper = torch.where(present & (open_price > 0) & (high >= open_price), torch.log(high.clamp_min(eps) / open_price.clamp_min(eps)), 0.0)
        lower = torch.where(present & (open_price > 0) & (low > 0) & (low <= open_price), torch.log(open_price.clamp_min(eps) / low.clamp_min(eps)), 0.0)
        vwap = torch.where((size > 0) & (price_size > 0), price_size / size.clamp_min(eps), close)
        vwap_bps = torch.where(present & (close > 0), (vwap / close.clamp_min(eps) - 1.0) * 10_000.0, 0.0)
        mean_size = size / count.clamp_min(1.0)
        variance = torch.where(
            size_squared > 0,
            (size_squared / count.clamp_min(1.0) - mean_size.square()).clamp_min(0.0),
            torch.zeros_like(size_squared),
        )
        size_cv = torch.where(count > 1, variance.sqrt() / mean_size.clamp_min(eps), 0.0)
        output.extend(
            (
                present.float(),
                torch.asinh(close_return * 100.0),
                torch.asinh(open_gap * 100.0),
                torch.asinh(upper * 100.0),
                torch.asinh(lower * 100.0),
                torch.log1p(size),
                torch.log1p(count),
                torch.asinh(vwap_bps / 10.0),
                torch.asinh(size_cv),
            )
        )
    quote_present = _column(raw, "quote_pair_present") > 0
    quote_count = _column(raw, "quote_pair_count")
    midpoint_close = _column(raw, "midpoint_close")
    midpoint_previous, midpoint_previous_available = _previous_valid(midpoint_close, quote_present)
    midpoint_return = _safe_log_ratio(midpoint_close, midpoint_previous, quote_present & midpoint_previous_available)
    midpoint_safe = midpoint_close.clamp_min(eps)
    spread_close = _column(raw, "spread_close")
    spread_mean = _column(raw, "spread_sum") / quote_count.clamp_min(1.0)
    spread_variance = (_column(raw, "spread_squared_sum") / quote_count.clamp_min(1.0) - spread_mean.square()).clamp_min(0.0)
    spread_range = (_column(raw, "spread_high") - _column(raw, "spread_low")).clamp_min(0.0)
    microprice_close = _column(raw, "microprice_close")
    microprice_mean = _column(raw, "microprice_sum") / quote_count.clamp_min(1.0)
    microprice_variance = (_column(raw, "microprice_squared_sum") / quote_count.clamp_min(1.0) - microprice_mean.square()).clamp_min(0.0)
    qi_mean = _column(raw, "queue_imbalance_sum") / quote_count.clamp_min(1.0)
    qi_variance = (_column(raw, "queue_imbalance_squared_sum") / quote_count.clamp_min(1.0) - qi_mean.square()).clamp_min(0.0)
    output.extend(
        (
            quote_present.float(),
            torch.log1p(quote_count),
            torch.asinh((spread_close / midpoint_safe) * 1_000.0),
            torch.asinh((spread_mean / midpoint_safe) * 1_000.0),
            torch.asinh((spread_variance.sqrt() / midpoint_safe) * 1_000.0),
            torch.asinh((spread_range / midpoint_safe) * 1_000.0),
            torch.asinh(midpoint_return * 100.0),
            torch.asinh(((microprice_close - midpoint_close) / midpoint_safe) * 1_000.0),
            torch.asinh(((microprice_mean - midpoint_close) / midpoint_safe) * 1_000.0),
            torch.asinh((microprice_variance.sqrt() / midpoint_safe) * 1_000.0),
            _column(raw, "queue_imbalance_close").clamp(-1.0, 1.0),
            qi_mean.clamp(-1.0, 1.0),
            qi_variance.sqrt().clamp(0.0, 1.0),
            (_column(raw, "locked_quote_count") / quote_count.clamp_min(1.0)).clamp(0.0, 1.0),
            (_column(raw, "crossed_quote_count") / quote_count.clamp_min(1.0)).clamp(0.0, 1.0),
            torch.log1p(_column(raw, "condition_nonzero_count")),
            torch.log1p(_column(raw, "source_event_count")),
        )
    )
    projected = torch.stack(output, dim=-1)
    return projected.squeeze(0) if squeeze else projected
