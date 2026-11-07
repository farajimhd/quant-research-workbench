"""Append nine deterministic time features to the 18-channel shard values.

This module contains no learned layers, loading, training, or market scaling.
It uses UTC completed-bar timestamps directly, without a session schedule.
Read ``BarInputTransformV1.forward`` for the numerical feature definitions.
No timezone conversion or exchange-session validation is performed.
"""

from dataclasses import dataclass

import torch
from torch import nn

from research.bar_gpt.v5.shards.phase1_contract import CHANNELS, FRAMES, WIDTH


TIME_CHANNELS = (
    "bar_index", "log_timeframe_seconds",
    "intraday_sin", "intraday_cos", "log_gap_ratio",
    "weekday_sin", "weekday_cos", "iso_week_sin", "iso_week_cos",
)
FEATURE_NAMES = CHANNELS + TIME_CHANNELS  # Stable order for all [B,64,27] arrays.
DAY_US = 86_400_000_000  # Exact POSIX UTC day; leap seconds are not represented.


@dataclass(frozen=True)
class TransformedBars:
    """Model inputs: Float32 ``values[B,64,27]`` and bool ``valid[B,64,27]``.

    Feature order is ``FEATURE_NAMES``. Invalid values are zero placeholders,
    distinguishable from observed zeros through ``valid``. No ticker, absolute
    timestamp, or year number is included. Calendar/timeframe context is explicit.
    Artificial reconstruction masking is separate from these evidence masks.
    """

    values: torch.Tensor
    valid: torch.Tensor


