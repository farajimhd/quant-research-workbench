from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import torch

from research.bar_gpt.v3.schema import FEATURE_INDEX, SESSION_TIMEZONE


_SESSION_OPEN_SECONDS = 4 * 60 * 60
_SESSION_LENGTH_SECONDS = 16 * 60 * 60
_SESSION_ZONE = ZoneInfo(SESSION_TIMEZONE)


def _family_names(prefix: str) -> tuple[str, ...]:
    names = (
        f"{prefix}_close_return",
        f"{prefix}_open_gap",
        f"{prefix}_high_from_open_return",
        f"{prefix}_low_from_open_return",
        f"{prefix}_log_size",
        f"{prefix}_vwap_deviation_bps",
        f"{prefix}_size_cv",
    )
    if prefix == "trade":
        return (*names[:5], "trade_log_count", *names[5:])
    return names


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
    "log_condition_count",
    "halt_pause_present",
    "log_halt_pause_count",
    "resume_present",
    "log_resume_count",
    "news_risk_present",
    "log_news_risk_count",
    "luld_limit_state_present",
    "log_luld_limit_state_count",
    "log_source_event_count",
    "log_elapsed_wall_ratio",
    "sequence_boundary",
    "session_progress_sin",
    "session_progress_cos",
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


def _session_progress_seconds(starts_us: torch.Tensor) -> torch.Tensor:
    """Vectorized New York wall-clock position; only unique UTC dates cross Python."""
    seconds = torch.div(starts_us, 1_000_000, rounding_mode="floor")
    utc_days = torch.div(seconds, 86_400, rounding_mode="floor")
    unique_days, inverse = torch.unique(utc_days, sorted=True, return_inverse=True)
    offsets = []
    for day in unique_days.detach().cpu().tolist():
        instant = dt.datetime.fromtimestamp(int(day) * 86_400, tz=dt.timezone.utc)
        offset = instant.astimezone(_SESSION_ZONE).utcoffset()
        offsets.append(int(offset.total_seconds()) if offset is not None else 0)
    offset_tensor = torch.as_tensor(offsets, dtype=seconds.dtype, device=seconds.device)
    local_seconds = torch.remainder(seconds + offset_tensor[inverse], 86_400)
    return (local_seconds - _SESSION_OPEN_SECONDS).clamp(0, _SESSION_LENGTH_SECONDS).float()


def project_stationary_features(
    raw_features: torch.Tensor,
    bar_start_us: torch.Tensor | None = None,
    *,
    timeframe_us: int | None = None,
) -> torch.Tensor:
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
        # Trade condition categories authorize open/extrema/last/volume
        # independently.  Quote fields share one paired-quote eligibility bit.
        open_valid = (open_price > 0) if prefix == "trade" else present
        high_valid = (high > 0) if prefix == "trade" else present
        low_valid = (low > 0) if prefix == "trade" else present
        close_valid = (close > 0) if prefix == "trade" else present
        previous_close, previous_available = _previous_valid(close, close_valid)
        close_return = _safe_log_ratio(close, previous_close, close_valid & previous_available)
        open_gap = _safe_log_ratio(open_price, previous_close, open_valid & previous_available)
        high_from_open = _safe_log_ratio(high, open_price, high_valid & open_valid)
        low_from_open = _safe_log_ratio(low, open_price, low_valid & open_valid)
        vwap_size = _column(raw, "trade_price_eligible_size_sum") if prefix == "trade" else size
        vwap = torch.where((vwap_size > 0) & (price_size > 0), price_size / vwap_size.clamp_min(eps), close)
        vwap_bps = torch.where((vwap_size > 0) & close_valid, (vwap / close.clamp_min(eps) - 1.0) * 10_000.0, 0.0)
        mean_size = size / count.clamp_min(1.0)
        variance = torch.where(
            size_squared > 0,
            (size_squared / count.clamp_min(1.0) - mean_size.square()).clamp_min(0.0),
            torch.zeros_like(size_squared),
        )
        size_cv = torch.where(count > 1, variance.sqrt() / mean_size.clamp_min(eps), 0.0)
        family_output = [
            torch.asinh(close_return * 100.0),
            torch.asinh(open_gap * 100.0),
            torch.asinh(high_from_open * 100.0),
            torch.asinh(low_from_open * 100.0),
            torch.log1p(size),
        ]
        if prefix == "trade":
            family_output.append(torch.log1p(count))
        family_output.extend((torch.asinh(vwap_bps / 10.0), torch.asinh(size_cv)))
        output.extend(family_output)
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
            torch.log1p(_column(raw, "condition_nonzero_count")),
            (_column(raw, "condition_halt_pause_count") > 0).float(),
            torch.log1p(_column(raw, "condition_halt_pause_count")),
            (_column(raw, "condition_resume_count") > 0).float(),
            torch.log1p(_column(raw, "condition_resume_count")),
            (_column(raw, "condition_news_risk_count") > 0).float(),
            torch.log1p(_column(raw, "condition_news_risk_count")),
            (_column(raw, "condition_luld_limit_state_count") > 0).float(),
            torch.log1p(_column(raw, "condition_luld_limit_state_count")),
            torch.log1p(_column(raw, "source_event_count")),
        )
    )
    duration = max(1, int(timeframe_us or 1_000_000))
    if bar_start_us is None:
        starts = torch.zeros(raw.shape[:2], dtype=torch.long, device=raw.device)
        elapsed_ratio = torch.ones(raw.shape[:2], dtype=torch.float32, device=raw.device)
        boundary = torch.zeros_like(elapsed_ratio)
        progress_seconds = torch.zeros_like(elapsed_ratio)
    else:
        starts = bar_start_us.unsqueeze(0) if bar_start_us.ndim == 1 else bar_start_us
        if starts.shape != raw.shape[:2]:
            raise ValueError("bar_start_us must align with raw [T] or [B,T]")
        elapsed = torch.cat((torch.full_like(starts[:, :1], duration), starts[:, 1:] - starts[:, :-1]), dim=1)
        elapsed_ratio = elapsed.float().clamp_min(0) / float(duration)
        boundary = (elapsed_ratio > 1.5).float()
        progress_seconds = _session_progress_seconds(starts)
    phase = progress_seconds / float(_SESSION_LENGTH_SECONDS) * (2.0 * torch.pi)
    output.extend((torch.log1p(elapsed_ratio), boundary, torch.sin(phase), torch.cos(phase)))
    projected = torch.stack(output, dim=-1)
    return projected.squeeze(0) if squeeze else projected