class BarInputTransformV1(nn.Module):
    """Append UTC context to market tensors without changing market semantics.

    Construct with ``BarInputTransformV1().to(device)``; inputs and outputs stay
    on that device. All date arithmetic is vectorized Torch. This module neither
    identifies trading sessions nor checks holiday coverage or session grids.
    """

    def __init__(self):
        """Register supported shard durations; no calendar or external input is needed."""
        super().__init__()
        self.register_buffer("supported_frames", torch.tensor(FRAMES, dtype=torch.int64))

    @staticmethod
    def _utc_week_fields(days: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return Monday-zero weekday, ISO week, and ISO-year length, all int64 [B,64].

        ``days`` is the integer UTC day since 1970-01-01, derived from shard ends.
        ISO weeks belong to the year containing their Thursday. Integer Gregorian
        400-year eras resolve that year without date libraries or lookup tables.
        Intermediate years are never included in the model input.
        """
        weekday = (days + 3) % 7  # The Unix epoch was Thursday (Monday-zero index 3).
        thursday = days + (3 - weekday)  # [B,64], Thursday in each bar's ISO week.
        shifted = thursday + 719468  # Rebase to 0000-03-01 for March-based leap-year arithmetic.
        era = shifted // 146097  # Gregorian leap-day pattern repeats every 400 years.
        day_of_era = shifted % 146097  # [0,146096], bounded within one era.
        year_of_era = (
            day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096
        ) // 365  # Correct for four-year cycles and non-leap century boundaries.
        march_day = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
        march_month = (5 * march_day + 2) // 153  # March=0 through February=11.
        year = era * 400 + year_of_era + march_month // 10  # January/February belong to next year.
        previous = year - 1  # Count leap days completed before January 1 of this ISO year.
        january_1 = (
            365 * (year - 1970)
            + previous // 4 - 1969 // 4
            - previous // 100 + 1969 // 100
            + previous // 400 - 1969 // 400
        )  # [B,64], January 1 as UTC days since the Unix epoch.
        week = (thursday - january_1) // 7 + 1  # First Thursday identifies ISO week 1.
        january_weekday = (january_1 + 3) % 7
        leap = (year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))
        weeks_in_year = 52 + ((january_weekday == 3) | ((january_weekday == 2) & leap)).long()
        return weekday, week, weeks_in_year

    def forward(
        self,
        values: torch.Tensor,
        valid: torch.Tensor,
        end_us: torch.Tensor,
        timeframe_us: torch.Tensor | int,
    ) -> TransformedBars:
        """Transform one batch entirely with vectorized Torch on the input device.

        Args:
            values: Torch floating [B,64,18], from shard ``values``; prices and activity
                remain unscaled. Invalid fields may hold NaN.
            valid: Torch bool or 0/1 integer [B,64,18], from shard evidence masks.
            end_us: Torch int64 [B,64], UTC completed-bar end timestamps in microseconds,
                strictly increasing for observed bars, then zero right padding.
                Cross-session gaps are supported for hourly/daily contexts.
            timeframe_us: Python int or Torch int64 scalar/[B], from shard timeframe metadata;
                supported durations are 100ms, 1s, 10s, 1m, 1h, and 1d.

        Returns:
            TransformedBars containing Float32 values and bool validity [B,64,27].
            The first gap is zero with validity False: no predecessor is supplied.
            The remaining eight time channels are valid for every accepted bar.

        Formulas:
            index = i/63; timeframe = log1p(duration_seconds).
            clock = sin/cos(2*pi*UTC_seconds_since_midnight/86400).
            gap[t] = log1p((end[t]-end[t-1])/timeframe_us), for t>0.
            weekday = sin/cos(2*pi*UTC_Monday_zero_weekday/7).
            week = sin/cos(2*pi*(UTC_ISO_week-1)/weeks_in_that_ISO_year).
            Daily bars use their actual UTC end date, not an inferred exchange date.

        Move this module and all input tensors to the same device before calling.
        No Pandas/NumPy operations or bulk CPU transfers occur in forward. Eager
        validation raises ValueError on bad evidence; these scalar checks synchronize
        CUDA, so this checked path does not promise asynchronous execution.

        These transformations never use market-value statistics, so calculating
        them before artificial masking cannot leak hidden prices or activity.
        """
        if not all(isinstance(item, torch.Tensor) for item in (values, valid, end_us)):
            raise TypeError("values, valid, and end_us must be Torch tensors.")
        if any(item.device != self.supported_frames.device for item in (values, valid, end_us)):
            raise ValueError("Move the transform and all input tensors to the same device.")
        if values.ndim != 3 or values.shape[1:] != (WIDTH, len(CHANNELS)) or not len(values):
            raise ValueError("values must have nonempty shape [B,64,18].")
        batch, device = len(values), values.device
        if not values.is_floating_point():
            raise ValueError("Market values must be floating point.")
        mask_types = (torch.bool, torch.uint8, torch.int8, torch.int16, torch.int32, torch.int64)
        if (valid.shape != values.shape or valid.dtype not in mask_types
                or not ((valid == 0) | (valid == 1)).all()):
            raise ValueError("valid must contain boolean/0-1 evidence masks [B,64,18].")
        observed = valid.bool()  # [B,64,18]; evidence masks, not augmentation masks.
        if not (torch.isfinite(values) | ~observed).all():
            raise ValueError("Observed market values must be finite.")
        if end_us.shape != (batch, WIDTH) or end_us.dtype != torch.int64 or (end_us < 0).any():
            raise ValueError("end_us must contain int64 timestamps or zero right padding [B,64].")
        present = end_us > 0  # [B,64]; padding is not market evidence or a time observation.
        if (~present[:, 0]).any() or (present[:, 1:] & ~present[:, :-1]).any():
            raise ValueError("Each sample needs an observed prefix followed only by right padding.")
        if (observed & ~present[..., None]).any():
            raise ValueError("Padded bars cannot contain valid market observations.")
        gaps = torch.diff(end_us, dim=1)  # [B,64] → [B,63], exact integer subtraction.
        if ((gaps <= 0) & present[:, 1:]).any():
            raise ValueError("Bar end timestamps must increase strictly within each sample.")
        gaps = torch.where(present[:, 1:], gaps, 0)  # Avoid logarithms of artificial negative padding gaps.
        if type(timeframe_us) is int:
            timeframe_us = torch.tensor(timeframe_us, dtype=torch.int64, device=device)
        if (not isinstance(timeframe_us, torch.Tensor) or timeframe_us.dtype != torch.int64
                or timeframe_us.device != device or timeframe_us.shape not in ((), (batch,))):
            raise ValueError("timeframe_us must be an int64 scalar or [B] on the input device.")
        if not torch.isin(timeframe_us, self.supported_frames).all():
            raise ValueError("Unsupported timeframe for the current shard contract.")
        frames = timeframe_us.expand(batch)  # Scalar/[B] → [B], without copying.

        time_values = torch.zeros((batch, WIDTH, len(TIME_CHANNELS)), dtype=torch.float32, device=device)
        # Relative sequence position broadcasts [64] across all samples → [B,64].
        time_values[..., 0] = torch.arange(WIDTH, device=device, dtype=torch.float32) / (WIDTH - 1)
        time_values[..., 1] = torch.log1p(frames.double() / 1_000_000)[:, None]  # [B,1] → [B,64].

        # Reduce exact int64 epochs BEFORE converting to float: preserve subsecond precision.
        seconds = (end_us % DAY_US).double() / 1_000_000
        clock_angle = 2 * torch.pi * seconds / 86400  # [B,64], UTC bar-end clock phase.
        time_values[..., 2] = torch.sin(clock_angle)
        time_values[..., 3] = torch.cos(clock_angle)
        time_values[:, 1:, 4] = torch.log1p(gaps.double() / frames[:, None])  # [B,63].
        weekday, week, weeks_in_year = self._utc_week_fields(end_us // DAY_US)
        weekday_angle = 2 * torch.pi * weekday.double() / 7  # UTC Monday=0.
        time_values[..., 5] = torch.sin(weekday_angle)
        time_values[..., 6] = torch.cos(weekday_angle)
        week_angle = 2 * torch.pi * (week - 1).double() / weeks_in_year
        time_values[..., 7] = torch.sin(week_angle)
        time_values[..., 8] = torch.cos(week_angle)

        time_valid = present[..., None].expand_as(time_values).clone()  # [B,64] → [B,64,9].
        time_valid[:, 0, 4] = False  # No predecessor exists in this shard sample.
        time_values = torch.where(time_valid, time_values, 0)  # Padding carries no clock or sequence signal.
        clean_values = torch.where(observed, values, 0).float()  # [B,64,18], unscaled, differentiable.
        if not torch.isfinite(clean_values).all():
            raise ValueError("Market values overflow Float32.")
        return TransformedBars(
            values=torch.cat((clean_values, time_values), dim=-1),  # 18+9 → [B,64,27].
            valid=torch.cat((observed, time_valid), dim=-1),  # Matching [B,64,27] evidence.
        )
